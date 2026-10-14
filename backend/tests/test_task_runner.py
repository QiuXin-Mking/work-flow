from pathlib import Path
import asyncio
import json
import os
import subprocess
import sys

from app.task_runner import run_attempt, run_check, validate_manifest


def test_reproduction_must_fail_before_and_pass_after(tmp_path: Path):
    file = tmp_path / "fixed.txt"
    command = ["python", "-c", "from pathlib import Path; assert Path('fixed.txt').read_text() == 'fixed'"]
    before = run_check(command, tmp_path, timeout=5)
    assert before["exit_code"] != 0
    file.write_text("fixed")
    after = run_check(command, tmp_path, timeout=5)
    assert after["exit_code"] == 0


def test_manifest_requires_every_repo_and_argv_commands():
    manifest = {"root_cause": "a", "repositories": {"acme/api": {"repro_command": ["pytest", "-q"]}}}
    assert validate_manifest(manifest, ["acme/api"])["repositories"]["acme/api"]["repro_command"] == ["pytest", "-q"]
    try:
        validate_manifest(manifest, ["acme/api", "acme/web"])
        assert False
    except ValueError:
        pass


def test_attempt_needs_real_before_failure_and_fresh_verification(tmp_path: Path):
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
for line in sys.stdin.buffer:
    cmd = json.loads(line.decode("utf-8"))
    if cmd["type"] != "prompt":
        continue
    if "独立验证会话" in cmd["message"]:
        (root / "verification.json").write_text(json.dumps({"verified": True, "reason": "checked"}))
    else:
        (root / "repos" / "acme__api" / "fixed.txt").write_text("fixed")
        (root / "manifest.json").write_text(json.dumps({"root_cause": "wrong value", "repositories": {
            "acme/api": {"repro_command": ["python", "-c", "from pathlib import Path; assert Path('fixed.txt').read_text() == 'fixed'"]}}}))
    print(json.dumps({"id": cmd["id"], "type": "response", "success": True, "disposition": "started"}), flush=True)
    print(json.dumps({"type": "agent_settled"}), flush=True)
''', encoding="utf-8")
    result = asyncio.run(run_attempt({"workspace": str(root),
        "snapshot": {"title": "broken", "description": "fix it"},
        "repos": {"acme/api": {"target_branch": "dev"}},
        "pi_command": [sys.executable, str(fake)], "pi_timeout": 3}))
    assert result["reproduced"] and result["verified"]
    assert result["candidate"]["acme/api"]["tree_id"]
