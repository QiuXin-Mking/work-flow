# AutoCodeRover 深度调研：AST 结构感知检索 + 频谱定位

> 调研方式：GitHub 仓库原文（README.md 全文、`app/` 目录结构 API、论文信息）+ [02 篇](./02-可立即使用的agent-flow.md) 已有横评结论。
> 仓库：https://github.com/AutoCodeRoverSG/auto-code-rover （NUS，MIT）
> 论文：arXiv 2404.05427《AutoCodeRover: Autonomous Program Improvement》，ISSTA 2024，pp. 1592–1604。
> 聚焦问题：它的**结构感知检索**和**频谱定位（SBFL）**能不能补上我们「定位」环节最缺的那块硬能力。

## 一、一句话定位

**把「找代码」从字符串搜索升级成「在 AST 上找方法 / 找类」，并在有测试套件时用 SBFL（频谱故障定位）直接算出可疑方法列表**，再让 LLM 分两阶段（先只读检索、后闭卷写补丁）产出修复。

作者 Yuntong Zhang、Haifeng Ruan、Zhiyu Fan、Abhik Roychoudhury（NUS）。README 里两个第一作者标注了 SonarSource 邮箱，致谢里提到新加坡 MoE Tier 3 资助 —— **学术项目转工程的典型形态：论文方法 + 可运行的 CLI**。

## 二、结论摘要（TL;DR）

1. **两个真正独有的东西**（README 原话）：Code search API **Program Structure Aware**（在 AST 上搜方法/类，而不是文件里做字符串匹配）；
   有测试套件时做 **statistical fault localization**（统计故障定位），修复率再提一档。
2. **三种运行模式**，其中 **`local-issue` 模式对我们是关键**：给一个本地仓库路径 + 一个 issue 文本文件就能跑，
   不需要 GitHub、不需要 SWE-bench。**这是三个候选项目里唯一天然支持「私有仓库 + 内部缺陷单」的**。
3. **两阶段生成**：先只读检索（工具调用循环），再**闭卷**生成补丁（不给工具）。这个设计刻意降低了「改代码时乱查乱改」的概率 —— 对我们「修复阶段不许再扩范围」的诉求是直接印证。
4. **效果**：v20240620 在 **SWE-bench Verified 46.20%**、**SWE-bench 全量 24.89%**、**Lite pass@1 30.67%**（README 更新日志，2024-11-21 / 2024-07 多组）。
   注意 README 里 2024-08-14 那条写的是 **38.40%**（Verified），与后来 46.20% 并存 —— **引用时必须带版本号，否则会互相打脸**。
5. **成本 $0.7/issue**（02 文档记录，本轮 README 未见该数字，属上一轮检索结论）。
6. **对我们最大的风险**：项目**迭代重心已明显转向论文复现**，SWE-bench 评测链要装 `nus-apr/SWE-bench-docker` fork + `yuntongzhang/SWE-bench` fork + conda 环境 + 逐任务 setup，**重型且脆弱**；模型清单也偏老（gpt-4o-2024-08-06 之后没更新，无 DeepSeek 原生项）。
7. **落地建议**：**只借两件东西 —— `app/search/` 的结构化检索 API 设计，和 `app/analysis/` 的 SBFL 思路。** 不整体部署。

## 三、技术路线与核心机制

### 3.1 `app/` 模块结构（读自 GitHub contents API）

| 路径 | 作用（按命名与 README 交叉确认） |
|---|---|
| `app/main.py` | CLI 入口；子命令 `github-issue` / `local-issue` / `swe-bench` |
| `app/inference.py` | 推理主流水线（检索 → 补丁生成 → 验证的驱动） |
| `app/agents/` | agent 循环定义（不同阶段不同 agent） |
| `app/search/` | **结构感知检索 API 实现**（AST 上搜方法/类） |
| `app/analysis/` | **故障定位**（SBFL：从测试通过与失败的频谱算可疑方法） |
| `app/model/` | 各家 LLM 适配（OpenAI / Anthropic / Bedrock / Groq / LiteLLM / Ollama） |
| `app/post_process.py` | 从模型输出里抽补丁、写 `selected_patch.json` |
| `app/task.py` / `app/raw_tasks.py` | 任务定义（GitHub issue / local issue / SWE-bench 三种来源统一表示） |
| `app/config.py` / `conf/example.conf` | 配置（可用 conf 文件一次跑一批任务，`python scripts/run.py conf/example.conf`） |
| `app/manage.py` / `app/result_analysis.py` / `app/task_counter.py` | 结果管理与统计 |
| `app/api/` | 生成补丁后**自动提 PR**（见 README「PR-only branch」说明） |
| `app/data_structures.py` | 代码实体（类 / 方法 / 文件）的数据模型 |

> 观察：**它把「检索」和「定位」拆成两个目录（`search/` vs `analysis/`）**。
> 检索 = 按名字/结构找代码；定位 = 用测试频谱算可疑点。这是两个正交能力，不该揉成一个工具。这个划分值得直接照搬。

### 3.2 三阶段流水线（论文方法 + README 对应）

```
issue 文本
   │
   ├─(可选) SBFL: 跑测试套件 → 通过/失败频谱 → 可疑方法列表  [app/analysis/]
   │
   ▼
阶段1 上下文检索循环（只读工具）
   LLM 反复调用 search API：
     - 结构感知：在类/方法粒度搜（README: search_function / search_method_in_class）
     - 文件级：按路径读片段
   产出：要改的类/方法 + 相关上下文
   │
   ▼
阶段2 补丁生成（闭卷，不再调工具）
   LLM 只看阶段1 收集到的上下文，写出补丁
   │
   ▼
阶段3 补丁验证与精修
   - 在 testbed 里应用补丁
   - 跑 FAIL_TO_PASS 测试 + 回归测试（fork 的 SWE-bench-docker 跑**全部**测试，不只是 FAIL_TO_PASS）
   - 失败则带着报错回到检索/生成
   │
   ▼
selected_patch.json / output/applicable_patch/<task>_<ts>/
```

> 阶段 1 / 阶段 2 的**工具剥夺**是全文最值得抄的一个设计细节：**检索阶段给工具，生成阶段收回工具**。
> 效果是模型不能在「写补丁」时顺手扩大检索范围，也就不能顺手改别的文件。

## 四、三环节拆解：定位 / 修复 / 验证

### 4.1 定位（这是它的护城河）

| 手段 | 说明 | 依赖 |
|---|---|---|
| **结构感知搜索** | 在抽象语法树里按「类 / 方法」单元检索，而不是文件级字符串匹配 | tree-sitter（Python 3.9+/Java 15） |
| **SBFL 频谱定位** | 用测试用例的通过/失败覆盖频谱算可疑方法排名 | **必须有可运行的测试套件** |
| **关键词文件定位** | 类名/文件名直查（较弱，兜底） | 无 |

**代价**：结构感知搜索要**预先为仓库建 AST 索引**（不需要向量，但需要解析）。这对多语言私有仓库是额外工程量。

### 4.2 修复

- 输出是**统一格式的 diff patch**，落到 `selected_patch.json`。
- 阶段 2 闭卷生成 → 模型不能边查边改。
- README 更新日志（2024-07-18）提到新增「**只输出潜在修复位置**」的模式 —— **定位到位置就停，不写补丁**。
  > 这对我们极有价值：**「定位」本身可以是一个独立交付物**，交给人工或另一个 agent 去改。三阶段流程正是这个形态。

### 4.3 验证

- SWE-bench 模式下用 `nus-apr/SWE-bench-docker`（fork 自 `aorwall/SWE-bench-docker`）跑**目标项目里的全部测试**，
  而不只是 `FAIL_TO_PASS`。**跑全量回归 = 防止修一个坏三个。**
- 非 SWE-bench 模式（github-issue / local-issue）**没有内建验证**：只生成补丁。
  > 这是个明确的能力缺口 —— 我们自己仓库跑 `local-issue` 时，「验证」环节必须由我们补上。

## 五、安装与运行

### 5.1 Docker（推荐）

```bash
export OPENAI_KEY=sk-...            # 或 ANTHROPIC_API_KEY / GROQ_API_KEY
docker build -f Dockerfile.minimal -t acr .
docker run -it -e OPENAI_KEY="${OPENAI_KEY:-OPENAI_API_KEY}" acr
```

### 5.2 本地 conda

```bash
conda env create -f environment.yml
conda activate auto-code-rover
```

### 5.3 三种模式的实际命令

**① GitHub issue 模式**（给 clone 链接 + commit + issue 链接）
```bash
cd /opt/auto-code-rover && conda activate auto-code-rover
PYTHONPATH=. python app/main.py github-issue \
  --output-dir output --setup-dir setup \
  --model gpt-4o-2024-05-13 --model-temperature 0.2 \
  --task-id langchain-20453 \
  --clone-link https://github.com/langchain-ai/langchain.git \
  --commit-hash cb6e5e5 \
  --issue-link https://github.com/langchain-ai/langchain/issues/20453
```

**② 本地 issue 模式**（★ 我们最该用的）
```bash
PYTHONPATH=. python app/main.py local-issue \
  --output-dir output \
  --model gpt-4o-2024-05-13 --model-temperature 0.2 \
  --task-id BUG-1234 \
  --local-repo /path/to/your/repo \
  --issue-file /path/to/issue.md
```

**③ SWE-bench 模式**（重）
```bash
# 1. 装 fork 的 SWE-bench-docker（跑回归测试用）
conda activate auto-code-rover
git submodule update --init --recursive
cd SWE-bench-docker && pip install . && cd ..

# 2. 用 yuntongzhang/SWE-bench fork 逐任务 setup
echo django__django-11133 > tasks.txt
cd <SWE-bench-path> && conda activate swe-bench
python harness/run_setup.py --log_dir logs --testbed testbed \
  --result_dir setup_result --subset_file tasks.txt
# → 产出 setup_result/setup_map.json、tasks_map.json

# 3. 跑任务
cd <AutoCodeRover-path> && conda activate auto-code-rover
PYTHONPATH=. python app/main.py swe-bench \
  --model gpt-4o-2024-05-13 \
  --setup-map <SWE-bench>/setup_result/setup_map.json \
  --tasks-map <SWE-bench>/setup_result/tasks_map.json \
  --output-dir output --task django__django-11133
# 批量：--task-list-file tasks.txt
```

**④ 配置文件批量**：`python scripts/run.py conf/example.conf`（字段说明见 `EXPERIMENT.md`）

**产物**：`output/applicable_patch/<task-id>_<yyyy-MM-dd_HH-mm-ss>/`，最终补丁路径写进 `selected_patch.json`。

## 六、模型支持

`--model` 单参数切换。README 列出的**全部**受支持项：

| 厂商 | 模型 | 参数示例 |
|---|---|---|
| OpenAI | gpt-4o-2024-08-06 / 05-13、gpt-4-turbo、gpt-4-0125/1106/0613、gpt-3.5 系列 | `--model gpt-4o-2024-08-06` |
| Anthropic | Claude 3.5 Sonnet v2 / Sonnet、Claude 3 Opus / Sonnet / Haiku | `--model claude-3-5-sonnet-20241022` |
| Meta（本地） | Llama 3 70B / 8B（走 ollama） | `--model llama3:70b` |
| AWS Bedrock | Claude 3.x、Nova Pro / Lite / Micro | `--model bedrock/anthropic.claude-3-opus-20240229-v1:0` |
| LiteLLM | **任意 LiteLLM 支持模型** | `--model litellm-generic-<MODEL_NAME>` |
| Groq | Llama 3 8B / 70B、Llama 2 70B | `--model groq/llama3-70b-8192` |

**关于 DeepSeek**：README 的官方清单里**没有 DeepSeek 条目**。理论路径只有两条：
- `--model litellm-generic-deepseek/deepseek-chat`（LiteLLM 透传，需自配 key/base_url）；
- 走 ollama 之类的 OpenAI-compatible 本地端点。

> 结论：**AutoCodeRover 对 DeepSeek 是「能凑」，不是「支持」**。真要用必须自己验证两条路径。

**ollama 在容器里的坑**（README 明写）：ACR 在容器、ollama 在宿主机时，`docker run` 要加 `--net=host`
或 `--add-host host.docker.internal=host-gateway`，否则连不上。

## 七、成本与效果

| 版本 / 数据集 | 结果 | 来源 |
|---|---|---|
| v20240620 · SWE-bench Verified | **46.20%** | README (2024-11-21) |
| v20240620 · SWE-bench 全量 | **24.89%** | README (2024-11-21) |
| v20240620 · SWE-bench Verified | 38.40% | README (2024-08-14)，**与上文并存，版本口径不同** |
| v20240620 · SWE-bench Lite | **30.67%** pass@1 | README (2024-07-01) |
| 单 issue 成本 | ~$0.7 | 02 文档（上一轮检索结论，本轮未复核） |

> 这三个数字并列出现，正说明**「跑分」在 SWE-bench 上是个版本敏感的事**。
> 我们自己测 DeepSeek 时必须锁死：数据集切分 + 版本 tag + 随机种子 + 判定口径（是否跑全量回归）。

## 八、沙箱与隔离

- Docker 镜像 `Dockerfile.minimal` 一键构建；**但 README 描述的是「运行 ACR 的容器」，不是「隔离被测仓库的沙箱」**。
- 真正的隔离来自 SWE-bench 的 testbed 容器（每任务一个 conda 环境 + 独立目录）。
- `local-issue` 模式 = **直接在本地仓库上操作，无隔离**。跑自己的仓库前必须自己准备 git worktree / 容器。
- 有 `api/` 目录可自动提 PR（README 提到 PR-only branch 流程）—— **意味着它有能力把改动推到远端，权限面要收紧**。

## 九、局限与已知坑

1. **SWE-bench 链路重到离谱**：两个 fork + conda env + 逐任务 setup + 手动 map 文件。**没有一条命令跑完的体验。**
2. **`--model` 清单整体落后**：最新只到 gpt-4o-2024-08-06、Claude 3.5，无 Claude 4、无 DeepSeek、无 Qwen-Coder。
3. **非 SWE-bench 模式没有验证能力**：`local-issue` 只出补丁，跑不跑测试它不管。
4. **依赖测试套件**：SBFL 和回归验证都要求目标仓库「测试可运行」。**测试跑不起来的仓库，两条能力同时失效。**
5. **多语言支持偏学术**：tree-sitter 重点覆盖 Python 3.9+ / Java 15；其他语言（Go/TS）支持偏弱。
6. **迭代停滞迹象**：README 最新更新日志停在 2024-11-21，之后无重大版本。
7. **结果口径混乱**：Verified 上 38.40% 和 46.20% 同源并列，外部引用极易混淆。

## 十、对我们（pi-bugfix-console）可借鉴的具体设计

| 借鉴点 | 具体做法 | 我们落地到哪 |
|---|---|---|
| **`search/` 与 `analysis/` 分离** | 「按结构找代码」和「按测试算可疑点」是两个独立工具，不是一个 | 定位阶段双引擎 |
| **AST 结构感知检索** | 以「类/方法」为最小检索单元，返回符号 + 所在文件 + 行号，不是文件内容 | 定位阶段首选工具 |
| **SBFL 频谱定位** | 有测试就先用：跑全量测试 → 失败用例覆盖到的代码 → 可疑方法排名 | 定位阶段（**有测试时优先级最高**） |
| **检索阶段给工具、生成阶段收工具** | 两阶段硬隔离，防止改代码时扩大范围 | 修复阶段边界控制 |
| **只输出修复位置的模式** | 「定位」可独立交付，不强制一次做到补丁 | 三阶段可拆分交付 |
| **跑全量回归而非只跑 FAIL_TO_PASS** | 修一个坏三个是最大隐患 | 验证阶段硬门槛 |
| **conf 文件批量跑** | 任务清单 + 参数外置成配置文件，比命令行拼参更可复现 | 批量回归 |
| **`--task-id` 贯穿产物目录名** | 产物路径天然带任务标识 + 时间戳，便于取证回溯 | 取证阶段目录规范 |

**明确不要抄的**：双 fork 的 SWE-bench 依赖链、conda environment.yml、`api/` 自动提 PR。

## 十一、对我们最直接的一个动作

写一份 `issue.md` 丢进 `--issue-file`，指向本地仓库跑 `local-issue` 模式 —— 这是**今天就能验证「私有仓库 + 内部缺陷单」可行性**最短的一条路。
缺的验证环节我们补：跑完补丁后自己起 Docker 跑目标仓库测试套件。

## 十二、待核实项

- [ ] `$0.7/issue` 成本的原始出处（02 文档转述）。
- [ ] `--model litellm-generic-deepseek/deepseek-chat` 实际能否跑通（需自配 `DEEPSEEK_API_KEY` / base_url）。
- [ ] `search_function` / `search_method_in_class` 的确切 API 名与签名（README 未直接列出，本轮未见源码）。
- [ ] v20240620 在 Verified 上 38.40% vs 46.20% 的差异原因（是否换了 SBFL/回归口径）。
- [ ] `local-issue` 模式对我们仓库的实际可用性（Python 版本、依赖安装、索引构建）。
