# Pi Bug 修复工作台 1.0

面向约 5 位测试人员：登录后粘贴飞书项目 Bug URL，确认推荐仓库，查看自动定位、修前复现与独立验证，另一位成员审核候选，再按仓库交付 PR。任务和结果对所有本地账号共享。

当前 42 机入口：`http://192.168.2.42:8090`。初始本地账号为 `admin`；初始密码仅存于 42 机 `/opt/pi-bugfix-console/admin-initial-password`（权限 `600`），由有 SSH 权限的管理员读取。此账号用于创建其余本地账号，实际候选仍须另一人审核。未填服务凭据时可登录和查看任务，飞书预览返回明确的 `503`。

## 组成

- `frontend/`：React 工作台。任务列表、工单预览、仓库确认、实时进度、证据、审核、PR。
- `backend/app/api.py`：FastAPI 控制面和本地账号；SQLite 保存任务、事件、租约、候选、审核及交付。
- `backend/app/worker.py`：单 worker 领取任务、预克隆仓库、启动一次性 Pi 容器，读取结构化结果；另轮询已批准候选做 PR 交付。
- `backend/app/task_runner.py`：主 Pi RPC 会话修复；在原始目标提交执行复现命令并要求失败；另起独立 Pi RPC 会话验证；在候选树执行同一命令并要求通过；冻结每仓树 ID。
- `backend/app/delivery.py`：审核绑定候选树和目标 SHA，创建各仓 Draft PR，全部完成才转 Ready；部分失败保留 WIP 并可重试。

## 部署到 Docker 主机

1. 在 42 机编辑 `/opt/pi-bugfix-console/.env`，填入飞书项目插件 ID/Secret/User Key、GitHub 只读克隆 token 和可创建分支/PR 的写 token。文件已按 `600` 权限创建；不要提交它。
2. 复制 `repos.example.json` 为 `repos.json`，把仓库 ID、目标分支、飞书项目 key、推荐关键词改为真实值。仓库 ID 是 `owner/repo`；必须有读取和 PR 权限。
3. 主机已有可供 Pi 1.1.0 读取的模型认证 Docker volume `pi-config`。一次性 Pi 容器只读挂载原卷，并把配置复制到容器内临时可写目录，供 Pi 创建锁文件；任务代码目录为单独 bind mount，不能访问其他 attempt 工作树。
4. `docker compose build`，然后 `docker compose up -d`。Web 默认监听主机 `8090`。生产入口若经 HTTPS 反代，应把登录 cookie 的 `secure` 设置为 true 并限制来源。
5. 为 5 位使用者分别建本地账号：`docker compose exec api python3 -m app.cli add-user <用户名>`，逐个输入密码。

42 机宿主已安装 `gh` 2.97.0，供人工检查 PR 使用。自动交付使用后端的 GitHub REST/GraphQL 客户端；不依赖 `gh auth` 会话。

服务端不使用飞书组织账号登录。飞书工单读取固定指向 `project.feishu.cn` 官方 OpenAPI，不请求用户粘贴的原始 URL。现仅提取基础字段及描述，真实租户字段与凭据仍需实测，见 [飞书适配说明](backend/FEISHU.md)。

用户提供的 `neptune_software_system/issue/detail/7128390467` 已经由飞书只读连接器确认处于“已修复”状态，可用于取单预览验收；服务会阻止对它创建新的修复任务。真实自动修复与 PR 验收还需一条未解决、允许测试的 Bug。

## 运行规则

- 创建任务前先读取工单并推荐仓库；用户选中的仓库再次按服务端白名单校验。创建时重读工单并原子保存快照。
- 一个任务只允许一个活跃 Runner 租约。任务取消或租约转移后，旧 Runner 写入被拒。
- 修前复现失败、修后通过和独立会话认可三者缺一，候选不能进入审核。
- 发起人不能审核自己创建的任务；审核绑定所有仓库树 ID、目标分支和目标提交。
- GitHub 目标分支发生漂移时交付暂停；多仓 PR 部分失败保持 Draft/WIP。
- `74` 环境没有任何写入入口；本版本也没有自动部署或自动合并。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm test -- --run
npm run build
```

测试包含共享账号/API、飞书 URL 和响应、Pi RPC `agent_settled`、模拟主/验证会话与修前/修后门槛、候选审核绑定、冻结树发布、多仓 PR WIP/重试。实际飞书租户读取、目标仓库的真实 Bug 修复和真实 PR 创建仍需要一条获授权的 Bug 和服务凭据做纵向验收；不能用单元测试结果替代该验收。
