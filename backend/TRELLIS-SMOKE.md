# Trellis 三阶段真实 Pi 烟测（2026-10-14）

在 42 机用 `pi-bugfix-backend:1.0` 和原有只读模型配置卷运行 `backend/smoke_trellis_model.py`。脚本创建临时本地 Git 仓库，不读取飞书、不访问业务仓库、不创建 PR。合成 Bug：`fixed.txt` 的基线内容为 `broken\n`，期望为 `fixed`。

| 阶段 | 观察 |
| --- | --- |
| Trellis 定位会话 | 产生 `diagnosis.json`；指出文件内容与期望不符，引用原始提交、文件字节及失败断言；仓库保持干净。 |
| Trellis 实施会话 | 改动 `fixed.txt`，产生 `manifest.json`；同一 Python 断言在原始提交退出 1。 |
| 独立验证会话 | 未继承实施对话，产生 `verification.json`；核对 diff、修前证据及期望；相同断言在冻结候选上退出 0。 |
| 结果 | `TRELLIS_SMOKE_OK`，进程退出码 0。 |

随后在加入按需规范库、JSONL manifest 和内存中可信系统上下文的最终镜像上复测，并启用与正式 Runner 一致的只读根文件系统、能力移除、`no-new-privileges` 和资源限制。再次产生三阶段工件，修前退出 1、修后退出 0，打印 `TRELLIS_SMOKE_OK`，进程退出码 0。本次未验证真实 Bug、复杂依赖、多仓库交叉测试、失败恢复或 PR 交付。
