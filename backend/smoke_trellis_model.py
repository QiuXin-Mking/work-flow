"""Optional live Pi smoke test for the three Trellis stages; creates no PR."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from app.task_runner import run_attempt


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="trellis-smoke-") as directory:
        root = Path(directory)
        repo = root / "repos" / "acme__api"
        repo.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Trellis Smoke",
               "GIT_AUTHOR_EMAIL": "trellis-smoke@local.invalid",
               "GIT_COMMITTER_NAME": "Trellis Smoke",
               "GIT_COMMITTER_EMAIL": "trellis-smoke@local.invalid"}
        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(repo), *args], check=True,
                           capture_output=True, env=env)
        git("init", "-b", "dev")
        git("remote", "add", "origin", "https://github.com/acme/api.git")
        (repo / "fixed.txt").write_text("broken\n", encoding="utf-8")
        git("add", "fixed.txt")
        git("commit", "-m", "baseline")
        result = asyncio.run(run_attempt({
            "workspace": str(root),
            "snapshot": {"title": "fixed.txt 内容错误",
                         "description": "fixed.txt 目前为 broken，期望为 fixed。请用 Python 断言文件内容作为复现命令。"},
            "repos": {"acme/api": {"target_branch": "dev"}},
            "pi_timeout": 300,
        }))
        assert result["reproduced"] and result["verified"]
        assert result["candidate"]["acme/api"]["tree_id"]
        print("TRELLIS_SMOKE_OK", flush=True)


if __name__ == "__main__":
    main()
