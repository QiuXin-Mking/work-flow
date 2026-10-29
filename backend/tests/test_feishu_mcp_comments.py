import json
import time

import httpx

from app.feishu import WorkItemRef
from app.feishu_mcp import FeishuMcpClient, parse_mcp_comments_result
from app.feishu_mcp_auth import OAuthStore
from app.trellis_context import prepare_trellis_context


REF = WorkItemRef("neptune_software_system", "7101461494")


def comment_result(comments, page_num=1, total_pages=1):
    return {"content": [{"type": "text", "text": json.dumps({
        "comments": comments,
        "pagination": {"page_num": page_num, "total_pages": total_pages},
    })}]}


def test_comment_parser_keeps_comment_identity_and_attachment():
    parsed = parse_mcp_comments_result(REF, comment_result([{
        "comment_id": "comment-1", "creator": "user-1", "created_at": "2026-10-27 18:49:05",
        "content": "please test", "file_url": "https://project.feishu.cn/file/1",
    }]))
    assert parsed == [{
        "comment_id": "comment-1", "creator": "user-1", "created_at": "2026-10-27 18:49:05",
        "content": "please test", "file_url": "https://project.feishu.cn/file/1",
    }]


def test_mcp_client_reads_all_comment_pages_into_bug_snapshot(tmp_path):
    store = OAuthStore(tmp_path / "oauth.json")
    store.save({"client_id": "client", "access_token": "token", "refresh_token": "refresh",
                "expires_at": time.time() + 3600})
    calls = []
    brief = {"content": [{"type": "text", "text": json.dumps({
        "work_item_attribute": {
            "owned_project": {"key": "project-id", "simple_name": REF.project_key},
            "work_item_id": REF.work_item_id, "work_item_name": "PDF preview",
            "work_item_type": {"key": "issue"}, "work_item_status": {"key": "IN PROGRESS"},
        },
        "work_item_fields": [{"key": "description", "value": "Add PDF preview"}],
    })}]}

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "initialize":
            return httpx.Response(200, headers={"Mcp-Session-Id": "session"}, json={
                "jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        args = body["params"]["arguments"]
        if body["params"]["name"] == "get_workitem_brief":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": brief})
        assert body["params"]["name"] == "list_workitem_comments"
        assert args["project_key"] == "project-id"
        page = args["page_num"]
        result = comment_result([{"comment_id": f"c{page}", "creator": "u",
                                  "created_at": f"2026-10-27 18:4{page}:00",
                                  "content": f"comment {page}", "file_url": ""}],
                                page_num=page, total_pages=2)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    bug = FeishuMcpClient(store=store, transport=httpx.MockTransport(respond)).get_bug(REF)
    assert [row["content"] for row in bug.comments] == ["comment 1", "comment 2"]
    assert [c["params"]["arguments"]["page_num"] for c in calls if c.get("params", {}).get("name") == "list_workitem_comments"] == [1, 2]


def test_snapshot_materializes_comments_without_generating_prd(tmp_path):
    prepare_trellis_context(tmp_path, {
        "title": "PDF preview", "description": "Add preview", "comments": [{
            "comment_id": "c1", "creator": "u1", "created_at": "2026-10-27 18:49:05",
            "content": "please test", "file_url": "",
        }],
    }, ["neptune-robotics/operations-dashboard"])
    prd = (tmp_path / "snapshot.json").read_text(encoding="utf-8")
    assert not (tmp_path / ".trellis/tasks/bugfix/prd.md").exists()
    assert '"comment_id": "c1"' in prd
    assert '"content": "please test"' in prd
