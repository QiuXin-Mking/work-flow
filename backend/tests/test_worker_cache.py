"""Repository cache updates must not mutate previous attempt workspaces."""
import os
import subprocess
from app import worker


def test_cache_fetch_updates_without_changing_previous_attempt(monkeypatch, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-b", "dev", str(origin))
    git("-C", str(origin), "config", "user.name", "Test")
    git("-C", str(origin), "config", "user.email", "test@example.com")
    (origin / "file").write_text("one")
    git("-C", str(origin), "add", ".")
    git("-C", str(origin), "commit", "-m", "one")
    monkeypatch.setattr(worker, "_repository_url", lambda _: origin.as_uri())
    monkeypatch.delenv("GIT_HTTP_PROXY", raising=False)
    first, second, cache = tmp_path / "first", tmp_path / "second", tmp_path / "cache"
    worker._prepare_checkout("acme/api", "dev", first, cache)
    (origin / "file").write_text("two")
    git("-C", str(origin), "add", ".")
    git("-C", str(origin), "commit", "-m", "two")
    worker._prepare_checkout("acme/api", "dev", second, cache)
    assert (first / "file").read_text() == "one"
    assert (second / "file").read_text() == "two"
    assert not (second / ".git/objects/info/alternates").exists()
    assert git("-C", str(second), "remote", "get-url", "origin") == origin.as_uri()
    assert git("-C", str(second), "rev-parse", "HEAD") == git("-C", str(origin), "rev-parse", "HEAD")
    assert len(list(cache.iterdir())) == 1
    cache_repo = next(cache.iterdir())
    for pack in (second / ".git/objects/pack").glob("*.pack"):
        assert not os.path.samefile(pack, cache_repo / "objects/pack" / pack.name)


def test_git_proxy_and_basic_credentials_are_process_scoped(monkeypatch):
    monkeypatch.setenv("GITHUB_READ_TOKEN", "read-secret")
    monkeypatch.setenv("GIT_HTTP_PROXY", "http://192.168.2.78:7997")
    monkeypatch.setenv("GIT_CONFIG_KEY_9", "stale")
    env = worker._read_git_env()
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_1"] == "http.proxy"
    assert env["GIT_CONFIG_VALUE_1"] == "http://192.168.2.78:7997"
    assert "GIT_CONFIG_KEY_9" not in env
    assert os.environ["GIT_CONFIG_KEY_9"] == "stale"
