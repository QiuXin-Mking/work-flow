import httpx
import pytest

from app.feishu import BugSnapshot, FeishuClient, WorkItemRef, parse_bug_url
from app.repos import recommend_repos


def test_parse_only_exact_feishu_issue_detail_url() -> None:
    ref = parse_bug_url("https://project.feishu.cn/team_1/issue/detail/123456")
    assert ref == WorkItemRef("team_1", "123456")
    assert parse_bug_url("https://project.feishu.cn/neptune_software_system/issue/detail/7128390467") == WorkItemRef(
        "neptune_software_system", "7128390467")

    for bad in (
        "http://project.feishu.cn/team_1/issue/detail/123456",
        "https://project.feishu.cn.evil.test/team_1/issue/detail/123456",
        "https://evil.test/team_1/issue/detail/123456",
        "https://project.feishu.cn@evil.test/team_1/issue/detail/123456",
        "https://project.feishu.cn:444/team_1/issue/detail/123456",
        "https://project.feishu.cn/team_1/story/detail/123456",
        "https://project.feishu.cn/team_1/issue/detail/123456/extra",
        "https://project.feishu.cn/team_1/issue/detail/../123456",
        "https://project.feishu.cn/team_1/issue/detail/123456\n",
        "https://project.feishu.cn/team_1/issue/detail/123\n456",
        "https://[project.feishu.cn/team_1/issue/detail/123456",
    ):
        with pytest.raises(ValueError):
            parse_bug_url(bad)


def test_client_fetches_fixed_host_only_and_checks_item_identity() -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/open_api/authen/plugin_token":
            return httpx.Response(200, json={"err_code": 0, "data": {"token": "test-token"}})
        return httpx.Response(200, json={"err_code": 0, "data": [{"id": 123456, "name": "Login fails",
            "description": "Repro", "work_item_status": {"key": "RESOLVED", "name": "已修复"}}]})

    client = FeishuClient(
        plugin_id="id",
        plugin_secret="secret",
        user_key="reader",
        transport=httpx.MockTransport(respond),
    )
    snapshot = client.get_bug(WorkItemRef("team_1", "123456"))
    assert snapshot.title == "Login fails"
    assert snapshot.description == "Repro"
    assert snapshot.status == "RESOLVED"
    assert len(requests) == 2
    assert requests[1].url.host == "project.feishu.cn"
    assert requests[1].headers["X-PLUGIN-TOKEN"] == "test-token"
    assert requests[1].headers["X-USER-KEY"] == "reader"


def test_client_rejects_mismatched_item() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/open_api/authen/plugin_token":
            return httpx.Response(200, json={"err_code": 0, "data": {"token": "test-token"}})
        return httpx.Response(200, json={"err_code": 0, "data": [{"id": 999, "name": "Wrong item"}]})

    client = FeishuClient("id", "secret", "reader", transport=httpx.MockTransport(respond))
    with pytest.raises(ValueError, match="matching"):
        client.get_bug(WorkItemRef("team_1", "123456"))


def test_client_preserves_rich_text_description() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/open_api/authen/plugin_token":
            return httpx.Response(200, json={"data": {"token": "test-token"}, "error": {"code": 0}})
        return httpx.Response(200, json={
            "data": [{"id": 123456, "name": "Crash", "multi_texts": [
                {"field_key": "description", "field_value": {"doc_text": "Steps to reproduce"}}
            ]}],
            "error": {"code": 0},
        })

    client = FeishuClient("id", "secret", "reader", transport=httpx.MockTransport(respond))
    assert client.get_bug(WorkItemRef("team_1", "123456")).description == "Steps to reproduce"


def test_recommendations_have_evidence_and_do_not_select_for_user() -> None:
    snapshot = BugSnapshot(WorkItemRef("team", "123"), "Login API timeout", "auth endpoint slow")
    suggestions = recommend_repos(
        snapshot,
        {
            "server": {"project_keys": ["team"], "keywords": ["api", "auth"]},
            "dashboard": {"project_keys": ["other"], "keywords": ["chart"]},
        },
    )
    assert [item.repo_id for item in suggestions] == ["server"]
    assert suggestions[0].evidence
    assert not hasattr(suggestions[0], "confirmed")
