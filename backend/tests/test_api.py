from fastapi.testclient import TestClient

from app.api import create_app
from app.feishu import BugSnapshot, WorkItemRef
from app.store import Store


class FakeFeishu:
    def get_bug(self, ref):
        return BugSnapshot(ref, "登录按钮报错", "点击登录出现 500", 123, {})


class ResolvedFeishu:
    def get_bug(self, ref):
        return BugSnapshot(ref, "已修复工单", "测试账号密码：TopSecret123", 123,
                           {"description": "密码: TopSecret123"}, "RESOLVED")


def test_login_preview_confirm_shared_job_and_cancel(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_user("alice", "secret one")
    store.create_user("bob", "secret two")
    app = create_app(store, FakeFeishu(), {"acme/server": {"project_keys": ["acme"]}})
    alice = TestClient(app)
    bob = TestClient(app)
    url = "https://project.feishu.cn/acme/issue/detail/123"

    assert alice.get("/api/jobs").status_code == 401
    assert alice.post("/api/login", json={"username": "alice", "password": "secret one"}).status_code == 200
    assert bob.post("/api/login", json={"username": "bob", "password": "secret two"}).status_code == 200
    preview = alice.post("/api/bug-preview", json={"url": url}).json()
    assert preview["title"] == "登录按钮报错"
    assert preview["repositories"][0]["id"] == "acme/server"
    assert alice.post("/api/jobs", json={"url": url, "repoIds": ["outside/repo"]}).status_code == 422

    response = alice.post("/api/jobs", json={"url": url, "repoIds": ["acme/server"]})
    assert response.status_code == 201
    job_id = response.json()["id"]
    assert bob.get("/api/jobs").json()[0]["id"] == job_id
    assert bob.get(f"/api/jobs/{job_id}").json()["title"] == "登录按钮报错"
    assert bob.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert alice.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


def test_bad_url_and_self_approval_are_rejected(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    alice_id = store.create_user("alice", "secret one")
    store.create_user("bob", "secret two")
    client = TestClient(create_app(store, FakeFeishu(), {"acme/server": {"project_keys": ["acme"]}}))
    client.post("/api/login", json={"username": "alice", "password": "secret one"})
    assert client.post("/api/bug-preview", json={"url": "http://127.0.0.1/secret"}).status_code == 422
    job_id = store.create_job("https://project.feishu.cn/acme/issue/detail/123", ["acme/server"], alice_id)
    candidate_id = store.save_candidate(job_id, {
        "acme/server": {"tree_id": "abc", "target_branch": "main", "target_sha": "def"}
    })
    assert client.post(f"/api/jobs/{job_id}/approve", json={"candidateId": candidate_id}).status_code == 409


def test_resolved_bug_is_previewable_but_cannot_create_new_fix_and_credentials_are_masked(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_user("alice", "secret one")
    client = TestClient(create_app(store, ResolvedFeishu(), {"acme/server": {"project_keys": ["acme"]}}))
    client.post("/api/login", json={"username": "alice", "password": "secret one"})
    url = "https://project.feishu.cn/acme/issue/detail/123"
    preview = client.post("/api/bug-preview", json={"url": url})
    assert preview.json()["status"] == "RESOLVED"
    assert "TopSecret123" not in preview.text
    assert client.post("/api/jobs", json={"url": url, "repoIds": ["acme/server"]}).status_code == 409
