"""Human review of a frozen, verified candidate."""

import hashlib
import json
from typing import Protocol


class ReviewStore(Protocol):
    def get_job(self, job_id: str) -> dict | None: ...
    def get_candidate(self, candidate_id: str) -> dict | None: ...
    def save_approval(self, candidate_id: str, reviewer_id: str, binding: str) -> None: ...
    def get_approval(self, candidate_id: str) -> dict | None: ...


def candidate_binding(candidate: dict) -> str:
    """Bind review to every frozen tree and observed target SHA."""
    repos = candidate.get("repos", {})
    if not repos:
        raise ValueError("candidate has no repositories")
    subjects = []
    for repo, item in sorted(repos.items()):
        if not all(item.get(key) for key in ("tree_id", "target_branch", "target_sha")):
            raise ValueError("candidate has incomplete repository binding")
        subjects.append([repo, item["tree_id"], item["target_branch"], item["target_sha"]])
    payload = json.dumps([candidate["id"], subjects], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def approve_candidate(store: ReviewStore, job_id: str, candidate_id: str, reviewer_id: str) -> dict:
    job = store.get_job(job_id)
    candidate = store.get_candidate(candidate_id)
    if not job or not candidate or candidate.get("job_id") != job_id or job.get("candidate_id") != candidate_id:
        raise ValueError("candidate is not current for job")
    if job.get("status") != "review_ready":
        raise ValueError("candidate is not review ready")
    if reviewer_id == job.get("created_by"):
        raise ValueError("job creator cannot approve")
    if not candidate.get("reproduced"):
        raise ValueError("credible pre-fix reproduction required")
    if not candidate.get("verified"):
        raise ValueError("independent verification required")
    binding = candidate_binding(candidate)
    approval = {"reviewer_id": reviewer_id, "binding": binding}
    store.save_approval(candidate_id, reviewer_id, binding)
    return approval
