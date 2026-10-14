"""One poll of approved candidates for the single delivery worker."""

from pathlib import Path
from typing import Callable, Protocol

from .delivery import CandidatePublisher, DeliveryStore, GitHubGateway, LocalGitPublisher, deliver_group


class DeliveryQueueStore(DeliveryStore, Protocol):
    def next_approved_candidate(self) -> str | None: ...
    def set_job_status(self, job_id: str, status: str) -> None: ...


def run_once(store: DeliveryQueueStore, github: GitHubGateway, workspace_root: str | Path,
             *, publisher_factory: Callable[[dict[str, Path]], CandidatePublisher] = LocalGitPublisher) -> dict | None:
    """Deliver one approved candidate; return None when the queue is empty.

    The caller runs this from a trusted single worker with GitHub credentials.
    Frozen checkouts belong to the worker workspace, not the Pi agent container.
    """
    candidate_id = store.next_approved_candidate()
    if candidate_id is None:
        return None
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        raise ValueError("approved candidate disappeared")
    root = Path(workspace_root).resolve(strict=True)
    paths: dict[str, Path] = {}
    for repo, item in candidate["repos"].items():
        raw_path = item.get("checkout_path")
        if not raw_path or not Path(raw_path).is_absolute():
            raise ValueError("candidate checkout must be absolute and inside workspace")
        path = Path(raw_path).resolve(strict=True)
        if not path.is_relative_to(root) or path == root or not path.is_dir():
            raise ValueError("candidate checkout is outside workspace")
        paths[repo] = path
    result = deliver_group(store, github, publisher_factory(paths), candidate_id)
    store.set_job_status(candidate["job_id"], result["status"])
    return result
