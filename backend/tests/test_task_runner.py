from pathlib import Path
import asyncio
import hashlib
import pytest
import json
import os
import subprocess
import sys

from app.task_runner import run_attempt, run_check, validate_manifest
from app.trellis_context import prepare_trellis_context


def test_reproduction_must_fail_before_and_pass_after(tmp_path: Path):
    file = tmp_path / "fixed.txt"
    command = ["python", "-c", "from pathlib import Path; assert Path('fixed.txt').read_text() == 'fixed'"]
    before = run_check(command, tmp_path, timeout=5)
    assert before["exit_code"] != 0
    file.write_text("fixed")
    after = run_check(command, tmp_path, timeout=5)
    assert after["exit_code"] == 0


def test_manifest_allows_modified_subset_but_not_unknown_repos():
    manifest = {"root_cause": "a", "repositories": {"acme/api": {"repro_command": ["pytest", "-q"]}}}
    assert validate_manifest(manifest, ["acme/api"])["repositories"]["acme/api"]["repro_command"] == ["pytest", "-q"]
    assert validate_manifest(manifest, ["acme/api", "acme/web"]) == manifest
    try:
        validate_manifest(manifest, ["acme/web"])
        assert False
    except ValueError:
        pass


def test_trellis_context_is_attempt_local_and_selects_relevant_specs(tmp_path: Path):
    context = prepare_trellis_context(tmp_path, {"title": "bug", "description": "external data"},
                                      ["neptune-robotics/voyager_server"])
    assert (tmp_path / "snapshot.json").exists()
    assert not (tmp_path / ".trellis/tasks/bugfix/prd.md").exists()
    assert "Backend context" in context["implement"]
    assert "Frontend context" not in context["implement"]
    assert "external data" not in context["implement"]
    assert "independent Trellis check subagent" in context["verify"]
    assert not list((tmp_path / ".trellis").rglob("*-system.md"))
    assert "backend/error-handling.md" in context["implement"]
    assert "frontend/type-safety.md" not in context["implement"]
    manifest = tmp_path / ".trellis" / "tasks" / "bugfix" / "implement.jsonl"
    assert "backend/error-handling.md" in manifest.read_text(encoding="utf-8")


@pytest.mark.parametrize("tamper_stage", [None, "implement", "verify"])
def test_attempt_needs_real_before_failure_and_fresh_verification(tmp_path: Path, tamper_stage):
    root = tmp_path / "attempt"
    checkout = root / "repos" / "acme__api"
    checkout.mkdir(parents=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(checkout), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-b", "dev")
    git("remote", "add", "origin", "https://github.com/acme/api.git")
    (checkout / "fixed.txt").write_text("broken")
    git("add", "fixed.txt")
    env = {**os.environ, "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "test@example.org",
           "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "test@example.org"}
    subprocess.run(["git", "-C", str(checkout), "commit", "-m", "baseline"],
                   check=True, capture_output=True, env=env)
    fake = tmp_path / "fake_pi.py"
    fake.write_text('''import json, pathlib, sys
root = pathlib.Path.cwd()
tamper_stage = 'TAMPER_STAGE'
for line in sys.stdin.buffer:
    cmd = json.loads(line.decode("utf-8"))
    if cmd["type"] != "prompt":
        continue
    if "独立取证与定位 subagent" in cmd["message"]:
        (root / "diagnosis.json").write_text(json.dumps({
            "root_cause": "wrong value", "evidence": "fixed.txt has broken",
            "plan": "write fixed", "repositories": ["acme/api"]}))
    elif "独立验证会话" in cmd["message"]:
        (root / "verification.json").write_text(json.dumps({"verified": True, "reason": "checked"}))
    else:
        (root / "worktrees" / "acme__api" / "fixed.txt").write_text("fixed")
        (root / "manifest.json").write_text(json.dumps({"root_cause": "wrong value", "repositories": {
            "acme/api": {"repro_command": ["python", "-c", "from pathlib import Path; assert Path('fixed.txt').read_text() == 'fixed'"]}}}))
    if tamper_stage in ("implement", "verify") and ((tamper_stage == "verify") == ("独立验证会话" in cmd["message"])):
        (root / ".trellis/tasks/bugfix/prd.md").write_text("unauthorized requirements")
    print(json.dumps({"id": cmd["id"], "type": "response", "success": True, "disposition": "started"}), flush=True)
    print(json.dumps({"type": "agent_settled"}), flush=True)
'''.replace('TAMPER_STAGE', str(tamper_stage)), encoding="utf-8")
    spec = {"workspace": str(root),
        "snapshot": {"title": "broken", "description": "fix it"},
        "repos": {"acme/api": {"target_branch": "dev"}},
        "pi_command": [sys.executable, str(fake)], "pi_timeout": 3,
        "workflow": {"ready_for_implementation": True, "prd":{"content":"approved requirements", "hash":hashlib.sha256(b"approved requirements").hexdigest()},
                     "root_cause":{"root_cause":"wrong value","evidence":"fixed.txt","repositories":["acme/api"],"repository_shas":{"acme/api":git("rev-parse","HEAD")}}}}
    if tamper_stage:
        with pytest.raises(ValueError, match="approved PRD"):
            asyncio.run(run_attempt(spec))
        return
    result = asyncio.run(run_attempt(spec))
    workspaces = json.loads((root / "worktrees.json").read_text())
    assert workspaces["acme/api"]["branch"].startswith("fix/bugfix-")
    assert git("symbolic-ref", "--short", "HEAD") == "dev"
    assert result["reproduced"] and result["verified"]
    assert result["candidate"]["acme/api"]["tree_id"]
    assert (root / ".trellis" / "tasks" / "bugfix" / "research" / "diagnosis.json").exists()
    assert (root / ".trellis" / "tasks" / "bugfix" / "research" / "verification.json").exists()
