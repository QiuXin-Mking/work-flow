# repo-bug-hunter 深度调研：把「先写失败测试」做成 harness 硬门禁

> 调研方式：GitHub 仓库原文（README.md / IMPLEMENTATION.md 全文、目录树）。
> 仓库：https://github.com/Manishmaurya89/repo-bug-hunter （Python 包名 `repo_bug_hunter`，PyPI 包名 `repo-bug-hunter`）
> 定位：它不是又一个 SWE-bench 刷榜 agent，而是**一个可复现的 A/B 实验装置**——用来回答「让 agent 先写失败测试再修，是否真能提高解决率」。
> 聚焦问题：它的**test-first 硬门禁**、**Docker 无网沙箱**、**成对实验统计**，正好对应我们三阶段流程的「验证」和「取证」两个最弱的环节。

## 一、一句话定位

**给 agent 六个朴素工具（`bash` / `read_file` / `search` / `edit_file` / `write_file` / `submit`），
但用 harness 而不是 prompt 来强制「先复现 bug 再改代码」，并把每次运行变成可回放、可成对比较的实验数据。**

IMPLEMENTATION.md 开篇就交代了动机：**它只测一个想法**——「让 agent 在修之前先写一个失败测试（test-first）」。
所有工程投入都服务于这一个变量能被干净地测量。

## 二、结论摘要（TL;DR）

1. **核心资产是「harness 强制」而非「prompt 请求」**。这是全文最重要的一句话：
   > *"The key design choice is that the **harness enforces** test-first rather than just asking for it in the prompt. That makes it a real, measurable variable."*
   我们做三阶段流程时，如果把「先定位再修」「先跑测试再提交」写进 system prompt，那是**愿望**；写成工具级门禁，才是**约束**。
2. **门禁的具体机制**（`tools.py`，`4.6` 节）：
   - 未登记失败测试前，`edit_file` / `write_file` **拒绝修改源码文件**（测试文件仍可写）；
   - 用 `record_failing_test` 登记复现命令，**退出码非 0 才算复现成功**，源码解锁；
   - 退出码 126/127（命令根本没起来）**不记录**——「跑不起来」不等于「bug 复现了」；
   - 若已通过 `bash` 绕过改了源码，登记会被拒，直到回滚；
   - `submit` 时**重跑该复现命令**，仍然失败就打回一次；
   - **逃生门**：连续 3 次登记失败自动解锁源码；第二次 submit 无条件接受。
3. **Docker 沙箱是硬隔离**：直接用**任务自己的 SWE-bench 镜像**（`docker run -d --rm --platform linux/amd64 ... sleep infinity`），
   `--network none` 断网（防止 `pip install` 和上网查答案），超时在**容器内**跑（`timeout -k 5`），真正杀死挂死进程。
4. **A/B 统计做得比大多数论文仓库都规范**：固定 `--seed` 抽样、逐任务配对、**McNemar 精确检验 + bootstrap 置信区间**、
   `run` 拒绝复用同名但配置不同的 run（`config.json` 校验），`--redo` / 断点续跑 / 日限额停止。
5. **零门槛起飞**：`setup` → `doctor` → `smoke` 三步，**默认用 OpenRouter 免费模型**；没 Docker 还能 `smoke --local`。
   **这是三个候选项目里唯一「半小时内能看到 agent 跑完一个真实 bug」的。**
6. **失败分类是显式设计**：`analyze.py` 给每个失败任务**只贴一个原因**——gave up / wrong file / broke other tests / ……。
   **这是取证能力的模板**：不是「失败了」，而是「因为哪一类原因失败」。
7. **对我们最大的风险**：它是**单点实验工具，不是产品**。没有 UI、没有多仓库管理、没有 issue 系统对接、没有权限模型。
   集成方式是「抄它的门禁与实验设计」，不是「部署它当我们的引擎」。

## 三、技术路线与核心机制

### 3.1 模块结构（IMPLEMENTATION.md 记录）

| 模块 | 职责 |
|---|---|
| `repo_bug_hunter/tasks.py` | 从 HuggingFace 载入 `SWE-bench/SWE-bench_Verified` test split；`select(tasks, n, seed)` 按 ID 排序后**种子随机抽样**；`patch_files()` 记录 gold 修复改了哪些文件 |
| `repo_bug_hunter/env.py` | `DockerEnv`（任务镜像 + `--network none` + 容器内 timeout）与 `LocalEnv`（本地目录，仅测试与 `smoke --local`） |
| `repo_bug_hunter/tools.py` | 六个工具 + **test-first gate** + patch 提取 |
| `repo_bug_hunter/agent.py` | 主循环：发对话 → 执行模型要的工具 → 重复，直到 submit 或撞到 step/cost 上限；Claude 走 prompt caching + adaptive |
| `repo_bug_hunter/run.py` | 批量执行器：预检查、并行线程、run 目录管理、断点续跑、日限额 |
| `repo_bug_hunter/evaluate.py` | 调官方 `python -m swebench.harness.run_evaluation` 判分 |
| `repo_bug_hunter/analyze.py` | resolve rate / cost / steps / 失败分类 / A/B 统计 → `results.md` |
| `repo_bug_hunter/viewer.py` | 生成**静态回放站点**（`index.html` + JSON）→ GitHub Pages |
| `repo_bug_hunter/pr.py` | 从**已合并的 GitHub PR** 构造新任务（避开数据污染） |

### 3.2 流水线（每个阶段落盘，可独立重跑）

```
run.py ──► runs/<name>/trajs/<id>.json   （每一步：thinking / text / tool calls / outputs / tokens / cost）
       └─► runs/<name>/preds.jsonl       （每任务一个 patch，SWE-bench harness 格式）
                    │
                    ▼
        evaluate.py ──► 官方 SWE-bench harness ──► runs/<name>/eval.json
                    │
                    ├─► analyze.py ──► results.md（解决率 / 成本 / 步数 / 失败分类 / A/B 统计）
                    └─► viewer.py  ──► 静态站点（可回放）
```

> **「每阶段读文件、写文件」是它能做取证的根本原因**：轨迹是文件，不是内存对象；站点是静态文件，不是服务。
> 我们做取证时同样应该**让每一步产出物落到磁盘上的固定命名文件**。

### 3.3 run 目录的防串味设计（值得直接抄）

- `run.py` 写 `runs/<name>/config.json`，含 provider、model、context window、variant、effort；
- **拒绝复用同名但 variant / provider / model / effort 不同的 run** —— 从机制上杜绝「两份配置结果混在一起」；
- 未完成任务（`env_error` / `api_error`）**不写进 `preds.jsonl`**，避免半个 patch 被误判分；
- 遇到 `DailyLimitReached` 停止排队，**后续任务不启动**，恢复时接着跑；
- `--print-instances` 只打印待跑任务 JSON 就退出（**不需要 key 和 Docker**），GitHub Actions 用它规划矩阵 job。

## 四、三环节拆解：定位 / 修复 / 验证

### 4.1 定位（这项目里最薄的一环，恰好反证了它的取舍）

只有两把镊子：`read_file`（带行号，单次最多 400 行）+ `search`（`grep -rnIE`，跳过 `.git`，最多 100 条命中）。
**没有 AST 检索，没有向量索引，没有 SBFL。**

这不是缺陷，是**控制变量**：作者要测的是「test-first 有没有用」，定位能力强弱会污染这个变量。
> **对我们的启示**：定位能力（AST / SBFL / 向量）要单独评估，「它有没有提升解决率」和「test-first 有没有提升解决率」是两个正交实验，别混着测。

### 4.2 修复

- `edit_file`：**替换唯一匹配的精确片段**；片段不存在或出现多次都**拒绝执行**，改完回显改动行。这是安全编辑的最小正确实现。
- `write_file`：创建/覆盖，自动建目录。
- **patch 构建**：任务开始时 `git stash create` 记下「base」状态 + 已存在未跟踪文件 →
  **镜像里预置的改动天然不会进 patch**；测试文件与复现脚本（`test_*`、`*_test.py`、`repro*`、`tests/` 下）**一律排除**，因为 grader 用自己的测试。
- **踩过的坑（写进 section 6）**：写完 `.py` 后必须删对应 `__pycache__/*.pyc`，否则 Python 会跑旧字节码。
  > 这种「工具层细碎但致命的坑」正是我们自研时最容易漏的。

### 4.3 验证（★ 这是它最值钱的部分）

三层验证，层层加码：

| 层 | 机制 | 强度 |
|---|---|---|
| ① 复现（Red） | 源码锁定，必须先登记一个当前代码下失败的测试命令 | **harness 强制** |
| ② 提交前复检（Green） | `submit` 时重跑复现命令，仍失败则打回一次 | harness 强制 |
| ③ 官方判分 | `FAIL_TO_PASS` 全过 **且** `PASS_TO_PASS` 不回归，才算 resolved；空 patch / patch 打不上 / 测试超时都能识别 | 官方 harness |

判分细节：`evaluate.py` 读每任务 `report.json`，记录是否 resolved、哪些 `FAIL_TO_PASS` 通过、`PASS_TO_PASS` 是否被破坏。
`--collect-only` 可只重读日志不重判；**若所有 patch 都是空（比如全卡在日限额），干脆不启动 grader**。

## 五、安装与运行

### 5.1 前置

1. 一个模型 + API key（**免费 OpenRouter key 就够**），`repo-bug-hunter setup` 交互式保存，**文件仅本人可读**；
2. Docker 运行中，**约 30 GB 空闲磁盘**（单任务镜像几 GB）。Apple Silicon 要在 Docker Desktop 打开 Rosetta（镜像是 x86-64）；
3. 平台：macOS / Linux；**Windows 用 WSL2（`wsl --install`）或 Codespaces**。

### 5.2 三步起飞

```bash
repo-bug-hunter setup      # 选模型 + 贴 key（一次）
repo-bug-hunter doctor     # 体检：key、Docker、磁盘，并告诉你该修什么
repo-bug-hunter smoke      # 端到端修一个玩具 bug，几分钟
# 没有 Docker：repo-bug-hunter smoke --local   （临时目录跑玩具 bug）
```

### 5.3 真实 bug

```bash
repo-bug-hunter run --name pilot --n 5 --difficulty "<15 min fix"
repo-bug-hunter evaluate runs/pilot     # 官方 SWE-bench harness 判分
repo-bug-hunter analyze runs/pilot      # 解决率 / 成本 / 步数 / 失败原因
repo-bug-hunter viewer  runs/pilot      # 生成 ./site 回放站
python3 -m http.server -d site 8000     # http://localhost:8000
```

### 5.4 A/B 实验（test-first 变量）

```bash
repo-bug-hunter run --name baseline   --variant baseline   --n 50
repo-bug-hunter run --name test_first --variant test_first --n 50
repo-bug-hunter evaluate runs/baseline && repo-bug-hunter evaluate runs/test_first
repo-bug-hunter analyze runs/baseline runs/test_first --out results.md
```

> **两个变体之间只有门禁不同**：任务、模型、effort、限额、基础 prompt 全部一致。

### 5.5 零安装路径

- **GitHub Actions**：fork 仓库 → Actions 里启用 → Secrets 加 `OPENROUTER_API_KEY` → Pages 源设为 GitHub Actions。
  每个任务一个 job 跑 agent + 判分，最后 `repo-bug-hunter merge` 汇总、出报告、发站。
- **Codespaces**：点徽章起环境，命令前加 `uv run`（如 `uv run repo-bug-hunter doctor`）。

### 5.6 从已合并 PR 造新任务（避开数据污染）

```bash
repo-bug-hunter pr more-itertools/more-itertools#1305 --name fresh
```

## 六、模型支持

| Provider | 选择方式 | API key |
|---|---|---|
| Anthropic | `--model claude-sonnet-5-5` | `ANTHROPIC_API_KEY` |
| OpenRouter | `--model vendor/model` | `OPENROUTER_API_KEY`（**默认走免费模型**） |
| Ollama（本地） | `--provider ollama --model NAME` | 无需 |
| 其他 OpenAI-compatible | `--base-url URL --model NAME` | `LLM_API_KEY` |

- **`--base-url` 这条就是 DeepSeek 的接入路径**：DeepSeek 提供 OpenAI 兼容接口，`--base-url https://api.deepseek.com --model deepseek-chat` + `LLM_API_KEY`。
  **这是三个候选项目里对「任意 OpenAI 兼容端点」最干净的支持方式**（不需要改代码，不需要 LiteLLM 透传）。
- 优先级：命令行 `--model` > 环境变量 key > `setup` 保存的默认值。
- **本地模型硬要求**：必须支持 tool calling，**上下文 ≥ 32k**。Ollama 默认 4k，要先造变体：
  ```bash
  printf 'FROM gemma4\nPARAMETER num_ctx 32768\n' > Modelfile && ollama create gemma4-32k -f Modelfile
  ```

## 七、成本与效果

- **本项目没有公布「我比 SOTA 高多少」的跑分**——它公布的是**实验装置**。IMPLEMENTATION.md 状态（30 September 2026）：
  整条流水线建成、**56 个测试全过**、两个变体各在**一个真实 SWE-bench 任务**上用**免费本地模型**端到端跑通。
  > 换句话说：**实验规模极小（n=1 级别的端到端验证），别指望拿它的数字当基线。**
- 成本是**逐任务记录**的（`trajs/<id>.json` 里有 tokens 和 cost），`analyze` 汇总 —— **它把成本当一等指标，而不是副产品**。
- **重要提醒**：SWE-bench Verified 是公开集，强模型可能见过真修复。**OpenAI 因污染问题于 2026 年 2 月起停止报告该集。**
  作者自己的对策：成对比较受影响较小 + `repo-bug-hunter pr` 用**新鲜 bug** 测。

## 八、沙箱与隔离（三个项目里最认真的）

- **镜像即判分镜像**：直接用任务自己的 SWE-bench 镜像，所以「agent 说测试过了」和「grader 说过了」是同一套环境，**消除了环境不一致导致的假绿**。
- **`--network none`**：彻底断网，agent 不能 `pip install`，也不能上网搜到真修复。
- **容器内超时**：`timeout -k 5 <seconds>` 在容器里执行，挂死的测试会被**真正杀死**，不是「客户端放弃等待」。
- **资源上限**：限制输出长度、文件读取行数、进程数，防止失控命令拖垮主机。
- **`LocalEnv` 的凭证保护**：名字像凭证的环境变量（含 `KEY` / `TOKEN` / `SECRET`）会被过滤掉，不传进本地执行环境。
- `--platform` 区分：SWE-bench 镜像 `linux/amd64`，smoke 用的 `python:3.12` 用原生。

## 九、局限与已知坑

1. **规模极小**：n=1 级别的端到端验证 + 56 个单测，**没有真实的 A/B 结论数字**。别把「装置完备」误读成「结论有力」。
2. **只支持 Python + pytest**：`repo-bug-hunter pr` 明确要求「Python 项目、用 pytest 测、PR 必须写明它修的 issue」。
3. **绑定 SWE-bench 镜像体系**：跑自己的私有仓库需要自己提供 Docker 镜像 + 任务定义，**「私有仓库怎么接」没有文档**（和另两个项目一个毛病）。
4. **Windows 原生不可用**：必须 WSL2。
5. **磁盘胃口大**：30 GB 起。
6. **自承的 bug 坑**（section 6 记录，非常真实）：
   - **跑不起来的命令被误判成「复现成功」**（Mac 上没有 `python`，退出码被当失败）→ 修法：126/127 白名单排除；
   - **stale `__pycache__` 让 Python 跑旧代码** → 修法：写完 `.py` 删对应 pyc；
   - **`search` 失败（坏正则/路径不存在）被当成「没命中」** → 修法：显式标记为 error 反馈给模型。
   > 这三条**每一条我们都会踩**，直接抄修法。
7. **单点设计**：没有 issue 系统对接、没有多仓库、没有权限与审批 —— 它是实验器，不是产品。

## 十、对我们（pi-bugfix-console）可借鉴的具体设计

| 借鉴点 | 具体做法 | 我们落地到哪 |
|---|---|---|
| **harness 强制而非 prompt 请求** | 关键规则下沉到工具层（锁定/拒绝），不是写进 system prompt | **全流程的哲学** |
| **test-first 门禁** | 未登记失败测试 → 源码只读；`submit` 前复检；连续失败有逃生门 | 验证阶段硬门槛 |
| **退出码白名单** | 126/127 不算复现；超时不算复现；`bash` 绕过改动需回滚才能登记 | 验证判定的正确性 |
| **`edit_file` 唯一匹配** | 片段缺失或多次匹配都拒绝，改后回显 | 修复阶段安全编辑 |
| **patch 排除测试文件** | `test_*` / `tests/` / `repro*` 不进 patch，因为 grader 用自己的测试 | patch 生成规范 |
| **`git stash create` 定 base** | 镜像预置改动天然不入 patch | patch 干净性 |
| **每阶段落盘 + 可独立重跑** | 轨迹 JSON、preds.jsonl、eval.json、results.md、静态站 | **取证阶段的数据契约** |
| **run config 防串味** | 同名 run 配置不符直接拒绝 | 实验可信度 |
| **失败单因归类** | 每个失败任务只贴一个原因（gave up / wrong file / broke other tests） | 取证阶段的失败分析 |
| **McNemar + bootstrap** | 成对比较 + 精确检验 + 置信区间 | 我们自己评估 DeepSeek vs 其他模型 |
| **固定 seed 抽样** | `--n` + `--seed` 恒定 → A/B 跑在完全相同的任务集上 | 实验可复现 |
| **`pr` 造新鲜任务** | 从已合并 PR 反推 bug，避开训练污染 | 长期评测集维护 |
| **错峰预热 `--print-instances`** | 不需要 key/Docker 就能规划任务 | CI 编排 |
| **`doctor` / `smoke` 两步自检** | 先体检再冒烟，把环境问题挡在实验之外 | 我们的环境自检 |

**明确不要抄的**：SWE-bench 镜像依赖（我们有自己的私有仓库）、GitHub Actions 矩阵编排、`viewer.py` 静态站（我们自己有前端）。

## 十一、三个项目横向对比（本轮新增结论）

| 维度 | Moatless-Tools | AutoCodeRover | repo-bug-hunter |
|---|---|---|---|
| 定位能力 | ★★★★☆ 结构化+语义双档 | ★★★★☆ AST + SBFL | ★☆☆☆☆ grep + read |
| 修复方式 | 细粒度编辑 action | 闭卷生成 diff | 唯一匹配替换 |
| 验证强度 | 测试可随时调用（无强制） | 跑全量回归 | **harness 强制 test-first** |
| 沙箱 | Docker/K8s runner | testbed 容器 | **同镜像 + 断网 + 容器内超时** |
| 可观测/取证 | 轨迹树 + OTel | 输出目录 | **轨迹 JSON + 静态回放 + 失败分类** |
| 实验方法论 | 有评测脚本 | 有 conf 批量 | **McNemar + bootstrap + 固定 seed** |
| 接 DeepSeek | LiteLLM / ReACT flow | LiteLLM 凑 | **`--base-url` 原生 OpenAI 兼容** |
| 上手成本 | 中（Redis+API） | 高（双 fork conda） | **低（三步起飞）** |
| 定位 | 工具库 + 评测框架 | 学术方法实现 | **实验装置** |

**结论性判断**：

- **定位能力**抄 AutoCodeRover 的 AST/SBFL 划分 + Moatless 的三档检索；
- **修复安全边界**抄 repo-bug-hunter 的唯一匹配替换 + 测试文件排除；
- **验证与取证**整体抄 repo-bug-hunter（harness 强制 + 落盘流水线 + 失败单因归类 + 成对统计）；
- **别整包嵌任何一个**。三者都有「绑定 SWE-bench / 不懂私有仓库」的共同短板，而这正是我们要填的坑。

## 十二、待核实项

- [ ] `--base-url https://api.deepseek.com --model deepseek-chat` 实测是否通过 `doctor` 的 tool-calling 检查。
- [ ] 56 个测试的实际内容与覆盖（`tests/test_tools.py` 里有门禁锁定/拒绝/解锁用例，`tests/test_pr.py` 有 PR 造任务用例）。
- [ ] A/B 在两个变体上跑 n=50 的实际耗时与成本（免费模型 vs 付费模型）。
- [ ] 对我们私有仓库，如何提供一个「自带测试的 Docker 镜像」并接入它的 task 定义。
- [ ] star 数 / 许可证 / 最近提交（本轮 GitHub 元数据未取到）。
