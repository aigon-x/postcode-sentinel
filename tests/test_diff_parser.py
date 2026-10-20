from sentinel.diff_parser import DiffParseError, parse_unified_diff

import pytest


def test_parses_git_diff_files_and_counts(risky_diff_text):
    changeset = parse_unified_diff(risky_diff_text)
    paths = changeset.paths()
    assert "app/settings.py" in paths
    assert "tests/test_billing.py" in paths
    assert len(changeset.files) == 6
    settings = changeset.by_path("app/settings.py")
    assert settings.change_type == "modified"
    assert settings.language == "python"
    assert settings.added_count == 2


def test_detects_added_and_deleted_files(risky_diff_text):
    changeset = parse_unified_diff(risky_diff_text)
    assert changeset.by_path("migrations/0007_drop_orders_table.py").change_type == "added"
    deleted = changeset.by_path("tests/test_billing.py")
    assert deleted.change_type == "deleted"
    assert deleted.added_count == 0
    assert deleted.removed_count == 11


def test_hunk_header_and_symbols(risky_diff_text):
    changeset = parse_unified_diff(risky_diff_text)
    auth = changeset.by_path("app/auth/tokens.py")
    assert auth.hunks[0].new_start == 18
    assert auth.symbols() == ["validate_signature"]


def test_plain_unified_diff_without_git_header():
    text = (
        "--- a/example.txt\n"
        "+++ b/example.txt\n"
        "@@ -1 +1 @@\n"
        "-old line\n"
        "+new line\n"
    )
    changeset = parse_unified_diff(text)
    assert changeset.paths() == ["example.txt"]
    assert changeset.files[0].added_lines == ["new line"]


def test_rejects_input_without_headers():
    with pytest.raises(DiffParseError):
        parse_unified_diff("this is not a diff\nat all\n")
