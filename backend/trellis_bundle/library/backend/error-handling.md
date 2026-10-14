# Error Handling

> 适用：helix（`voyager_server/internal/helix`）的错误码定义、service 返回错误、gateway 写 HTTP 响应。权威条文是 `internal/helix/AGENTS.md` §8；本篇记录代码实际怎么做、选哪一套、以及踩过的坑。

---

## 1. 先判断你在哪一套体系里

helix 里**并存两套错误体系**，按 URL 前缀分开，**不能混用**：

| | A. GF 迁移口 | B. helix 原生口 |
|---|---|---|
| URL | `/api/cloud/v1/...`（227 个 `@Router`） | `/api/user/v1`、`/api/storage/v1`、`/api/authz/v1`、新业务 |
| 错误码来源 | `pkg/errno`（码值与 GF gcode 一致，≤ 10116） | `idl/error.proto` 的 `enum ErrorCode`（40000+ / 50000+），多语言文案在 `i18n/i18n.go` 的 `errorMessages` |
| 构造 | `errno.ErrXxx`、`errno.CodeXxx.WithMessage(msg)`、兜底 `asGfError(err)` | `i18n.NewBizStatusError(ctx, infra.ErrorCode_X, fallback)` |
| gateway 写响应 | `WriteGfResponse` / `WriteGfRawData` | `WriteResponse` / `WriteParamError` |
| 代表 | `app/cloud/biz/service/*`（几乎全部） | `app/cloud/internal/serviceorder/`、`handler/*` |

**怎么选：**

- 修改或新增**迁移过来的 `/api/cloud/v1` 口**：用 A，复用已有 GF 码值。`pkg/errno` 只在迁移口复用 GF 已有码时才新增条目（`pkg/errno/codes.go:7-8`、`doc.go:78-79`）。
- **全新接口或新业务**：用 B，在 `idl/error.proto` 里加码，同时在 `i18n/i18n.go` 补中、英、日三语文案。不要为新接口复制 `WriteGfResponse`、code 50、`asGfError` 这些 GF 兼容行为（helix AGENTS.md §5 第 108 行：「全新接口不复制旧 GF 兼容实现」）。
- AGENTS.md §8 写的是「协议错误码…使用 `pkg/errno`」，但新业务的实际做法（serviceorder）是走 `ErrorCode` 枚举 + i18n。按上面的分法执行。

两套码段不重叠（errno ≤ 10116，枚举 ≥ 40000）。`idl/error.proto` 里码段曾发生冲突，是整段搬迁解决的（CLEANING 从 422xx 搬到 424xx）。加码前 grep 一遍 proto，确认没被占用。

---

## 2. 错误码长什么样

```go
// pkg/errno/errno.go —— 每个码都是 Kitex BizStatusError
type Code struct { code int32; message string }
func (c Code) Err() error                       { return kerrors.NewBizStatusError(c.code, c.message) }
func (c Code) WithMessage(message string) error { return kerrors.NewBizStatusError(c.code, message) }

// pkg/errno/codes.go
CodeParameter = reg(1002, "请求参数错误") // reg 在 init 期遇到重复码直接 panic
ErrParameter  = CodeParameter.Err()
```

- errno 码段：0 / 50–65 为 GF 框架码（`GcodeValidationFailed=51`、`GcodeInternalError=50`），999 未登录，1001–2000 通用，2001–3000 鉴权，7001–7200 mqtt，10001+ 业务。
- GF 遗留的重复含义（`CodeAccountNotExist=10001` 和 `ErrAccountNotExist=10017`）保持两个都在，不合并（`doc.go:74-76`）。
- 共享的 `ErrXxx` 变量是只读的，不要对它调 `SetBizExtra`（`doc.go:61-62`）。

---

## 3. service 怎么返回错误

### 3.1 GF 迁移口（A）

```go
// app/cloud/biz/service/mobi_write_create.go
return nil, errno.ErrParameter
return nil, errno.CodeParameter.WithMessage("当前执行方式下检测方案版本为必填项")
// DB / dal 错误一律过 asGfError（82 个文件用了它）
if err != nil { return nil, asGfError(err) }
```

`asGfError`（`app/cloud/biz/service/dto.go:24-37`）的作用：已经是 BizStatusError 的原样返回，其他错误包成 `code=50, message=err.Error()`。**不包的话**，Kitex 会把它当 remote error 处理，文案带上框架前缀，gateway 拿不到码。

### 3.2 新业务（B）：biz 用哨兵错误，service 只翻译一次

```go
// app/cloud/internal/serviceorder/biz/errors.go
var ErrNotFound = errors.New("service_order: not found")
// biz 里包装时保留原因
return fmt.Errorf("%w: oss put: %v", ErrDependencyUnavailable, err)

// app/cloud/internal/serviceorder/service/frontend.go mapBizErr
case errors.Is(err, biz.ErrNotFound):
	return i18n.NewBizStatusError(ctx, infra.ErrorCode_SERVICE_ORDER_NOT_FOUND, err.Error())
default:
	return i18n.NewBizStatusError(ctx, infra.ErrorCode_INTERNAL_ERROR, err.Error())
```

- biz 层不 import errno、kerrors、i18n，只返回能被 `errors.Is` 识别的错误。
- 只声明真的会被 `errors.Is` 匹配的哨兵，不要包一层没人匹配的哨兵，见 [outbound-integration.md](./outbound-integration.md) §7.4。

### 3.3 Kitex handler 不处理错误

`app/cloud/biz/handler/*` 只做转发（`return h.XxxSvc.Do(ctx, req)`），里面没有任何 errno 或 kerrors 代码。错误码之所以能跨 RPC 完整传到 gateway，靠的是 TTHeader：服务端设 `transmeta.ServerTTHeaderHandler`（`app/cloud/internal/app/builder.go`），客户端设 `client/options.go`。新建 Kitex server 或 client 时必须带上这两项，否则码会丢。

---

## 4. gateway 响应壳

```go
// app/gateway/biz/handler/infra/response.go
type Response struct { Code int64 `json:"code"`; Message string `json:"message"`; Data any `json:"data"` }
```

**HTTP 状态码一律 200**，前端靠 `code === 0` 判断成功，`code !== 0` 时全局弹 `Message.error`（前端 spec：[component-guidelines.md](../frontend/component-guidelines.md) 第 1 条）。唯一真正的 HTTP 401 是原生口的 JWT 中间件（`router/infra/middleware.go`）。

| writer | 业务错误 | 非业务错误 | 成功 |
|---|---|---|---|
| `WriteGfResponse`（A） | `{code, message}`，原样透传 | `{code: 50, message: err.Error()}`，**原文暴露给用户** | `{code: 0, message: "", data}` |
| `WriteGfRawData`（A） | — | — | service 已经序列化好的 `data`；出错时仍然走 `WriteGfResponse(c, nil, err)` |
| `WriteResponse`（B） | `{code, message}` | `{code: 50000, message: "internal error"}`，隐藏原文 | `message: "success"` |
| `WriteParamError`（B） | 固定 1002「请求参数错误」 | | |

其他会返回给前端的特殊码：

| 码 | 来源 | 备注 |
|---|---|---|
| 51 | gateway 复刻 GF `v:` 校验（`errno.GcodeValidationFailed`），文案是 GF gvalid 的英文，如 `"The %s field is required"`；body 解码失败也返回 51 | 校验顺序 = GF 输入结构体的字段声明顺序（`cloud_mobi_write_service.go:98-139`），helper 见 `gvalidRequiredMessage` / `gvalidEnumsMessage` / `gvalidBetweenMessage` |
| 999 | gtoken 未登录 `{999, "请登录"}`（`gtokenauth/middleware.go` `writeNotLogin`） | |
| 2005 | allowlist 拒绝（`writeAllowlistForbidden`） | **JSON 形状不同**：`{code, message, timestamp, traceID}` |
| 50099 | Kitex `RecoverMiddleware` 捕获 panic（`pkg/middleware/middleware.go:89-108`） | 不在 errno 也不在枚举里；gateway 侧 panic 则是 hertz 默认 recovery，返回 **HTTP 500 空 body** |

`gf_json_bind.go` 的 `gfBindJSONBody` 会宽松地把 `{"value":4}` 强转为 0，这是有意复刻 GF 的行为，不要"修"。

---

## 5. 错误在哪里记日志

- **gateway A 口**：handler 不记 RPC 错误。`WriteGfResponse` 把错误挂到请求上下文，由 access log（`app/gateway/fx.go` 约 221-280 行）统一打一行，带 `biz_code` / `biz_err`：BizStatusError 记 Warn，兜底 50 记 Error。handler 只在拿 client 失败、bind 失败时自己记日志。
- **Kitex 服务端**：`AccessLogMiddleware` 每个 RPC 打一行，有错就记 Warn。
- **service 层**：只在「吞掉错误、降级继续」的分支记日志，例如 `mobi_write_create.go:356` 的 `"...generate uid failed continue without uid"`。已经返回给调用方的错误不要再记一遍，否则会和 access log 重复。级别规则见 [logging-guidelines.md](./logging-guidelines.md)。

---

## 6. 常见错误

### 6.1 裸 error 直接 return，丢了错误码

**症状**：前端收到 `code=50`，文案带 `biz error: code=...` 或 Kitex 框架前缀。
**原因**：A 口 service 直接 `return nil, err`，err 既不是 BizStatusError，也没经过 `asGfError`。
**处理**：A 口 dal 错误一律 `asGfError(err)`；业务拒绝用 `errno`。`jwt.go:210` 为了兼容这种情况，不得不从文本里解析码，不要照抄那种写法。

### 6.2 以为 fallback 文案会显示给用户

`i18n.bizMessage`（`i18n/i18n.go`）的取文案逻辑是：**先查 `errorMessages` 的翻译，查到就用翻译，查不到才用 fallback**；语言取不到时默认英文。于是：

- `i18n.NewBizStatusError(ctx, ErrorCode_PARAMETER_INVALID, "只支持[gif、jpg、jpeg、png]格式的图片")`（`mobi_photo_write.go:116`）实际显示的是「请求参数错误」，fallback 被翻译覆盖。要给用户看具体原因，就新开一个码并配文案。
- 反过来，码没有配文案时，用户看到的就是 fallback 原文。`SERVICE_ORDER_*`（41602–41612）目前在 `errorMessages` 里**一条都没有**，所以用户会看到 `"service_order: not found"` 这类英文哨兵文本。**新加枚举码必须同时补 i18n 三语文案。**

### 6.3 A 口会把内部错误原文返回给用户

`asGfError` 和 `WriteGfResponse` 的兜底 50 会把 `err.Error()`（可能是 SQL 或驱动报错文本）直接放进 message。这是为了和 GF 对拍、有意保留的存量行为。新接口走 B 体系，由 `WriteResponse` 隐藏原文。

### 6.4 业务冲突用错码

`vesselwrite.go:55-57` 用 `kerrors.NewBizStatusError(gfCodeInternalError, "「IMO」重复")`，把业务冲突报成了 50；`handler/client_app_analytics.go` 用了 401、400 这类 HTTP 风格的魔法码。新代码只从 errno 或枚举里取码。

### 6.5 吞错误

`_ = json.Unmarshal(...)`（`mobi_group_config_fill.go:88-89` 等）和 `_ = s.Store.InsertMobiOpsLog(...)`（`mobi_write_status.go:228`，注释写明是照搬 GF）属于存量。新代码不允许静默吞错误：确实要吞，就在那一行记日志说明原因和影响（helix AGENTS.md §8）。

---

## 7. 测试

- service 测试用 `kerrors.FromBizStatusError(err)` 断言码值，参照 `app/cloud/biz/service/create_port_test.go`。
- biz 测试用 `errors.Is(err, biz.ErrXxx)` 断言。
- 新增 errno 码时，`pkg/errno/errno_test.go` 里的 `TestGfCodeMapping` 负责保证码值和 GF 一致。
