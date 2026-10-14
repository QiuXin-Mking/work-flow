"""HTTP API for the shared five-person Bug repair console."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from typing import Any, Mapping

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .feishu import BugSnapshot, FeishuClient, parse_bug_url
from .repos import recommend_repos
from .store import Store


class LoginInput(BaseModel):
    username: str
    password: str


class BugInput(BaseModel):
    url: str


class JobInput(BugInput):
    repoIds: list[str] = Field(min_length=1)


class ApprovalInput(BaseModel):
    candidateId: str


def _snapshot_dict(snapshot: BugSnapshot) -> dict[str, Any]:
    def redact(value: Any) -> Any:
        if isinstance(value, str):
            return re.sub(r"(?i)(\bpassword\b|密码|token|api[_ -]?key)(\s*[:：=]\s*)([^\s,;，；]+)",
                          r"\1\2[REDACTED]", value)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        return value
    return {
        "title": snapshot.title, "description": redact(snapshot.description),
        "updated_at": snapshot.updated_at, "fields": redact(snapshot.fields),
        "status": snapshot.status,
        "source_url": snapshot.source_url,
        "ref": asdict(snapshot.ref),
    }


def create_app(store: Store, feishu: FeishuClient,
               repos: Mapping[str, Mapping[str, Any]]) -> FastAPI:
    app = FastAPI(title="Pi Bugfix Console", version="1.0")

    def current_user(session: str | None = Cookie(default=None)) -> dict[str, str]:
        user = store.get_user_by_session(session)
        if user is None:
            raise HTTPException(401, "请先登录")
        return user

    def read_bug(url: str) -> BugSnapshot:
        try:
            ref = parse_bug_url(url)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return feishu.get_bug(ref)
        except ValueError as exc:
            if "credentials" in str(exc):
                raise HTTPException(503, "飞书项目凭据尚未配置") from exc
            raise HTTPException(502, f"飞书工单内容不可用：{exc}") from exc
        except Exception as exc:
            raise HTTPException(502, f"飞书工单读取失败：{type(exc).__name__}") from exc

    def present_job(job: dict[str, Any]) -> dict[str, Any]:
        candidate = store.get_candidate(job["candidate_id"]) if job["candidate_id"] else None
        delivery = store.get_delivery(job["candidate_id"]) if job["candidate_id"] else None
        events = store.get_events(job["id"])
        evidence: dict[str, Any] = {}
        for event in events:
            if event["kind"] == "evidence":
                evidence.update(event["payload"])
        repository_rows = []
        for repo_id in job["repo_ids"]:
            item = (candidate or {}).get("repos", {}).get(repo_id, {})
            pr = (delivery or {}).get("prs", {}).get(repo_id, {})
            repository_rows.append({"id": repo_id, "name": repo_id,
                                    "tree_id": item.get("tree_id"),
                                    "status": "pr_ready" if pr and not pr.get("draft") else "wip" if pr else "pending",
                                    "pr_url": pr.get("url")})
        result = {**job, "title": (job.get("snapshot") or {}).get("title", "待读取工单"),
                  "repositories": repository_rows, "events": events,
                  "candidate": candidate, "delivery": delivery, "evidence": evidence}
        if delivery:
            result["status"] = delivery["status"]
        return result

    @app.post("/api/login")
    def login(body: LoginInput, response: Response) -> dict[str, str]:
        user_id = store.authenticate(body.username, body.password)
        if user_id is None:
            raise HTTPException(401, "账号或密码错误")
        response.set_cookie("session", store.create_session(user_id), httponly=True,
                            samesite="strict", secure=False, max_age=604800)
        return {"id": user_id, "username": body.username.strip().lower()}

    @app.post("/api/logout")
    def logout(response: Response, session: str | None = Cookie(default=None)) -> dict[str, bool]:
        store.delete_session(session)
        response.delete_cookie("session")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
        return user

    @app.post("/api/bug-preview")
    def bug_preview(body: BugInput, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        snapshot = read_bug(body.url)
        suggestions = recommend_repos(snapshot, repos)
        return {"title": snapshot.title, "snapshot": _snapshot_dict(snapshot),
                "status": snapshot.status,
                "repositories": [{"id": item.repo_id, "name": item.repo_id,
                                  "reason": ", ".join(item.evidence), "score": item.score}
                                 for item in suggestions]}

    @app.post("/api/jobs", status_code=201)
    def create_job(body: JobInput, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        if len(set(body.repoIds)) != len(body.repoIds) or any(repo not in repos for repo in body.repoIds):
            raise HTTPException(422, "仓库必须来自已配置白名单且不能重复")
        snapshot = read_bug(body.url)
        if snapshot.status and snapshot.status.casefold() in {
                "resolved", "closed", "done", "已修复", "已关闭", "已完成"}:
            raise HTTPException(409, "工单已修复或关闭，不能重复创建修复任务")
        job_id = store.create_job(snapshot.source_url, body.repoIds, user["id"],
                                  snapshot=_snapshot_dict(snapshot))
        return present_job(store.get_job(job_id))

    @app.get("/api/jobs")
    def list_jobs(user: dict[str, str] = Depends(current_user)) -> list[dict[str, Any]]:
        return [present_job(job) for job in store.list_jobs(user["id"])]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        return present_job(job)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        if job["status"] not in ("queued", "running", "needs_input"):
            raise HTTPException(409, "此任务无法取消")
        store.set_job_status(job_id, "cancelled")
        store.append_event(job_id, "status", {"message": "任务已取消"})
        return present_job(store.get_job(job_id))

    @app.post("/api/jobs/{job_id}/approve")
    def approve(job_id: str, body: ApprovalInput,
                user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        if not store.approve_candidate(job_id, body.candidateId, user["id"]):
            raise HTTPException(409, "候选不存在、证据不足或不能审核自己的任务")
        store.append_event(job_id, "status", {"message": "人工审核通过，等待创建 PR"})
        return present_job(store.get_job(job_id))

    @app.get("/api/jobs/{job_id}/events")
    async def event_stream(job_id: str, request: Request,
                           user: dict[str, str] = Depends(current_user)) -> StreamingResponse:
        if store.get_job(job_id) is None:
            raise HTTPException(404, "任务不存在")
        async def stream():
            last = 0
            while not await request.is_disconnected():
                for event in store.get_events(job_id, last):
                    last = event["id"]
                    yield f"id: {last}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app
