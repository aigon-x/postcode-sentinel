"""Data structures shared by the parser, rule engine and renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    context: tuple[str, ...] = ()

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def removed_count(self) -> int:
        return len(self.removed)


@dataclass(frozen=True)
class DiffFile:
    path: str
    old_path: str
    new_path: str
    change_type: str  # added | modified | deleted | renamed
    hunks: tuple[Hunk, ...] = ()
    binary: bool = False
    language: str = ""

    @property
    def added_lines(self) -> list[str]:
        return [line for hunk in self.hunks for line in hunk.added]

    @property
    def removed_lines(self) -> list[str]:
        return [line for hunk in self.hunks for line in hunk.removed]

    @property
    def added_count(self) -> int:
        return sum(h.added_count for h in self.hunks)

    @property
    def removed_count(self) -> int:
        return sum(h.removed_count for h in self.hunks)

    def symbols(self) -> list[str]:
        names: list[str] = []
        for hunk in self.hunks:
            name = _symbol_from_header(hunk.header)
            if name and name not in names:
                names.append(name)
        return names


@dataclass(frozen=True)
class ChangeSet:
    files: tuple[DiffFile, ...] = ()
    source: str = ""

    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def by_path(self, path: str) -> DiffFile | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    def select(self, predicate) -> list[DiffFile]:
        return [f for f in self.files if predicate(f)]

    @property
    def total_added(self) -> int:
        return sum(f.added_count for f in self.files)

    @property
    def total_removed(self) -> int:
        return sum(f.removed_count for f in self.files)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    severity: str  # critical | high | medium | low
    weight: int
    file: str
    reason: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "rule": self.rule_id,
            "title": self.title,
            "severity": self.severity,
            "weight": self.weight,
            "file": self.file,
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReviewResult:
    score: int
    level: str  # low | moderate | high | critical
    findings: tuple[Finding, ...]
    changeset: ChangeSet
    summary: dict = field(default_factory=dict)

    @property
    def reasons(self) -> list[str]:
        return [f.reason for f in self.findings]

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "files_total": len(self.changeset.files),
            "files_changed": self.changeset.paths(),
            "lines_added": self.changeset.total_added,
            "lines_removed": self.changeset.total_removed,
            "findings": [f.to_dict() for f in self.findings],
            "reasons": self.reasons,
        }


_SYMBOL_PREFIXES = ("def ", "class ", "func ", "function ", "async def ", "pub fn ", "fn ")


def _symbol_from_header(header: str) -> str:
    """Extract a trailing symbol name from a ``@@ ... @@`` hunk header."""
    marker = header.rfind("@@")
    if marker < 0:
        return ""
    tail = header[marker + 2:].strip()
    if not tail:
        return ""
    for prefix in _SYMBOL_PREFIXES:
        if tail.startswith(prefix):
            tail = tail[len(prefix):]
            break
    name = tail.split("(")[0].strip()
    return name[:120]


def symbols_in(lines: Iterable[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        text = line.strip()
        for prefix in _SYMBOL_PREFIXES:
            if text.startswith(prefix):
                name = text[len(prefix):].split("(")[0].strip().rstrip(":{")
                if name and name not in found:
                    found.append(name)
                break
    return found
