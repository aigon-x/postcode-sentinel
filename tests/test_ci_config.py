"""Static checks for the GitLab CI config and Duo Agent Platform files."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".gitlab-ci.yml"
FLOW_PATH = ROOT / ".gitlab" / "duo" / "flows" / "postcode-sentinel.yml"


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def flow():
    return yaml.safe_load(FLOW_PATH.read_text(encoding="utf-8"))


def _jobs(ci):
    return {
        name: config
        for name, config in ci.items()
        if not name.startswith(".") and isinstance(config, dict) and "script" in config
    }


def _script(job):
    return "\n".join(job["script"])


def test_ci_yaml_parses_with_expected_jobs_and_stages(ci):
    assert ci["stages"] == ["test", "quality", "postcode"]
    assert set(_jobs(ci)) == {"unit-tests", "quality-gates", "postcode-sentinel"}
    gates = ci["quality-gates"]
    assert "ci/quality_gates.py" in _script(gates)
    assert gates["artifacts"]["reports"]["codequality"] == "codequality.json"


def test_ci_image_installs_git_python_and_pytest(ci):
    assert ci["default"]["image"].startswith("alpine:3.20")
    packages = " ".join(ci["default"]["before_script"])
    assert "apk add" in packages
    tokens = packages.split()
    for required in ("git", "python3", "py3-pytest", "py3-yaml"):
        assert required in tokens, f"{required} missing from apk add: {packages}"


def test_ci_runs_for_merge_requests_and_default_branch(ci):
    rules = _jobs(ci)["postcode-sentinel"]["rules"]
    conditions = " ".join(rule["if"] for rule in rules)
    assert "merge_request_event" in conditions
    assert "CI_COMMIT_BRANCH" in conditions  # API and push pipelines both carry a branch
    assert "push" in conditions


def test_ci_diff_uses_mr_target_or_before_sha(ci):
    script = _script(_jobs(ci)["postcode-sentinel"])
    assert "CI_MERGE_REQUEST_TARGET_BRANCH_NAME" in script
    assert "origin/$TARGET...HEAD" in script
    assert "CI_COMMIT_BEFORE_SHA" in script
    assert "git diff" in script
    assert "tee " not in script  # no pipe that could mask a failure
    assert "HEAD~1..HEAD" in script


def test_ci_runs_sentinel_entirely_offline(ci):
    script = _script(_jobs(ci)["postcode-sentinel"])
    assert "--offline" in script
    assert "python3 -m sentinel review" in script
    assert "python3 -m sentinel analyze" in script
    assert "pytest tests -q" in _script(_jobs(ci)["unit-tests"])


def test_flow_is_flow_registry_v1(flow):
    allowed = {"version", "environment", "components", "routers", "flow", "prompts", "coding_environment"}
    assert set(flow) <= allowed, f"custom flows reject these top-level fields: {set(flow) - allowed}"
    assert flow["version"] == "v1"
    assert flow["environment"] == "ambient"


def test_flow_components_prompts_and_entry_point_are_consistent(flow):
    names = [component["name"] for component in flow["components"]]
    prompt_ids = [prompt["prompt_id"] for prompt in flow["prompts"]]
    assert flow["flow"]["entry_point"] in names
    for component in flow["components"]:
        assert component["type"] == "AgentComponent"
        assert component["prompt_id"] in prompt_ids
    assert flow["routers"] == [{"from": flow["flow"]["entry_point"], "to": "end"}]


def test_flow_avoids_restricted_custom_flow_fields(flow):
    for component in flow["components"]:
        assert "response_schema_id" not in component
        assert "response_schema_version" not in component
        assert "ui_role_as" not in component
    for prompt in flow["prompts"]:
        assert "model" not in prompt
        assert "prompt_template" in prompt
        assert "{{goal}}" in prompt["prompt_template"]["user"]


def test_duo_directory_contains_rules_and_agent_registration():
    rules = (ROOT / ".gitlab" / "duo" / "chat-rules.md").read_text(encoding="utf-8")
    agent = (ROOT / ".gitlab" / "duo" / "agent.md").read_text(encoding="utf-8")
    assert "deterministic" in rules
    assert "AI Catalog" in agent
    assert "get_repository_file" in agent


def test_workspace_subagent_markdown_has_front_matter():
    path = ROOT / ".agents" / "agents" / "postcode-sentinel-reviewer.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front_matter = text.split("---", 2)[1]
    assert "name: postcode-sentinel-reviewer" in front_matter
    assert "description:" in front_matter
    assert "tools:" in front_matter
    assert len(text.split("---", 2)[2].strip()) > 0
