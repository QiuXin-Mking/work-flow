import asyncio
import sys

from app.pi_rpc import PiRpcSession


def test_rpc_waits_for_agent_settled_not_prompt_ack(tmp_path):
    fake = tmp_path / "fake_pi.py"
    fake.write_text('''import json, sys, time
for line in sys.stdin:
    command = json.loads(line)
    print(json.dumps({"id": command["id"], "type": "response", "command": command["type"], "success": True}), flush=True)
    print(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "working"}}), flush=True)
    time.sleep(0.05)
    print(json.dumps({"type": "agent_settled"}), flush=True)
''')

    async def exercise():
        async with PiRpcSession([sys.executable, str(fake)], cwd=tmp_path) as session:
            result = await session.prompt("debug", timeout=2)
            assert result.accepted is True
            assert result.settled is True
            assert any(event.get("type") == "message_update" for event in result.events)

    asyncio.run(exercise())


def test_rpc_abort_on_timeout(tmp_path):
    fake = tmp_path / "stuck.py"
    fake.write_text('''import json, sys, time
for line in sys.stdin:
    command = json.loads(line)
    print(json.dumps({"id": command["id"], "type": "response", "command": command["type"], "success": True}), flush=True)
    time.sleep(60)
''')

    async def exercise():
        async with PiRpcSession([sys.executable, str(fake)], cwd=tmp_path) as session:
            try:
                await session.prompt("debug", timeout=0.1)
                assert False, "expected timeout"
            except TimeoutError:
                assert session.process.returncode is not None

    asyncio.run(exercise())


def test_rpc_prompt_write_has_same_timeout_budget(tmp_path):
    fake = tmp_path/'not_reading.py'
    fake.write_text('import time;time.sleep(60)')
    async def exercise():
        import time
        started=time.monotonic()
        async with PiRpcSession([sys.executable,str(fake)],cwd=tmp_path) as session:
            import pytest
            with pytest.raises(TimeoutError):
                await session.prompt('x'*8_000_000,timeout=0.1)
        assert time.monotonic()-started < 5
    asyncio.run(exercise())
