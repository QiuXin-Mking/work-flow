"""Single trusted scheduler. Each attempt runs in a fresh Docker container."""

from __future__ import annotations

import base64
import json
import hashlib
import shutil
import tempfile
import os
import re
import select
import subprocess
import time
import threading
from pathlib import Path

import httpx

from .delivery_worker import run_once as deliver_once
from .github import GitHubClient
from .store import Store
from .stage_runner import validate_stage_input
from .log_source import read_trace_logs
from .execution_errors import safe_error


def _github_basic_authorization(token: str) -> str:
    """Encode a GitHub PAT for Git HTTPS and API requests without URL credentials."""
    credentials = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {credentials}"


def _read_git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not re.fullmatch(r"GIT_CONFIG_(COUNT|KEY_\d+|VALUE_\d+)", key)}
    settings = []
    token = env.get("GITHUB_READ_TOKEN")
    if token:
        settings.append(("http.extraHeader", f"Authorization: {_github_basic_authorization(token)}"))
    if env.get("GIT_HTTP_PROXY"):
        settings.append(("http.proxy", env["GIT_HTTP_PROXY"]))
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _repository_url(repo_id: str) -> str:
    return f"https://github.com/{repo_id}.git"


def _prepare_checkout(repo_id: str, branch: str, checkout: Path, cache_root: Path) -> None:
    """Fetch trusted shallow cache, then copy objects into an independent attempt."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id):
        raise ValueError("invalid configured repository")
    if not branch or branch.startswith("-"):
        raise ValueError("invalid configured branch")
    env = _read_git_env()
    def git(*args: str) -> None:
        subprocess.run(["git", *args], env=env, capture_output=True, check=True, timeout=300)

    key = hashlib.sha256(f"{repo_id}:{branch}".encode()).hexdigest()
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = cache_root / key
    url = _repository_url(repo_id)
    if not cache.exists():
        staging = Path(tempfile.mkdtemp(prefix=f"{key}-", dir=cache_root))
        try:
            git("clone", "--bare", "--depth", "40", "--single-branch", "--branch", branch,
                url, str(staging))
            staging.rename(cache)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    else:
        git("--git-dir", str(cache), "fetch", "--depth", "40", "--no-tags", "origin",
            f"+refs/heads/{branch}:refs/heads/{branch}")
    checkout.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--no-hardlinks", "--single-branch", "--branch", branch, str(cache), str(checkout))
    git("-C", str(checkout), "remote", "set-url", "origin", url)


def _run_attempt_body(store: Store, claim: dict, repos: dict, workspace_root: Path,
                 image: str, volume: str) -> None:
    job_id, attempt_id, epoch = claim["job_id"], claim["attempt_id"], claim["epoch"]
    job = store.get_job(job_id)
    if not job or not job.get("snapshot"):
        store.set_job_status(job_id, "failed", epoch=epoch, error="缺少飞书工单快照")
        return
    workflow = store.ensure_workflow(job_id)
    snapshot = store.get_snapshot_version(job_id, workflow['snapshot_version'])
    if snapshot is None:
        raise ValueError('versioned workflow snapshot missing')
    stage = workflow['stage']
    if stage == 'implementation':
        validate_stage_input(stage, workflow)
    blocked = validate_stage_input(stage, workflow)
    if blocked is not None:
        store.record_workflow_result(job_id, epoch, stage, blocked)
        return
    log_evidence = None
    if stage == 'root_cause':
        log_evidence = read_trace_logs(workflow.get('input') or {})
        if log_evidence is None:
            store.record_workflow_result(job_id, epoch, stage, {
                'confirmed':False, 'evidence':{'logs':[], 'code':[]},
                'business_changes':[], 'questions':['请配置74免密取证身份和受控日志查询程序，并提供可查询的mobi；偶现问题还需工单trace-id。']})
            return
    attempt_dir = workspace_root / job_id / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=True)
    selected = {repo: {"target_branch": repos[repo]["target_branch"]} for repo in job["repo_ids"]}
    target_shas: dict[str, str] = {}
    for repo_id, config in (selected.items() if stage in ("root_cause", "implementation") else []):
        if not repo_id or "/" not in repo_id:
            raise ValueError("invalid configured repository")
        checkout = attempt_dir / "repos" / repo_id.replace("/", "__")
        checkout.parent.mkdir(parents=True, exist_ok=True)
        store.append_event(job_id, "progress", {"message": f"准备仓库 {repo_id}（缓存更新，历史深度 40）"}, epoch=epoch)
        try:
            _prepare_checkout(repo_id, config["target_branch"], checkout,
                              workspace_root.parent / "repo-cache")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            message = f"仓库准备失败：{repo_id}，{type(exc).__name__}（命令超时上限 300 秒）"
            store.append_event(job_id, "failure", {"message": message}, epoch=epoch)
            store.set_job_status(job_id, "failed", epoch=epoch, error=message)
            return
        target_shas[repo_id] = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                               capture_output=True, text=True, check=True).stdout.strip()
    spec = {"workspace": "/workspace",
            "snapshot": snapshot, "repos": selected, "workflow": workflow,
            "stage": stage, "log_evidence": log_evidence, "pi_timeout": 300}
    (attempt_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    name = f"pi-bug-{attempt_id[:20]}"
    volume_host_path = subprocess.run(
        ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", volume],
        capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    host_attempt = Path(volume_host_path) / "attempts" / job_id / attempt_id
    runner_module = "app.task_runner" if stage == "implementation" else "app.stage_runner"
    command = ["docker", "run", "--rm", "--name", name, "--network", "bridge",
               "--cpus", "1.5", "--memory", "2g", "--pids-limit", "256",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--tmpfs", "/tmp:rw,size=512m", "--tmpfs", "/root/.pi:rw,size=128m",
               "--tmpfs", "/root/.npm:rw,size=256m", "--tmpfs", "/root/.cache:rw,size=256m",
               "--mount", f"type=bind,source={host_attempt},target=/workspace",
               "--mount", "type=volume,source=pi-config,target=/pi-seed,readonly",
               image, "sh", "-lc",
               f"cp -a /pi-seed/. /root/.pi/ && exec python3 -m {runner_module} "
               "/workspace/spec.json /workspace/result.json"]
    store.append_event(job_id, "progress", {"message": "隔离 Runner 已启动"}, epoch=epoch)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
    assert process.stdout is not None
    lost_lease = False
    last_failure = None
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
                            if event['kind'] == 'failure':
                                last_failure = str(event.get('payload', {}).get('message', 'Runner failed'))[:1000]
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
        error = f"{stage}: {last_failure or 'Runner exited without a successful result'}"
        store.append_event(job_id, "failure", {"message": error}, epoch=epoch)
        store.set_job_status(job_id, "failed", epoch=epoch, error=error)
        return
    result = json.loads(result_file.read_text(encoding="utf-8"))
    if stage != 'implementation':
        if result.get('stage') != stage:
            raise ValueError('runner stage identity mismatch')
        report = result['report']
        if stage == 'root_cause':
            # Raw evidence is supplied by the trusted SSH adapter, never accepted
            # from model-written files or lost when the container exits.
            report['collected_logs'] = log_evidence
            report['repository_shas'] = target_shas
            if report.get('confirmed') is True:
                report.setdefault('evidence', {})['logs'] = [log_evidence]
        store.record_workflow_result(job_id, epoch, stage, report)
        return
    if result.get("status") in ("unreproduced", "diagnosis_only"):
        store.append_event(job_id, "status", {"message": result.get("message", "仅有诊断结果")}, epoch=epoch)
        store.set_job_status(job_id, result["status"], epoch=epoch)
        return
    if not result["candidate"] or not set(result["candidate"]).issubset(job["repo_ids"]):
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


def _run_attempt(store: Store, claim: dict, repos: dict, workspace_root: Path,
                 image: str, volume: str) -> None:
    """Renew throughout preparation too; stale completions are fenced by Store."""
    stop = threading.Event()
    def renew():
        while not stop.wait(15):
            if not store.renew_lease(claim['job_id'], claim['epoch'], 'worker', lease_seconds=60):
                return
    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        _run_attempt_body(store, claim, repos, workspace_root, image, volume)
    finally:
        stop.set()
        thread.join(timeout=1)


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
                workflow = store.get_workflow(claim['job_id']) or {}
                store.set_job_status(claim["job_id"], "failed", epoch=claim["epoch"],
                                     error=f"{workflow.get('stage', 'runner')}: {safe_error(exc)}")
            continue
        if token:
            pending = store.next_approved_candidate()
            try:
                os.environ.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader",
                                   "GIT_CONFIG_VALUE_0": f"Authorization: {_github_basic_authorization(token)}"})
                with httpx.Client(base_url="https://api.github.com", timeout=20,
                                  headers={"Authorization": _github_basic_authorization(token),
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
