"""Unified diff parser.

Supports git-style diffs (``diff --git``, renames, binary markers) and plain
unified diffs that only carry ``---``/``+++`` headers. The parser is tolerant:
malformed hunks are skipped rather than aborting the whole review.
"""

from __future__ import annotations

import re
import shlex

from .models import ChangeSet, DiffFile, Hunk

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".sh": "shell",
    ".tf": "terraform",
    ".md": "markdown",
    ".toml": "toml",
}


class DiffParseError(ValueError):
    """Raised when input is not a diff at all (no file headers found)."""


def parse_unified_diff(text: str, source: str = "") -> ChangeSet:
    lines = text.splitlines()
    blocks = _split_blocks(lines)
    files: list[DiffFile] = []
    for block in blocks:
        parsed = _parse_block(block)
        if parsed is not None:
            files.append(parsed)
    if not blocks:
        raise DiffParseError("input contains no file headers (expected 'diff --git' or '---/+++')")
    return ChangeSet(files=tuple(files), source=source)


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("diff --git "):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if line.startswith("--- ") and current and _looks_like_new_block(current):
            blocks.append(current)
            current = [line]
            continue
        if line.startswith("--- ") and not current:
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _looks_like_new_block(block: list[str]) -> bool:
    if block[0].startswith("diff --git "):
        return False
    return any(line.startswith("+++ ") for line in block)


def _parse_block(block: list[str]) -> DiffFile | None:
    old_path = ""
    new_path = ""
    change_type = "modified"
    binary = False
    renamed_from = ""
    hunks: list[Hunk] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            hunks.append(_build_hunk(current))
            current = None

    for line in block:
        if line.startswith("@@"):
            flush()
            match = _HUNK_RE.match(line)
            if not match:
                current = None
                continue
            current = {
                "old_start": int(match.group(1)),
                "old_len": int(match.group(2) or 1),
                "new_start": int(match.group(3)),
                "new_len": int(match.group(4) or 1),
                "header": line,
                "added": [],
                "removed": [],
                "context": [],
            }
            continue

        if current is not None:
            if line.startswith("+"):
                current["added"].append(line[1:])
            elif line.startswith("-"):
                current["removed"].append(line[1:])
            elif line.startswith(" "):
                current["context"].append(line[1:])
            elif line.startswith("\\"):
                continue
            else:
                # Hunk body ended without a new header (truncated diff).
                flush()
            continue

        if line.startswith("new file mode"):
            change_type = "added"
        elif line.startswith("deleted file mode"):
            change_type = "deleted"
        elif line.startswith("rename from "):
            renamed_from = line[len("rename from "):].strip()
            change_type = "renamed"
        elif line.startswith("rename to "):
            new_path = line[len("rename to "):].strip()
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary = True
        elif line.startswith("--- "):
            old_path = _clean_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _clean_path(line[4:])
        elif line.startswith("diff --git "):
            parts = _split_git_header(line)
            if len(parts) == 2:
                old_path = old_path or parts[0]
                new_path = new_path or parts[1]

    flush()

    path = new_path or renamed_from or old_path
    if not path:
        return None
    if new_path == "/dev/null":
        change_type = "deleted"
        path = old_path
    elif old_path == "/dev/null":
        change_type = "added"
    if change_type == "modified" and not hunks and not binary:
        return None

    return DiffFile(
        path=path,
        old_path=old_path,
        new_path=new_path or path,
        change_type=change_type,
        hunks=tuple(hunks),
        binary=binary,
        language=_language(path),
    )


def _build_hunk(raw: dict) -> Hunk:
    return Hunk(
        old_start=raw["old_start"],
        old_len=raw["old_len"],
        new_start=raw["new_start"],
        new_len=raw["new_len"],
        header=raw["header"],
        added=tuple(raw["added"]),
        removed=tuple(raw["removed"]),
        context=tuple(raw["context"]),
    )


def _clean_path(raw: str) -> str:
    token = raw.strip()
    if token.startswith('"') and token.endswith('"'):
        try:
            token = shlex.split(token)[0]
        except ValueError:
            token = token.strip('"')
    if token.startswith("a/") or token.startswith("b/"):
        token = token[2:]
    return token


def _split_git_header(line: str) -> list[str]:
    rest = line[len("diff --git "):].strip()
    if rest.startswith('"'):
        try:
            parts = shlex.split(rest)
        except ValueError:
            return []
    else:
        parts = rest.split(" ")
    cleaned = [_clean_path(p) for p in parts]
    return cleaned if len(cleaned) == 2 else []


def _language(path: str) -> str:
    lower = path.lower()
    for ext, name in _LANG_BY_EXT.items():
        if lower.endswith(ext):
            return name
    return ""
