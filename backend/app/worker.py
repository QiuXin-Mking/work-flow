"""Single trusted scheduler. Each attempt runs in a fresh Docker container."""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import time
from pathlib import Path

import httpx

from .delivery_worker import run_once as deliver_once
from .github import GitHubClient
from .store import Store


def _run_attempt(store: Store, claim: dict, repos: dict, workspace_root: Path,
                 image: str, volume: str) -> None:
    job_id, attempt_id, epoch = claim["job_id"], claim["attempt_id"], claim["epoch"]
    job = store.get_job(job_id)
    if not job or not job.get("snapshot"):
        store.set_job_status(job_id, "failed", epoch=epoch, error="缺少飞书工单快照")
        return
    attempt_dir = workspace_root / job_id / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=True)
    selected = {repo: {"target_branch": repos[repo]["target_branch"]} for repo in job["repo_ids"]}
    target_shas: dict[str, str] = {}
    for repo_id, config in selected.items():
        if not repo_id or "/" not in repo_id:
            raise ValueError("invalid configured repository")
        checkout = attempt_dir / "repos" / repo_id.replace("/", "__")
        checkout.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
            env.pop(key, None)
        read_token = env.get("GITHUB_READ_TOKEN")
        if read_token:
            env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                        "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {read_token}"})
        subprocess.run(["git", "clone", "--single-branch", "--branch", config["target_branch"],
                        f"https://github.com/{repo_id}.git", str(checkout)],
                       env=env, capture_output=True, check=True, timeout=180)
        target_shas[repo_id] = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                               capture_output=True, text=True, check=True).stdout.strip()
    spec = {"workspace": "/workspace",
            "snapshot": job["snapshot"], "repos": selected}
    (attempt_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    name = f"pi-bug-{attempt_id[:20]}"
    volume_host_path = subprocess.run(
        ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", volume],
        capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    host_attempt = Path(volume_host_path) / "attempts" / job_id / attempt_id
    command = ["docker", "run", "--rm", "--name", name, "--network", "bridge",
               "--cpus", "1.5", "--memory", "2g", "--pids-limit", "256",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--tmpfs", "/tmp:rw,size=512m", "--tmpfs", "/root/.pi:rw,size=128m",
               "--tmpfs", "/root/.npm:rw,size=256m", "--tmpfs", "/root/.cache:rw,size=256m",
               "--mount", f"type=bind,source={host_attempt},target=/workspace",
               "--mount", "type=volume,source=pi-config,target=/pi-seed,readonly",
               image, "sh", "-lc",
               "cp -a /pi-seed/. /root/.pi/ && exec python3 -m app.task_runner "
               "/workspace/spec.json /workspace/result.json"]
    store.append_event(job_id, "progress", {"message": "隔离 Runner 已启动"}, epoch=epoch)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
    assert process.stdout is not None
    lost_lease = False
    last_renew = time.monotonic()
    try:
        while process.poll() is None:
            readable, _, _ = select.select([process.stdout], [], [], 1)
            if readable:
                line = process.stdout.readline()
                if line:
                    try:
                        event = json.loads(line)
                        if event.get("kind") in ("progress", "evidence", "failure"):
                            store.append_event(job_id, event["kind"], event.get("payload", {}), epoch=epoch)
                    except json.JSONDecodeError:
                        pass
            if time.monotonic() - last_renew > 15:
                if not store.renew_lease(job_id, epoch, "worker", lease_seconds=60):
                    lost_lease = True
                    subprocess.run(["docker", "stop", "-t", "2", name], capture_output=True, timeout=15)
                    break
                last_renew = time.monotonic()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if lost_lease:
        return
    result_file = attempt_dir / "result.json"
    if process.returncode != 0 or not result_file.exists() or result_file.is_symlink():
        error = "Pi 修复或验证未通过"
        store.append_event(job_id, "failure", {"message": error}, epoch=epoch)
        store.set_job_status(job_id, "failed", epoch=epoch, error=error)
        return
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if result.get("status") in ("unreproduced", "diagnosis_only"):
        store.append_event(job_id, "status", {"message": result.get("message", "仅有诊断结果")}, epoch=epoch)
        store.set_job_status(job_id, result["status"], epoch=epoch)
        return
    if set(result["candidate"]) != set(job["repo_ids"]):
        raise ValueError("runner returned unexpected repository set")
    for repo_id, item in result["candidate"].items():
        expected = f"/workspace/repos/{repo_id.replace('/', '__')}"
        if item.get("checkout_path") != expected:
            raise ValueError("runner returned unexpected checkout path")
        if item.get("target_branch") != selected[repo_id]["target_branch"]:
            raise ValueError("runner changed target branch")
        if item.get("target_sha") != target_shas[repo_id] or not re.fullmatch(
                r"[0-9a-f]{40}", str(item.get("tree_id", ""))):
            raise ValueError("runner changed frozen Git identity")
        item["checkout_path"] = str(attempt_dir / "repos" / repo_id.replace("/", "__"))
    try:
        candidate_id = store.save_candidate(job_id, result["candidate"],
                                            reproduced=result["reproduced"],
                                            verified=result["verified"],
                                            attempt_id=attempt_id, epoch=epoch)
    except ValueError:
        return
    store.append_event(job_id, "status", {"message": "候选已冻结，等待另一位成员审核",
                                           "candidate_id": candidate_id}, epoch=epoch)


def run_forever() -> None:
    store = Store(os.environ.get("DATABASE_PATH", "/data/jobs.sqlite"))
    repos_path = Path(os.environ.get("REPOS_CONFIG", "/app/repos.json"))
    repos = json.loads(repos_path.read_text(encoding="utf-8"))
    workspace_root = Path(os.environ.get("WORKSPACE_ROOT", "/data/attempts"))
    workspace_root.mkdir(parents=True, exist_ok=True)
    image = os.environ.get("RUNNER_IMAGE", "pi-bugfix-backend:1.0")
    volume = os.environ.get("DATA_VOLUME", "pi-bugfix-data")
    token = os.environ.get("GITHUB_TOKEN")
    while True:
        claim = store.claim_next_job("worker", lease_seconds=60)
        if claim:
            try:
                _run_attempt(store, claim, repos, workspace_root, image, volume)
            except Exception as exc:
                store.set_job_status(claim["job_id"], "failed", epoch=claim["epoch"],
                                     error=f"Runner 异常：{type(exc).__name__}")
            continue
        if token:
            pending = store.next_approved_candidate()
            try:
                os.environ.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                                   "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}"})
                with httpx.Client(base_url="https://api.github.com", timeout=20,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Accept": "application/vnd.github+json"}) as http:
                    deliver_once(store, GitHubClient(http), workspace_root)
            except Exception as exc:
                if pending:
                    candidate = store.get_candidate(pending)
                    if candidate:
                        store.append_event(candidate["job_id"], "failure",
                                           {"message": f"PR 交付失败：{type(exc).__name__}"})
                        store.set_job_status(candidate["job_id"], "delivery_wip")
                time.sleep(30)
        time.sleep(2)


if __name__ == "__main__":
    run_forever()
