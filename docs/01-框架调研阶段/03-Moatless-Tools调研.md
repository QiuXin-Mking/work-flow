# Moatless Tools 深度调研：工具优先的代码编辑 agent

> 调研方式：GitHub 仓库原文（README.md / docker.md / pyproject.toml）+ 源码目录树，全部读自 `aorwall/moatless-tools` main 分支。
> 与 [02-可立即使用的agent-flow.md](./02-可立即使用的agent-flow.md) 的关系：02 是横评，本篇是 Moatless 单点深挖，补上架构、模块、可复用点。
> 聚焦问题：这套「工具塞上下文」的路子，能不能直接当我们的**定位 + 修复引擎**，哪几块设计值得抄进 pi-bugfix-console。

## 一、一句话定位

**它不相信让 agent「推理出答案」，而是相信「把正确的上下文用工具塞进 prompt，再把响应处理好」。**
作者 Albert Örwall 在 README 里明说这是 hobby project，用来验证自己关于「LLM 如何编辑大型既有代码库」的想法。核心资产是**一套围绕代码索引（CodeIndex）设计的 action 工具集 + AgenticLoop 编排 + Docker/K8s runner**，不是一个端到端产品。

## 二、结论摘要（TL;DR）

1. **最强的部分不是 agent，是 action 库**。仓库里 `moatless/actions/` 下有 40+ 个 action 文件，覆盖
   `find_class` / `find_function` / `find_code_snippet` / `semantic_search` / `grep_tool` / `glob` / `list_files` /
   `read_file` / `string_replace` / `insert_line` / `create_file` / `append_string` / `think` / `finish` /
   `run_tests` / `bash` / `run_mypy`……**「定位」这一环它做到了工具级别的完备**，这正是我们三阶段流程里最容易做薄的一段。
2. **有 Java/Python 双语言的 AST 级检索**：依赖里明确带 `tree-sitter` 0.24.0 + `tree-sitter-python` + `tree-sitter-java`，
   配合 `moatless/index/`（`code_index.py` / `code_block_index.py` / `code_node.py` / `epic_split.py`）做代码块切分与向量索引。
   这对我们是好消息：**私有仓库若不是 Python，索引层仍有可借鉴的切分思路**。
3. **工程骨架完整到可以拆件复用**：`runtime/`（local / testbed 两种执行环境）、`flow/`（`loop.py` 普通循环、`search_tree.py` 树搜索、`trajectory_tree.py` 轨迹树）、`agent/`（`agent.py` + `manager.py` + `agents.json` 预设）、
   `api` + Redis 事件总线 + Docker runner（docker.md 记载 `make run` 起 Redis + API server）。
   **「轨迹可回放 + 可做树搜索」是它相对其他项目的独特资产。**
4. **效果数字**：SWE-bench 上 Claude 4 Sonnet **70.8% solve rate，$0.63/instance**（README 首屏）。
   这是本轮调研三个项目里**单实例成本最低、解决率最高**的一组数字，但它绑的是 Claude 4 + Voyage 向量索引。
5. **对我们的最大风险**：语义检索默认走 Voyage AI embedding（`llama-index-embeddings-voyageai` + `voyageai`），
   SWE-bench 评测甚至需要预构建的 vector store（README：「需要 Voyage AI API key 来使用预嵌入向量库」）。
   **国内网络 + 私有仓库场景下这条链路要换掉**（换 OpenAI-compatible embedding 或退化成 grep/AST 检索）。
6. **落地建议**：不要整包嵌。**抄 action 分层 + runtime 抽象 + 轨迹 JSON 结构，索引层自己实现。**

## 三、技术路线与核心机制

### 3.1 分层结构（读自源码目录）

| 层 | 目录 / 文件 | 职责 |
|---|---|---|
| Action 层 | `moatless/actions/*.py`（40+ 个） | 原子操作：检索、读、改、跑测试、思考、终止 |
| Agent 层 | `agent/agent.py`、`agent/manager.py`、`agent/agents.json`、`agent/events.py` | `ActionAgent` 主循环、多 agent 管理、预设配置、事件流 |
| Flow 层 | `flow/flow.py`、`flow/loop.py`、`flow/search_tree.py`、`flow/trajectory_tree.py`、`flow/oneoff.py`、`flow/run_flow.py` | 编排策略：线性循环 / 树搜索 / 一次性任务；含**轨迹树** |
| Index 层 | `index/code_index.py`、`code_block_index.py`、`code_node.py`、`embed_model.py`、`epic_split.py`、`simple_faiss.py`、`types.py` | 代码结构解析（tree-sitter）、代码块切分、向量化、FAISS 检索 |
| Runtime 层 | `runtime/runtime.py`、`runtime/local.py`、`runtime/testbed.py` | 执行环境抽象：本地目录 / SWE-bench testbed 容器 |
| 服务层 | `docker-compose.yml`、Redis、FastAPI + uvicorn、`kubernetes` / `boto3` / `azure-storage-blob` | API server、事件总线、K8s runner、云端产物存储 |
| 可观测 | OpenTelemetry（sdk / api / otlp exporter / fastapi instrumentation）+ SQLAlchemy + psycopg2 | 链路追踪落到 DB |

> 值得注意：**它已经把「可观测 + 可恢复」当一等公民**（OTel + Redis 事件 + 轨迹树）。这正是「取证」能力的地基。

### 3.2 编排模型

`Flow` 抽象出「怎么跑」，`Action` 抽象出「能做什么」，`Runtime` 抽象出「在哪跑」。三者正交，所以同一套 action 可以在 `loop`（线性）和 `search_tree`（MCTS/树搜索）下复用。
README 里提到的 SWE-Search 论文实现被单独拆到 `moatless-tree-search` 仓库 —— **说明树搜索是「实验性上层」，主仓库保持简洁循环**。这个拆分方式本身值得学：**花活放插件，主干保持可读**。

### 3.3 四种官方 flow（用于 SWE-bench 评测）

| Flow ID | 调用格式 | 适用 | 默认模型 |
|---|---|---|---|
| `swebench_tools` | Function calling | 原生支持函数调用的模型 | `gpt-4o-mini-2024-07-18` |
| `swebench_tools_and_reasoning` | Function calling | 支持函数调用的**推理模型** | `claude-sonnet-4-20250514` |
| `swebench_react` | ReACT（文本解析） | **无原生函数调用**的开源模型 | `openrouter/mistralai/devstral-small` |
| `swebench_react_reasoning` | ReACT | 无函数调用的推理模型 | `openrouter/deepseek/deepseek-r1-0528` |

> 关键情报：**官方 flow 表里就有 `openrouter/deepseek/deepseek-r1-0528`**。也就是说 DeepSeek 走的是「ReACT + 文本解析」这条路，
> 不是因为 DeepSeek 不能调工具，而是它的工具调用格式在这些 flow 里被归到 ReACT 一类。我们接 DeepSeek 时**要重点验证 function calling 分支是否稳定**。

## 四、三环节拆解：定位 / 修复 / 验证

### 4.1 定位（检索）

三种检索入口并存，按成本从低到高：

1. **结构化精确查**：`find_class`、`find_function`、`find_code_snippet` —— 按符号名直取，依赖 tree-sitter 解析出的代码节点。
2. **正则/文件级**：`grep_tool`、`glob`、`list_files` —— 廉价、无索引依赖，模型拿来自救。
3. **语义检索**：`semantic_search` + `index/simple_faiss.py` —— 向量化后按相似度取代码块。

### 4.2 修复（编辑）

它**不做整文件重写**，而是提供一组细粒度编辑 action：`string_replace`（字符串替换）、`insert_line`（插行）、`append_string`、`create_file`、`create_python_file`。
还有专门的 `claude_text_editor` —— 说明针对不同模型的编辑格式做过适配。**细粒度 diff 式编辑 = 更小的 token 成本 + 更好审阅**，这点和我们的「人工审核」诉求完全对齐。

### 4.3 验证（执行 + 反馈）

`run_tests`、`run_maven_tests`（Java 专用）、`run_python_code`、`run_python_script`、`run_mypy`（静态检查）、`bash`。
验证不是「最后一步」，而是**可被 agent 在循环里随时调用的 action** —— agent 自己决定什么时候跑测试。这与 repo-bug-hunter 的「harness 强制 test-first」是相反的哲学（见 [05 篇](./05-repo-bug-hunter调研.md)）。

### 4.4 上下文管理

`cleanup_context`（上下文清理）、`think`（无副作用思考步）、`finish` / `respond` / `reject`（终止语义区分）。
**`cleanup_context` 是长任务的关键**：上下文长度是 agent 失败的主要来源之一，它把这个动作显式暴露给模型。

## 五、安装与运行

### 5.1 Docker（推荐，含 Web UI）

```bash
git clone https://github.com/aorwall/moatless-tools.git
cd moatless-tools
cp .env.example .env          # 填 API key
# 必填：MOATLESS_DIR=/path/to/your/moatless/data
#       —— 存配置文件与轨迹数据，会作为 volume 挂进容器
make run                      # 起 Redis（事件总线）+ API server（Docker runner）
# 访问 http://localhost
```

源码覆盖配置（docker.md）：
- API 容器内 moatless 代码位置 `/app/moatless`，由 `MOATLESS_SOURCE_DIR` 控制（默认 `./moatless`）；
- 任务容器内可选挂载到 `/opt/moatless`，由 `MOATLESS_RUNNER_MOUNT_SOURCE_DIR` / `MOATLESS_HOST_RUNNER_SOURCE_DIR` 控制。

### 5.2 PyPI

```bash
pip install moatless                  # 基础包
pip install "moatless[kubernetes]"    # + K8s runner
```

### 5.3 源码（uv）

```bash
git clone https://github.com/aorwall/moatless-tools.git && cd moatless-tools
uv sync
```

### 5.4 冒烟测试：单实例跑通

```bash
uv run python scripts/docker_run.py \
  --flow swebench_tools \
  --model-id gpt-4o-mini-2024-07-18 \
  --instance-id django__django-11099 \
  --evaluation-name testing_setup
# 结果落在 .moatless/projects/testing_setup
```

### 5.5 批量评测

```bash
python3 scripts/run_evaluation.py \
  --model-id claude-3-5-sonnet-20241022 \
  --flow swebench_tools_and_reasoning \
  --dataset-split verified_mini \
  --num-parallel-jobs 5
```

- 只换模型名、保留 flow 的其余模型设置：`--litellm-model-name openrouter/qwen/qwen-2.5-coder-32b-instruct`
- 数据集切分：`lite` 300 / `verified` 500 / `verified_mini` 50 / `lite_and_verified_solvable` 84

## 六、模型支持

- **抽象层**：`litellm` + 官方 `openai` / `anthropic` SDK —— 所以**任何 LiteLLM 能连的端点都能连**（含自建网关、vLLM、Ollama）。
- **DeepSeek**：官方 flow 表里直接给了 `openrouter/deepseek/deepseek-r1-0528`（`swebench_react_reasoning`）。
  > 02 文档记录的 `deepseek-reasoner` 50%（verified_mini）、`deepseek-chat` 36% 这两组数字**本轮未复核**（来自上一轮搜索，未见官方表格原文），引用时标注来源。
- **Embedding**：`llama-index-embeddings-openai` + `llama-index-embeddings-voyageai`，向量库 `faiss-cpu`。
  **换成本地 embedding（如 bge / m3e）是必须做的一步**，否则私有仓库 + 内网环境跑不起来。
- Python 版本约束：`>=3.10, <3.14`（pyproject.toml）。

## 七、成本与效果

| 指标 | 数值 | 来源 |
|---|---|---|
| SWE-bench 解决率 | **70.8%**（Claude 4 Sonnet） | README 首屏 |
| 单实例成本 | **$0.63**（Claude 4 Sonnet） | README 首屏 |
| DeepSeek-R1（0528）flow | 存在官方配置，未给数字 | README flows 表 |
| 每实例耗时 | 文档未说明 | — |

> 对比：repo-bug-hunter 的卖点是**免费模型也能跑**，AutoCodeRover 是 **46.2% / $0.7**。Moatless 的 $0.63 是在 Claude 4 上达成的，
> **换 DeepSeek 后成本会显著下降，但解决率也会降**——这条曲线需要我们自己测（见 05 篇的方法论：成对比较 + McNemar）。
H:应该是之前版本的ds。
## 八、沙箱与隔离

- `runtime/local.py`：直接在本机目录跑（开发/单测用，**无隔离**）。
- `runtime/testbed.py`：SWE-bench testbed 容器内跑（有隔离）。
- Docker runner：每个任务一个容器，`MOATLESS_RUNNER_*` 系列环境变量控制镜像与挂载。
- K8s runner：`kubernetes` / `kubernetes-asyncio` 依赖，`pip install "moatless[kubernetes]"` 开启。
- **注意**：README 的 docker 说明是「跑 Moatless API 本身」，**没把它描述成安全沙箱**。真正隔离靠 runner 的容器，不是安全加固过的 jail。

## 九、局限与已知坑

1. **自称 hobby project，API 不稳定**：`moatless/__init__.py` 版本 0.1.1，40+ action 文件说明迭代快、破坏性变更概率高。
2. **Voyage AI 依赖是硬门槛**：SWE-bench 评测要预嵌入 vector store + Voyage key。国内环境基本要改。
3. **索引构建成本未文档化**：大型私有仓库首次建索引的耗时/存储/embedding 费用，README 没写。
4. **文档偏「怎么跑评测」，不偏「怎么修自己的仓库」**：所有示例都是 SWE-bench 实例，**「对自己仓库跑」的路径没有官方文档**（02 文档也指出这点）。
5. **DeepSeek 走 ReACT 文本解析**：文本解析型工具调用在中文/长上下文下更脆弱，需要额外容错（参考 repo-bug-hunter 的「no tool call nudge」）。
6. **依赖很重**：Redis + Postgres(psycopg2) + OTel + FastAPI + K8s + 云存储 SDK 全塞一个包，**只要 action 层的话会牵进一大堆无关依赖**。

## 十、对我们（pi-bugfix-console）可借鉴的具体设计

| 借鉴点 | 具体做法 | 我们落地到哪 |
|---|---|---|
| **Action 分层** | 把定位工具拆成「符号级 / 正则级 / 语义级」三档，暴露给 agent 自选，而不是写死检索流程 | 定位阶段工具集 |
| **细粒度编辑** | `string_replace` / `insert_line` / `append_string` 三件套，禁止整文件重写 | 修复阶段 patch 生成 |
| **`think` 动作** | 无副作用思考步，让模型可以「只想不做」 | 三阶段通用的降噪手段 |
| **`cleanup_context`** | 显式上下文清理动作，控制长任务上下文膨胀 | 长任务稳定性 |
| **`reject` / `finish` 语义分离** | 「放弃」和「完成」是不同终止状态，不是同一个 exit | 取证与统计 |
| **轨迹树 trajectory_tree** | 一次运行记录所有分支，可回放可比较 | **取证阶段的核心数据结构** |
| **`agents.json` 预设** | agent 配置外置成 JSON，不同阶段用不同 action 子集 | 定位/修复/验证三阶段其实就该是三个 agent 预设 |
| **Flow × Runtime 正交** | 编排策略与执行环境解耦，同一 flow 可跑 local/testbed/k8s | 42 机 Docker 环境抽象 |

**明确不要抄的**：Flow 层的树搜索（MCTS）、Redis 事件总线、OTel 全家桶、K8s runner —— 对我们的第一阶段是过度工程。

## 十一、待核实项

- [ ] `deepseek-reasoner` 50% / `deepseek-chat` 36%（verified_mini）数字的原始出处（02 文档转述，未见官方表格）。
- [ ] star 数、最近提交时间（本轮 GitHub API 受限，未取到）。
- [ ] 对我们自己的仓库跑一次的真实耗时与索引成本。
- [ ] function calling 模式接 DeepSeek-V3.x 是否比 ReACT 更稳。
