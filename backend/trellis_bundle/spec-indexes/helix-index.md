# Helix 规范索引（按需加载）

> 源：`voyager_server/AGENTS.md`、`voyager_server/internal/helix/AGENTS.md` 及 69 份下级 AGENTS.md（共 70 份）@ 2026-10-26
> 仓库 AGENTS.md 仍是团队权威；本目录是个人按需摘要，冲突时以仓库为准。

## 1. 怎么用

| 你要做什么 | 先读 |
|---|---|
| 改任意 helix 代码 | [platform.md](./platform.md)（跨服务硬约束） |
| 给 URL / 业务名找代码 | [find-code.md](./find-code.md) |
| 改某个服务 | platform + `services/<服务>.md`（50–141 行） |
| 改某个业务域 | 服务文件的域索引表 → `domains/<服务>/<域>.md`（17–72 行） |

不要一次读全目录。默认只读本页；命中服务后再读该服务文件；命中域后再读域文件。

## 2. 服务索引（13）

| 服务 | 一句话职责 | 域数 |
|---|---|---:|
| [gateway](./services/gateway.md) | 唯一 HTTP 入口：路由、gtoken、切流、WebSocket | — |
| [cloud](./services/cloud.md) | GF cloud 收敛进程（域优先） | 33 |
| [mobilization](./services/mobilization.md) | 主业务链路：出动→作业→AI→报告→决策→客户端（域优先） | 20 |
| [authz](./services/authz.md) | 权限判定与策略（Kitex + casbin） | — |
| [magneto](./services/magneto.md) | 设备消息与清洗状态聚合（MQTT） | — |
| [hangar](./services/hangar.md) | 设备媒体/AI 处理（集群内；与 BOS hangar 同名不同物） | — |
| [factory](./services/factory.md) | 极简栈工厂服务（Hertz + fx + sqlx） | — |
| [clipcloud](./services/clipcloud.md) | 视频片段投递与完成回执 | — |
| [feishu](./services/feishu.md) | 飞书事件与消息（Hertz + ws） | — |
| [workflow](./services/workflow.md) | Temporal 工作流 | — |
| [event-ingestor](./services/event-ingestor.md) | 事件入站（Watermill；Nacos 名 event_ingestor） | — |
| [event-processor](./services/event-processor.md) | 事件消费（Watermill + RocketMQ） | — |
| [cronjob](./services/cronjob.md) | XXL-JOB 旧执行器（退役中） | — |

## 3. 域索引（53）

> 本表只做快速定位；“什么时候读 / 一句话不变量”的完整版在对应服务文件，以那里为准。
> cloud 的完整表在 [services/cloud.md §域索引](./services/cloud.md)，mobilization 在 [services/mobilization.md §4](./services/mobilization.md)。

| 域 | 服务 | 什么时候读（动作） | 详情 |
|---|---|---|---|
| appauth | cloud | 改权限同步/船舶授权补录 RPC | [appauth](./domains/cloud/appauth.md) |
| appfrontend | cloud | 改客户端出动列表/详情、设备上报、客户下单 | [appfrontend](./domains/cloud/appfrontend.md) |
| biofouling | cloud | 改海生物字典或它的排序/删除 | [biofouling](./domains/cloud/biofouling.md) |
| calendar | cloud | 改日程查询或分页/脱敏 | [calendar](./domains/cloud/calendar.md) |
| customer | cloud | 改客户 CRUD、成员删除、订单转移 | [customer](./domains/cloud/customer.md) |
| device | cloud | 改设备启停/列表/开盖记录或设备侧档案读取 | [device](./domains/cloud/device.md) |
| devicechain | cloud | 改设备上传扫描/消费链、ClickHouse 批写 | [devicechain](./domains/cloud/devicechain.md) |
| deviceedge | cloud | 改设备侧 RPC、开盖告警、离线登录资料 | [deviceedge](./domains/cloud/deviceedge.md) |
| dispatch | cloud | 改出动/用户/群名向派工调度服务推送 | [dispatch](./domains/cloud/dispatch.md) |
| dispatcherregion | cloud | 改调度区域配置的列表/详情/写口 | [dispatcherregion](./domains/cloud/dispatcherregion.md) |
| enum | cloud | 改船型枚举全量查询 | [enum](./domains/cloud/enum.md) |
| fleet | cloud | 改船队 CRUD、组织船舶授权 | [fleet](./domains/cloud/fleet.md) |
| fleetmigrate | cloud | 改旧授权一次性迁移 | [cloud 索引](./services/cloud.md) |
| fleetnodeauth | cloud | 改账号到船队树节点的授权/继承 | [fleetnodeauth](./domains/cloud/fleetnodeauth.md) |
| fleetoverview | cloud | 改船队管理客户列表的行指标 | [cloud 索引](./services/cloud.md) |
| fleettree | cloud | 改公司/一级/二级树节点 | [fleettree](./domains/cloud/fleettree.md) |
| fleetvisibility | cloud | 改客户数据可见性判定 | [cloud 索引](./services/cloud.md) |
| lookup | cloud | 改客户端下拉/列表查询或它的错误码 | [lookup](./domains/cloud/lookup.md) |
| mobigroup | cloud | 改建群、行动编号、群名、建群通知 | [mobigroup](./domains/cloud/mobigroup.md) |
| mobigroupconfig | cloud | 改建群配置读写、机器人清单、补偿同步 | [mobigroupconfig](./domains/cloud/mobigroupconfig.md) |
| mobilization | cloud | 改出动读取/媒体/生命周期/草稿 | [mobilization](./domains/cloud/mobilization.md) |
| ops | cloud | 改运营任务列表、创建作业/系统任务 | [ops](./domains/cloud/ops.md) |
| orgprofile | cloud | 改「用户在组织下看得到哪些船」 | [orgprofile](./domains/cloud/orgprofile.md) |
| overaccess | cloud | 改越权报告待审批 | [cloud 索引](./services/cloud.md) |
| port | cloud | 改港口 CRUD、港口列表排序或区域上溯 | [port](./domains/cloud/port.md) |
| profile | cloud | 改个人资料读写 | [profile](./domains/cloud/profile.md) |
| serviceorder | cloud | 改客户服务订单、年度额度预占、订单通知/恢复 | [serviceorder](./domains/cloud/serviceorder.md) |
| systask | cloud | 改 system_task 状态机或调度装配 | [systask](./domains/cloud/systask.md) |
| user | cloud | 改用户/角色/登录查库两段 | [user](./domains/cloud/user.md) |
| vessel | cloud | 改客户船、船型、主图、油漆写回 | [vessel](./domains/cloud/vessel.md) |
| vesselassignment | cloud | 改船舶归属周期与按节点删除 | [vesselassignment](./domains/cloud/vesselassignment.md) |
| vesselpart | cloud | 改船舶部位字典或部位名称映射 | [vesselpart](./domains/cloud/vesselpart.md) |
| workingship | cloud | 改工作船字典或 rc_box_list 解析 | [workingship](./domains/cloud/workingship.md) |
| mobi | mobilization | 改出动读写、进度报告、建群信息、油漆字典 | [mobi](./domains/mobilization/mobi.md) |
| media | mobilization | 改照片入库、视频登记、视频库、视频处理记账 | [media](./domains/mobilization/media.md) |
| ai | mobilization | 改识别调用、相机模型路由、反馈回流、海生物补跑 | [ai](./domains/mobilization/ai.md) |
| report | mobilization | 改报告组装/读取/审核/水印/照片库/打包下载/预览 | [report](./domains/mobilization/report.md) |
| reportmanage | mobilization | 改报告工单列表、分配、状态流转、冻结 | [reportmanage](./domains/mobilization/reportmanage.md) |
| cleaningdecision | mobilization | 改洗船建议开关、两个决策按钮、决策通知 | [cleaningdecision](./domains/mobilization/cleaningdecision.md) |
| cleaningstrategy | mobilization | 改档位计算、锁定格、下发、回填 | [cleaningstrategy](./domains/mobilization/cleaningstrategy.md) |
| n0video | mobilization | 改 N 系列视频段审核/编辑/时间轴 | [n0video](./domains/mobilization/n0video.md) |
| quant | mobilization | 改节油/减碳公式、系数发布 | [quant](./domains/mobilization/quant.md) |
| notify | mobilization | 改提醒邮件、App 推送、照片下载通知 | [notify](./domains/mobilization/notify.md) |
| quotanotice | mobilization | 改额度通报的填表、预览、重发 | [quotanotice](./domains/mobilization/quotanotice.md) |
| clientapp | mobilization | 改客户 App 消息/长连接推送/设备名册/照片反馈 | [clientapp](./domains/mobilization/clientapp.md) |
| fleetapp | mobilization | 改船队 App 任务列表、异常上报 | [fleetapp](./domains/mobilization/fleetapp.md) |
| annualorder | mobilization | 改额度消耗、年度额度预占、订单可见范围 | [annualorder](./domains/mobilization/annualorder.md) |
| identity | mobilization | 改凭据校验、改密/找回、可见船舶范围 | [identity](./domains/mobilization/identity.md) |
| storage | mobilization | 改上传签名、OSS 回调、分片、前端地址 | [storage](./domains/mobilization/storage.md) |
| dict | mobilization | 改部位树、角色、船型字典 | [dict](./domains/mobilization/dict.md) |
| inspectroute | mobilization | 改线路草稿、校验、发布、多套 | [inspectroute](./domains/mobilization/inspectroute.md) |
| authzscope | mobilization | 改报告/年度订单的数据范围注入 | [mobilization 索引](./services/mobilization.md) |
| eventbus | mobilization | 改发布/订阅，或动 Redis 键前缀、字段名、消费组名 | [eventbus](./domains/mobilization/eventbus.md) |

## 4. 与仓库的关系

- **仓库 AGENTS.md 是团队权威**；本目录只做个人按需摘要，不替代、不入库。
- 重新同步触发：仓库 AGENTS.md 增删、服务增删、架构测试规则变化、`spec-gap` 状态变化。不做定时同步，避免第二份规范腐化。
- 源引用一律写 `voyager_server/...`（仓库相对路径），不写盘符或 worktree 前缀。
- **引用代码位置只用「文件 + 类型/函数/章节」，不写行号**：行号会随改动失效，文件名和符号名在绝大多数改动中不变。
- 本目录产物用中文；**本 Trellis 工作区的提交规范与 voyager_server 不同**：无 scope、无 `Co-Authored-By`，见 [commit-convention](../../process/commit-convention.md)。
- 单份文件预算：platform ≤350 行、服务 ≤220 行（其他服务 ≤140）、域 ≤80 行、本页 ≤120 行。
