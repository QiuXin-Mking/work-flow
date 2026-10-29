from workflow_helpers import pass_planning
from pathlib import Path

import pytest

from app.store import Store


def test_five_local_accounts_share_jobs_but_cannot_self_approve(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    alice = store.create_user("alice", "secret one")
    bob = store.create_user("bob", "secret two")
    job = store.create_job("https://project.feishu.cn/acme/issue/detail/123", ["server"], alice, snapshot={"description": "mobi:42 trace-id:t42"})

    assert [item["id"] for item in store.list_jobs(bob)] == [job]
    pass_planning(store, job)
    candidate = store.save_candidate(
        job,
        {"server": {"tree_id": "abc", "target_branch": "dev", "target_sha": "def"}},
        reproduced=True,
        verified=True,
    )
    assert store.approve_candidate(job, candidate, alice) is False
    assert store.approve_candidate(job, candidate, bob) is True


def test_claim_uses_new_lease_epoch_and_old_runner_cannot_write(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    owner = store.create_user("alice", "secret")
    job = store.create_job("https://project.feishu.cn/acme/issue/detail/123", ["server"], owner, snapshot={"description": "mobi:42 trace-id:t42"})

    claim = store.claim_next_job("runner-a", now=10, lease_seconds=5)
    assert claim is not None
    assert claim["job_id"] == job
    assert claim["epoch"] == 1
    assert store.append_event(job, "progress", {"step": 1}, epoch=1) is True

    renewed = store.claim_next_job("runner-b", now=20, lease_seconds=5)
    assert renewed is not None
    assert renewed["epoch"] == 2
    assert store.append_event(job, "progress", {"step": 2}, epoch=1) is False
    assert store.append_event(job, "progress", {"step": 2}, epoch=2) is True


def test_unreproduced_job_cannot_enter_review_ready(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    owner = store.create_user("alice", "secret")
    job = store.create_job("https://project.feishu.cn/acme/issue/detail/123", ["server"], owner, snapshot={"description": "mobi:42 trace-id:t42"})
    claim = store.claim_next_job("runner-a", now=10, lease_seconds=5)
    assert claim is not None

    with pytest.raises(ValueError, match="reproduction"):
        store.mark_review_ready(job, {"server": {"tree": "abc"}}, reproduced=False, epoch=1)


def test_cancelled_attempt_cannot_publish_candidate_or_overwrite_status(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")
    creator = store.create_user("alice", "secret")
    job = store.create_job("https://project.feishu.cn/acme/issue/detail/123", ["server"], creator, snapshot={"description": "mobi:42 trace-id:t42"})
    claim = pass_planning(store, job)
    assert claim
    store.set_job_status(job, "cancelled")
    assert store.set_job_status(job, "failed", epoch=claim["epoch"]) is False
    with pytest.raises(ValueError, match="stale lease"):
        store.save_candidate(job, {"server": {"tree_id": "abc"}}, epoch=claim["epoch"])
