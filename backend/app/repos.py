"""Explainable repository suggestions; selection remains a user action."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .feishu import BugSnapshot


@dataclass(frozen=True)
class RepoSuggestion:
    repo_id: str
    score: int
    evidence: tuple[str, ...]


def recommend_repos(
    snapshot: BugSnapshot, configured_repos: Mapping[str, Mapping[str, Any]]
) -> list[RepoSuggestion]:
    """Rank allowlisted repositories using configured project and keyword evidence."""
    text = f"{snapshot.title} {snapshot.description}".casefold()
    suggestions: list[RepoSuggestion] = []
    for repo_id, config in configured_repos.items():
        evidence: list[str] = []
        if snapshot.ref.project_key in config.get("project_keys", ()):
            evidence.append(f"project_key:{snapshot.ref.project_key}")
        for keyword in config.get("keywords", ()):
            if not isinstance(keyword, str) or not keyword.strip():
                continue
            word = keyword.strip().casefold()
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text):
                evidence.append(f"keyword:{keyword.strip()}")
        if evidence:
            suggestions.append(RepoSuggestion(repo_id, len(evidence), tuple(evidence)))
    return sorted(suggestions, key=lambda entry: (-entry.score, entry.repo_id))
