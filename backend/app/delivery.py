"""Group-level PR delivery. Remote reconciliation is required before each create."""

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

from .review import ReviewStore, candidate_binding


class DeliveryStore(ReviewStore, Protocol):
    def get_delivery(self, candidate_id: str) -> dict | None: ...
    def save_delivery(self, candidate_id: str, status: str, prs: dict) -> None: ...


class GitHubGateway(Protocol):
    def target_sha(self, repo: str, branch: str) -> str: ...
    def commit_tree(self, repo: str, sha: str) -> str: ...
    def find_pr(self, repo: str, branch: str, base: str) -> dict | None: ...
    def create_draft_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> dict: ...
    def set_draft(self, repo: str, node_id: str, draft: bool) -> None: ...


class CandidatePublisher(Protocol):
    def publish(self, repo: str, branch: str, tree_id: str, target_sha: str) -> str: ...


class LocalGitPublisher:
    """Publish a frozen local tree as a new branch, without modifying the base.

    The delivery service owns these checkouts and their Git credentials. Agent
    containers must not receive the publisher or its credentials.
    """

    def __init__(self, repo_paths: dict[str, str | Path], *, allow_local_remotes: bool = False):
        self.repo_paths = {repo: Path(path).resolve() for repo, path in repo_paths.items()}
        self.allow_local_remotes = allow_local_remotes

    def _git(self, path: Path, *args: str, env: dict | None = None, check: bool = True):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                text=True, env=env, check=False)
        if check and result.returncode:
            # Git stderr may contain a credential-bearing remote URL.
            raise RuntimeError(f"git {args[0]} failed (exit {result.returncode})")
        return result

    def publish(self, repo: str, branch: str, tree_id: str, target_sha: str) -> str:
        path = self.repo_paths.get(repo)
        if path is None or not path.is_dir():
            raise ValueError("repository is not configured for delivery")
        remote_url = self._git(path, "config", "--get", "remote.origin.url").stdout.strip()
        match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", remote_url)
        if match:
            if match.group(1).lower() != repo.lower():
                raise ValueError("delivery remote does not match configured repository")
        elif not self.allow_local_remotes:
            raise ValueError("delivery remote must be the configured GitHub repository")
        elif "github.com" in remote_url:
            raise ValueError("unrecognized GitHub delivery remote")
        if not branch.startswith("pi-bugfix/") or self._git(path, "check-ref-format", "--branch", branch,
                                                            check=False).returncode:
            raise ValueError("invalid delivery branch")
        if self._git(path, "cat-file", "-t", tree_id).stdout.strip() != "tree":
            raise ValueError("frozen tree is unavailable")
        if self._git(path, "cat-file", "-t", target_sha).stdout.strip() != "commit":
            raise ValueError("target commit is unavailable")

        # Fixed identity/time/message make commit-tree deterministic for retries.
        env = {**os.environ, "GIT_AUTHOR_NAME": "Pi Bugfix Delivery",
               "GIT_AUTHOR_EMAIL": "pi-bugfix@local.invalid",
               "GIT_COMMITTER_NAME": "Pi Bugfix Delivery",
               "GIT_COMMITTER_EMAIL": "pi-bugfix@local.invalid",
               "GIT_AUTHOR_DATE": "2000-01-01T00:00:00 +0000",
               "GIT_COMMITTER_DATE": "2000-01-01T00:00:00 +0000"}
        head_sha = self._git(path, "commit-tree", tree_id, "-p", target_sha,
                             "-m", f"Fix candidate {branch}", env=env).stdout.strip()
        ref = f"refs/heads/{branch}"
        remote = self._git(path, "ls-remote", "origin", ref).stdout.strip()
        if remote:
            if remote.split()[0] != head_sha:
                raise ValueError("delivery branch points to a different commit")
            return head_sha
        try:
            # Empty lease expectation requires the ref to remain absent. This
            # prevents a concurrent attempt from silently replacing a branch.
            self._git(path, "push", f"--force-with-lease={ref}:", "origin", f"{head_sha}:{ref}")
        except RuntimeError:
            remote = self._git(path, "ls-remote", "origin", ref).stdout.strip()
            if not remote or remote.split()[0] != head_sha:
                raise
        return head_sha


def _branch(candidate_id: str, repo: str, tree_id: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}:{repo}:{tree_id}".encode()).hexdigest()[:20]
    return f"pi-bugfix/{digest}"


def deliver_group(store: DeliveryStore, github: GitHubGateway, publisher: CandidatePublisher,
                  candidate_id: str) -> dict:
    candidate = store.get_candidate(candidate_id)
    if not candidate:
        raise ValueError("candidate not found")
    job = store.get_job(candidate["job_id"])
    approval = store.get_approval(candidate_id)
    if not job or job.get("candidate_id") != candidate_id or not approval:
        raise ValueError("current candidate approval required")
    if job.get("status") not in {"review_ready", "delivery_wip"}:
        raise ValueError("job status does not allow delivery")
    if candidate_binding(candidate) != approval["binding"]:
        raise ValueError("candidate binding changed since approval")
    if not candidate.get("reproduced") or not candidate.get("verified"):
        raise ValueError("candidate evidence changed")

    repos = candidate["repos"]
    # Check every target before making any external change.
    for repo, item in repos.items():
        if github.target_sha(repo, item["target_branch"]) != item["target_sha"]:
            raise ValueError(f"target branch moved: {repo}")

    existing = store.get_delivery(candidate_id) or {}
    prs = existing.get("prs", {})
    if existing.get("status") == "delivered":
        return existing

    for repo, item in sorted(repos.items()):
        branch = _branch(candidate_id, repo, item["tree_id"])
        try:
            head_sha = publisher.publish(repo, branch, item["tree_id"], item["target_sha"])
            if github.commit_tree(repo, head_sha) != item["tree_id"]:
                raise ValueError(f"published commit tree differs from candidate: {repo}")
            pr = github.find_pr(repo, branch, item["target_branch"])
            if pr is None:
                pr = github.create_draft_pr(
                    repo, branch, item["target_branch"],
                    f"fix: 修复 {candidate['job_id']} 问题",
                    f"Job: {candidate['job_id']}\nCandidate: {candidate_id}\nTree: {item['tree_id']}",
                )
            if pr["head_sha"] != head_sha or pr["head_ref"] != branch or pr["base_ref"] != item["target_branch"]:
                raise ValueError(f"remote PR differs from approved candidate: {repo}")
            if not pr["draft"]:
                github.set_draft(repo, pr["node_id"], True)
                pr["draft"] = True
            prs[repo] = pr
            store.save_delivery(candidate_id, "delivery_wip", prs)
        except Exception:
            store.save_delivery(candidate_id, "delivery_wip", prs)
            return {"status": "delivery_wip", "prs": prs}

    # All PRs exist as Draft; now make the entire group reviewable.
    try:
        for repo, pr in prs.items():
            github.set_draft(repo, pr["node_id"], False)
            pr["draft"] = False
            store.save_delivery(candidate_id, "delivery_wip", prs)
    except Exception:
        rollback_ok = True
        for repo, pr in prs.items():
            if not pr["draft"]:
                try:
                    github.set_draft(repo, pr["node_id"], True)
                    pr["draft"] = True
                except Exception:
                    rollback_ok = False
        status = "delivery_wip" if rollback_ok else "wip_incomplete"
        store.save_delivery(candidate_id, status, prs)
        return {"status": status, "prs": prs}
    store.save_delivery(candidate_id, "delivered", prs)
    return {"status": "delivered", "prs": prs}
