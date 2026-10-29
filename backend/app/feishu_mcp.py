"""Read one Feishu Project Bug through its fixed-origin MCP endpoint."""

from __future__ import annotations

import json
from dataclasses import replace
import re
from typing import Any

import httpx

from .feishu import BugSnapshot, WorkItemRef, parse_bug_url
from .feishu_mcp_auth import MCP_URL, OAuthStore, ServiceOAuth


_LOG_SUFFIX = re.compile(r"log_id:\s*[A-Za-z0-9_-]+\s*\Z")


def _content_payload(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError") or result.get("is_error"):
        raise ValueError("Feishu MCP tool returned an error")
    structured = result.get("structuredContent") or result.get("structured_content")
    if isinstance(structured, dict):
        return structured
    blocks = result.get("content")
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("Feishu MCP tool returned no content")
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        raw = block.get("text")
        if not isinstance(raw, str) or len(raw) > 2_000_000:
            continue
        try:
            payload, end = json.JSONDecoder().raw_decode(raw.lstrip())
        except json.JSONDecodeError:
            continue
        suffix = raw.lstrip()[end:].strip()
        if suffix and not _LOG_SUFFIX.fullmatch(suffix):
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Feishu MCP tool returned invalid JSON")


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    payload = _content_payload(result)
    if "work_item_attribute" not in payload:
        raise ValueError("Feishu MCP tool returned no work item")
    return payload


def parse_mcp_comments_result(ref: WorkItemRef, result: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize comment pages while retaining identity, time, content, and attachment URL."""
    parse_bug_url(ref.source_url)
    payload = _content_payload(result)
    rows = payload.get("comments")
    if not isinstance(rows, list):
        raise ValueError("Feishu MCP comments result has no comment list")
    comments = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid Feishu MCP comment")
        comments.append({
            "comment_id": str(row.get("comment_id") or ""),
            "creator": str(row.get("creator") or ""),
            "created_at": str(row.get("created_at") or ""),
            "content": str(row.get("content") or ""),
            "file_url": str(row.get("file_url") or ""),
        })
    return comments


def parse_mcp_workitem_result(ref: WorkItemRef, result: dict[str, Any]) -> BugSnapshot:
    """Validate the requested identity and normalize the MCP tool's work item."""
    ref = parse_bug_url(ref.source_url)
    if not isinstance(result, dict):
        raise ValueError("Invalid Feishu MCP result")
    payload = _tool_payload(result)
    attr = payload.get("work_item_attribute")
    if not isinstance(attr, dict):
        raise ValueError("Feishu MCP result has no work item")
    project = attr.get("owned_project")
    item_type = attr.get("work_item_type")
    if (not isinstance(project, dict)
            or project.get("simple_name") != ref.project_key
            or str(attr.get("work_item_id")) != ref.work_item_id
            or not isinstance(item_type, dict)
            or item_type.get("key") != "issue"):
        raise ValueError("Feishu MCP result does not match requested issue")
    title = attr.get("work_item_name")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Feishu MCP issue has no title")
    rows = payload.get("work_item_fields")
    if not isinstance(rows, list):
        raise ValueError("Feishu MCP issue has no fields")
    fields = {row["key"]: row.get("value") for row in rows
              if isinstance(row, dict) and isinstance(row.get("key"), str)}
    description = fields.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Feishu MCP issue has no description")
    status = attr.get("work_item_status")
    updated = fields.get("updated_at")
    updated_at = updated.get("timestamp") if isinstance(updated, dict) else None
    return BugSnapshot(ref=ref, title=title.strip(), description=description,
                       fields=fields, status=status.get("key") if isinstance(status, dict) else None,
                       updated_at=updated_at if isinstance(updated_at, int) else None)


def _rpc_result(response: httpx.Response, request_id: int) -> dict[str, Any]:
    if response.status_code == 401:
        raise ValueError("Feishu MCP authorization expired; run the device login command")
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        messages = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError as exc:
                    raise ValueError("Invalid Feishu MCP event") from exc
        matching = [value for value in messages if isinstance(value, dict) and value.get("id") == request_id]
        if len(matching) != 1:
            raise ValueError("Feishu MCP did not return the requested response")
        payload = matching[0]
    else:
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("id") != request_id:
        raise ValueError("Invalid Feishu MCP response")
    if "error" in payload:
        raise ValueError("Feishu MCP request failed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Feishu MCP response has no result")
    return result


class FeishuMcpClient:
    """Use OAuth and MCP directly in the API process; Pi never sees these credentials."""

    def __init__(self, store: OAuthStore | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.oauth = ServiceOAuth(store=store, transport=transport)
        self.transport = transport

    def get_bug(self, ref: WorkItemRef) -> BugSnapshot:
        ref = parse_bug_url(ref.source_url)
        token = self.oauth.access_token()
        headers = {"Authorization": f"Bearer {token}",
                   "Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        with httpx.Client(transport=self.transport, timeout=30,
                          follow_redirects=False, trust_env=False) as client:
            response = client.post(MCP_URL, headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "pi-bugfix-console", "version": "1.0"}},
            })
            initialized = _rpc_result(response, 1)
            session_id = response.headers.get("Mcp-Session-Id")
            protocol = initialized.get("protocolVersion", "2025-03-26")
            headers["MCP-Protocol-Version"] = protocol
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            notification = client.post(MCP_URL, headers=headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized"})
            if notification.status_code not in (200, 202, 204):
                raise ValueError("Feishu MCP session initialization failed")
            response = client.post(MCP_URL, headers=headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "get_workitem_brief", "arguments": {
                    "url": ref.source_url, "fields": ["_all"], "page_size": 100}},
            })
            result = _rpc_result(response, 2)
            payload = _tool_payload(result)
            bug = parse_mcp_workitem_result(ref, result)
            project = payload.get("work_item_attribute", {}).get("owned_project", {})
            project_key = project.get("key") if isinstance(project, dict) else None
            if not isinstance(project_key, str) or not project_key:
                raise ValueError("Feishu MCP issue has no project key for comment lookup")
            comments: list[dict[str, Any]] = []
            page_num = 1
            while True:
                response = client.post(MCP_URL, headers=headers, json={
                    "jsonrpc": "2.0", "id": page_num + 2, "method": "tools/call",
                    "params": {"name": "list_workitem_comments", "arguments": {
                        "project_key": project_key, "work_item_id": ref.work_item_id,
                        "page_num": page_num}},
                })
                comment_result = _rpc_result(response, page_num + 2)
                comment_payload = _content_payload(comment_result)
                comments.extend(parse_mcp_comments_result(ref, comment_result))
                pagination = comment_payload.get("pagination", {})
                total_pages = pagination.get("total_pages", page_num) if isinstance(pagination, dict) else page_num
                if not isinstance(total_pages, int) or total_pages < page_num or total_pages > 100:
                    raise ValueError("Invalid Feishu MCP comments pagination")
                if page_num >= total_pages:
                    break
                page_num += 1
        return replace(bug, comments=comments)
