# 可立即使用的 Agent Flow：现成 GitHub 项目调研

> 调研方式：DeepSeek 联网搜索（deepseek-search MCP）。
> 聚焦问题：**哪些找 Bug / 修 Bug 的 agent flow 今天就能 clone / pip install / docker 跑起来**，而不是停留在论文层面。
> 选型前提（贴合本工作台）：DeepSeek 模型 + 私有/本地仓库 + 42 机 Docker + 中文团队。

## 一、结论摘要（TL;DR）

1. **能立刻跑的项目有 6 个**，按「对你们场景的直接可用度」排序：
   - 🥇 **Moatless Tools** — `pip install moatless`，**原生支持 DeepSeek**（deepseek-reasoner 50%），自带 Docker + Web UI，可对任意仓库建语义索引，是唯一「开箱即用 + 支持你们模型 + 有界面」的。
   - 🥈 **AutoCodeRover** — 有 **local-issue 模式**（本地仓库 + issue 文本文件直接跑），Docker 一条命令，MIT 许可，$0.7/issue，46.2% SWE-bench Verified。
   - 🥉 **repo-bug-hunter** — `pip install repo-bug-hunter`，**强制 test-first（先复现 failing test 才解锁源码）**，与你们「修前复现失败」理念完全一致；支持任意 OpenAI-compatible server + Ollama。
   - 其余：mini-swe-agent（100 行 Python 最轻量）、SWE-agent（成熟但已维护模式）、Agentless（最低成本 $0.34 但许可不明 + 无沙箱）。
2. **共同规律**：没有一个项目天然支持「多仓库 + 角色分离 + 独立验证 + 人工审核」这一整套。它们的价值在于**单仓库、单 issue 的定位→修复→验证闭环**可以直接复用或拆解参考，外层编排仍要你们自建（这也是本工作台的价值所在）。
3. **最省力的落地路径**：先拿 Moatless Tools（或 AutoCodeRover local-issue 模式）做「单仓库 bugfix 引擎」跑通一个真实 Bug，再决定是包一层还是拆出其中「定位/修复/验证」模块嵌入现有 Pi 三阶段流程。

---

## 二、逐个项目：安装、启动命令、License、适配度

### 1. Moatless Tools ⭐ 最推荐
- **仓库**：https://github.com/aorwall/moatless-tools （Python + React，~640 star，活跃维护）
- **理念**：不靠 agent 推理，靠**好工具把正确上下文塞进 prompt + 处理好响应**；`CodeIndex` 语义代码检索 + `FileContext` 管理改动 + `AgenticLoop` 编排。
- **安装（三选一）**：
  ```bash
  pip install moatless                 # 基础包
  pip install "moatless[api]"          # 带 API server
  # 或 Docker 一键起（含 Web UI）：
  git clone https://github.com/aorwall/moatless-tools.git && cd moatless-tools
  cp .env.example .env    # 填 API key + MOATLESS_DIR
  make run                # 打开 http://localhost
  ```
- **关键优势（对本工作台）**：
  - **原生支持 DeepSeek**：verified 模型表里直接有 `deepseek/deepseek-chat`（36%）和 `deepseek/deepseek-reasoner`（**50%** Verified Mini）——这是所有项目里对你们模型支持最明确的。
  - 支持 `--base-url` 自定义 LLM API 端点（可指向自建网关）。
  - 提供 Python API：`ActionAgent(completion_model=..., actions=[...])` 可编程调用，适合嵌入你们的 Runner 调度。
  - 有 SvelteKit Web UI 可视化代码编辑轨迹。
- **成本/效果**：Claude 4 Sonnet 70.8%、$0.63/instance（SWE-bench Verified）。
- **注意**：语义检索默认用 Voyage AI embedding（需额外 key）；SWE-bench 评测要 Voyage key，但对自定义仓库可用自己的索引方式。

### 2. AutoCodeRover ⭐ 次推荐
- **仓库**：https://github.com/AutoCodeRoverSG/auto-code-rover （NUS，MIT）
- **理念**：AST 结构感知检索（`search_function` / `search_method_in_class`）+ 频谱定位 + patch 生成；两阶段（检索循环用只读工具 → 补丁生成不调工具）。
- **安装/启动（Docker 最快）**：
  ```bash
  export OPENAI_KEY=sk-...   # 或 ANTHROPIC_API_KEY / GROQ_API_KEY
  docker build -f Dockerfile.minimal -t acr .
  docker run -it -e OPENAI_KEY="${OPENAI_KEY}" acr
  # 三种模式里最贴你们的是 local-issue 模式（本地仓库 + issue 文本文件）：
  PYTHONPATH=. python app/main.py local-issue \
    --output-dir output --model gpt-4o-2024-05-13 \
    --task-id <id> --local-repo <本地仓库路径> --issue-file <issue 描述文件>
  ```
- **关键优势**：**local-issue 模式直接吃「本地仓库 + issue 文件」**，不需要 GitHub 链接，天然适配私有仓库/内网。Docker 隔离执行回归测试。
- **成本/效果**：46.2% SWE-bench Verified，每任务 <$0.7、<7 分钟。
- **注意**：模型主要面向 OpenAI/Anthropic/Groq/Gemini，**未明确支持 DeepSeek**（需改 model 接入层，工作量中等）；无测试套件时修复率明显下降（靠 SBFL 频谱定位需要测试）。

### 3. repo-bug-hunter
- **仓库/包**：PyPI `repo-bug-hunter`（pip 安装，MIT 风格）
- **理念**：**test-first 强制复现**——源码文件锁定，直到 agent 注册一个在现有代码上失败的测试命令；提交时再跑一次，仍失败则打回。这正是你们「修前复现失败 → 修后同一命令通过」的工程化实现。
- **安装/启动**：
  ```bash
  pip install repo-bug-hunter            # Python 3.10+
  repo-bug-hunter demo                   # 无 key、无 Docker，浏览器回放示例
  export OPENROUTER_API_KEY=...          # 或 Claude / Ollama / 任意 OpenAI-compatible
  repo-bug-hunter doctor                 # 体检（key + Docker）
  repo-bug-hunter smoke                  # 端到端修一个玩具 bug
  repo-bug-hunter run --name pilot --n 5 --difficulty "<15 min fix"
  repo-bug-hunter evaluate runs/pilot    # 用官方 SWE-bench harness 打分
  ```
- **关键优势**：agent 主体**一个文件 <200 行**，纯循环无框架，每一步可浏览器回放——最适合当「最小可读参考实现」。沙箱 Docker 镜像**无网络**（不能装包、不能查真实答案）。
- **注意**：Python 3.10+，Windows 需 WSL2；模型用 OpenRouter/Claude/Ollama/任意 OpenAI-compatible。

### 4. mini-swe-agent
- **仓库**：SWE-agent 同团队（Princeton）的简化版，**100 行 Python**，74%/65% SWE-bench Verified。
- **价值**：不是「跑起来用」，而是「**抄一个最小 agent 循环**」——当你们想把 bugfix 引擎做成自有极简实现时，这是最佳起点（README 明确：团队主力开发已转到这里，SWE-agent 本体进入维护模式）。

### 5. SWE-agent（原始版）
- **仓库**：https://github.com/SWE-agent/SWE-agent （MIT，~18.8k star）
- **安装**：`pip install sweagent`（需 Docker）；CLI `sweagent run` / `sweagent run-batch`，单 YAML 配置全量可控；经 LiteLLM 接任意 provider（**含 DeepSeek**）。
- **定位**：研究级 harness 标杆，但**已进维护模式**，团队推荐新用户用 mini-swe-agent。除非要 SWE-bench 正式跑分或 EnIGMA 安全场景，否则不必上这个重型版本。

### 6. Agentless（OpenAutoCoder）
- **仓库**：https://github.com/OpenAutoCoder/Agentless （~2k star）
- **安装**：clone + conda + `pip install -r requirements.txt`（**无 Docker 沙箱，直接在本机跑**）。
- **定位**：三阶段流水线（层级定位 → 多候选 patch → 复现测试+回归过滤+投票），$0.34/issue 最低成本，被 OpenAI/DeepSeek 用来做模型评测。
- **注意（两个硬坑）**：① **README 未声明 License**（商用/集成有法律风险，需先确认）；② 无沙箱隔离，直接跑测试套件。适合做**方法论参考**（层级定位 + 多候选投票），不适合直接拿来跑你们的私有仓库。

---

## 三、横向对比表

| 项目 | 安装门槛 | Docker 沙箱 | DeepSeek | 本地/私有仓库 | License | 效果(Verified) | 单 issue 成本 | 立即可用度 |
|---|---|---|---|---|---|---|---|---|
| **Moatless Tools** | `pip install` / `make run` | ✅ 内置 | ✅ 原生(50%) | ✅ 任意仓库索引 | 开源 | 70.8%(Claude4) | ~$0.63 | ⭐⭐⭐ 最高 |
| **AutoCodeRover** | Docker 一条命令 | ✅ | ⚠️ 需改接入 | ✅ local-issue 模式 | MIT | 46.2% | <$0.7 | ⭐⭐⭐ 高 |
| **repo-bug-hunter** | `pip install` | ✅ 无网络 | ⚠️ 经 OpenAI-compat | ✅ 任意 repo | 开源 | 玩具/实测 | 低 | ⭐⭐ 中（最小可读实现）|
| **mini-swe-agent** | 抄代码/极简 | ✅(用 SWE-ReX) | ⚠️ | ✅ | MIT | 65–74% | 低 | ⭐⭐ 参考模板 |
| **SWE-agent** | `pip install sweagent` | ✅ SWE-ReX | ✅ 经 LiteLLM | ✅ | MIT | ~SOTA | $1–10 | ⭐ 重型/维护模式 |
| **Agentless** | clone+conda | ❌ 无沙箱 | ⚠️ | ⚠️ 需改造 | ❌ 未声明 | 50.8% | **$0.34** | ⭐ 方法论参考 |

---

## 四、与本工作台的关系与落地建议

### 4.1 它们能覆盖什么、不能覆盖什么
- ✅ 能覆盖：**单仓库、单 issue 的「定位 → 修复 → 验证」闭环**——正是你们三阶段里「实施会话」的内部循环。
- ❌ 不能覆盖：多仓库并发、角色会话隔离（定位/实施/验证互不继承上下文）、修前/修后确定性复现命令的对外契约、人工审核队列、凭据与仓库只读镜像管控。这些是本工作台 Runner 编排层的职责，任何现成项目都没做。

### 4.2 推荐落地路径（按投入递增）
1. **零成本验证（半天）**：在 42 机 Docker 里 `make run` 起 Moatless Tools，配 DeepSeek key + 一个真实仓库的语义索引，跑 1 个真实 Bug，记录效果与 token 成本。
2. **选定引擎（1–2 天）**：
   - 要「省事 + 支持 DeepSeek + 有 UI」→ 选 **Moatless Tools**，包成单仓库 bugfix 引擎，Runner 调用其 Python API（`ActionAgent`）。
   - 要「AST 结构定位 + MIT + local-issue 模式」→ 选 **AutoCodeRover**，自行加 DeepSeek 接入。
3. **自研极简引擎（若上面两个都不贴）**：以 **repo-bug-hunter** 的 <200 行循环 + **mini-swe-agent** 的 100 行最小实现为骨架，内置你们已定好的「修前复现」门槛，代码量可控、完全自主。

### 4.3 三个务必复用的设计点（来自这些现成项目）
1. **test-first 锁定源码**（repo-bug-hunter）：源码在复现失败前不可改，是防 overfitting patch 的工程闸门，你们已有此规则，可直接借鉴其「三次不 fail 自动解锁 + 提交再验证打回」的容错实现。
2. **AST 结构检索优先于全文 grep**（AutoCodeRover / Moatless CodeIndex）：定位阶段给只读的结构化检索工具，避免上下文爆炸。
3. **多候选 patch + 复现测试投票**（Agentless）：$0.34 就能靠采样+投票提分，若单次命中率不足可让实施阶段产 N 候选、验证阶段筛。

---

## 五、参考来源
- Moatless Tools：github.com/aorwall/moatless-tools、PyPI `moatless`、market.dev/repoportal 记录
- AutoCodeRover：github.com/AutoCodeRoverSG/auto-code-rover（README 三模式）、FSE'26 industry、OSS AI Hub
- repo-bug-hunter：PyPI `repo-bug-hunter` 0.3.0 README
- SWE-agent / mini-swe-agent：github.com/SWE-agent/SWE-agent、EdwardAThomson/agent-reviews、AI Wiki
- Agentless：github.com/OpenAutoCoder/Agentless（README + README_swebench）、Fastpedia、SourcePulse
