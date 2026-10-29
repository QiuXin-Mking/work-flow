# Pi Bug 修复工作台 1.0

面向约 5 位测试人员：登录后粘贴飞书项目 Bug URL，确认分析仓库，补齐故障信息，确认根因后的业务范围与收敛 PRD，再查看修复、修前复现与独立验证，另一位成员审核候选，再按仓库交付 PR。任务和结果对所有本地账号共享。

当前 42 机入口：`http://192.168.2.42:80`。初始本地账号为 `admin`；初始密码仅存于 42 机 `/opt/pi-bugfix-console/admin-initial-password`（权限 `600`），由有 SSH 权限的管理员读取。此账号用于创建其余本地账号，实际候选仍须另一人审核。未填服务凭据时可登录和查看任务，飞书预览返回明确的 `503`。

## 组成

- `frontend/`：React 工作台。任务列表、工单预览、仓库确认、实时进度、证据、审核、PR。
- `backend/app/api.py`：FastAPI 控制面和本地账号；SQLite 保存任务、事件、租约、候选、审核及交付。
- `backend/app/worker.py`：按持久化门槛领取一个阶段，信息不足在克隆前阻断；根因取证、PRD 编写及独立审查分阶段运行，人工确认期间不自动推进。
- `backend/app/workflow.py`：不可变快照、revision/epoch、用户确认、PRD 哈希及双重审查门槛。
- `backend/app/stage_runner.py`：信息、根因、PRD 编写、PRD 独立审查会话。
- `backend/app/task_runner.py`：仅在双审后创建修改 worktree，启动独立 Pi 实施和验证；在原始目标提交执行复现命令并要求失败；在候选树执行同一命令并要求通过；冻结每仓树 ID。
- `backend/trellis_bundle/`：从本机 Trellis 实践中筛选出的团队规范快照，包含工作流、通用检查、前后端关注点。每次运行生成独立的 `.trellis/tasks/bugfix/` 工件，按角色从只读镜像生成系统上下文；飞书工单正文仅作为不可信任务数据传给 Pi。团队版不包含本机 `.trellis/workspace/`、个人环境约定或 SOP 资料。见 [Trellis 接入说明](docs/trellis-integration.md)。
- `backend/app/delivery.py`：审核绑定候选树和目标 SHA，创建各仓 Draft PR，全部完成才转 Ready；部分失败保留 WIP 并可重试。

## 部署到 Docker 主机

42 机已有部署可从本机 Git for Windows Bash 执行 `./scripts/deploy-42.sh` 更新并启动。
脚本默认使用 `root@192.168.2.42` 和 `/opt/pi-bugfix-console`；需要更换时设置
`PI_BUGFIX_DEPLOY_TARGET`、`PI_BUGFIX_DEPLOY_DIR`。它先检查远端 `.env`、
`repos.json`、Docker 和运行中的任务，再同步应用源码、构建并启动三个容器，
最后检查容器状态及 `http://192.168.2.42:80/`。脚本不会同步或覆盖远端凭据和数据库。
Compose 为三个服务设置了 `restart: unless-stopped`，42 机的 Docker 服务已设置开机启动。

1. 在 42 机编辑 `/opt/pi-bugfix-console/.env`，填入 GitHub 只读克隆 token 和可创建分支/PR 的写 token；飞书项目默认通过 MCP 设备授权，不需要 Plugin ID/Secret/User Key。文件已按 `600` 权限创建；不要提交它。
2. 复制 `repos.example.json` 为 `repos.json`，把仓库 ID、目标分支、飞书项目 key、推荐关键词改为真实值。仓库 ID 是 `owner/repo`；必须有读取和 PR 权限。
3. 主机已有可供 Pi 1.1.0 读取的模型认证 Docker volume `pi-config`。一次性 Pi 容器只读挂载原卷，并把配置复制到容器内临时可写目录，供 Pi 创建锁文件；任务代码目录为单独 bind mount，不能访问其他 attempt 工作树。
4. `docker compose build`，然后 `docker compose up -d`。Web 默认监听主机 `80`。生产入口若经 HTTPS 反代，应把登录 cookie 的 `secure` 设置为 true 并限制来源。
5. 为 5 位使用者分别建本地账号：`docker compose exec api python3 -m app.cli add-user <用户名>`，逐个输入密码。
6. 在 42 机运行 `docker compose exec api python3 -m app.feishu_mcp_auth`，打开输出的飞书地址并批准一次性代码。授权存放于服务数据卷，续期由后端完成。详见 [飞书适配说明](backend/FEISHU.md)。

42 机宿主已安装 `gh` 2.97.0，供人工检查 PR 使用。自动交付使用后端的 GitHub REST/GraphQL 客户端；不依赖 `gh auth` 会话。

服务端不使用飞书组织账号登录工作台。飞书工单读取固定指向 `project.feishu.cn` 官方 MCP，不请求用户粘贴的原始 URL。后端使用独立的飞书项目授权取单，Pi 仍禁用 MCP，只接收脱敏后的工单快照。提取字段、描述及分页评论；附件保留来源 URL，不自动下载或解析，见 [飞书适配说明](backend/FEISHU.md)。

用户提供的 `neptune_software_system/issue/detail/7128390467` 已经由飞书只读连接器确认处于“已修复”状态，可用于取单预览验收；服务会阻止对它创建新的修复任务。真实自动修复与 PR 验收还需一条未解决、允许测试的 Bug。

## 运行规则

- 创建任务前先读取工单并推荐仓库；用户选中的仓库再次按服务端白名单校验。创建时重读工单并原子保存快照。
- 一个任务只允许一个活跃 Runner 租约。任务取消或租约转移后，旧 Runner 写入被拒。
- 修前复现失败、修后通过和独立会话认可三者缺一，候选不能进入审核。
- 发起人不能审核自己创建的任务；审核绑定所有仓库树 ID、目标分支和目标提交。
- GitHub 目标分支发生漂移时交付暂停；多仓 PR 部分失败保持 Draft/WIP。
- `74` 环境没有任何写入入口；本版本也没有自动部署或自动合并。

## 验证

```bash
export SHELL=/usr/bin/bash
.venv/Scripts/python -m pytest backend/tests -q
cd frontend
npm test -- --run
npm run build
```

测试包含共享账号/API、飞书 URL 和响应、Pi RPC `agent_settled`、模拟主/验证会话与修前/修后门槛、候选审核绑定、冻结树发布、多仓 PR WIP/重试。实际飞书租户读取、目标仓库的真实 Bug 修复和真实 PR 创建仍需要一条获授权的 Bug 和服务凭据做纵向验收；不能用单元测试结果替代该验收。

### Git 拉取与缓存

Worker 通过 `GIT_HTTP_PROXY` 配置 Git 代理，部署默认值为 `http://192.168.2.78:7997`。代理必须监听局域网地址并允许 42 访问。该设置仅用于 Git 子进程。

仓库缓存在持久数据卷的 `/data/repo-cache`：首次下载分支最近 40 条历史，后续通过 `fetch --depth 40` 更新。每次任务复制为独立仓库，不共享可修改的工作目录或 Git 对象。网络克隆和 fetch 超时均为 300 秒。

### 系统设计与整体评审

从[系统设计导航](docs/02-系统设计/README.md)阅读当前架构、任务执行协议、数据模型与设计评审清单。总体设计只描述大组件，Agent 流程、数据契约与运行协议分别维护。

### 74 只读取证

Worker 通过只读挂载的 `./ssh74:/run/ssh74` 使用部署专用身份；主机指纹必须校验。`DIAG_SSH_USER`、`DIAG_SSH_KEY_PATH`、`DIAG_SSH_KNOWN_HOSTS`、`DIAG_LOG_QUERY_PROGRAM` 配置受控查询。密钥不进入 Runner、模型输入或代码仓库。74 上的 `scripts/query-74-logs.py` 部署为 `/home/neptune/.local/libexec/pi-bugfix-query-logs`，仅接受 `--trace` 或 `--mobi` 标识，查询固定容器最近 24 小时最多 10000 行，返回命中片段；无匹配时阻塞根因确认。

### 用户门槛

工单须包含 `mobi:xxxx`。缺失信息在任务详情补充；工单更新后使用“重新读取工单”，新快照使旧确认和 PRD 失效。偶现问题必须确认在 74 触发并带工单 trace-id。根因明确后，由发起人选择并确认业务变更；PRD 关键问题需答复，主 Agent 与独立审查必须对同版通过才能实施。
