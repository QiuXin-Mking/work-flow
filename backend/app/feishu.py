"""Read-only Feishu Project work item adapter.

The pasted URL is parsed as an identifier. It is never used as a request URL.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


_HOST = "project.feishu.cn"
_DETAIL_PATH = re.compile(r"^/([A-Za-z0-9_-]{1,80})/issue/detail/([0-9]{1,19})/?$")


@dataclass(frozen=True)
class WorkItemRef:
    project_key: str
    work_item_id: str

    @property
    def source_url(self) -> str:
        return f"https://{_HOST}/{self.project_key}/issue/detail/{self.work_item_id}"


@dataclass(frozen=True)
class BugSnapshot:
    ref: WorkItemRef
    title: str
    description: str
    updated_at: int | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    comments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source_url(self) -> str:
        return self.ref.source_url


def parse_bug_url(url: str) -> WorkItemRef:
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or url != url.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in url)
    ):
        raise ValueError("Invalid Feishu Project URL")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != _HOST
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("Only the configured Feishu Project issue URL is accepted")
    match = _DETAIL_PATH.fullmatch(parts.path)
    if match is None:
        raise ValueError("Expected a Feishu Project issue detail URL")
    return WorkItemRef(match.group(1), match.group(2))


def _check_api_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Invalid Feishu response")
    error = payload.get("error")
    if isinstance(error, dict) and error.get("code", 0) != 0:
        raise ValueError(f"Feishu API error {error['code']}")
    for key in ("err_code", "code"):
        if key in payload and payload[key] != 0:
            raise ValueError(f"Feishu API error {payload[key]}")
    return payload


class FeishuClient:
    """Fetch one issue using a plugin identity bound to a fixed API origin."""

    def __init__(
        self,
        plugin_id: str | None = None,
        plugin_secret: str | None = None,
        user_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.plugin_id = plugin_id or os.getenv("FEISHU_PROJECT_PLUGIN_ID")
        self.plugin_secret = plugin_secret or os.getenv("FEISHU_PROJECT_PLUGIN_SECRET")
        self.user_key = user_key or os.getenv("FEISHU_PROJECT_USER_KEY")
        if not all((self.plugin_id, self.plugin_secret, self.user_key)):
            raise ValueError("Feishu Project plugin credentials and user key are required")
        self.transport = transport

    def get_bug(self, ref: WorkItemRef) -> BugSnapshot:
        # Revalidate even when callers construct WorkItemRef directly.
        ref = parse_bug_url(ref.source_url)
        with httpx.Client(
            base_url=f"https://{_HOST}",
            transport=self.transport,
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            auth_response = client.post(
                "/open_api/authen/plugin_token",
                json={"plugin_id": self.plugin_id, "plugin_secret": self.plugin_secret, "type": 0},
            )
            auth_response.raise_for_status()
            auth = _check_api_result(auth_response.json())
            auth_data = auth.get("data")
            token = auth_data.get("token") if isinstance(auth_data, dict) else None
            if not isinstance(token, str) or not token:
                raise ValueError("Feishu plugin token missing")

            response = client.post(
                f"/open_api/{ref.project_key}/work_item/issue/query",
                headers={"X-PLUGIN-TOKEN": token, "X-USER-KEY": self.user_key},
                json={"work_item_ids": [int(ref.work_item_id)], "expand": {"need_multi_text": True}},
            )
            response.raise_for_status()
            result = _check_api_result(response.json())
            items = result.get("data")
            if not isinstance(items, list):
                raise ValueError("Feishu work item response has no item list")
            matches = [
                item for item in items
                if isinstance(item, dict)
                and str(item.get("id")) == ref.work_item_id
                and item.get("work_item_type_key", "issue") == "issue"
                and item.get("project_key", ref.project_key) == ref.project_key
            ]
            if len(matches) != 1:
                raise ValueError("Feishu response has no matching issue")
            item = matches[0]
            title = item.get("name")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("Feishu issue has no title")
            fields = {
                entry["field_key"]: entry.get("field_value")
                for entry in item.get("fields", [])
                if isinstance(entry, dict) and isinstance(entry.get("field_key"), str)
            }
            description = item.get("description") or fields.get("description") or ""
            if not description:
                for entry in item.get("multi_texts", []):
                    if isinstance(entry, dict) and entry.get("field_key") == "description":
                        value = entry.get("field_value")
                        if isinstance(value, dict):
                            description = value.get("doc_text") or ""
                            fields["description_rich_text"] = value
                        break
            if not isinstance(description, str):
                description = str(description)
            updated_at = item.get("updated_at")
            raw_status = item.get("work_item_status") or item.get("status") or fields.get("work_item_status")
            if isinstance(raw_status, dict):
                status = raw_status.get("key") or raw_status.get("name")
            else:
                status = raw_status
            return BugSnapshot(
                ref=ref,
                title=title.strip(),
                description=description,
                updated_at=updated_at if isinstance(updated_at, int) else None,
                fields=fields,
                status=status if isinstance(status, str) else None,
            )
