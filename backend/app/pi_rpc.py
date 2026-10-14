"""Pi RPC JSONL subprocess adapter. A prompt finishes only at agent_settled."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PromptResult:
    accepted: bool
    settled: bool
    events: list[dict[str, Any]]


class PiRpcSession:
    def __init__(self, command: list[str], *, cwd: str | Path):
        self.command = command
        self.cwd = str(cwd)
        self.process: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._stderr: asyncio.Task | None = None

    async def __aenter__(self) -> "PiRpcSession":
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={key: value for key, value in os.environ.items()
                 if "TOKEN" not in key.upper() and "SECRET" not in key.upper()})
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr = asyncio.create_task(self._drain_stderr())
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.stop()

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        try:
            while line := await self.process.stdout.readline():
                if len(line) > 2_000_000:
                    raise ValueError("Pi RPC line too long")
                event = json.loads(line)
                if isinstance(event, dict):
                    await self._queue.put(event)
        except Exception as exc:
            await self._queue.put({"type": "rpc_error", "error": str(exc)})
        finally:
            await self._queue.put({"type": "rpc_eof"})

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        while await self.process.stderr.read(8192):
            pass

    async def _send(self, command: dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write((json.dumps(command, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def prompt(self, text: str, *, timeout: float = 1800) -> PromptResult:
        if self.process is None:
            raise RuntimeError("Pi session is not running")
        request_id = uuid.uuid4().hex
        await self._send({"id": request_id, "type": "prompt", "message": text})
        events: list[dict[str, Any]] = []
        accepted = False

        async def receive() -> PromptResult:
            nonlocal accepted
            while True:
                event = await self._queue.get()
                if event.get("type") == "rpc_eof":
                    raise RuntimeError("Pi RPC exited before agent_settled")
                if event.get("type") == "rpc_error":
                    raise RuntimeError(event.get("error", "Pi RPC error"))
                if event.get("id") == request_id and event.get("type") == "response":
                    if not event.get("success"):
                        raise RuntimeError(f"Pi prompt rejected: {event.get('error')}")
                    accepted = True
                    if (event.get("data") or {}).get("disposition") == "handled":
                        return PromptResult(True, False, events)
                    continue
                if len(events) < 1000:
                    events.append({"type": event.get("type")})
                if event.get("type") == "agent_settled" and accepted:
                    return PromptResult(True, True, events)

        try:
            return await asyncio.wait_for(receive(), timeout)
        except TimeoutError:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            try:
                await self._send({"id": uuid.uuid4().hex, "type": "abort"})
                await asyncio.wait_for(self.process.wait(), 0.25)
            except (TimeoutError, BrokenPipeError, ConnectionResetError):
                self.process.kill()
                await self.process.wait()
        if self.process.stdin and not self.process.stdin.is_closing():
            self.process.stdin.close()
            try:
                await self.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        for task in (self._reader, self._stderr):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
