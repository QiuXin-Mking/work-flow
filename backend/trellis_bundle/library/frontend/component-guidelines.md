# 组件写法

> 适用：在 `operations-dashboard/` 写或改 React 组件。颜色、Popover 样式、Modal/Table 高度链、`showSearch` 等 Arco 细节坑已在 `operations-dashboard/AGENTS.md`「硬约束与易错点」与「范式索引」里，本篇只链接不复述。

## 1. 最重要的五条

1. **错误提示别双弹**：全局响应拦截器已对 `code !== 0` 弹 `Message.error`（`src/main.tsx:180-189`）。组件想自己提示就必须在该次请求上传 `skipErrorHandler: true`；不传又自己 `Message.error`，用户会看到两条（反例：`src/pages/operation-config/port/add.tsx:63-70` 在未跳过全局处理的情况下再弹一次）。正例：`src/pages/ai-report/report-list/detail/components/LineContent/LineContent.tsx:559-560`、`src/pages/ai-report/report-list/detail/components/SpecialContent/SpecialContent.tsx:705-706`。
2. **按钮级权限读 Redux 的 `buttonCodes`**，不读角色名，见第 6 节。
3. **Props 用具名 `interface XxxProps`**，见第 2 节。
4. **样式一律 CSS Module**，颜色走变量（AGENTS.md「硬约束与易错点」中「颜色一律走变量」一条）。
5. **新列表页用 `ListPage` 复合组件搭骨架**，见第 3 节。

## 2. 组件声明与 Props

主流写法（`interface ...Props` 出现在 98 个 tsx 文件，`React.FC<` 出现在 79 个）：

```tsx
// src/pages/am-config/quota-notice/components/SendStatusTag/SendStatusTag.tsx
interface SendStatusTagProps {
  status: SendStatus;
  /** 传入后，"发送中"状态会出现刷新按钮；其余状态一律不出现 */
  onRefresh?: () => void;
}
const SendStatusTag: React.FC<SendStatusTagProps> = (props) => {
  const { status, results = [], onRefresh, refreshing } = props;
```

同形的还有 `src/pages/operation-config/port/add.tsx:22-34`、`src/pages/operation-config/working-ship/add.tsx:11-17`、`src/pages/am-config/quota-notice/components/SendResultCard/SendResultCard.tsx`。

- 在函数体第一行解构 props 并在解构处给默认值（`results = []`）。
- 非显而易见的 prop 写 `/** */` 注释说明业务含义与约束（`SendStatusTag.tsx`、`SendResultCard.tsx`），组件本身的业务口径写在组件上方的块注释里。
- 老代码的内联 props 类型 `function Edit(props: { data: any | undefined; onHidden: () => void; onRefresh: () => void })`（`src/pages/operation-config/port/edit.tsx:34`、`src/pages/operation-config/dispatcher-region/edit.tsx:29`）是存量，新代码不照抄其中的 `any`。
- 仓库没有 class 组件，也没有 `React.memo`。需要父组件命令式调用时用 `forwardRef` + `useImperativeHandle`（`src/pages/operation-config/operation-group/ConfigList/index.tsx:20`、`src/pages/operations/ops-settings/components/AiCleanStrategy/AiCleanStrategy.tsx`）。
- 用了 `React.FC` 但不 `import React` 是正常的：jsx 用 `react-jsx` 运行时（`tsconfig.json` 的 `"jsx": "react-jsx"`），`React.FC` 只作类型引用。

## 3. CRUD 列表页的组件分工

多数配置类页面是同一个结构，新页面照抄：

| 组件 | 状态归属 | 打开方式 | 佐证 |
|---|---|---|---|
| `index.tsx` 列表 | `data`、`pagination`、`loading`、`formParams`、`editDrawerData` | — | `src/pages/operation-config/port/index.tsx:37-48` |
| `form.tsx` 的 `SearchForm` | 自己的 `Form` 实例 | `onSearch(params)` 回调 | `src/pages/operation-config/port/form.tsx`、`src/pages/operations/ops-settings/form.tsx` |
| `add.tsx` 的 `AddModal` | 自带触发按钮与 `visible`，外部只传 `onRefresh` | 组件内按钮 | `src/pages/operation-config/port/add.tsx:33-35`、`src/pages/operation-config/dispatcher-region/add.tsx:37` |
| `edit.tsx` 的 `Edit` | 受控：`data` 非 `undefined` 即打开 `Drawer` | 父组件 `setEditDrawerData(record)` | `src/pages/operation-config/port/index.tsx:155-166`、`src/pages/operation-config/working-ship/edit.tsx:50` |

- `Table` 统一带 `rowKey="id"`、`placeholder="-"`，`pagination` 是 `useState<PaginationProps>`，初值 `{ sizeCanChange: true, showTotal: true, pageSize: 10, current: 1, pageSizeChangeResetCurrent: true }`（`src/pages/operation-config/port/index.tsx:39-45`、`src/pages/operations/ops-settings/index.tsx:54-60`、`src/pages/am-config/annual-orders/index.tsx`）。
- 页面外壳**现状并存**：老页面手写 `<Card>` + `<Title heading={6}>`（`src/pages/operation-config/port/index.tsx:134-135`）；2026 年新模块用 `src/components/ListPage/` 的 `ListPage.Header / Search / Toolbar / Content`（`src/pages/am-config/annual-orders/index.tsx`、`src/pages/am-config/email-reminder/index.tsx`、`src/pages/am-config/quota-notice/components/QuotaNoticeList/QuotaNoticeList.tsx`）。**新页面推荐 `ListPage`**：间距由它的样式统一，不用每页再写 `button-group` 之类的 margin。
- 详情需要可分享链接时用 URL 驱动的 `useDetailDrawer` + `src/components/DetailDrawer/`（`src/pages/am-config/annual-orders/edit.tsx`、`src/pages/am-config/email-reminder/edit.tsx`），而不是本地 `editDrawerData`；整页切换见 `state-management.md`。
- 筛选表单的写法（`debounce`、空串转 `undefined`、归 1 页）见 AGENTS.md「范式索引」中「给列表页加筛选框」一条。

## 4. Arco 的用法惯例

- 组件从 `@arco-design/web-react` 具名导入，图标从 `@arco-design/web-react/icon`；子组件在模块顶层解构一次：`const FormItem = Form.Item; const Option = Select.Option;`、`const { Title } = Typography;`（`src/pages/operation-config/port/add.tsx:19-20`、`src/pages/operation-config/port/index.tsx:19`、`src/pages/operation-config/working-ship/add.tsx:8-9`）。
- 全局 `ConfigProvider` 已经把 `Card bordered`、`List bordered`、`Table border` 设为 `false`（`src/main.tsx:206-218`），组件里不必再逐个传。
- `Message` 全局 `maxCount: 5`（`src/main.tsx:27`）。同一类错误可能反复触发时带 `id` 去重：`Message.error({ id: 'paint-model-load', content })`（`src/pages/operations/ops-settings/components/PaintTypeCascader/usePaintModels.ts`、`src/main.tsx:185-188`）。
- 禁用的按钮外包 `Tooltip` 说明为什么不能点（`src/pages/product-config/inspection-route/index.tsx:917-930`，单例观察）。

## 5. 样式

- 组件 `import styles from './Xxx.module.less'`，页面入口 `import styles from './style/index.module.less'`（`src/pages/operation-config/port/index.tsx:17`、`src/pages/am-config/quota-notice/components/SendResultCard/SendResultCard.tsx`）。
- 类名：`styles.xxx` 点号访问 1196 处、`styles['xxx-yyy']` 134 处。新写的类名用 camelCase，才能点号访问（`styles.card`、`styles.summary`）；老文件里 kebab 类名保持原样，不顺手改。
- 条件类名用 `classnames`，全仓统一别名 `cs`（9 个文件全是 `import cs from 'classnames'`，如 `src/components/ListPage/index.tsx:2`、`src/components/DetailDrawer/index.tsx:3`）。
- 覆盖 Arco 内部样式用 `:global(.arco-xxx)` 写在模块类名下面（72 个 less 文件这么做）；浮层类组件的特殊要求见 AGENTS.md「硬约束与易错点」中 `Popover` / `Tooltip` 一条。
- 内联 `style={{ ... }}` 很常见（617 处），可用于一次性的间距（`SendResultCard.tsx` 的 `marginTop: 16`），但颜色不能内联写 hex，理由同第 1 节第 4 条。

## 6. 权限在组件里的接法

- 读取：`useSelector((state: GlobalState) => state.buttonCodes ?? [])`，再 `useMemo` 成 `Set` 用 `.has(code)`（`src/pages/operations/ops-settings/index.tsx:50-51`、`src/pages/ai-report/report-list/index.tsx:56-57`），或只判一个码时用 `.includes`（`src/pages/product-config/inspection-route/index.tsx:166-167`）。权限码常量放模块 `constants.ts`（`EDIT_BUTTON_CODE`，`src/pages/product-config/inspection-route/constants.ts`）。
- 无权限时**禁用并说明**，同时在处理函数入口再判一次（`src/pages/product-config/inspection-route/index.tsx:279`、`:538` 的 `if (!canEdit)`）。报告页的具体口径见 AGENTS.md「硬约束与易错点」中「AI 清洗报告详情按报告权限访问」一条。
- `useRoles` 返回的角色只能用于展示，不能用于判权（`src/hooks/useRoles.ts` 顶部注释）。`src/pages/operations/ops-settings/index.tsx:297` 的 `role === '财务'` 是存量写法，不要效仿。

## 7. 界面文案与国际化

现状并存：

- 2024 年建的模块有 `locale/index.ts`，组件里 `const t = useLocale(locale)` 取文案（`src/pages/operations/ops-settings/form.tsx:12`、`:32`，`src/pages/system/users/form.tsx`、`src/pages/vessels/vessels-database/form.tsx`）。
- 2025-05 之后新建的模块全部直接写中文文案，没有 `locale/`（`src/pages/operation-config/port/`、`src/pages/am-config/quota-notice/`、`src/pages/product-config/inspection-route/`、`src/pages/ai-report/`）。

推荐：改老模块时沿用它已有的 `locale` 键，不要在同一模块里混写；新模块按近期惯例直接写中文。菜单名是例外，走 `src/locale/index.ts` 的 `menu.*` 键，`layout.tsx` 用 `locale[route.name] || route.name` 取（`src/layout.tsx:220`）。
