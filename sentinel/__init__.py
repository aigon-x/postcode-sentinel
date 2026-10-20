"""PostCode Sentinel — post-merge diff risk review agent."""

__version__ = "0.1.0"

from .models import ChangeSet, DiffFile, Finding, ReviewResult  # noqa: F401
from .diff_parser import parse_unified_diff  # noqa: F401
from .scoring import analyze_changeset  # noqa: F401
