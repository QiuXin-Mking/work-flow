# 按 URL / 业务名找 helix 代码

> 源：`voyager_server/docs/specs/服务目录布局.md`、`voyager_server/internal/helix/AGENTS.md` @ 2026-10-26（源码实测）
> 仓库 AGENTS.md 仍是团队权威；本文件是个人按需摘要，冲突时以仓库为准。
> **引用代码位置只用「文件 + 类型/函数/章节」，不写行号**：行号随改动失效；文件名和符号名在绝大多数改动中不变。

## 1. 当前布局（实测）

云端 HTTP 只有 gateway 一个入口：

- 路由注册：`internal/helix/app/gateway/biz/router/infra/`（唯一位置，按 URL 组分文件）
- 网关 handler：`internal/helix/app/gateway/biz/handler/infra/`
- 鉴权与 403 白名单：`internal/helix/app/gateway/biz/gtokenauth/`

业务按服务/域分：

- 域优先（cloud、mobilization）：`app/<服务>/internal/<域>/{service,biz,data}/`，进程运行时在服务级 `internal/server/`
- 层优先（gateway、authz、magneto 等）：`service|biz|data` 常在服务根，`conf|server` 在 `internal/`
- Kitex handler 就在域/服务的 `service/` 包，文件形如 `<动作>_rpc.go`；服务侧不再有 `biz/handler`（`biz/handler` 只是 gateway 自己的 HTTP handler 位置）
- 契约：`internal/helix/idl/*.proto`；客户端工厂：`internal/helix/client/`；装配：服务根 `fx.go`/`wiring_*.go` 与 `internal/app/`

## 2. 四层定位链（真实例）

URL：`GET /api/cloud/v1/app/backend/mobilization`

| 层 | 文件 | 锚点（搜这个符号） | 说明 |
|---|---|---|---|
| ① 路由 | `internal/helix/app/gateway/biz/router/infra/cloud_app_backend.go` | `GetAppBackendMobiList`（同文件 `grp.GET("/mobilization", ...)`） | 组前缀看同文件的 `router.Group` 声明；完整路径写法在 gtoken 组外 |
| ② 网关 handler | `internal/helix/app/gateway/biz/handler/infra/cloud_mobi_read_service.go` | `func (h *...) GetAppBackendMobiList` | 参数绑定、GF `v:` 校验复刻、`WriteGfResponse` 错误壳 |
| ③ Kitex handler | `internal/helix/app/cloud/internal/mobilization/service/get_mobi_list_rpc.go` | `CloudMobiReadServiceImpl.GetMobiList` | 薄转发：DTO → biz 输入，调 `h.GetMobiListSvc.Do`，错误经 `rpcError` 映射；② 里调 `cli.GetMobiList` |
| ④ 业务 service | `internal/helix/app/cloud/internal/mobilization/biz/get_mobi_list_usecase.go` | `type GetMobiList` + `func (s *GetMobiList) Do` | 权限、筛选、编排；仓储/Port 接口也在 biz |
| ④ data（读） | `internal/helix/app/cloud/internal/mobilization/data/mobilization_list_reader.go` | `PageMobis` | 读仓储，ent 查询在此 |
| ④ data（写） | 同域 `data/` | biz 顶部声明的 `MobiCreateStore` → `var _ biz.MobiCreateStore = (*LifecycleRepository)(nil)` | 写路径方法名通常**不是** RPC 名：`CreateMobi` 落到 `InsertMobiRow` / `InsertOrderRow`。先读 biz 的 Store 窄接口，再搜实现 |
| 契约 | `internal/helix/idl/cloud_mobi_read.proto` | `rpc GetMobiList` | 生成 `kitex_gen/helix/cloudmobiread` |
| 装配 | `internal/helix/app/cloud/internal/app/builder.go` | `CloudMobiReadServiceImpl{` | 组合根构造并注入用例 |

## 3. 命名规律

- 路由文件名按 URL 组/业务域命名，常见形态是 URL 段用下划线连接：`cloud/app/backend` → `cloud_app_backend.go`，`cloud/app/frontend` → `cloud_app_frontend.go`；但不总是逐段拼（`/user/vessel` 在 `cloud_vessel.go`）。不确定时直接 `grep -rn '("/mobilization"' internal/helix/app/gateway/biz/router/infra/`。
- 网关 handler = 动作 + URL 组 + 资源：`GetAppBackendMobiList`、`CreateAppBackendMobi`、`DeleteAppBackendMobiFile`。路由行里的 handler 名可反查 `handler/infra/`。
- 业务 service = RPC 方法名，类型 + `Do()`：Kitex `GetMobiList` ↔ 字段 `GetMobiListSvc` ↔ `biz.GetMobiList` ↔ `Do(ctx, input)`。
- 同一 path 可挂多个 method 且分属不同 Kitex service：`/mobilization` 的 GET 走 `CloudMobiReadService`，POST/PUT 走 `CloudMobiWriteService`；`GET /mobilization/:id` 也在 write 侧 `cloud_mobi_write_service.go` 的 `GetAppBackendMobi`。看到第一条别停。

## 4. 工具与已知断点

- `SOP/技术/16-如何快速找到接口位置/hroute.sh` 的 ① 路由、② 网关 handler 仍可用；③ 固定 `grep '/biz/handler/'`，当前布局下必报「Kitex handler: 未找到」，④ 又按旧的同级 `service/` 目录推断，不能照用。
- 手工兜底（Kitex 方法名取 ② 里的 `cli.Xxx`）：

```bash
# ③ Kitex handler：按 RPC 方法名搜目标服务的 internal（已知域就缩到 internal/<域>/service/）
grep -rn --include='*.go' 'func (.*) GetMobiList(ctx' internal/helix/app/cloud/internal | grep -v _test.go
# ④ 业务 service：从 handler 里取 h.XxxSvc 的类型，去同域 biz 找 Do
grep -rn 'func (s \*GetMobiList) Do(' internal/helix/app/cloud/internal/mobilization/biz
# ④ data（读）：按 biz 仓储方法名找实现
grep -rn 'PageMobis' internal/helix/app/cloud/internal/mobilization/data
# ④ data（写）：先找 biz 声明的 Store 窄接口，再找它的实现
grep -rn 'MobiCreateStore' internal/helix/app/cloud/internal/mobilization/data
grep -rn 'func .*InsertMobiRow\|func .*InsertOrderRow' internal/helix/app/cloud/internal/mobilization/data
```

- 403 排查看 `internal/helix/app/gateway/biz/gtokenauth/allowlist.go`：白名单按 `"METHOD:/完整路径"` 精确全串匹配，父路径放行不代表 `/:id` 子路径放行。
- hroute 的片段匹配会跨服务命中（同一个 `/mobilization` 会同时列出 frontend、device 口），按 method + 完整 path 筛。

## 5. 参考实现

完整四层、注释写明口径来源，按符号名顺着读：

`cloud_app_backend.go`（注册 `GetAppBackendMobiList`）→ `cloud_mobi_read_service.go`（`GetAppBackendMobiList`）→ `get_mobi_list_rpc.go`（`GetMobiList`）→ `get_mobi_list_usecase.go`（`GetMobiList.Do`）→ `mobilization_list_reader.go`（`PageMobis`）。
