"""FastAPI web layer for PostCode Sentinel.

Serves the single-page UI and exposes the existing deterministic analyzer over
HTTP. The risk engine (``sentinel.rules`` / ``sentinel.scoring``) is used
unchanged; this module only parses input, calls it and shapes the response.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from sentinel.diff_parser import DiffParseError, parse_unified_diff
from sentinel.report import render_markdown
from sentinel.scoring import analyze_changeset

WEB_DIR = Path(__file__).resolve().parent
INDEX_FILE = WEB_DIR / "index.html"

app = FastAPI(title="PostCode Sentinel", version="0.1.0")


MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_DIFF_CHARS = MAX_REQUEST_BYTES


class AnalyzeRequest(BaseModel):
    # Defence in depth: the middleware below rejects by byte length before FastAPI
    # reads the body; this cap still applies if that layer is ever bypassed.
    diff: str = Field(default="", max_length=MAX_DIFF_CHARS)


def _body_rejection(content_length: str | None) -> JSONResponse | None:
    """Decide on Content-Length alone, so an oversized body is never buffered."""
    if content_length is None:
        return JSONResponse(
            status_code=411,
            content={"detail": "Content-Length is required for /api/analyze."},
        )
    if not content_length.isdigit() or int(content_length) > MAX_REQUEST_BYTES:
        limit = MAX_REQUEST_BYTES // (1024 * 1024)
        return JSONResponse(
            status_code=413,
            content={"detail": f"Diff is larger than {limit} MB. Send a smaller unified diff."},
        )
    return None


MAX_DRAIN_BYTES = 16 * 1024 * 1024


async def _discard(request: Request, limit: int) -> None:
    """Read the rejected body without keeping it, so the client can see the 413.

    A server that answers before draining makes the peer see a connection reset
    instead of the error body. Above MAX_DRAIN_BYTES we stop and let it close.
    """
    seen = 0
    try:
        async for chunk in request.stream():
            seen += len(chunk)
            if seen > limit:
                break
    except Exception:  # the client gave up mid-upload; nothing left to drain
        pass


@app.middleware("http")
async def bounded_body(request: Request, call_next):
    """A public demo must not accept an unbounded diff: 8.6 MB took 7.3 s of one worker."""
    if request.url.path == "/api/analyze":
        declared = request.headers.get("content-length")
        rejection = _body_rejection(declared)
        if rejection is not None:
            if declared and declared.isdigit():
                await _discard(request, min(int(declared), MAX_DRAIN_BYTES))
            return rejection
    return await call_next(request)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "postcode-sentinel-web", "version": app.version}


@app.get("/")
def index() -> FileResponse:
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=500, detail="index.html missing")
    return FileResponse(INDEX_FILE, media_type="text/html; charset=utf-8")


@app.post("/api/analyze")
def analyze(payload: AnalyzeRequest) -> JSONResponse:
    text = payload.diff.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Diff is empty. Paste a unified diff to analyze.")

    try:
        changeset = parse_unified_diff(text)
    except DiffParseError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse diff: {exc}") from exc

    if not changeset.files:
        raise HTTPException(
            status_code=400,
            detail="No file changes found. Expected a unified diff with ---/+++ headers.",
        )

    result = analyze_changeset(changeset)
    markdown = render_markdown(result, mode="offline")
    findings = [
        {
            "rule": f.rule_id,
            "title": f.title,
            "severity": f.severity,
            "weight": f.weight,
            "file": f.file,
            "reason": f.reason,
            "evidence": list(f.evidence),
        }
        for f in result.findings
    ]
    body = {
        "score": result.score,
        "level": result.level,
        "files_total": len(changeset.files),
        "lines_added": changeset.total_added,
        "lines_removed": changeset.total_removed,
        "findings": findings,
        "markdown": markdown,
    }
    return JSONResponse(body)
