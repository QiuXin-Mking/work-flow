# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

Guidelines for the helix (CloudWego) cloud backend in `voyager_server/internal/helix`. GF cloud code is deprecated and out of scope. Authoritative rules live in `voyager_server/AGENTS.md` and `voyager_server/internal/helix/AGENTS.md`; these files record actual code shape, gotchas and examples, and link rather than copy. The `helix/` subtree is a personal, on-demand digest of those AGENTS.md files; the repository files remain authoritative and conflicts resolve to the repository.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Helix Platform Rules](./helix/platform.md) | 跨服务硬约束总纲：两栈边界、四层职责、三级调用、四类模型、命名、持久化顺序、生成物、测试、红线速查 | **Filled** |
| [Helix Find Code](./helix/find-code.md) | 给 URL / 业务名定位四层链路；含 `hroute.sh` 当前断点与手工兜底命令 | **Filled** |
| [Helix Service & Domain Index](./helix/README.md) | 13 个服务 + 53 个域的触发式索引；按需加载入口 | **Filled** |
| [Directory Structure](./directory-structure.md) | 薄转引：helix 当前目录骨架指向 [platform.md](./helix/platform.md)，URL→代码链路指向 [find-code.md](./helix/find-code.md)；不再维护第二份目录树 | **Pointer** |
| [Database Guidelines](./database-guidelines.md) | 库与 DSN（Nacos `mysql.<db>.link`）；迁移全手工（`hack/sql/<db>/`，新文件用 `YYYYMMDDHHMMSS_<verb>_<object>.sql`，DDL+schema+生成物同 commit，发版前先执行，脚本**并非都幂等**）；命名（单数表名、两套时间列族、`uk_`/`idx_`/`fk_`、ent 无 mixin + 带 delete_at 必配 FilterDeleted）；事务 `defer Rollback` 形态；ent 软删 `HasXWith` 子查询坑；`user` 身份契约；`update_time` 不是最后修改时间 | **Filled** |
| [Error Handling](./error-handling.md) | 两套错误体系按 URL 分：`/api/cloud/v1` 迁移口用 `pkg/errno` + `asGfError` + `WriteGfResponse`，全新接口用 `idl/error.proto` + i18n + `WriteResponse`；HTTP 恒 200、`code==0` 成功；特殊码 51/999/2005/50099；i18n 翻译覆盖 fallback、缺文案时露英文哨兵；biz 哨兵 + service 翻译一次 | **Filled** |
| [Quality Guidelines](./quality-guidelines.md) | 规则权威在 `voyager_server/AGENTS.md` + `internal/helix/AGENTS.md`（本篇只记现状与坑）；CI 只对 inspection-route 分支生效；代码生成只改源头（gen.sh / `go generate ./ent`）；cloud 用例 `struct+Do` + 窄接口 + 组合根装配；禁止 import 清单与存量规模；测试按层照抄范例（fake 窄接口 / `entcapture` 截 SQL / sqlmock）、已知坏测试 | **Filled** |
| [Logging Guidelines](./logging-guidelines.md) | Kitex 用 klog、Hertz 用 hlog，JSON 到 stdout、级别写死；一律 Ctx 版本（只有 klog Ctx 自动带 trace_id）；级别划分与正例；`"msg failed. k=v, err=%v"` 格式；只记一次；存量敏感信息日志清单；SLS 查法 | **Filled** |
| [Outbound Integration](./outbound-integration.md) | Fire-and-forget notifications to external services: component shape, call-site placement, failure matrix, sentinel discipline, the create-push/change-pull exception list, probe ordering for call sites that must fetch (§7.5), and the destination-driven guard against field-by-field copies silently dropping a field (2026-10-15 `mobi_no` 恒为空事故) | **Filled** |
| [Service-order Dispatch](./service-order-dispatch.md) | Approved contracts for App order first confirmation and customer cancellation; transaction boundary, async snapshot reads and per-entry-point regression coverage | **Implementation tracked in task** |
| [Time Window Grouping](./time-window-grouping.md) | 时间窗分组读接口的口径单一来源：分组键/排序键/SQL 预过滤键三层同源、窗口常量单一定义点、带回退链的派生值在 SQL 侧逐分支复刻（含 nullable 列的 IS NULL + 零值双覆盖）、**回退末端是预计值时派生键会落在未来导致行从所有 tab 消失（C4）**、**只改 SQL 的局部修复不成立（C5）**、谓词渲染成文本的测试手法 | **Filled** |
| [Report Partition Sync](./report-partition-sync.md) | Adding/removing inspection report partitions after a business write (helix cloud → report-tools `UpdatePartition` logs, replay idempotency, published skip); reference-image gotcha (`imgs.go` vessel-type fallback); `vessel_part` vs `vessel_part_v2` id-space convention | **Filled** |
| [Inspection Ratio Batch](./inspection-ratio-batch.md) | Single-Line photo ratio editing: wrapper presence, proportional normalization, clipped scope, lossless historical metadata, atomic logs and rating boundaries | **Filled** |
| [Inspection Summary Edit](./inspection-summary-edit.md) | Overview full snapshots, marine cell-count / paint per-type denominators, calcium presence, atomic lock/refresh/audit and legacy format compatibility | **Verified locally; external PDF/App pending** |
| [Above-water Report Photos](./above-water-report-photos.md) | Independent inspection/cleaning associations, cleaning phases, current-MOBI authorization, CAS limits, replay, used/download/PDF consumers | **Implementation; system acceptance pending** |

---

## Quality Check

Before finishing backend work, verify against the filled guides above. For outbound
integrations specifically, walk the Validation & Error Matrix and the five Wrong-vs-Correct
pairs in [Outbound Integration](./outbound-integration.md) — placement (§7.1), call-site
coverage (§7.2) and probe-before-caller-fetch (§7.5) are the three that have actually bitten us.

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: 本目录的 helix 规范用中文，与 `voyager_server` 的文档规范一致；API 路径、代码标识符可保留英文。
