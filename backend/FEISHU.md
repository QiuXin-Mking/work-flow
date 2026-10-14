# 飞书项目只读取单适配器

`app.feishu.parse_bug_url(url)` 只接受 `https://project.feishu.cn/<project_key>/issue/detail/<数字 ID>`，产出 `WorkItemRef`。服务不会请求粘贴的 URL，而是用校验后的 ID 在固定的 `project.feishu.cn` 调用只读 OpenAPI。HTTP 客户端不跟随重定向，也不使用环境代理。其他租户域名及链接格式需要先核实，再通过显式白名单扩展。

`FeishuClient.get_bug(ref)` 返回 `BugSnapshot(ref, title, description, updated_at, fields)`。服务端配置：

| 环境变量 | 内容 |
| --- | --- |
| `FEISHU_PROJECT_PLUGIN_ID` | 飞书项目插件 ID |
| `FEISHU_PROJECT_PLUGIN_SECRET` | 插件密钥 |
| `FEISHU_PROJECT_USER_KEY` | 对目标空间有只读权限的用户 key |

插件必须先安装到对应空间并取得读取权限。凭据只置于服务端密钥管理或进程环境，不能传给浏览器或 Pi 容器。当前适配器每次请求重新取 plugin token，适合首版低并发；稳定后可加入按过期时间缓存。取单失败应阻断任务，不得把空内容交给 Agent 当作完整 Bug。

接口根据飞书项目[官方 Go SDK 的取单示例](https://github.com/larksuite/project-oapi-sdk-golang/blob/main/sample/demo.go)和[工作项查询请求定义](https://pkg.go.dev/github.com/larksuite/project-oapi-sdk-golang@v1.0.29/service/workitem#QueryWorkItemDetailReqBody)实现：`POST /open_api/{project_key}/work_item/issue/query`，请求 `work_item_ids`，并设置 `expand.need_multi_text`；该 SDK 也定义了[路径常量与响应数据结构](https://pkg.go.dev/github.com/larksuite/project-oapi-sdk-golang@v1.0.29/service/workitem)。[飞书官方 FAQ](https://www.feishu.cn/content/60bl79n2)确认插件须安装到空间，`X-USER-KEY` 需要对应空间权限，系统缺陷类型为 `issue`。[飞书低代码连接器说明](https://www.feishu.cn/content/852030409092)确认 Plugin ID、Plugin Secret、ApiBaseURL 和 User Key 的配置关系。

**服务端插件调用尚未实测当前租户。** 2026-10-14 使用已连接的飞书项目只读工具确认用户提供的真实 URL 可读，类型为缺陷、状态为已修复；这证明 URL 与连接器访问，不证明本服务的插件凭据已就绪。当前实现只提取工作项基础字段和 `multi_texts.description.doc_text`；不读取评论或附件，不声称工单上下文完整。若 Bug 的复现步骤存在自定义富文本字段，需明确字段键并扩展映射。已修复/关闭工单在 Web API 预览可见，但不能重复创建修复任务。入库快照对描述和字段中的常见密码/token 格式做遮蔽。无可信修前复现证据时，不允许生成“已修复”PR。

仓库建议入口为 `app.repos.recommend_repos(snapshot, configured_repos)`。`configured_repos` 是 `{repo_id: {project_keys: [...], keywords: [...]}}` 白名单；结果 `RepoSuggestion(repo_id, score, evidence)` 只提供推荐及依据，不自动确认仓库。用户必须在 UI 明确确认最终仓库集合，后端再对仓库 allowlist 复核。
