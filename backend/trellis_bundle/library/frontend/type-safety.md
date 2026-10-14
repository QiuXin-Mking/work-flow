# 类型写法

> 适用：在 `operations-dashboard/` 使用生成类型、定义本地类型、写枚举与常量。
> 以下三件事已在 `operations-dashboard/AGENTS.md`「硬约束与易错点」里讲清，本篇只链接：helix 新字段不会进生成类型（「helix 侧新增的请求参数与响应字段」一条）、protobuf 零值被吞（「protobuf 生成的响应体会吞掉一切零值」一条）、两套部位枚举与三板编码（「部位枚举有两套同名不同义的 `1`」「三板编码多套并存」两条）。

## 1. 最重要的四条

1. **生成类型只读**：`API.*` / `ExhibitionAPI.*` 来自生成目录，缺字段在业务目录用交叉类型补，见第 2 节。
2. **组件只见前端模型**：接口原样形状在模块转换层翻译掉，见第 3 节。
3. **`domain/` 里零 `any`**：现有域层实现文件 `any` 出现 0 次，并且 CI 以 `--max-warnings 0` 检查域层，见第 6 节。
4. **只作类型用的导入导出写 `import type` / `export type`**：`tsconfig.json` 开了 `isolatedModules`，见第 5 节。

## 2. 生成类型怎么用

- 生成的 `typings.d.ts` 用 `declare namespace` 声明全局命名空间：`API`（`src/services/gateway/typings.d.ts`）和 `ExhibitionAPI`（`src/services/exhibition/typings.d.ts`）。业务代码**不 import，直接写** `API.PortRegion`、`API.MobiOutput`（`src/pages/operation-config/port/add.tsx:39`、`src/pages/operations/ops-settings/index.tsx:71`）。在非 `services/` 代码里，88 个文件用 `API.`，3 个文件用 `ExhibitionAPI.`（`src/pages/exhibition/intent-list/api.ts`、`src/pages/exhibition/vip-list/index.tsx`）。
- 请求参数类型用生成的 `API.getXxxParams`，不要自己手抄一份。
- **生成类型缺字段 / 字段已废弃**，在业务目录声明本地类型，现有三种写法：

  | 写法 | 场景 | 佐证 |
  |---|---|---|
  | `API.X & { 新字段?: T }` | 后端加了字段 | `src/pages/operations/ops-settings/index.tsx:32` 的 `MobiListQuery`、`src/components/PhotoLibrary/index.tsx:83` 的 `LibraryPhoto` |
  | `Omit<API.X, '旧字段' \| ...> & { 新字段?: T }` | 后端用新字段替换了旧字段 | `src/pages/am-config/photo-feedback/service.ts` 的 `PhotoFeedbackItem` |
  | 行内窄化断言 `(res?.data as { roles?: FrontendRole[] })?.roles` | 只在一处读一个字段 | `src/main.tsx:115`（单例观察） |

  新增字段都写成可选（`?:`），并在注释里说明字段缺失时的含义（`LibraryPhoto` 的每个字段都这么写）。
- `src/services/gateway/` 名字是历史误名，指向 cloud 服务，勿改名（AGENTS.md「硬约束与易错点」第二条）。

## 3. 接口形状与前端模型分层

较新的模块把“接口原样”与“前端模型”分成两套类型，翻译只发生在一处：

```ts
// src/pages/am-config/quota-notice/types.ts
export type RawQuotaNotice = API.QuotaNotice;          // 接口原样（snake_case）
export interface MonthQuota { month: string; total: number; consumed: number; remaining: number } // 前端模型
```

- `Raw*` 前缀 = 对生成类型的别名，让 `domain/` 及其单测不直接依赖生成目录（`types.ts` 文件头注释）。
- 翻译函数放 `domain/mapping.ts`（`toQuotaNotice`、`toQuotaNoticeListResult`），由 `service.ts` 调用，组件只拿到前端模型（`src/pages/am-config/quota-notice/service.ts`、`src/pages/am-config/quota-notice/domain/mapping.ts`）。
- 同样的“转换层隔离后端编码”也见于 `src/pages/operations/ops-settings/components/AiCleanStrategy/cleaningStrategyApi.ts`（`BACKEND_TO_BOARD_ID`）与 `src/pages/operation-config/dispatcher-region/service.ts`（`toRegionCascaderOptions` 返回自定义的 `RegionCascaderOption`）。
- `service.ts` 失败时**不要把未翻译的原始 `data` 断言成前端模型返回**，只回传 `code` / `message`（`src/pages/am-config/quota-notice/service.ts` 中 `getQuotaNoticeList` 的注释：那等于给调用方一个类型谎言）。

老模块直接把生成类型或行内 `interface` 用到组件里（`src/pages/operation-config/port/index.tsx:22-35` 的 `PortItem`），属存量，改到时不必强行分层；新模块或涉及零值、命名差异的接口按上面分层写。

## 4. 枚举与常量

**全局业务枚举：`enum` + 同名 `namespace` 合并**，namespace 里放 `getString` / `isValid`（有颜色的再加 `getColor`）。`src/constants/config.ts`、`src/constants/mobi.ts`、`src/constants/index.ts` 全部是这个形状：

```ts
// src/constants/config.ts
export enum MobiModelType { None, Single, Twin, End } // 原文件逐项带注释，此处压成一行
export namespace MobiModelType {
  export function getString(value: MobiModelType): string { /* switch 返回中文名 */ }
  export function isValid(value: any): value is MobiModelType {
    if (!Number.isInteger(value)) return false;
    return value > MobiModelType.None && value < MobiModelType.End;
  }
}
```

- `None` 作首项、`End` 作哨兵，`isValid` 靠这两个判区间。`.eslintrc` 专门关了 `@typescript-eslint/no-namespace` 来允许这种写法。
- 列表渲染直接用 `PortType.getString(type)`（`src/pages/operation-config/port/index.tsx:117`）。
- **与后端数值对齐的模块私有枚举**写显式值，并用注释说明 0 的含义：`SendStatus.Unknown = 0`（`src/pages/am-config/quota-notice/types.ts`）。
- **枚举 → 展示值的映射表用 `Record<Enum, T>`**，加了新枚举成员而漏写映射时编译会报错：`BADGE_STATUS`（`src/pages/am-config/quota-notice/components/SendStatusTag/SendStatusTag.tsx`）、`STATUS_LABELS` / `STATUS_TONES`（`src/components/PhotoLibrary/domain/videoLibrary.ts:188`、`:198`）。只覆盖部分成员时显式写 `Partial<Record<...>>`（`SendStatusTag.tsx` 的 `TIPS`）。
- 模块私有常量放模块的 `constants.ts`；要被 `domain/` 引用的常量文件必须零 `@/` 依赖（范式 `src/constants/ratingLevels.ts`，原因见 AGENTS.md「范式索引」中「需要单测的纯逻辑」一条）。

## 5. 导入导出与类型守卫

- `tsconfig.json` 设了 `"isolatedModules": true`。转出一个纯类型时必须 `export type { X }`，否则 vite 单文件转译找不到值会报错。非 `services/` 代码里有 34 处 `export type {`（`src/hooks/useRoles.ts:5`、`src/components/TrajectoryCanvas/index.ts:3-4`、`src/pages/am-config/quota-notice/components/LogoUpload/index.ts:2`）。
- 只作类型用的导入写 `import type`（非 `services/` 代码里 105 处，如 `src/pages/am-config/quota-notice/domain/mapping.ts:1`、`src/components/ListPage/index.tsx:3`）。同一模块既要值又要类型时拆两行（`SendStatusTag.tsx` 对 `../../types` 就是 `import type { SendResult }` + `import { SendStatus }`）。
- 过滤空值用类型谓词让结果收窄：`.filter((c): c is string => !!c)`（`src/hooks/useRoles.ts:33`），以及 constants 里的 `isValid(value): value is X`。
- 全局常量的类型声明放 `src/vite-env.d.ts`（`__APP_VERSION__`）；资源模块声明（`*.less`、`*.svg`、`*.png`）在 `src/declaration.d.ts`。新增 vite `define` 时要同步补声明。

## 6. `any` 与严格度

- `tsconfig.json` 是 `"strict": false`，非 `services/` 代码里 `: any` / `as any` 约 454 处，集中在老页面（`src/pages/operation-config/port/add.tsx:38`、`:54`，`src/pages/operation-config/port/edit.tsx:34`）。这是存量，改到附近时不必顺手清。
- 新代码的要求分两档：
  - `domain/` 与 `service.ts`：不写 `any`。现有域层实现文件是 0 处，5 个模块 `service.ts` 里 3 个是 0 处；CI 的 `quality-gate` 对域层跑 `eslint ... --max-warnings 0`（`.github/workflows/quality-gate.yml:52`），`@typescript-eslint/recommended` 把 `no-explicit-any` 定为 warning，一个 `any` 就会让门禁失败。该 job 目前只在检测线路相关分支上触发（同文件 `if:` 条件），但按此标准写不吃亏。
  - 组件：外部输入先落到有类型的前端模型再用；Arco 回调参数这类确实拿不到类型的地方可以接受 `any`，但不要把 `any` 继续往下传。
- 运行时校验：仓库没有 zod / yup。表单即时校验用 Arco `Form` 的 `rules`（196 处）；带业务口径的校验写成 `domain/` 纯函数并单测（`src/pages/am-config/quota-notice/domain/validate.ts`、`src/pages/ai-report/report-list/domain/reportAccess.ts`），以服务端为最终裁决。
