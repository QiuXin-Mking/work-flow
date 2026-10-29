# Agent 沙箱与找 Bug / 修 Bug Agent Workflow 调研

> 调研方式：DeepSeek 联网搜索（deepseek-search MCP），2026 年多轮检索。
> 范围：① Agent 沙箱（隔离技术 + 托管平台 + 安全实践）；② 现成的「找 Bug → 修 Bug」agent workflow。

## 一、结论摘要（TL;DR）

1. **找 Bug / 修 Bug 的 workflow 是成熟领域，有大量现成范式可借鉴**，不需要从零发明。行业共识的流水线结构是 **定位(localization) → 修复(repair) → 验证(validation)**，可选加独立 review / patch 选择阶段。本工作台的「定位会话 → 实施会话 → 独立验证会话 → 人工审核」几乎逐条命中这个共识，且「独立验证不继承实施对话」的做法正好对应学术界最强的纠偏设计（SpecRover 的 Reviewer agent、Agentless 的回归测试验证）。
2. **沙箱隔离分三层，Docker 不是安全边界**（NIST SP 800-190 明确：容器共享内核，隔离度远低于 VM）。托管方案里 E2B（Firecracker microVM）是最成熟默认项；自建则按威胁模型在「加固容器 → gVisor → Firecracker/Kata」之间选。
3. **两大范式对立**：agent-based（LLM 自主决定下一步，如 SWE-agent/OpenHands）vs process-based / agentless（固定流水线，LLM 只做单个子任务，如 Agentless/Moatless）。后者成本低 5–10 倍、过程可解释、可审计，与本工作台「显式阶段调度、不让模型自行决定是否调用子工具」的取向一致。

---

## 二、Agent 沙箱技术选型

### 2.1 隔离强度分层（核心结论）

| 层级 | 隔离边界 | 内核 | 冷启动 | 适用场景 |
|---|---|---|---|---|
| Docker / 加固容器 | namespace + cgroup + seccomp | 共享宿主内核 | 毫秒级 | 可信/半可信代码、内部 CI、速度敏感 |
| gVisor (runsc) | 用户态内核 Sentry 拦截 syscall | 独立应用内核（Go） | 进程级启动，快 | 中等风险、K8s 环境、计算密集 |
| Firecracker microVM | 硬件虚拟化（KVM） | 每沙箱独立 guest 内核 | ~125ms | 不可信代码、多租户、serverless |
| Kata Containers | 硬件虚拟化 | 独立 guest 内核 | 慢于容器 | K8s 上要 VM 级隔离 + OCI 兼容 |

**为什么 Docker 不够**：容器共享宿主内核，一个内核漏洞或容器逃逸即可打到宿主机并横向到其它容器。NIST SP 800-190 原话——「containers share the same kernel… the degree of segmentation between them is far less than that provided to VMs by a hypervisor」。

关键技术指标：
- **Firecracker**：~125ms 启动、<5MiB 内存开销/VM、150 microVM/秒/宿主机、仅 5 个 virtio 设备、约 5 万行 Rust；AWS Lambda 的底层。
- **gVisor**：Sentry 用户态内核（Go，约 25 万行），syscall 拦截；CPU 密集近原生，文件 I/O 有 15–30% 开销、网络 30–50%；Google（Cloud Run / GKE Sandbox / App Engine）、OpenAI、Anthropic（claude.ai 代码执行）、Modal 都在用。
- **结论**：agent 循环（git clone + npm install + 跑测试 = 大量小文件/socket 操作）对 syscall 开销敏感，Fly.io 明确建议这种负载选 Firecracker；若在 K8s / 云 VM 内无法直连 KVM，则 gVisor 是「强于容器、免 hypervisor」的升级。

### 2.2 托管沙箱平台对比

| 平台 | 隔离技术 | 关键特性 | 取舍 |
|---|---|---|---|
| **E2B** | Firecracker microVM | Apache-2.0 开源、可自托管(E2B Embed)、BYOC(AWS/GCP/Azure)；~150ms 冷启动；内存保留的 pause/resume；snapshot fork 一次复制最多 20 份；阻断 metadata service、secret 注入让凭据不进沙箱 | 最成熟默认项；纯 ephemeral、无 GPU、不自带持久态 |
| **Daytona** | Docker 容器 | <90ms 冷启动；workspace 持久化（有状态，多轮连续开发）；2026-06 起闭源 | 共享内核；CPU-only、无 GPU |
| **Modal** | gVisor | 唯一有 GPU 的沙箱；内存快照；按秒计费 | 沙箱价格约 3×、300–500ms 冷启动、仅 Python SDK |
| **OpenHands Enterprise** | 加固容器(K8s RuntimeClass) + 用户命名空间重映射 | 沙箱内跑 Docker daemon（不挂宿主 socket、非特权、每 pod 一个沙箱、无 SA token、无 host 网络） | 平台级强制，agent 无法关闭 |
| 其它 | — | Vercel Sandbox(Firecracker)、Cloudflare Sandbox、Blaxel(microVM+Agent Drive)、Fly.io Sprites(VM 持久态)、Northflank/Qovery(BYOC) | 各有 niche |

选型口诀（Qovery 2026）：不可信 LLM 代码走 microVM 沙箱 API；要碰你私有系统/数据库的 agent 走自己云内的 ephemeral 环境。**自建 microVM 是长期工程投入**（VMM + 预热池 + 冷启动 + 调度），除非有强合规要求，否则优先托管。

### 2.3 沙箱安全实践清单（defense-in-depth）

隔离边界只是地基，逐层叠加：
1. **资源上限**：CPU / 内存 / 磁盘 / 进程数（pids-limit 防 fork bomb）每沙箱封顶。
2. **文件系统最小化**：除显式 scratch 目录外只读；workspace 仅按需 bind-mount 到具体 repo；临时执行用 tmpfs。**绝不把 `~/.aws`、`~/.kube`、`~/.docker/config.json` 等云凭据文件挂进沙箱**——云端访问走 gateway 铸造的短时 scoped token。
3. **网络默认拒绝 egress**：只放行内部 API（经认证代理）、自控包镜像、少数已知公共端点（PyPI/npm/官方文档）；DNS 也要管控。这是出事后最关键的闸门。
4. **非 root 运行**：`--user uid:gid` + user namespace remapping；`--cap-drop ALL` + `no-new-privileges`；注意 bind-mount 下容器内 root 写出的文件会变成宿主 root 属主（Linux 上）。
5. **每次会话即弃**：无持久态，任务结束销毁重建，防恶意持久化。
6. **危险命令/工具白名单**：主机侧在执行前拦截（tool_allowlist / command denylist 只是纵深防御，不是主墙）。
7. 一个现成的参考实现：`agt_sandbox`（PyPI）把 Docker/gVisor/OS 原生内核沙箱统一成一个 provider 接口 + YAML 策略文档，可参考其「策略驱动 + 多后端」的抽象方式。

### 2.4 与本工作台的关系（重要）

本工作台（42 机 docker + Pi 常驻容器）跑的是**克隆回来的 bugfix 仓库 + 模型生成的复现/修复命令**，属于「不可信代码」。当前若用裸 Docker 容器，按上述共识**不构成安全边界**。但需先定威胁模型：
- 威胁是「agent 犯蠢 / prompt 注入删仓库、乱写文件」→ **加固容器**（scoped 凭据 + 锁死网络 + 非 root + 资源上限）即可覆盖大部分（Fly.io 原话）。
- 威胁是「有人主动逃逸 / 跑外部恶意输入」→ 需要**第二个内核级边界**（gVisor runsc 或 Firecracker/Kata）。

落地建议：先给现有 Pi 容器加 `--cap-drop ALL`、`no-new-privileges`、非 root、`--network none` 或 egress 白名单、内存/pids 上限，成本最低；若后续要跑多租户/外部工单，再评估 gVisor（runsc 直接注册进 dockerd，改动小）或 E2B。

---

## 三、现成的「找 Bug / 修 Bug」Agent Workflow

### 3.1 行业共识的流水线结构

几乎所有主流系统（无论 agent-based 还是 process-based）都收敛到同一条主线，只是自主程度不同：

```
问题描述(Issue) → 定位(localize 到文件/类/函数/编辑点) → 修复(generate patch) → 验证(跑测试/复现) → [可选] 独立评审/选 patch
```

学术界的差异化主要在：定位的检索方式（AST 结构化搜索 vs 语义 embedding vs 频谱定位）、修复是否多候选采样 + 投票、验证是否用「修前复现失败 → 修后同一命令通过」、以及是否引入独立 reviewer。

### 3.2 代表性工作流

**A. Agent-based（LLM 自主决策下一步）**
- **SWE-agent**（SWE-bench 基准的鼻祖之一）：提出 Agent-Computer Interface(ACI)，把文件查看/编辑/上下文管理做成标准工具接口，ReAct 循环（Observation→Thought→Action→Repeat）。是基准参照物，但控制不透明、成本高。
- **OpenHands**（前 OpenDevin，最完整的开源平台）：CodeAct 范式（动作=代码，通用化所有工具）、AgentSkills 工具库、Docker/K8s 沙箱、可插拔 SecurityAnalyzer（Invariant/Grayswan/LLM 三种策略引擎 + ActionSecurityRisk 分级 + confirmation_mode）。77.6% SWE-bench。适合「通用 AI 工程师」，但对你的场景偏重。
- **AutoCodeRover**（ISSTA 2024，**最值得精读**）：两阶段——① context retrieval 循环（搜索 agent 只有 8 个**只读**工具，基于 AST 结构化搜索 + 频谱定位）② patch generation 循环（**不调任何工具**，直接按 `<original>/<patched>` XML 生成补丁）。19% SWE-bench-lite，$0.43/issue，57 个 issue 平均 4 分钟。核心理念：**定位和修复是工具需求不同的两个任务，定位阶段要结构化导航、补丁阶段只需要收集好的上下文**——与本工作台「定位会话只读、实施会话改代码」完全同构。

**B. Process-based / Agentless（固定流水线，LLM 只做单子任务）**
- **Agentless**（OpenAutoCoder/Agentless，**第二值得精读**）：三阶段 localization→repair→validation，**LLM 不能决定未来动作、不操作复杂工具**。定位用「文件→类/函数→编辑点」层级 + embedding 检索；修复采样多个候选 patch；同时生成能复现原始错误的 reproduction tests；最后用回归测试 + 复现测试过滤 + 多数投票选最终 patch。32% SWE-bench-lite（2024-10 开源最高），**$0.34/fix**；被 OpenAI 用来展示 GPT-4o/o1 编码能力。数据硬结论：它只比 5-agent 协作的 CodeR 低 1%，成本却是后者的 1/9（$0.34 vs $3.09）。
- **Moatless Tools**：embedding 语义检索（Voyage AI）+ 迭代编辑，simple diff 输出，固定 ground-truthed 调用流程。SWE-Search 报告其成本比 agent-based 低 5–14 倍。
- **CodeR**（多 agent 对照）：Reproducer / Fault Localizer / Editor / Verifier 四角色 + Manager 编排 task graph，$3.09/fix——是「多 agent 协作成本高、收益边际小」的反面教材。

**C. 加强「验证/评审」环节的工作流（与本工作台独立验证最相关）**
- **SpecRover**（AutoCodeRover 之上）：加规格推断（spec inference）+ 独立 **Reviewer agent** 对 patch 做 vetting 并给置信度，评审意见回传给 Patching agent 迭代。$0.65/issue。→ 对应你的「独立验证会话输出 verification.json」。
- **PhoenixRepair**（2026 强基线）：多位置集采样 + 迭代反思，74.4% pass@1（SWE-bench Verified）。
- **Google AutoRepair**（工业级落地）：pre-submit 流程里 generate-and-validate 循环 + pre/post-execution 过滤器，先过滤明显不合格的 patch 再送人审。

### 3.3 与本工作台三阶段流程的对照

| 本工作台 | 学术对应物 | 一致性 |
|---|---|---|
| 定位会话（只读仓库 → diagnosis.json） | Agentless 定位、AutoCodeRover 检索循环（只读工具） | 完全同构，且 AutoCodeRover 的「定位阶段只读」是被验证过的有效约束 |
| 实施会话（按诊断改代码 → manifest.json + 修前/修后复现命令） | Agentless repair、AutoCodeRover patch 生成（无工具） | 同构；「修前复现失败、修后同一命令通过」= Agentless 的 reproduction test 思路 |
| 独立验证会话（不继承实施对话，只读候选 → verification.json） | SpecRover Reviewer agent、Agentless 回归验证、Google AutoRepair 的 post-filter | **这是你们相对多数开源方案的高点**——多数开源 agent 让同一 LLM 边写边验，正是它们 overfitting patch 的主因 |
| 人工审核后交付 PR | Agentless 多数投票选 patch、CodeR Manager 终审 | 你们把「终审」从 LLM 换成人类，更保守更可审计 |

---

## 四、可直接借鉴的设计要点（迁移建议）

1. **定位/修复分离已是共识，坚持并显式化**：AutoCodeRover 证明「定位阶段只读工具、补丁阶段不调工具」既降成本又提可控性。可在 diagnosis 阶段限定 Pi 只暴露只读检索工具（类/函数签名优先、按需取内容、频谱定位）。
2. **修前复现是硬门槛**：Agentless 的 reproduction test + 回归过滤，等价于你们「服务端在原始 commit 执行修前命令确认失败才冻结候选树」。这是防 overfitting patch（改了同一个方法但改错）的关键闸门。
3. **独立验证别省**：SpecRover/Google AutoRepair 都用「独立 reviewer / 过滤器」而非「生成者自评」。你们已做到「验证会话不继承实施对话」，继续保持即可，并可加一条：验证者若发现改树即判不合格（你们已有此规则）。
4. **多候选采样 + 投票是低成本提分项**：Agentless 采样多个 patch 再投票，$0.34 就能做到。若单次修复命中率不够，可让实施阶段产出 N 个候选、验证阶段跑同一命令筛掉不通过的，再送审。
5. **沙箱先加固容器、按威胁升级**：先加 cap-drop / 非 root / egress 白名单 / 资源上限（近乎零成本）；需要更强隔离时优先 gVisor（runsc 注册进现有 dockerd，改动最小），再考虑 Firecracker/Kata 或托管 E2B。

---

## 五、参考来源

- 沙箱隔离与平台：NIST SP 800-190；Northflank《Daytona vs E2B》《How to run AI-generated code safely》《Firecracker vs gVisor》；Fly.io《Firecracker vs gVisor: Sandbox Isolation》；Tembo《AI Agent Sandbox》；Safeguard《Sandboxing LLM Agent Code Execution》《gVisor vs Firecracker in 2026》；Qovery《9 Best Ephemeral Code Execution Environments》；Morph《Daytona Alternatives》；OpenHands 官方文档（Docker Runtime / Running Docker in the Agent Sandbox）。
- 找 Bug/修 Bug workflow：SWE-bench & SWE-agent（Jimenez et al., ICLR 2024）；Agentless《Demystifying LLM-Based Software Engineering Agents》(FSE 2025, OpenAutoCoder/Agentless)；AutoCodeRover (ISSTA 2024, arXiv:2404.05427)；SpecRover (IEEE/ICSE 2025)；Moatless Tools；CodeR；PhoenixRepair (GitHub)；Google AutoRepair (arXiv:2610.07289)；《Patch Generation in APR: A Survey》(ACM 2026)；《Dissecting the SWE-Bench Leaderboards》(arXiv:2506.17208)。
