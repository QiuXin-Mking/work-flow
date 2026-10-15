"""Production ASGI entry point."""

import json
import os
from pathlib import Path

from .api import create_app
from .feishu import FeishuClient, WorkItemRef, BugSnapshot
from .feishu_mcp import FeishuMcpClient
from .store import Store


class LazyFeishu:
    """Keep the login UI available until the service has Feishu authorization."""

    def get_bug(self, ref: WorkItemRef) -> BugSnapshot:
        if os.getenv("FEISHU_SOURCE", "mcp") == "plugin":
            return FeishuClient().get_bug(ref)
        return FeishuMcpClient().get_bug(ref)


repos = json.loads(Path(os.environ.get("REPOS_CONFIG", "/app/repos.json")).read_text(encoding="utf-8"))
app = create_app(Store(os.environ.get("DATABASE_PATH", "/data/jobs.sqlite")), LazyFeishu(), repos)
