"""Pytest bootstrap: make the project root importable and expose fixtures."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"

import pytest  # noqa: E402


@pytest.fixture()
def risky_diff_text() -> str:
    return (FIXTURES / "risky.diff").read_text(encoding="utf-8")


@pytest.fixture()
def clean_diff_text() -> str:
    return (FIXTURES / "clean.diff").read_text(encoding="utf-8")
