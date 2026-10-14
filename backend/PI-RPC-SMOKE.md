# Pi v1.1.0 RPC 烟测（2026-10-14）

目标为 42 机已有的实验容器 `pi-dev`。本次仅运行临时 Pi 进程，使用 `--no-session --no-extensions --no-mcp`，没有修改容器配置、工作区或业务仓库。测试提示词要求只输出固定字符串，不调用工具。

## 观察

| 项目 | 实际结果 |
| --- | --- |
| `docker exec pi-dev pi --version` | `1.1.0` |
| `get_state` | 同请求 ID 的 `response`，`success:true`；当前选定模型为 `deepseek/deepseek-v4-pro`，`isStreaming:false` |
| 短 `prompt` | 同请求 ID 的 `response`，`command:"prompt"`，`success:true`，`data.disposition:"started"` |
| 模型输出 | `message_update` 的 `assistantMessageEvent.type:"text_delta"` 依次给出 `R`、`PC`、`_OK`，合并为 `RPC_OK` |
| 完成顺序 | `agent_start` → `turn_start` → 消息事件 → `turn_end` → `agent_end` → `agent_settled` |
| 退出 | 收到 `agent_settled` 后关闭 stdin，进程退出码 0；本机 Windows SSH stderr 有 `close - IO is still pending on closed socket`，不在 RPC stdout 内 |

因此，当前模型凭据至少可完成一次真实短文本调用，RPC `prompt` 受理与 `agent_settled` 完成是两个不同事件。此次没有测试工具调用、长任务、连续 prompt、取消、重试、会话恢复、并发隔离或费用控制。`pi-dev` 仍是共享实验容器，不能据此推断可作为 5 人生产 Runner。

## 最小调用样例

此样例在能 SSH 到 42 机的执行端运行。Runner 正式部署时应直接启动隔离容器内的 Pi 子进程，并单独保存 stderr。

```python
import json
import subprocess

process = subprocess.Popen(
    ["ssh", "-o", "BatchMode=yes", "root@192.168.2.42",
     "docker exec -i pi-dev pi --mode rpc --no-session --no-extensions --no-mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
assert process.stdin and process.stdout
command = {"id": "prompt-1", "type": "prompt", "message": "Reply with exactly RPC_OK. Do not call tools."}
process.stdin.write(json.dumps(command).encode("utf-8") + b"\n")
process.stdin.flush()
for line in process.stdout:
    record = json.loads(line)
    if record.get("type") == "response" and record.get("id") == "prompt-1":
        assert record["success"] and record["data"]["disposition"] == "started"
    if record.get("type") == "agent_settled":
        break
process.stdin.close()
assert process.wait(timeout=5) == 0
```

生产实现需要持续读取 stdout、按 LF 分帧并关联请求 ID；`agent_settled` 到达前不能把阶段判为完成。要给真实调用设置超时，并处理 provider 错误、stderr、异常退出及 `disposition:"handled"`。协议依据：[Pi v1.1.0 RPC 文档](https://github.com/earendil-works/pi/blob/v1.1.0/packages/coding-agent/docs/rpc.md)。

## 一次性 Runner 镜像的真实模型工具烟测

同日另用 `pi-bugfix-backend:1.0` 镜像在 42 机执行一个**合成 Bug**：本地 Git 仓库 `fixed.txt` 初始为 `broken\n`，任务要求改成 `fixed\n`。容器只挂单 attempt 目录、只读模型配置种子卷，根文件系统只读，禁扩展/技能发现。主 Pi 会话实际改写文件并生成 manifest；原始提交的复现命令因 `AssertionError` 退出 1；新的 Pi RPC 进程检查干净候选工作树，写入 `verified:true` 及具体原因；修后同一命令退出 0，生成冻结候选结果文件。该调用成功退出，未访问业务仓库、飞书或 GitHub。

此烟测验证了模型工具调用、主/验证会话切换、修前/修后门槛及容器权限组合；没有证明真实飞书工单读取、复杂项目依赖安装、多仓修复质量或 PR 写入。后续须用用户给出的真实 Bug URL 和独立配置的凭据完成纵向验收。
