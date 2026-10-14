import copy
import os
import subprocess

import pytest
import httpx

from app.delivery import LocalGitPublisher, deliver_group
from app.github import GitHubClient
from app.review import approve_candidate
from app.delivery_worker import run_once


class MemoryStore:
    def __init__(self):
        self.job = {"id": "job-1", "created_by": "alice", "candidate_id": "cand-1", "status": "review_ready"}
        self.candidate = {
            "id": "cand-1", "job_id": "job-1", "reproduced": True, "verified": True,
            "repos": {
                "acme/api": {"tree_id": "tree-a", "target_branch": "dev", "target_sha": "base-a"},
                "acme/ui": {"tree_id": "tree-b", "target_branch": "dev", "target_sha": "base-b"},
            },
        }
        self.approval = None
        self.delivery = None

    def get_job(self, job_id):
        return copy.deepcopy(self.job) if job_id == self.job["id"] else None

    def get_candidate(self, candidate_id):
        return copy.deepcopy(self.candidate) if candidate_id == self.candidate["id"] else None

    def save_approval(self, candidate_id, reviewer_id, binding):
        self.approval = {"reviewer_id": reviewer_id, "binding": binding}

    def get_approval(self, candidate_id):
        return copy.deepcopy(self.approval)

    def get_delivery(self, candidate_id):
        return copy.deepcopy(self.delivery)

    def save_delivery(self, candidate_id, status, prs):
        self.delivery = {"status": status, "prs": copy.deepcopy(prs)}

    def next_approved_candidate(self):
        if self.approval and self.job["status"] in {"review_ready", "delivery_wip"}:
            return self.candidate["id"]
        return None

    def set_job_status(self, job_id, status):
        self.job["status"] = status


class FakeGitHub:
    def __init__(self):
        self.prs = {}
        self.fail_create_for = None
        self.target_shas = {"acme/api": "base-a", "acme/ui": "base-b"}
        self.commit_trees = {"acme/api": "tree-a", "acme/ui": "tree-b"}
        self.fail_ready_for = None

    def target_sha(self, repo, branch):
        return self.target_shas[repo]

    def commit_tree(self, repo, sha):
        return self.commit_trees[repo]

    def find_pr(self, repo, branch, base):
        return copy.deepcopy(self.prs.get(repo))

    def create_draft_pr(self, repo, branch, base, title, body):
        if repo == self.fail_create_for:
            raise RuntimeError("GitHub unavailable")
        pr = {"number": len(self.prs) + 1, "node_id": f"node-{repo}", "draft": True,
              "head_sha": f"head-{repo}", "head_ref": branch,
              "base_ref": base, "url": f"https://github.com/{repo}/pull/{len(self.prs) + 1}"}
        self.prs[repo] = pr
        return copy.deepcopy(pr)

    def set_draft(self, repo, node_id, draft):
        if not draft and repo == self.fail_ready_for:
            raise RuntimeError("GitHub stage transition failed")
        self.prs[repo]["draft"] = draft


class FakePublisher:
    def publish(self, repo, branch, tree_id, target_sha):
        return f"head-{repo}"


def test_only_another_user_can_approve_frozen_verified_candidate():
    store = MemoryStore()
    with pytest.raises(ValueError, match="creator"):
        approve_candidate(store, "job-1", "cand-1", "alice")
    assert approve_candidate(store, "job-1", "cand-1", "bob")["reviewer_id"] == "bob"
    store.candidate["repos"]["acme/api"]["tree_id"] = "changed"
    with pytest.raises(ValueError, match="binding"):
        deliver_group(store, FakeGitHub(), FakePublisher(), "cand-1")


def test_unreproduced_candidate_cannot_be_approved():
    store = MemoryStore()
    store.candidate["reproduced"] = False
    with pytest.raises(ValueError, match="reproduction"):
        approve_candidate(store, "job-1", "cand-1", "bob")


def test_cancelled_job_cannot_deliver_even_with_existing_approval():
    store = MemoryStore()
    approve_candidate(store, "job-1", "cand-1", "bob")
    store.job["status"] = "cancelled"
    with pytest.raises(ValueError, match="status"):
        deliver_group(store, FakeGitHub(), FakePublisher(), "cand-1")


def test_second_repo_failure_leaves_first_draft_and_group_not_delivered():
    store, github = MemoryStore(), FakeGitHub()
    approve_candidate(store, "job-1", "cand-1", "bob")
    github.fail_create_for = "acme/ui"
    result = deliver_group(store, github, FakePublisher(), "cand-1")
    assert result["status"] == "delivery_wip"
    assert result["prs"]["acme/api"]["draft"] is True
    assert github.prs["acme/api"]["draft"] is True
    assert "acme/ui" not in github.prs


def test_retry_reconciles_remote_pr_without_creating_duplicate():
    store, github = MemoryStore(), FakeGitHub()
    approve_candidate(store, "job-1", "cand-1", "bob")
    github.fail_create_for = "acme/ui"
    deliver_group(store, github, FakePublisher(), "cand-1")
    github.fail_create_for = None
    result = deliver_group(store, github, FakePublisher(), "cand-1")
    assert result["status"] == "delivered"
    assert len(github.prs) == 2
    assert all(not pr["draft"] for pr in github.prs.values())


def test_target_move_blocks_delivery_before_creating_pr():
    store, github = MemoryStore(), FakeGitHub()
    approve_candidate(store, "job-1", "cand-1", "bob")
    github.target_shas["acme/ui"] = "moved"
    with pytest.raises(ValueError, match="target"):
        deliver_group(store, github, FakePublisher(), "cand-1")
    assert github.prs == {}


def test_ready_transition_failure_restores_all_prs_to_draft():
    store, github = MemoryStore(), FakeGitHub()
    approve_candidate(store, "job-1", "cand-1", "bob")
    github.fail_ready_for = "acme/ui"
    result = deliver_group(store, github, FakePublisher(), "cand-1")
    assert result["status"] == "delivery_wip"
    assert all(pr["draft"] for pr in github.prs.values())


def test_github_client_creates_draft_and_uses_graphql_to_change_stage():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.content.decode()))
        if request.url.path.endswith("/pulls"):
            return httpx.Response(201, json={"number": 12, "node_id": "PR_node", "draft": True,
                "head": {"sha": "commit-1", "ref": "pi-bugfix/123"}, "base": {"ref": "dev"},
                "html_url": "https://github.com/acme/api/pull/12"})
        return httpx.Response(200, json={"data": {"markPullRequestReadyForReview": {
            "pullRequest": {"id": "PR_node", "isDraft": False}}}})

    client = GitHubClient(httpx.Client(transport=httpx.MockTransport(handler),
        base_url="https://api.github.com", headers={"Authorization": "Bearer test"}))
    pr = client.create_draft_pr("acme/api", "pi-bugfix/123", "dev", "title", "body")
    client.set_draft("acme/api", pr["node_id"], False)
    assert pr["draft"] is True
    assert '"draft":true' in seen[0][2]
    assert "markPullRequestReadyForReview" in seen[1][2]


def test_github_client_rejects_graphql_errors_even_with_http_200():
    client = GitHubClient(httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"errors": [{"message": "permission denied"}]})),
        base_url="https://api.github.com"))
    with pytest.raises(ValueError, match="stage change"):
        client.set_draft("acme/api", "PR_node", True)


def test_github_client_does_not_reuse_closed_pr_on_retry():
    def handler(request):
        return httpx.Response(200, json=[{"number": 9, "node_id": "old", "state": "closed",
            "draft": True, "head": {"sha": "old-sha", "ref": "pi-bugfix/123"},
            "base": {"ref": "dev"}, "html_url": "https://github.com/acme/api/pull/9"}])

    client = GitHubClient(httpx.Client(transport=httpx.MockTransport(handler),
        base_url="https://api.github.com"))
    assert client.find_pr("acme/api", "pi-bugfix/123", "dev") is None


def test_local_publisher_pushes_frozen_tree_once_without_touching_target(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "dev", str(checkout)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", str(remote)], check=True)

    def git(*args, env=None):
        return subprocess.run(["git", "-C", str(checkout), *args], check=True,
            capture_output=True, text=True, env=env).stdout.strip()

    (checkout / "bug.txt").write_text("before")
    git("add", "bug.txt")
    env = {**os.environ, "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "test@example.org",
           "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "test@example.org"}
    git("commit", "-m", "baseline", env=env)
    target = git("rev-parse", "HEAD")
    git("push", "origin", "HEAD:refs/heads/dev")
    (checkout / "bug.txt").write_text("after")
    git("add", "bug.txt")
    tree = git("write-tree")

    publisher = LocalGitPublisher({"acme/api": checkout}, allow_local_remotes=True)
    first = publisher.publish("acme/api", "pi-bugfix/frozen", tree, target)
    second = publisher.publish("acme/api", "pi-bugfix/frozen", tree, target)
    assert first == second
    assert git("ls-remote", "origin", "refs/heads/pi-bugfix/frozen").split()[0] == first
    assert git("ls-remote", "origin", "refs/heads/dev").split()[0] == target
    assert git("rev-parse", f"{first}^{{tree}}") == tree
    assert git("rev-parse", f"{first}^" ) == target

    with pytest.raises(ValueError, match="different commit"):
        publisher.publish("acme/api", "pi-bugfix/frozen", git("rev-parse", f"{target}^{{tree}}"), target)

    git("remote", "set-url", "origin", "git@github.com:wrong/repo.git")
    with pytest.raises(ValueError, match="remote"):
        publisher.publish("acme/api", "pi-bugfix/other", tree, target)


def test_delivery_worker_polls_approval_and_updates_job_status(tmp_path):
    store, github = MemoryStore(), FakeGitHub()
    for repo, item in store.candidate["repos"].items():
        path = tmp_path / repo.replace("/", "-")
        path.mkdir()
        item["checkout_path"] = str(path)
    assert run_once(store, github, tmp_path, publisher_factory=lambda paths: FakePublisher()) is None
    approve_candidate(store, "job-1", "cand-1", "bob")
    github.fail_create_for = "acme/ui"
    assert run_once(store, github, tmp_path, publisher_factory=lambda paths: FakePublisher())["status"] == "delivery_wip"
    assert store.job["status"] == "delivery_wip"
    github.fail_create_for = None
    assert run_once(store, github, tmp_path, publisher_factory=lambda paths: FakePublisher())["status"] == "delivered"
    assert store.job["status"] == "delivered"


def test_delivery_worker_rejects_checkout_outside_workspace(tmp_path):
    store = MemoryStore()
    approve_candidate(store, "job-1", "cand-1", "bob")
    for item in store.candidate["repos"].values():
        item["checkout_path"] = str(tmp_path.parent)
    with pytest.raises(ValueError, match="workspace"):
        run_once(store, FakeGitHub(), tmp_path, publisher_factory=lambda paths: FakePublisher())
