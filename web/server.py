"""FastAPI web layer for PostCode Sentinel.

Serves the single-page UI and exposes the existing deterministic analyzer over
HTTP. The risk engine (``sentinel.rules`` / ``sentinel.scoring``) is used
unchanged; this module only parses input, calls it and shapes the response.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from sentinel.diff_parser import DiffParseError, parse_unified_diff
from sentinel.report import render_markdown
from sentinel.scoring import analyze_changeset

WEB_DIR = Path(__file__).resolve().parent
INDEX_FILE = WEB_DIR / "index.html"

app = FastAPI(title="PostCode Sentinel", version="0.1.0")


class AnalyzeRequest(BaseModel):
    diff: str = ""


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
