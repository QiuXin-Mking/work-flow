"""Minimal GitHub REST/GraphQL adapter with an injectable HTTP client.

GitHub REST creates Draft PRs; GraphQL transitions them to Ready or back to Draft.
https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request
https://docs.github.com/en/graphql/reference/mutations
"""

from urllib.parse import quote

import httpx


class GitHubClient:
    def __init__(self, http: httpx.Client):
        self.http = http

    @staticmethod
    def _repo(repo: str) -> str:
        parts = repo.split("/")
        if len(parts) != 2 or not all(part and part.replace("-", "").replace("_", "").replace(".", "").isalnum() for part in parts):
            raise ValueError("invalid GitHub repository")
        return f"/repos/{parts[0]}/{parts[1]}"

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        response = self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def target_sha(self, repo: str, branch: str) -> str:
        data = self._request("GET", self._repo(repo) + "/git/ref/heads/" + quote(branch, safe=""))
        return data["object"]["sha"]

    def commit_tree(self, repo: str, sha: str) -> str:
        data = self._request("GET", self._repo(repo) + "/git/commits/" + quote(sha, safe=""))
        return data["tree"]["sha"]

    def find_pr(self, repo: str, branch: str, base: str) -> dict | None:
        owner = repo.split("/", 1)[0]
        data = self._request("GET", self._repo(repo) + "/pulls", params={
            "state": "all", "head": f"{owner}:{branch}", "base": base, "per_page": 100})
        matches = [self._pr(pr) for pr in data if pr.get("state", "open") == "open"
                   and pr["head"]["ref"] == branch and pr["base"]["ref"] == base]
        if len(matches) > 1:
            raise ValueError("multiple PRs for deterministic branch")
        return matches[0] if matches else None

    def create_draft_pr(self, repo: str, branch: str, base: str, title: str, body: str) -> dict:
        data = self._request("POST", self._repo(repo) + "/pulls", json={
            "title": title, "head": branch, "base": base, "body": body, "draft": True})
        return self._pr(data)

    def set_draft(self, repo: str, node_id: str, draft: bool) -> None:
        self._repo(repo)
        field = "convertPullRequestToDraft" if draft else "markPullRequestReadyForReview"
        query = f"mutation($id: ID!) {{{field}(input: {{pullRequestId: $id}}) {{pullRequest {{id isDraft}}}}}}"
        data = self._request("POST", "/graphql", json={"query": query, "variables": {"id": node_id}})
        if data.get("errors") or data.get("data", {}).get(field, {}).get("pullRequest", {}).get("isDraft") is not draft:
            raise ValueError("GitHub PR stage change failed")

    @staticmethod
    def _pr(data: dict) -> dict:
        return {"number": data["number"], "node_id": data["node_id"],
            "draft": data["draft"], "head_sha": data["head"]["sha"],
            "head_ref": data["head"]["ref"], "base_ref": data["base"]["ref"],
            "url": data["html_url"]}
