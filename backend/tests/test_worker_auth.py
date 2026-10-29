"""Regression coverage for credentials passed to Git and GitHub HTTP."""
import base64
import os
from types import SimpleNamespace

import pytest

from app import worker


class StopWorker(BaseException):
    pass


def assert_basic(header, token):
    scheme, encoded = header.split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == "x-access-token:" + token


@pytest.mark.parametrize("token", ["read-token", ""])
def test_clone_uses_read_token_without_inheriting_delivery_credentials(monkeypatch, tmp_path, token):
    monkeypatch.setenv("GITHUB_READ_TOKEN", token)
    monkeypatch.setenv("GITHUB_TOKEN", "write-token")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: stale-write-credential")
    captured = {}

    def run(args, **kwargs):
        assert args[:2] == ["git", "clone"]
        captured.update(kwargs["env"])
        raise StopWorker()

    monkeypatch.setattr(worker.subprocess, "run", run)
    store = SimpleNamespace(ensure_workflow=lambda _: {'stage':'implementation','ready_for_implementation':True,'snapshot_version':1}, get_snapshot_version=lambda *a: {'title':'bug'}, append_event=lambda *a, **k: None, get_job=lambda _: {"snapshot": {"title": "bug"}, "repo_ids": ["acme/api"]})
    with pytest.raises(StopWorker):
        worker._run_attempt(store, {"job_id": "job", "attempt_id": "attempt", "epoch": 1},
                            {"acme/api": {"target_branch": "dev"}}, tmp_path, "image", "volume")
    if token:
        assert captured["GIT_CONFIG_KEY_0"] == "http.extraHeader"
        assert_basic(captured["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: "), token)
    else:
        assert "GIT_CONFIG_VALUE_0" not in captured


def test_delivery_uses_basic_for_git_and_github_api(monkeypatch, tmp_path):
    repos = tmp_path / "repos.json"
    repos.write_text("{}")
    monkeypatch.setenv("REPOS_CONFIG", str(repos))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("GITHUB_TOKEN", "write-token")
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key, raising=False)
    store = SimpleNamespace(claim_next_job=lambda *a, **k: None, next_approved_candidate=lambda: None)
    monkeypatch.setattr(worker, "Store", lambda _: store)
    captured = {}

    def deliver(store, github, workspace):
        captured["git"] = os.environ["GIT_CONFIG_VALUE_0"]
        captured["api"] = github.http.build_request("GET", "/user").headers["Authorization"]
        raise StopWorker()

    monkeypatch.setattr(worker, "deliver_once", deliver)
    with pytest.raises(StopWorker):
        worker.run_forever()
    assert_basic(captured["git"].removeprefix("Authorization: "), "write-token")
    assert_basic(captured["api"], "write-token")
