# 前端开发规范

## 适用范围

- 本目录只适用于 `operations-dashboard/`（Neptune OS Cloud 管理后台：React 17 + TypeScript 4.5 + Vite 2 + Arco Design + Redux + umi-request）。它是本工作区唯一的业务前端；`婚礼小游戏/`、`Trellis/` 不适用。
- 基础规范来自 `operations-dashboard` 在 `dev` 分支（2026-09-23，`6dd06ec`）的实际代码；后续专题另标核对日期、分支和提交。文中以 `src/` 开头的路径都相对于 `operations-dashboard/`。

## 权威来源与阅读顺序

1. **`operations-dashboard/AGENTS.md` 是硬约束的权威来源，必须与本目录一起读。** 它的「范式索引」（做 X 照抄 Y）和「硬约束与易错点」（禁止清单）本目录不复制，只按节名链接。两者说法冲突时以 AGENTS.md 为准；AGENTS.md 与代码冲突时以代码为准，并在任务里记 TODO，不在本目录里改写。
2. AGENTS.md 超过 32KB，自动注入会被截断，做前端任务时要用 Read 读完整原文件。
3. 本目录补充 AGENTS.md 没有系统讲过的写法约定：文件放哪、组件/hook/状态/类型怎么写、改完怎么自检。

## 文件列表

| 文件 | 内容 | Status |
|---|---|---|
| [directory-structure.md](./directory-structure.md) | 页面模块内部的文件落位（`service.ts`、`types.ts`、`domain/`、`components/`）、共用组件的升格条件、命名与 import 路径 | 已填写（源码核对 2026-09-23） |
| [component-guidelines.md](./component-guidelines.md) | Props 声明、CRUD 列表页分工、Arco 用法、CSS Module、按钮权限接法、文案国际化现状 | 已填写（源码核对 2026-09-23） |
| [hook-guidelines.md](./hook-guidelines.md) | hook 落位（多处并存的现状与推荐）、取数模式、防过期响应、hook 返回值形状 | 已填写（源码核对 2026-09-23） |
| [state-management.md](./state-management.md) | 状态放组件 / URL / userPrefs / Context / Redux 的判定表，Redux 实际字段 | 已填写（源码核对 2026-09-23） |
| [type-safety.md](./type-safety.md) | 生成类型 `API` / `ExhibitionAPI` 的用法、本地交叉类型、Raw 与前端模型分层、enum + namespace 惯例、`any` 分档 | 已填写（源码核对 2026-09-23） |
| [quality-guidelines.md](./quality-guidelines.md) | 只读的本地检查命令、单测收集范围与写法、提交前清单、注释风格 | 已填写（源码核对 2026-09-23） |
| [clean-report-photo-selection.md](./clean-report-photo-selection.md) | 清洗编辑页After默认最干净、后端初始化与存量选择保留边界 | 2026-10-28 用户确认 |
| [clean-report-photo-library.md](./clean-report-photo-library.md) | 部位标签行右侧照片库入口，复用检测报告浏览模式 | 2026-10-29 用户确认 |
| [clean-report-above-water.md](./clean-report-above-water.md) | 水上三锚点、Before只增保护、成组与清洗PDF输出 | 2026-10-29 用户确认 |
| [clean-report-timestamp.md](./clean-report-timestamp.md) | 洗前/洗后时间戳开关保存成功后的图片URL回读契约 | 2026-10-28 核对 |
| [clean-report-synced-photo-tag.md](./clean-report-synced-photo-tag.md) | 检测同步来源识别、After选项限制与手动上传放行 | 2026-10-28 用户确认 |
| [report-list-creator-am.md](./report-list-creator-am.md) | 报告列表负责AM取MOBI创建人，避免船组织AM替代 | 2026-10-28 核对1312 |
| [report-list-mobi-search.md](./report-list-mobi-search.md) | 搜索同时支持数字MOBI主键与Unique ID，保持权限过滤 | 2026-10-28 |
| [report-list-port-path.md](./report-list-port-path.md) | 报告列表完整中文国家/城市/港口/二级港区，保留长文本 | 2026-10-28 |
| [report-conclusion-migration.md](./report-conclusion-migration.md) | 三板增量协议、失败处理、原文字兼容与接口2延期边界 | 接口1已实现；真实写入待联调（2026-10-09） |
| [biofouling-percentage.md](./biofouling-percentage.md) | 原始比例精度、逐项向下取整显示（与结论表同口径，无整数分配）、保存容差与回归断言 | 2026-10-16 更新于 fix-bio-percent-floor（基于 origin/dev `b542123`，未提交）；原实现于 feature/add-batch-bio，9fbb07b（2026-10-09） |
| [inspection-summary-percentage.md](./inspection-summary-percentage.md) | 结论表格比例一律向下取整、0% 项只读隐藏、显示下标与原始下标分离；**与单照片口径只在保存语义上仍相反（显示取整 2026-10-16 已统一）**；§8 油漆段落只读改显面积档（2026-10-15） | 2026-10-16 核对 fix-bio-percent-floor（基于 origin/dev `b542123`，未提交）；原实现于 feature/inspection-summary-edit，5e9e43c（2026-10-12）；§8 于 modify-oil-ratio（2026-10-15） |
| [inspection-summary-visual.md](./inspection-summary-visual.md) | 接口2 Figma布局、SVG URL导入、主题高度覆盖及实际浏览器断言 | 已在接口2前端worktree核对（2026-10-10） |
| [inspection-summary-item-cap.md](./inspection-summary-item-cap.md) | **2026-10-16 整体回滚作废**：汇总位不再设条数上限，加号只按字典占用判定；保留翻转史（10-09 不限制4种 → 10-15 fouling/paint 封顶 → 10-16 回滚）与「后端 `maxSummaryItems=4` 仍在」的已知不一致 | 作废于 fix-revert-summary-cap（2026-10-16） |
| [report-summary-client.md](./report-summary-client.md) | 接口2生成Helix客户端、错误处理、完整schema及wire验证 | PR审查修复（2026-10-10） |

## 用语约定

- **现状并存**：仓库里同一件事有多种写法，文中会列出各自的位置，并给出新代码的推荐和理由；改老代码时沿用该文件已有写法。
- **单例观察**：只在一处见到的写法，仅供参考，不作为要求。
- **存量**：已有代码中不符合推荐写法的部分，不要求顺手修改（与 AGENTS.md「开发约定」的最小改动原则一致）。
