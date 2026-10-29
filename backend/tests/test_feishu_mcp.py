import json
import time

import httpx
import pytest

from app.feishu import WorkItemRef
from app.feishu_mcp import FeishuMcpClient, parse_mcp_workitem_result
from app.feishu_mcp_auth import OAuthStore, ServiceOAuth


REF = WorkItemRef("neptune_software_system", "7128390467")


def tool_result(*, project="neptune_software_system", item_id="7128390467", description="Repro steps"):
    payload = {
        "work_item_attribute": {
            "owned_project": {"key": "project-id", "simple_name": project},
            "work_item_id": item_id,
            "work_item_name": "AM cannot save Remarks",
            "work_item_type": {"key": "issue"},
            "work_item_status": {"key": "RESOLVED", "name": "已修复"},
        },
        "work_item_fields": [
            {"key": "description", "name": "任务描述", "value": description},
            {"key": "updated_at", "name": "更新时间", "value": {"timestamp": 1791442800519}},
            {"key": "priority", "name": "缺陷评级", "value": {"label": "P1"}},
        ],
        "pagination": {"has_more": False},
    }
    return {"content": [{"type": "text", "text": json.dumps(payload) + "\nlog_id: abc123"}]}


def test_mcp_result_normalizes_real_workitem_shape():
    bug = parse_mcp_workitem_result(REF, tool_result())
    assert bug.title == "AM cannot save Remarks"
    assert bug.description == "Repro steps"
    assert bug.status == "RESOLVED"
    assert bug.updated_at == 1791442800519
    assert bug.fields["priority"] == {"label": "P1"}


@pytest.mark.parametrize("result", [
    tool_result(project="other"),
    tool_result(item_id="123"),
    tool_result(description=""),
    {"isError": True, "content": [{"type": "text", "text": "Permission denied"}]},
    {"content": [{"type": "text", "text": "not JSON"}]},
])
def test_mcp_result_rejects_missing_or_wrong_bug(result):
    with pytest.raises(ValueError):
        parse_mcp_workitem_result(REF, result)


def test_backend_mcp_client_initializes_then_calls_only_read_tool(tmp_path):
    store = OAuthStore(tmp_path / "oauth.json")
    store.save({"client_id": "client", "access_token": "token", "refresh_token": "refresh",
                "expires_at": time.time() + 3600})
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append((request, body))
        assert request.headers["Authorization"] == "Bearer token"
        if body["method"] == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "session-1"},
                                  json={"jsonrpc": "2.0", "id": 1,
                                        "result": {"protocolVersion": "2025-03-26"}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert body["method"] == "tools/call"
        if body["id"] == 2:
            assert body["params"] == {"name": "get_workitem_brief", "arguments": {
                "url": REF.source_url, "fields": ["_all"], "page_size": 100}}
            result = tool_result()
        else:
            assert body["params"] == {"name": "list_workitem_comments", "arguments": {
                "project_key": "project-id", "work_item_id": REF.work_item_id, "page_num": 1}}
            result = {"content": [{"type": "text", "text": json.dumps({
                "comments": [{"comment_id": "comment-1", "creator": "user-1",
                              "created_at": "2026-10-27 18:49:05", "content": "please test", "file_url": ""}],
                "pagination": {"page_num": 1, "total_pages": 1}})}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    client = FeishuMcpClient(store=store, transport=httpx.MockTransport(respond))
    bug = client.get_bug(REF)
    assert bug.title == "AM cannot save Remarks"
    assert bug.comments[0]["content"] == "please test"
    assert [body["method"] for _, body in calls] == [
        "initialize", "notifications/initialized", "tools/call", "tools/call"]
    assert all(req.url.host == "project.feishu.cn" for req, _ in calls)
    assert calls[2][0].headers["Mcp-Session-Id"] == "session-1"


def test_expired_token_is_refreshed_and_persisted(tmp_path):
    store = OAuthStore(tmp_path / "oauth.json")
    store.save({"client_id": "client", "client_secret": "secret", "access_token": "old",
                "refresh_token": "refresh", "expires_at": 1})

    def respond(request):
        assert request.url.host == "project.feishu.cn"
        assert request.url.path == "/mcp_server/oauth/token"
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "next",
                                          "expires_in": 3600, "token_type": "Bearer"})

    oauth = ServiceOAuth(store=store, transport=httpx.MockTransport(respond))
    assert oauth.access_token() == "new"
    assert store.load()["refresh_token"] == "next"


def test_missing_service_authorization_is_explicit(tmp_path):
    client = FeishuMcpClient(store=OAuthStore(tmp_path / "none.json"))
    with pytest.raises(ValueError, match="MCP authorization"):
        client.get_bug(REF)


def test_device_login_reregisters_expired_client(tmp_path):
    store = OAuthStore(tmp_path / "oauth.json")
    store.save({"client_id": "old", "client_secret": "expired"})
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/device_code"):
            if b"client_id=old" in request.content:
                return httpx.Response(200, json={"code": 1000053834,
                                                 "msg": "Oauth 客户端信息失效，请重新生成"})
            return httpx.Response(200, json={"device_code": "device", "user_code": "ABCD",
                                             "verification_uri": "https://project.feishu.cn/verify",
                                             "expires_in": 600, "interval": 5})
        assert request.url.path.endswith("/register")
        return httpx.Response(200, json={"client_id": "fresh", "client_secret": "new"})

    oauth = ServiceOAuth(store=store, transport=httpx.MockTransport(respond))
    assert oauth.begin_device_login()["user_code"] == "ABCD"
    assert store.load() == {"client_id": "fresh", "client_secret": "new"}
    assert requests == ["/goapi/v5/mcp_server/oauth/device_code",
                        "/mcp_server/oauth/register",
                        "/goapi/v5/mcp_server/oauth/device_code"]
