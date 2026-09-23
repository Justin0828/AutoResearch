# AI Research Partner — 系统设计 v1.2（模块级）

> v0 由 `CLARIFICATION.md` 转换而来，只做模块划分。v1 锁定了运行环境约束、首个研究方向 profile 与若干悬空的架构决定。v1.1 加入了讨论/验证双模式（0.2）与网络分流。v1.2 在 §5 定下 Phase 1 的 State schema、Briefing/Handoff 契约、Task 契约与增量提交协议；v1.3 在 §5.5–5.8 定下 Phase 2 的 Paper 与证据出处、评判类任务、模式与交棒、Paper Request Queue 四个契约；v1.4 在 §5.9–5.14 定下 Phase 2.5（自演进）的契约（**草案，待用户确认**）。其余模块仍不涉及 API 签名和代码结构。

---

## 0. 设计出发点

五条决定整个架构形状的判断。所有模块划分都从这里推出。

### 0.1 系统的中心不是 Agent，而是 Research State

需求 §8 要求“换掉一个 agent session 后，research 不能失忆”。这意味着 Claude Code session 必须被当作**无状态的、可随时丢弃的 worker**：

```text
持久的 Research State  ──briefing──▶  临时 Agent Session  ──状态变更──▶  持久的 Research State
        （系统的本体）                    （可丢弃的执行器）
```

任何一次 agent 执行，输入是从 State 装配出来的 briefing，输出必须回写成 State 里的结构化对象。**agent session 的上下文永远不是真相来源。**

### 0.2 研究由两种模式交替构成，模式切换权在人

系统的常态**不是** AI 自主驱动。早期探索必须由人主导，AI 作对话伙伴；只有人认为讨论已经充分，才交棒让 AI 去自主验证。

| 模式 | 谁主导 | loop engine 的权限 | 产出 |
|---|---|---|---|
| **讨论模式** | 人 | 不得自主发起验证、实验或方向变更；仅允许为当下讨论做低成本文献查证 | 想法、`Assumption`、候选 `Hypothesis` / `Question` / `Uncertainty` |
| **自演进模式** | AI（你不在场时） | **关闭检索**，从冻结的基本盘做长链推演；不得发起实验或方向变更 | `Idea`、新 `Assumption` |
| **验证模式** | AI | 按 M2 的 next-action 选择自主推进 | `Evidence`、实验结果、信念更新 |

模式间由人显式**交棒（Handoff）**切换。典型流转：

```text
讨论模式（人主导，搜索开）
  → 问题达到 formalized，基本盘冻结
  → 自演进模式（AI 主导，搜索关）产出 Idea
  → 接地任务（搜索开，对抗性，只标注不改写）
  → 候选区 → 人判断
  → 回到讨论模式，或交棒进验证模式
```

验证结果回流后通常应回到讨论模式重新解释，而不是让 AI 自己闭环。需求 §1 里“分析结果 → 修正理解 → 产生新的 hypothesis”这一段，天然属于讨论模式。

推论：**前端对话不是附属功能，而是系统的第一入口**，必须早于自主验证能力交付（见 §7 排期）。

### 0.3 State 是一个 git 仓库，agent 直接编辑文件

产出必须结构化落库，否则几个月后 State 会退化成一堆读不动的日志。但**不应该要求 agent 返回 JSON blob**——那是在跟 Claude Code 的长处对着干，它最强的能力就是文件读写。

因此：State 是一个独立 git 仓库，内容是带 YAML frontmatter 的 markdown，agent 用原生文件工具编辑，`git diff` 就是 delta。这顺带免费解决了三件事：

| 需求 | 免费获得 |
|---|---|
| 状态演化历史（“理解是怎么一步步变的”） | `git log` |
| 并发冲突处理 | git merge |
| 人直接修改 State（需求 §10） | 用编辑器打开文件就行 |

纯文件的弱点是无法强制约束，由 0.4 补上。

### 0.4 需要校验的状态迁移走 MCP tool，自由内容走文件

自建一个 MCP server 挂给 agent，只暴露**必须校验**的操作——典型如 `transition_hypothesis(id, status, evidence_ids[])`：拿不出 evidence 就直接拒绝，agent 没有绕过的路径。

这回答了「hypothesis 状态变更必须附证据，这条规则在哪里强制执行」：**在 MCP tool 里**，而不是靠 prompt 里的一句要求。

两条从 Phase 0 实测中得到的补充原则：

- **校验类工具做“保证可见”，相关性判断交给 agent**。`check_dead_ends` 最初用词汇重叠过滤，结果英文查询打不中中文 dead-end——而 State 里论文是英文、讨论是中文，这种混合必然发生。静默返回“没找到”比没有这个工具更危险：agent 会理直气壮地重走死路。改为全部列出、由 agent 判断。凡是漏报代价远大于误报的检索，都该这样设计。
- **簿记归工具，不归 agent**。`record_evidence` 最初不回写假设的 `evidence` 字段，agent 只能手动编辑文件补上。凡是可由工具确定性维护的反向引用与索引，都不应该交给 agent 手写。

### 0.5 5 小时工作窗口是系统的基本节拍

Claude Code Max 5x 的 5 小时滚动窗口会强制结束 session。**系统不是一个连续运行的 daemon，而是一串离散的「班次(Shift)」**，且班次可能在任何一个任务的中途被切断。三条推论：

1. **任何任务都必须假设自己会被 SIGKILL** —— 状态边做边提交，禁止“任务结束时统一写回”。
2. **班次边界需要交接记录（handoff）** —— 而这与 0.1 的 briefing 是同一个机制：班次交接就是生成下一次的 briefing。约束因此强化了现有设计，而不是额外负担。
3. **任务粒度按“能在一个班次内闭合”来切** —— 宁可多个小任务各自提交，不要一个跑满整个窗口的大任务。

### 0.6 Autonomy 是策略问题，且策略落在 CLI 的权限旗标上

需求 §13 要的不是全自动或全手动，而是按**代价、可逆性、是否改变研究方向**分级。分级不自己造引擎，直接映射到 Claude Code 已有的能力。

> **Phase 0 实测修正（重要）**：`--allowedTools` 是**免批准白名单**，并**不移除**未列出的工具——smoke test 中 agent 在白名单外照常调用了 `Bash` 与 `ToolSearch`。真正的禁用要靠 `--disallowedTools`（deny-list）或 `--restricted`（移除执行类工具与 WebFetch）。
>
> 这条纠正是实质性的：若按原假设实现，M2.5 的“自动档不给 Bash”与 M11 的“物理上做不到检索”**都会是假的保证**。凡是安全性依赖“某工具不可用”的地方，必须用 deny-list 表达，白名单只用来消除权限弹窗。

### 0.7 不污染宿主机是硬约束，不是加分项

这台服务器是日常在用的机器。系统对宿主机的全部影响必须收敛在一个可整体删除的根目录内。细则见 M10。

---

## 1. 运行环境与既定约束（v1 锁定）

| 项 | 现状 | 设计含义 |
|---|---|---|
| Claude Code | 已装 v2.1.280 | 唯一 executor |
| Codex | **未安装** | 双 agent 交叉检查降级为后置可选能力，不进 v1 |
| 额度 | Max 5x，5h 滚动窗口，到点强制结束 | 见 0.5；额度耗尽时暂停 + 前端通知 |
| GPU | 2× RTX 5090 32GB（Blackwell / sm_120） | 本机可做 sim 实验与中小规模训练；不与人争用 |
| 驱动 / 工具链 | Driver 580.105，CUDA 13.0；**无 nvcc、无 torch** | 环境完全干净，从零建立，正好立规矩 |
| 隔离工具 | `uv` ✓ `conda` ✓ `docker` ✓（用户在 docker 组） | **docker 组等价 root**，不交给 agent；见 M10 |
| 磁盘 | 1.4T 可用，单盘 | embodied 数据集是 TB 级，**必须有磁盘预算管理** |
| 前端访问 | 清华网络受限；已配置 SSH key | 前端只绑 127.0.0.1，经 SSH 端口转发访问，无需鉴权 |
| 文献 | 开放源为主；受限文献人工补 | 需要 Paper Request Queue，见 M4 |
| 网络 | sing-box 1.12 TUN 模式接管全部流量并限速；配置需 root | 数据集下载按 UID 分流绕过代理，见 M10；与 Claude 的交互必须继续走代理 |
| 部署迁移 | 跑通后要部署到公司服务器 | 全系统需可整体迁移；实验需要 compute backend 抽象 |

---

## 2. 首个研究方向 Profile

> 系统的第一个 `ResearchProject`。记录在此是因为它决定了 M4 的检索面和 M6 的实验基座形态。

**方向**：具身智能中的上下层接口划分。上层（如 GPT6 Astra 类模型）提供视觉知识、空间理解与任务拆解，通过 interface 下传；下层是通用 action model，结合自身 observation 输出 action。**不做上层**，聚焦这一设定下的通用 action model。

**当前关注点**：在 interface 已给定的前提下，如何提高 action model 的**精细操作（fine-grained manipulation）**能力。

**开放问题**（研究本身的，非系统的）：interface 应该划分在哪一层。

**对系统的推论**：

1. **文献面**：arXiv 的 cs.RO / cs.LG / cs.CV 覆盖绝大部分；重点会议 CoRL、RSS、ICRA、ICLR、NeurIPS。这个领域 arXiv 覆盖率高，A2 的“开放源优先”策略基本够用。
2. **实验基座**：本方向的实验意味着**仿真环境 + 策略训练**（ManiSkill / LIBERO / RoboCasa / MetaWorld / Isaac 系，以及 Open X-Embodiment / DROID 类数据）。这类依赖极重。
3. **算力分层**：本机 2×5090 适合 sim 评测、消融、中小策略（ACT / diffusion policy 类）训练；基模训练要去公司服务器。M6 必须有 compute backend 抽象。
4. **已知的领域级不确定性**（建议作为初始 `Uncertainty` 写入 State）：精细操作方向的 sim benchmark 与真机表现脱节是公认问题，任何基于纯 sim 的结论都带这个折扣。

**需要提前讲明的风险**：这个方向的早期瓶颈**不是想法，是实验基座**。在 Blackwell GPU 上把一个仿真 benchmark + baseline 策略训练跑到可复现，本身就是数天的工作量。如果不把「建立可复现实验基座」当作一个显式的早期里程碑，系统会长期停留在文献综述阶段，永远拿不出能区分假设的证据。

---

## 3. 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│  M9  Web Frontend（看板 / 干预 / PDF 投喂 / 审批）             │
└───────────────┬──────────────────────────────────────────────┘
                │ API + 事件流（127.0.0.1，经 SSH 隧道访问）
┌───────────────▼──────────────────────────────────────────────┐
│  M8  Observability & Progress                                │
├──────────────────────────────────────────────────────────────┤
│  M7  Human-in-the-loop（干预 / 提问 / 审批 / 解阻）            │
├──────────────────────────────────────────────────────────────┤
│  M2  Research Loop Engine（班次调度 / 下一步做什么）           │
│      └── M2.5 Autonomy & Budget Policy                       │
├──────────┬──────────┬──────────┬────────────────────────────┤
│ M4 文献  │ M5 假设  │ M6 实验  │ M11 想法自演进（搜索关闭）  │
├──────────┴──────────┴──────────┴────────────────────────────┤
│  M3  Agent Execution Layer（Claude Code 适配）                │
├──────────────────────────────────────────────────────────────┤
│  M1  Research State & Memory（git 仓库，唯一真相来源）         │
├──────────────────────────────────────────────────────────────┤
│  M10 Infrastructure（存储、隔离、环境卫生、部署）              │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. 模块清单

| 编号 | 模块 | 一句话职责 | 覆盖需求 |
|---|---|---|---|
| M1 | Research State & Memory | 长期研究状态的唯一真相来源 | §8 §9 §14 |
| M2 | Research Loop Engine | 按班次决定并编排“下一步研究什么” | §1 §4 §13 |
| M3 | Agent Execution Layer | 把任务交给 Claude Code 执行 | §7 |
| M4 | Literature Research | 持续检索、阅读、证据抽取、受限文献补全 | §5 |
| M5 | Hypothesis & Belief | 假设生命周期与信念更新 | §3 §4 §9 |
| M6 | Experiment | 从零建立、运行、调试、分析实验 | §6 |
| M7 | Human-in-the-loop | 人的介入、质疑、改变方向、解阻 | §10 §13 |
| M8 | Observability & Progress | 在做什么 + 是否真的进步了 | §12 §14 §15 |
| M9 | Web Frontend | 长期交互与状态理解入口 | §11 §15 |
| M10 | Infrastructure | 存储、隔离、环境卫生、部署 | 全局 |
| M11 | Idea Incubation | 关闭检索下的长链自我推演，产出直觉层面的新想法 | §1 §3 §13 |

---

## M1. Research State & Memory

**职责**：保存全部结构化研究状态，并能按需装配成任何一次 agent 执行所需的上下文。

### 需要实现的功能

1. **核心对象建模**（文件化，非 schema 定义）
   - `ResearchProject` / `Question`（成熟度：vague → scoped → formalized）
   - `DeadEnd` 的一条约束：**不内嵌实验结果，而是引用关闭它的 `Evidence` / `Experiment`**。Phase 0 中 10 次 trial 有 6 次把 `source` 写成 dead-end id，因为 fixture 里 D001 内嵌了一份内部复现结果而这份结果没有独立的实验记录。结果就是证据的出处链断在 dead-end 上，追不到原始实验。
   - `Idea`：自演进模式的产出——直觉层面的陈述 + 证伪条件 + 推演中新引入的前提清单 + 接地标注 + 与既有假设的关系。不要求工程细节。
   - `Foundation`（基本盘）：某一轮自演进进入时对 State 取的只读快照，作为该轮推演的约束面。
   - `Assumption`：当前被默认为真、整个方向据以成立的**前提**，带 examined / unexamined 标记。与 `Hypothesis` 区分开是为了抓住“整条研究方向建立在一条从没人检查过的前提上”这个失败模式。

     > **Phase 0 实测修正**：原先的定义（“前提” vs “待验证命题”）在实践中分类不稳定——5 次独立 trial 对同一条命题（“感知不是瓶颈”）给出了 3 种分类。原因是这条命题两者都像：它可证伪，同时又正在被用来剪枝而无人验证。
     >
     > **区分依据是当前角色，不是内在属性**：正在被**依赖**（用来剪枝或支撑其他推理）而未安排验证的 → `Assumption`；已**安排验证**的 → `Hypothesis`。同一命题可以先后是二者，`Assumption` 被安排验证时提升为 `Hypothesis` 并保留链接（M5.1b）。分类规则必须写进 agent 的协议提示，否则候选区的分类会随机。
   - `Insight`（理解，已定 2026-09-23）：研究的**产出**而非输入——做了一段研究后形成的看法、直觉、对事物的感觉，可以是描述性的、**不要求可证伪**。需求 §14 把进展定义为“是否理解得更清楚了”，只承认 supported 的 Hypothesis 为结论会把这部分进展丢掉。
     - 与 Assumption 的区别：Assumption 是研究据以成立的前提（输入），Insight 是研究学到的东西（产出）。与 Hypothesis 的区别：不强制证伪条件与验证安排（“什么会让我改观”鼓励写、不强制）。
     - **必须说出根基**（`basis`）：它长自哪些讨论、证据、假设结果、论文。AI 提出的理解只能进候选区，且根基必须指向 State 里真实存在的对象——流畅地产出“像洞见”的话正是 AI 最容易空转的地方（§6），要求说出根基是主要防线。人提出的理解可以用 `basis_note` 写“经验 / 手感”一类 State 之外的来源。
     - 牢固程度 `firmness`：hunch（直觉）/ working（工作理解）/ settled（稳固理解），由人判断。
     - **演变可追溯**：修订不覆盖，新建一条并以 `superseded_by` 链接，旧的保留——“理解是怎么一步步变的”本身就是进展记录。
     - **评判类任务看不到 Insight**：评估证据、决定状态迁移只看证据；理解是解读框架，会带来锚定。讨论类任务把它放在研究问题之后，并标明“是理解，不是证据”。
     - **拿理解做决定要留痕**：一旦某条理解被用来剪枝或排除方向，这个用法按角色规则就是 Assumption——另建一条 Assumption，以 `derived_from` 链接回该理解。直觉本身不需要验证，但“凭感觉排除了 X”必须显式可见。
     - 接入推翻的传播（M5.6b）：根基中的对象被推翻 → 该理解进待重新审视；理解被取代或放弃 → 由它派生的 Assumption 进待重新审视。
   - `Hypothesis`：提出者（`origin`，human / AI）、表述、可证伪条件、状态、置信度

     > **`origin` 的边界（已定）**：`origin` **被记录、被审计、被用于路由，但在做证据判断的那一刻对 agent 不可见**。
     >
     > 理由：若 `origin` 参与可信度判断，会产生**慢动作的谄媚**——人提的假设系统性地活得更久、更容易被支持，不是因为证据而是因为权威，且每次单独判断看起来都合理，无法察觉。它还自我强化：受优待的假设活得久 → 积累更多调查 → 显得更核心 → 更受保护。这直接摧毁系统的价值前提（证据决定信念），把它变成回音壁，违背 §3「双方的 hypothesis 都应被验证」。
     >
     > 另一重理由：Phase 0 发现 H002 的 `origin` 记错了，且直到 agent 用对话记录交叉核对才被发现。**一个既难保持准确、又参与判断的字段，是最糟的组合**。
     >
     > 实现（属 M1.5 briefing 装配规则，非新机器）：
     > - **评判类任务**（评估证据、决定状态迁移、对抗性接地）的 briefing **剥离 `origin`**，并规范化正文避免出现“研究者认为”之类的署名线索。
     > - **策展 / 路由类任务**（该让人审阅什么、议程、进展报告、漂移检测）保留 `origin`。
     > - M8 按 `origin` 分别统计反驳率，作为**偏差指标**：若人提假设的反驳率显著偏低，要么人确实准，要么屏蔽漏了——这是检测该偏差的唯一仪器，也是记录 `origin` 的主要正当用途之一。
     > - 屏蔽不可能完美（git 历史、行文风格都可能泄漏），因此偏差指标是必须的，不是可选的。
     > - **归属冲突**由蒸馏时的对话记录裁定；冲突时标记待人确认，**不自动改写**（Phase 0 中 agent 的实际行为，固化为规则）。
   - `Evidence`：来源（论文 / 实验）、对某假设 support / contradict / neutral、强度
   - `Paper`、`Experiment`、`Decision`（“为什么现在做这个”）、`Uncertainty`、`DeadEnd`
2. **关系与溯源**：任何结论都能回答“凭什么”，一路追到具体证据、实验 run、论文段落。
3. **状态演化历史**：由 git 承载，可回放理解的变化过程。
4. **Negative result 一等公民**：被证伪的假设、无效实验、inconclusive 结果、失败方向长期保留且可检索。
5. **Context Assembly**：给定任务产出 briefing，至少含：当前问题理解、相关假设、相关证据、**相关失败记录与禁止重复事项**、本次目标与预期产出。
6. **Shift Handoff**：班次结束（正常或被截断）时产出交接记录——在做什么、做到哪、下一班从哪继续。与 briefing 共用机制。
7. **增量提交**：状态变更随做随提交（0.5 的硬性要求），禁止攒到任务末尾。
8. **Memory Compaction**：分层摘要（项目级 / 方向级 / 原始记录），保证 briefing 不无限膨胀。
9. **讨论的持久化**：讨论跨 session 也跨班次，因此对话记录本身进 State，与从中蒸馏出的结构化对象分开存放并互相链接。
10. **候选区**：讨论中 agent 提出的候选对象先进候选区，经人确认后才成为正式 State 对象——不是每句话都该变成一个 `Hypothesis`。
11. **检索**：按主题、假设、时间、相关性检索，供装配和前端使用。
12. **写回校验**：状态迁移经 MCP tool 校验；自由内容经事后 schema 检查。

---

## M2. Research Loop Engine

**职责**：系统的驱动器。持续回答“现在最值得做什么”，编排执行，消费结果。

### 需要实现的功能

0. **模式门（Mode Gate）** —— 0.2 的落实处，优先于以下一切逻辑
   - State 持有当前 `mode`（`discussion` / `incubation` / `validation`），人拥有唯一切换权。
   - 讨论模式下，loop engine **不得**自主发起验证任务、实验或方向变更；只放行低成本文献查证。
   - 自演进模式下，**检索在 executor 层被真正移除**（非提示词约束，也非“白名单里不写”）：`--tools` 只给文件读取、MCP 不注册联网工具、deny-list 兜底，且用 `--restricted` 把文件读取关进只含基本盘快照的工作目录（Phase 2.5 spike 实测，§5.9）；不得发起实验或方向变更；详见 M11。
   - **窗口预算分配（已定）**：自演进占 **5h 窗口的 30% 上限**。按 Phase 0 实测（一个 evidence 量级任务约占窗口 1%），30% 约合 30 个任务当量；一轮"短推演 + 接地"约 2 个当量，即**每窗口约 15 轮迭代**，足够 M11.6 的累进循环跑开。
   - **交棒（Handoff）**：人从候选区挑选本轮要验证的 assumption / hypothesis 批次，确认后切到验证模式。交棒记录写入 `Decision`。
   - 验证模式产出重大结果后，主动建议切回讨论模式，而不是自行继续。
   - **夜间预习（已定保留）**：讨论模式下若窗口空闲，agent 去做文献查证，结果在你下次打开前端时已就位。仅限文献，不含实验与方向变更。
     **选题来源必须与人相关**，不能自己随便找事做：
     1. **显式问**：讨论收尾时主动问“今晚去查什么方向”，人可答可不答；
     2. **自行拟定**：人未作答时，从当前进展推——未检验的 assumption、讨论中提到却没查的点、当前最大的 uncertainty；
     3. 无论哪种，**次日简报必须说明选了什么、为什么选它**，让人能纠偏。

0b. **无人值守窗口的优先级阶梯**

   窗口不按固定排班填充，而是取当下价值最高的可做之事。推演循环（M11.6）单独撑不满一个窗口，其余由阶梯补齐：

   | 优先级 | 工作 | 为什么适合无人值守 |
   |---|---|---|
   | 1 | 闭合上一班被截断的任务 | 否则半成品持续堆积 |
   | 2 | 推演 / 接地循环（M11.6） | 主线 |
   | 3 | 未检验 assumption 的文献核查 | 直接喂养下一轮基本盘，是主线的上游 |
   | 4 | 待读文献深读与证据抽取 | 永远有存货，永远不亏 |
   | 5 | 矛盾扫描：已积累证据之间的冲突检测 | 真正的研究活动，且完全不需要人在场 |
   | 6 | 实验推进（Phase 3 后） | 跑实验与调试本就最适合夜间 |
   | 7 | State 整理与摘要压缩（M1.8） | 不需要判断力，却是 briefing 不退化的唯一保障 |
   | 8 | 准备下次讨论的议程 | 你打开前端时，"变了什么、哪些需要你判断"已摆好 |
   | 9 | **停下，交白卷，留额度** | 见下 |

   **额度不是必须花完的预算。** 真正的约束是周上限，不是单个 5h 窗口。若系统每晚都把窗口烧满去做边际价值的工作，代价是你真正想和它一起工作时它没额度了——**把额度留给交互式使用是正当用途**。因此阶梯必须有第 9 级：以上都没有够格的工作时主动收工。这与 M11.9 "允许输出本轮没有值得你看的东西"是同一条原则在调度层的体现。

1. **班次调度（Shift Scheduler）**
   - 开班：装配交接记录，恢复上一班未完成的工作。
   - 运行：在窗口内持续派发任务。
   - 额度耗尽：检测 5h 窗口终止，冻结当前进度，写交接记录，**前端通知**。
   - 恢复：**不需要估算**。stream-json 的 `rate_limit_event` 直接给出 `five_hour.resetsAt`（精确时间戳）、`five_hour.utilization` 与 `seven_day.utilization`。窗口恢复后自动开下一班（默认开启，可关；无论自动与否都通知）。
   - 周额度同源可读，M2.0b 第 9 级“留额度”的判断因此有真实依据，而非拍脑袋。
   - **周额度硬闸（已定）**：`seven_day.utilization` **≥ 95% 时全面暂停**并前端通知“额度用尽”。这条闸保护的是**你在本项目之外的 Claude Code 使用**（其他项目开发与日常问答），因此是全局阈值，不是 autoresearch 的配额。
     - **不对 autoresearch 单独限额**，只记录消耗（百分比或 token）供你查看。
     - 检查时机在**任务启动前**，而非仅在运行中观察。
     - 越线时**允许在飞任务完成提交**再停（§0.5 的增量提交），避免造出半成品——95% 处剩余余量足够。
2. **研究阶段模型**：把需求 §1 的循环显式建模为可迁移阶段，允许跳转而非死板串行。
3. **Next-action 选择**，依据至少包括：
   - 哪个 uncertainty 最关键；
   - 哪个假设最重要且最缺证据；
   - 哪个验证**最能区分不同解释**（需求 §4，优先于单纯性能提升）；
   - 预期信息量 vs 代价；
   - 人设定的优先级与被暂停的方向。
4. **任务定义**：目标、briefing 需求、预期产出对象类型、代价等级、**预计能否在班次内闭合**、成功/失败判据。
5. **任务状态机**：含 `blocked_on_human` 作为一等状态——等待 PDF 投喂、等待审批、等待人回答问题，三种阻塞共用同一机制。阻塞任务挂起，loop 继续做其他不冲突的事。
6. **决策记录**：每次选择写一条 `Decision`，前端“为什么现在做这个”直接读它，而不是事后让模型编解释。
7. **结果消费**：把变更交给 M5 做信念更新，据此调整后续计划。

### M2.5 Autonomy & Budget Policy

1. **动作分级**：
   - **自动**（搜索、阅读、思考、提假设、分析已有结果）：白名单给 Read/Grep/WebFetch + state MCP，**并用 `--disallowedTools` 显式禁掉 Bash / Task / NotebookEdit 等**。跑飞也只能写 State。（禁用必须走 deny-list，见 §0.6 的实测修正。）
   - **通知后执行**（低成本验证、sim 小实验）：加 Bash/Edit，限定在实验 workspace 内。
   - **必须审批**（长训练、装系统级依赖、改研究方向、大数据集下载）：agent 先产出 plan 并停下，批准后才以更宽权限执行。
2. **审批请求**：附带做什么、为什么、预计代价与磁盘/时间占用，推送前端。
3. **预算管理**：额度消耗跟踪与限流；磁盘预算（见 M10）。
4. **护栏**：禁止 sudo / apt / 全局 pip / 修改 shell 配置 / 直接使用 docker（见 M10）。

---

## M3. Agent Execution Layer

**职责**：把抽象任务变成一次真实的 Claude Code 执行，并把输出变回状态变更。

### 需要实现的功能

1. **执行方式**：子进程调用 `claude -p`，`--output-format stream-json --include-partial-messages` 取事件流。
   - 自定 `--session-id` 便于记账；`--resume` / `--fork-session` 用于续跑。
   - `--append-system-prompt` 注入 State 协议；briefing 以文件形式给出而非塞进 prompt。
   - `--mcp-config` 挂 state server；`--allowedTools`（免批准）+ `--disallowedTools`（真禁用）+ `--permission-mode` + `--add-dir` 共同实施 M2.5 分级。二者语义不同，不可混用。
   - **不使用 `--bare`**：它强制走 API key、不读 OAuth，会破坏“复用订阅额度”的前提。
2. **Session 生命周期**：创建、注入、流式收集、超时、中断、清理。session 是一次性的。
3. **中断安全**：随时可能被额度耗尽或人工中断，需保证已完成部分已提交、可恢复。
4. **产出校验**：schema 检查变更文件；不合法时定向重试修复。
5. **执行隔离**：每任务独立工作目录。
6. **执行日志留存**：完整日志归档供排查，但只有结构化结果进 M1。
7. **失败处理**：区分「agent 执行失败」与「研究结论是否定的」——后者是有价值的结果，不能当错误吞掉。
8. **（后置）第二 executor**：Codex 装好后作为交叉检查与能力路由，不进 v1。

---

## M4. Literature Research

**职责**：让文献工作贯穿整个研究周期，而非只在开头（需求 §5）。

### 需要实现的功能

1. **检索**：由当前问题/假设生成查询，覆盖 arXiv 等开放源；按方向 profile 配置检索面（cs.RO / cs.LG / cs.CV，CoRL / RSS / ICRA 等）。
2. **筛选与去重**：相关性判断，与已读文献去重。
3. **阅读与笔记**：抽取问题、方法、结论、实验设置、局限。
4. **面向假设的证据抽取**：明确回答“这篇对我们哪个假设构成支持/反驳/无关”，写成 `Evidence`。
5. **Paper Request Queue（受限文献补全）**：
   - agent 判定某文献必要但取不到全文时，登记一条请求（标题、DOI、链接、为什么需要它、阻塞了哪个任务）。
   - 前端展示待取文献列表并通知。
   - 人经清华认证取得 PDF 后**从前端上传**。
   - 系统入库、解阻对应任务、继续推进。
   - 依赖 M2 的 `blocked_on_human` 状态：等待期间 loop 不停摆。
6. **对抗性接地（Grounding Pass）**：为 M11 产出的 `Idea` 做事后核查——有人做过吗？有什么直接反驳？是否与基本盘矛盾？**只能标注，不能改写 idea**，否则自演进刚买到的不锚定性又被交还给文献框架。
7. **Gap 检测**：识别“已被做过”“没人做过”，供 M2 调整方向。
8. **持续监控**：定期发现新论文，判断是否改变现有结论；改变时触发信念更新与通知。
9. **文献地图**：展示某假设周围的相关工作全貌。

---

## M5. Hypothesis & Belief

**职责**：管理假设生命周期，把证据转化为信念变化。需求 §3 §4 §9 的核心。

### 需要实现的功能

1. **双向来源**：human 与 AI 提出的假设平等进入同一队列，标注来源。
1a. **Idea 的落地**：经你确认的 `Idea` 拆成一条或多条可证伪的 `Hypothesis` 进入验证队列；未被采纳的 `Idea` 连同理由归档，与 dead-end 同等对待。
1b. **Assumption 的审视**：定期检查 unexamined 的前提，把其中可证伪的提升为 `Hypothesis` 送入验证；不可证伪但关键的，标记为研究方向的已知脆弱点。
2. **主动提出对立假设**：系统需主动生成与当前主流解释不同甚至相反的解释。
3. **可证伪化**：推到“什么结果会反驳它”的形式；无证伪条件者标记为未成熟。
4. **竞争假设组**：把解释同一现象的多个假设组织在一起，突出分歧点。
5. **区分性验证设计**：优先设计能**区分**竞争假设的验证，而非只能确认某一个的验证。
6. **状态机**：proposed → under investigation → supported / refuted / inconclusive / abandoned；迁移必须附证据，由 MCP tool 强制。
6b. **推翻的传播（已定，2026-09-23）**：一条 Hypothesis 被 refuted、或一条 Assumption 被推翻（`invalidated`）时，**所有与它相关的对象都进“待重新审视”清单，交人判断，系统不自动改写任何对象**。
   - 相关的定义（按 State 里的结构化链接，不靠文本相似度）：
     - 被推翻的前提 A → `A.relied_on_by` 里的每个对象（它们失去了前提），并沿 assumption 的 `relied_on_by` 链**向下游传递**，标出间接受影响者与传递路径；以及 A 被提升成的假设（`promoted_to`）。
     - 被反驳的假设 H → 为它提供前提的 assumption（`relied_on_by` 含 H 者，它们是否还有存在意义）；H 的来源前提（`promoted_from`）——**假设被反驳即其来源前提被推翻**，按上一条继续传播；同组竞争假设（`group`）；关联它的待确认候选（`relates_to`）。
   - 被推翻对象出现在某条 `Insight` 的 `basis` 里 → 该理解进清单；`Insight` 被取代（superseded）或放弃（abandoned）→ `derived_from` 指向它的 Assumption 进清单。
   - 实现为一个**幂等的对账函数**：扫描 State 里所有已推翻的对象，为每个 (触发者, 受影响者) 确保有一条 `Review`。无论状态是谁改的（工具、前端、人在编辑器里手改），对账都能补上，不存在绕过的写入路径。已处理过的 Review 不会重复生成。
   - 人处理 Review 时写明结论：`resolved`（已据此调整）或 `dismissed`（判断不受影响）。二者都保留，成为“为什么这条还站着”的可追溯记录。
   - 理由：`relied_on_by` 记下了依赖关系却不在推翻时使用，等于让“建立在已被推翻的前提上”这个失败模式重新溜回来——而抓住它正是 Assumption 与 Hypothesis 分开建模的原因（M1）。
7. **信念更新**：新证据更新置信度与状态；处理证据冲突（论文说 A，实验说 B）。
7b. **矛盾扫描**：主动遍历已积累证据，找出彼此冲突或与现有假设冲突之处。被动等冲突撞上来会漏掉大部分；这项工作不需要人在场，是无人值守窗口的固定项（M2.0b 第 5 级）。
8. **重复检测**：新假设与已证伪/已放弃的撞车时提示并引用历史结论。
9. **优先级评估**：输出给 M2。

---

## M6. Experiment

**职责**：在没有任何 repo 的前提下也能一路走到真正跑实验（需求 §6）。

### 需要实现的功能

1. **Compute Backend 抽象**：`local-gpu`（本机 2×5090）与 `remote-cluster`（公司服务器，后置）两类后端同一接口。实验 spec 与执行位置解耦，这是后续迁移的前提。
2. **验证方式设计**：把假设翻译成具体方案——分析、小规模探针、或完整实验；明确 metric、baseline、预期区分度、代价估计。
3. **分级验证**：低成本快速验证（自动）与高成本实验（审批）分开，保证无人时 loop 仍能推进。
4. **实验基座 bootstrap**：建立仿真环境与 baseline 训练流程，作为显式早期里程碑（见 §2 风险）。后续实验在其上演进，而非每次重来。
5. **环境隔离**：每个实验独立 `uv` venv，禁止复用全局环境（见 M10）。
6. **代码编写与修改**：通过 M3 的 coding agent 完成。
7. **运行与调试**：执行、捕获报错、自动调试循环、失败上限与放弃条件。
8. **长跑与中断恢复**：训练任务的 checkpoint 与断点续跑——不只是为了容错，更因为班次会在 5h 处切断（0.5）。
9. **结果采集与分析**：日志、指标、产物归档并与配置绑定；结论必须回答“这对目标假设意味着什么”，包括**是否存在其他解释**。
10. **可复现记录**：代码版本、配置、环境、随机性。
11. **负结果归档**：没效果的实验同样完整入库。
12. **数据集获取**：按固定顺序解析每个数据源——**校内/国内镜像（TUNA、hf-mirror 等）→ 直连绕过代理 → 走代理**。前两条自动，第三条因为慢且占用代理，一律挂起等审批（`blocked_on_human`）。实际下载委托给 M10 的 Download Broker。
13. **磁盘管理**：登记各数据集与实验占用，接近总量上限时告警并要求清理；大数据集下载需审批。

---

## M7. Human-in-the-loop

**职责**：让人始终是 loop 的一部分（需求 §10），而不只是旁观。

### 需要实现的功能

1. **讨论模式对话**：系统的第一入口。要求 agent 是有立场的对话伙伴——主动给出不同甚至相反的解释、指出前提里的漏洞、援引文献，而不是顺着你说。
2. **候选对象蒸馏与策展**：把讨论蒸馏成候选 `Assumption` / `Hypothesis` / `Question` / `Uncertainty`，由你确认、修改或丢弃后才正式入库。
3. **交棒控制**：挑选本轮要验证的批次，切到验证模式；以及反向切回讨论模式。
4. **对话入口（验证模式下）**：能把对话意图路由成实际状态变更或任务，而不只是回一段话。
5. **干预动作**：提新假设、改问题理解、质疑结论、要求解释、改优先级、暂停方向、要求深挖、终止方向。
6. **质疑处理**：人质疑某结论时重新评估其证据链，而不是简单顺从或简单坚持。
7. **解释能力**：基于 `Decision` 与证据链回答“为什么现在做这个”“为什么认为 X 可信”，不允许事后编造。
8. **解阻入口**：PDF 上传、审批（含代理下载审批）、回答系统提问——各类阻塞的统一处理界面。
9. **人类输入的地位**：作为高权重输入进入 State，但**同样可被后续证据修正**；明确标注“这是人的直觉”而非“已验证结论”。
10. **异步交互**：人可能几天不出现，提问与通知排队等待，不阻塞 loop。

---

## M8. Observability & Progress

**职责**：让人判断“有没有跑偏”（§12）与“是否真的更懂了”（§14）。

### 需要实现的功能

1. **事件流**：由 `stream-json` 输出转成统一活动事件（在读哪篇论文、在验证哪个假设、实验进展、状态变更），实时推送 + 历史回看。
2. **当前活动视图**：在做什么、到哪一步、为什么做、下一步计划。
3. **班次视图**：当前班次已用时长/额度、预计何时耗尽、上一班停在哪。
4. **合适的透明度**：展示决策、依据与进度，不倾倒完整内部推理。
5. **进展度量**（按“理解程度”而非产出数量）：关键 uncertainty 的收敛情况、被排除的错误解释数、问题形式化成熟度、假设的证据覆盖强度、新发现的关键问题。同时**保留**活动量统计，但显式标注为过程指标而非进展指标。
6. **周期性研究简报**（对应需求 §15）：哪些可信、哪些已被反驳、哪些无结论、新出现的假设、哪些论文改变了理解、哪些实验完成、最大 uncertainty、下一步最值得做什么。
7. **跑偏检测**：当前活动与声明方向的一致性检查，偏离时主动提示。
8. **审计**：任何结论可展开成完整来源链。

---

## M9. Web Frontend

**职责**：长期交互入口。不是聊天窗口，而是**研究状态看板 + 干预控制台**（需求 §11）。

### 访问方式

只绑 `127.0.0.1`，通过 SSH 本地端口转发访问（`ssh -L <port>:localhost:<port> <server>`）。好处是零暴露、零鉴权工作量，且直接复用已配置的 SSH key。代价是手机端不便——如后续需要，再评估 Tailscale 一类方案，不进 v1。

### 需要实现的界面能力

1. **讨论面板**：与 agent 就研究方向对话的主界面，带当前模式指示与**交棒按钮**。这是系统的第一入口，不是附属功能。
2. **候选区**：讨论中产出的候选 assumption / hypothesis / question，支持确认、修改、丢弃、选入验证批次。
3. **研究总览**：当前问题陈述、可信结论、最大 uncertainty、下一步计划。即 §15 里“隔一段时间回来看到的那一屏”。
4. **假设与前提看板**：假设按状态分列，可展开看证据链、提出者、演化历史；前提单列 unexamined 清单；支持直接新增与质疑。
5. **文献视图**：已读论文、与假设的关联、最近改变理解的论文。
6. **待取文献区**：系统请求的受限文献列表 + **PDF 上传入口**（M4.5）。
7. **实验视图**：实验列表、状态、结果、失败记录、在跑实验的实时进度。
8. **活动流与班次状态**：在做什么、额度还剩多少、何时会被迫暂停。
9. **对话能直接产生状态变更**（验证模式下）。
10. **干预控制**：暂停/恢复、改优先级、终止方向、要求深挖。
11. **审批收件箱**：高成本/高风险动作待批，含“数据集需走代理下载”一类。
12. **通知中心**：额度耗尽暂停、待取文献、待审批、重大结论变化。
13. **“为什么”入口**：任何结论、任何当前任务旁可直接追问理由。
14. **实时性**：事件流推送，而非刷新。

---

## M10. Infrastructure

**职责**：运行底座，以及**环境卫生**这条硬约束的落实处。

### 环境卫生规则（0.7 的细则）

1. **单一根目录**：系统与 agent 的全部写入收敛在一个根目录下（State 仓库、实验 workspace、venv、数据集、日志、产物）。删掉这个目录 = 系统对宿主机零残留。
2. **禁止事项**（由 `--disallowedTools` + Bash 命令 deny-list 双重实施；白名单不具备禁用能力）：`sudo`、`apt`、全局 `pip install`、写入 `~/.bashrc` 等 shell 配置、写入 conda base 环境、根目录之外的任何写操作。
3. **每实验独立 venv**：统一用 `uv`，快且自包含，死掉的实验环境可直接回收。
4. **docker 不交给 agent**：docker 组成员权限等价于 root，交给自主 agent 会使所有目录限制失效。需要系统级依赖时，agent 只能**提交** Dockerfile/compose spec，由 orchestrator 在审批后代为构建运行。
5. **磁盘预算**：登记各实验与数据集占用，设总量上限，接近上限时告警并要求清理；大数据集下载走审批。
6. **GPU 使用**：两张卡系统可自由使用；仅需避免自身并发实验互相抢占，通过简单租约与 `CUDA_VISIBLE_DEVICES` 分配。

### 网络分流与 Download Broker

宿主机的 sing-box 以 TUN 模式接管全部流量并限速，而与 Claude 的交互依赖它。目标是**只让数据集下载绕过代理，其余一切不变**。

1. **按 UID 分流，不按 IP 分流**。数据集宿主基本都在 CDN 后面（HuggingFace / S3 / GCS），IP 频繁变动且与其他服务共用，IP 白名单会长期处于失效状态。UID 规则稳定且零维护。
2. **专用下载 UID**：建一个只用于下载的系统用户，在 sing-box 中对该 UID 放行——TUN 入站排除该 UID（流量根本不进隧道，同时避开隧道 DNS），或路由规则将其导向 `direct`。具体字段按 1.12 的 schema 确认。
3. **这是一次性的人工 root 操作**。配置在 `/etc/sing-box/config.json`，需 sudo，agent 不得触碰（与本节第 2 条禁令一致）。
4. **Download Broker**：常驻在该 UID 下的服务，orchestrator 经本地 socket 投递下载任务。运行期不需要 sudo。Broker 同时承担断点续传、去重、磁盘预算核算与速率上报。
5. **获取顺序**：校内/国内镜像 → 直连绕过 → 走代理。前两条自动，第三条因慢且占用代理，一律挂起等审批。清华校内的 TUNA 镜像与 hf-mirror 很可能快于直连，应优先尝试。
6. **不得泄漏**：放行严格限于该 UID 白名单，下载用户不跑任何其他东西；orchestrator 与 agent 本身以普通用户身份运行，流量照常走代理。

### 其他功能

1. **持久化与备份**：State 仓库定期推到远端备份（研究状态丢失等于项目死亡）；实验产物与日志的归档策略。
2. **服务运行**：后端服务 + 班次 daemon + 任务执行进程的编排与重启恢复。
3. **API 层**：前后端接口与实时推送通道。
4. **配置管理**：CLI 路径与认证（`claude setup-token` 产出的长期 token）、检索源、预算阈值、自治策略参数。
5. **可迁移性**：全系统可整体迁移到公司服务器，配置与路径不硬编码。
6. **可观测底座**：日志、错误上报、额度使用统计。

---

## M11. Idea Incubation（想法自演进）

**职责**：在**关闭检索**的条件下，从冻结的基本盘出发做长链自我推演，产出**直觉层面的、可证伪的新想法**，而非工程实现细节。这是系统在你不在场时最有价值的一种产出。

### 为什么要关掉检索

两个真实机制：检索会把思路锚定到文献的既有框架上，模型转而复述而非重组；检索也消耗注意力与上下文预算，读完十篇论文就没剩多少余量真正去想。代价是产出不接地。

**特征失败模式**：链条越推越长、越推越自信，但并不更真——而模型从内部分辨不出洞见与空话，因为两者用的是同一套流畅性。因此本模块的设计目标不是“让它自由发挥”，而是**在保留不锚定的前提下，用结构手段挡住失控的抽象漂移**。以下 2/5/7/8 四条是主要防线。

### 需要实现的功能

1. **入场闸门**：仅当 `Question` 成熟度达到 `formalized` 时可进入。基本盘未成形就自演进，产出必然是空转。
2. **基本盘冻结（Foundation Snapshot）**：进入时对 State 取只读快照——形式化问题定义、已确立事实、已知 dead-end、当前 assumption 集。运行期间不变，作为本轮推演的约束面。
3. **搜索关闭的执行环境**：禁掉 WebSearch / WebFetch / Bash / Task 等一切可能触达外部的工具（**仅从白名单里省略是不够的**，见 §0.6），只保留基本盘读取与推演记录写入。**物理上做不到检索，而不是靠提示词请它别搜**。Phase 2.5 spike 实测：网络一侧三层禁用成立，但裸 `Read` 能读宿主机任意文件（论文可能在任何地方），必须加 `--restricted` 把读取关进工作目录——具体形态见 §5.9。
4. **允许挑战基本盘，但必须显式**：推翻基本盘里的某条前提本身可能就是最有价值的 idea。但必须显式登记为“对 X 的挑战”，不能靠悄悄漂移。
5. **新前提追踪（Assumption Trace）**：推演中每引入一条新前提，登记为一条 `Assumption`。终局可直接读出“这个 idea 建立在它自己发明的 N 条未检验前提上”。**最有效的机械质量信号**——N 大的大概率是流畅空话，N 小的才值得看。
6. **短链累进循环，而非单条长链**：推演以较短的链为单位（量级上是十几分钟到半小时，不是数小时），每条链结束后立刻接一次接地核查，核查结果更新基本盘，再从**新的**基本盘开出下一条链：

   ```text
   短推演（搜索关）→ 接地核查（搜索开）→ 基本盘更新 → 下一轮短推演
   ```

   长链的问题是复利式累积错误；而"从同一冻结基本盘并行跑 N 条"的问题是 N 条之间高度重叠——基本盘相同、问题相同，跑到第四五条就只是换说法。**循环把重叠变成累进**：每轮起点都被上一轮真实推动过。一个 5h 窗口约合六到八轮迭代。
   
   同一轮内仍可开若干条不同角度的短链，独立推演之间的收敛是"有料"的弱证据；短链也更适配窗口的随时截断。

6b. **基本盘的更新规则（防漂移的关键）**：基本盘**在一条链内冻结，只在链之间更新，且只能由外部证据推动，永不由自我推演推动**。链条不能中途给自己挪球门；但一次带回真实文献证据的接地核查，有资格更新基本盘。这条规则同时保住了累进性与 2 的防漂移性质。
7. **双重停止条件（已定）**：**产出 3 条高价值 hint** 或 **用满 30% 窗口预算**，先到先停。三条早早凑齐就收工，不继续烧额度。

   > **“3”是目标，不是配额。** 配额会制造凑数压力——这是本模块最危险的激励。若预算用尽只有 1 条过关，就交 1 条并说明；一条都没有就交白卷（M11.9）。**绝不允许为了凑够 3 条而降低门槛。**
   >
   > 什么算“过关”不能由模型自评（自评新颖性正是它最不可靠的能力）。判定是机械的两道闸：先过下面第 8 条的结构性门槛，再经接地核查存活（不是已被做过、不被文献直接反驳）。**最终价值只有人能判断**——系统的职责是把值得你花十分钟的东西挑出来，不是替你决定什么是好的。

8. **结构性门槛**（过不了就不算候选，与自我评价无关）：
   - 能说出这个想法**在什么情况下是错的**；
   - 已调用 `check_dead_ends` 与自身历史比对；
   - 新引入的前提已全部登记。

   “我觉得这个想法不错”**不算**过关。这三条同时也是防止它写散文的闸门：要求给出证伪条件，就逼它把直觉收敛成一个有内容的断言。
9. **事后对抗性接地**：交给 M4 的 Grounding Pass，由**另一个开着检索的任务**执行，只标注不改写。
10. **选择与排序**：多条推演产出后做一次选择，只把最值得你花十分钟的送进候选区。**允许且应当输出“本轮没有值得你看的东西”**。
11. **产出形态**：`Idea` 对象（见 M1）。要求直觉层面的洞察，不要求工程细节。
12. **中断恢复**：推演链增量提交，5h 截断后从断点续推。半条链是合法的可恢复状态。

---

## 5. 关键跨模块约定（实现前定死）

1. **Task 契约**：M2 → M3 的任务描述，以及 M3 → M1 的状态变更形态。整个系统的关节。见 5.3。
2. **Briefing / Handoff 契约**：M1 如何把长期状态压缩成一次 session 的上下文。决定“不失忆”能否成立。见 5.2。
3. **增量提交协议**：agent 在什么时机提交状态，保证被 SIGKILL 时不丢工作。见 5.4。
4. **状态迁移规则**：由 MCP tool 强制，见 0.4。
5. **Decision 记录时机**：凡改变研究方向的动作必须留理由。

以下 5.1–5.4 为 Phase 1 定下的具体形态（v1.2）；5.5–5.8 为 Phase 2 的四个契约（v1.3，2026-09-23 用户确认：摘要级证据规则、无人值守额度默认值、H002 保持 disputed 单列 unknown、已有对象的署名线索由人审过 diff 后中性化）；5.9–5.14 为 Phase 2.5（自演进）的契约（v1.4，草案待确认）。机器可读的单一定义在 `autoresearch/schema.py`，本节是它的说明；二者冲突时以改本节为准、再改代码。

### 5.1 State schema

#### 根目录

整个系统写入收敛在一个根目录 `$AR_ROOT`（默认 `~/autoresearch`，删掉即零残留）。它**不放在代码仓库内**：agent 的工作目录若位于代码仓库之下，会自动加载开发用的 `CLAUDE.md`，污染研究 agent 的上下文。

```text
$AR_ROOT/
  state/        Research State，独立 git 仓库（唯一真相来源）
  run/          运行期数据，不入 git：任务目录、额度、班次、事件、通知、锁
```

#### State 仓库布局

| 路径 | 对象 | 谁能写 |
|---|---|---|
| `project.md` | ResearchProject，含当前 `mode` | 后端（mode 只有人能改） |
| `provenance.json` | 所有对象的 `origin` 与归属来源 | 后端 |
| `questions/Q###.md` | Question | agent / 人 |
| `assumptions/A###.md` | Assumption | agent / 人（Phase 1 经候选区） |
| `hypotheses/H###.md` | Hypothesis | agent / 人；**状态迁移只能经 `transition_hypothesis`** |
| `evidence/E###.md` | Evidence | 仅 `record_evidence` |
| `groundings/GR###.md` | 对抗性接地结论（§5.6） | 仅 `annotate_grounding` |
| `requests/RQ###.md` | Paper Request（§5.8） | 仅 `request_paper`；人经前端处理 |
| `papers/P###.md` | Paper | 仅 `register_paper` 建；正文（阅读笔记）agent / 人可改，身份与阅读状态字段归工具（§5.5） |
| `dead-ends/D###.md` | DeadEnd | agent / 人 |
| `uncertainties/U###.md` | Uncertainty | agent / 人 |
| `insights/IN###.md` | Insight（理解） | agent / 人（Phase 1 经候选区；人可直接记） |
| `decisions/DEC###.md` | Decision | 仅 `log_decision` 与后端 |
| `candidates/C###.md` | 候选对象 | 仅 `propose_candidate`；人经前端改 |
| `discussions/DS###/transcript.md` | 讨论记录 | 仅后端追加 |
| `discussions/DS###/summary.md` | 讨论摘要 | 仅 `update_discussion_summary` |
| `handoffs/HO###.md` | 班次交接记录 | 仅后端（机械生成） |
| `reviews/R###.md` | 待重新审视（推翻的传播，M5.6b） | 仅对账函数；人经前端处理 |
| `ideas/I###.md` | Idea（自演进的产出，§5.10） | 仅 `record_idea` 建；分诊由人经前端做 |
| `foundations/F###.md` | 基本盘快照（§5.11） | 仅后端（机械生成） |

「仅某工具」的路径对 agent 是**受保护路径**：runner 在 stream 里看到 agent 用 Write/Edit 触碰它们时回滚并记违规。Phase 1 的讨论类任务根本不给 Write/Edit，此条是给后续阶段留的闸。

#### frontmatter 格式

- 只允许**扁平** `key: value`，值为标量或行内列表 `[A001, H002]`；字符串可加双引号。**不允许嵌套**——对 agent 与人手改都最不易出错，校验器也能写得确定。
- 所有对象必填：`id`（与文件名一致）、`type`、`created`（ISO 日期）。可选 `updated`。
- id 由工具/后端在锁内分配（前缀 + 至少 3 位序号）。agent 不得编造 id。

#### 各对象字段与枚举

| type | 必填字段（除通用三项） | 枚举 / 约束 |
|---|---|---|
| `project` | `title` `mode` `main_question` | `mode`: discussion / incubation / validation |
| `question` | `maturity` | vague / scoped / formalized |
| `assumption` | `status` `relied_on_by` | `status`: unexamined / examined / promoted / retired / invalidated；`relied_on_by` 非空，元素须是已存在 id；可选 `fragile`（true/false）、`promoted_to`、`derived_from`（IN###，由某条理解派生）、`idea`（I###：推演中新引入的前提，§5.10）；invalidated 时须有 `invalidated_by`（E### 或 DEC###：证据，或人的推翻决定） |
| `hypothesis` | `status` `confidence` `falsifier` `validation` `evidence` | `status`: proposed / investigating / supported / refuted / inconclusive / abandoned；`confidence`: low / medium / high；`falsifier` `validation` 非空；`evidence` 元素须存在；status≠proposed 时 evidence 非空；可选 `promoted_from`、`group`、`idea`（由哪条 Idea 拆出，§5.14） |
| `evidence` | `hypothesis` `stance` `strength` `source` | `stance`: support / contradict / neutral；`strength`: weak / moderate / strong；`source` 必须是已存在的 `P###` 或 `X###`（**不得是 dead-end**） |
| `paper` | `title` | 可选 `url` `venue` `year` `read` |
| `dead-end` | `status` `closed_by` | `status`: closed / reopened；`closed_by` 非空，元素是 `E###` / `X###` |
| `insight` | `status` `firmness`，以及 `basis` 或 `basis_note` 至少其一 | `status`: active / superseded / abandoned；`firmness`: hunch / working / settled；`basis` 元素须是已存在 id；可选 `informs`、`change_mind`、`superseded_by` |
| `uncertainty` | `status` `importance` | `status`: open / reduced / resolved；`importance`: low / medium / high |
| `decision` | `kind` `refs` | `kind`: research / curation / mode / handoff |
| `candidate` | `kind` `status` `origin` `source` `turns` | 见下 |
| `discussion` | `title` `status` | open / closed |
| `discussion_summary` | `discussion` `covers_through` | 摘要覆盖到第几轮 |
| `handoff` | `shift` `reason` `started` `ended` | `reason`: normal / quota_5h / quota_7d / cutoff / crash / manual |
| `review` | `trigger` `event` `target` `status` `depth` | `event`: hypothesis_refuted / assumption_invalidated / insight_withdrawn；`status`: open / resolved / dismissed；`depth` 为传递距离（1 = 直接相关） |
| `idea` | `status` `falsifier` `premises` `foundation` `chain` | 见 §5.10 |
| `foundation` | `session` `round` `question` | 见 §5.11 |

几条容易做错、因此写死的规则：

1. **`origin` 不进对象文件**，统一存在 `provenance.json`（`{ "H001": {"origin": "human", "source": "C003", "discussion": "DS001", "turns": [4, 6]} }`）。理由：origin 要在评判时对 agent **不可见**（M1），而 agent 有 Read 权限——只在 briefing 里删掉是假屏蔽，它会自己去读 `hypotheses/H001.md`。放进单独文件后，评判类任务可以用 `--disallowedTools "Read(provenance.json)"` 从路径上挡住（Phase 1 实测：`Read(path)` 拒绝规则同时让 Grep 搜不到该文件）。对象文件 frontmatter 里出现 `origin` 视为**校验错误**（泄漏）。
2. **Assumption / Hypothesis 按当前角色区分**：被**依赖**（用来剪枝或支撑其他推理）而**未安排验证** → Assumption；**已安排验证** → Hypothesis。这条落在结构上而不只是提示词里：Assumption 必须填 `relied_on_by`（它支撑着谁），Hypothesis 必须填 `validation`（验证怎么安排的）。说不出“怎么验证”就还不是 Hypothesis。
3. **DeadEnd 不内嵌实验结果**，只用 `closed_by` 引用关闭它的 Evidence / Experiment；`closed_by` 为空是校验错误。正文只写“关了什么、为什么、什么条件下重开”。
4. **候选对象**（M1.10）：`kind` ∈ assumption / hypothesis / question / uncertainty / insight；`status` ∈ pending / accepted / rejected / superseded；`origin` ∈ human / ai / unclear（**归属冲突写 `unclear` 加 `origin_note`，不自动裁定**，人在确认时必须选定）；`source` 为 `DS###`，`turns` 为依据的轮次。按 kind 附带目标对象所需字段（assumption → `relied_on_by`；hypothesis → `falsifier` `validation`；question → `maturity`；uncertainty → `importance`）。确认后由后端建正式对象、写 provenance、回填 `promoted_to`；被拒的保留并写明理由，供后续去重。
5. **讨论记录**以 `<!-- turn N human|ai ISO时间 -->` 注释行分隔轮次（而非 markdown 标题），正文里出现任何标题都不会切错。

### 5.2 Briefing / Handoff 契约

**一个装配器，两种用途**：`assemble(profile, task)` 从 State + 运行期台账产出 markdown。给 agent 用时写成任务目录下的 `briefing.md`（prompt 里只说“先读 briefing.md”，符合 M3.1）；班次结束时以 `handoff` profile 调用同一个装配器，结果落成 `handoffs/HO###.md`。下一班所有 briefing 都含最新一份交接。

交接记录**必须能在没有 agent 的情况下生成**：窗口被切断时恰恰没有额度可以请 agent 写总结。因此交接是机械装配的——来源是任务台账、agent 在任务中途用 `checkpoint` 写下的进度笔记、本班次的 git 提交、候选区与讨论的状态。agent 的贡献在“边做边记”，不在“收尾总结”。

#### 章节（按顺序）

| # | 章节 | 内容 | 截断 |
|---|---|---|---|
| 1 | 本次任务 | 目标、预期产出、当前模式及其禁止事项 | 不截断 |
| 2 | 上一班交接 | 最新 handoff 的正文 | 不截断 |
| 3 | 研究问题 | project + main question 正文与成熟度 | 不截断 |
| 3b | 当前理解 | active 的 Insight，按牢固程度排序，标明“是理解，不是证据” | 超预算列摘要行 |
| 4 | 前提 | 全部 assumption，unexamined 在前 | 超预算列摘要行 |
| 5 | 假设 | 全部 hypothesis（状态、置信度、证伪条件、证据 id） | 超预算列摘要行 |
| 6 | 证据 | 与上面假设相关的 evidence | 超预算只给最新 |
| 7 | 已关闭方向（禁止重复） | **全部** dead-end | **不截断**（§0.4 “保证可见”） |
| 8 | 未决不确定性 | open 的 uncertainty | 超预算列摘要行 |
| 9 | 候选区 | pending 与近期 rejected 候选（含拒绝理由），用于去重 | 超预算只给最新 |
| 10 | 讨论上下文 | 该讨论的 summary + 未被摘要覆盖的最近若干轮原文 | 按预算保留最近的 |
| 11 | 待重新审视 | open 的 Review：谁被推翻、传到了谁、路径 | 不截断 |

截断处一律写明“已截断，完整内容见 `<路径>`”，agent 可自行 Read。

#### Profile

| profile | 任务 | origin | 章节 |
|---|---|---|---|
| `discuss` | 讨论回合 | 保留 | 1–11（含 3b） |
| `distill` | 讨论蒸馏（策展） | 保留 | 1–11（含 3b），讨论上下文给未蒸馏部分的全文 |
| `judge` | 评估证据 / 状态迁移 / 对抗性接地 | **剥离** | 1–8 与 11；不含候选区与讨论（二者都暴露归属），**不含 3b 当前理解**（避免锚定） |
| `incubate` | 自演进的推演链（§5.12） | **剥离** | 只有两章：本次任务、基本盘快照（F### 原文）。理由见 §5.12 |
| `handoff` | 班次交接记录 | 保留 | 专用章节：班次概况、已完成、被截断（含 checkpoint）、队列、本班 State 变更、待人处理 |

`judge` profile 的剥离规则：

1. 不输出 provenance，也不输出含对话的章节。
2. 正文规范化：替换“研究者认为 / 我认为 / 人提出 / AI 提出 / 你提出”一类署名线索为中性表述。
3. 同时在执行层封路：`--disallowedTools` 加 `Read(provenance.json)`、`Read(discussions/**)`、`Read(candidates/**)`、`Read(handoffs/**)`（及对应 Grep/Glob）。Phase 2 起手 spike 实测绝对路径写法 `Read(//$STATE/…)` 成立，并补封 `.git/**`；完整清单见 §5.6。
4. 屏蔽不可能完美（git 历史、行文风格都会泄漏），所以 **M8 的按-origin 反驳率是必需项**：Phase 1 即在总览里给出，数据来自 provenance + hypothesis status，不依赖任何 agent。

### 5.3 Task 契约（Phase 1 子集）

任务台账在 `run/tasks/T#####/`，每任务一个目录（M3.5 执行隔离，M3.6 日志留存）：`task.json`（契约与状态）、`briefing.md`、`mcp.json`、`stream.jsonl`、`tools.jsonl`、`checkpoints.jsonl`、`stderr.txt`。任务目录同时是 agent 的 cwd，State 经 `--add-dir` 挂入。

`task.json` 字段：`id` `kind` `lane` `goal` `refs` `profile` `status` `attempts` `created` `started` `ended` `shift` `session_id` `result` `error`。

状态机：`queued → running → done | failed | interrupted`；`interrupted` 在下一班开班时自动重新入队（M2.0b 第 1 级）；另有 `blocked_on_human` 与 `cancelled`。`failed` 只表示**执行**失败，研究结论是否定的不算失败（M3.7）。

Phase 1 的任务种类：

| kind | lane | 可用工具（`--tools` 真移除 + `--disallowedTools` 兜底） | MCP 工具（server 端按 toolset 过滤） |
|---|---|---|---|
| `discuss_turn` | interactive | Read Grep Glob WebSearch WebFetch | check_dead_ends, propose_candidate, checkpoint |
| `distill` | background | Read Grep Glob | check_dead_ends, propose_candidate, update_discussion_summary, checkpoint |

Phase 2 新增的评判类任务（全部 `judge` profile、background 通道，由 planner 机械派发，§5.7）：

| kind | 可用工具 | MCP 工具 | 产出 |
|---|---|---|---|
| `lit_search` | Read Grep Glob WebSearch | search_papers, register_paper, check_dead_ends, checkpoint | 为目标登记 0–6 篇最能检验它的论文（两个方向都搜） |
| `read_paper` | Read Grep Glob **Edit（仅 `papers/**`）** | open_paper, record_evidence, request_paper, register_paper, check_dead_ends, checkpoint | 阅读笔记 + 追到段落的证据；拿不到全文时请求并挂起 |
| `assess` | Read Grep Glob | transition_hypothesis, examine_assumption, invalidate_assumption, propose_candidate, check_dead_ends, checkpoint | 据全部证据决定状态；证据不够就不动 |
| `contradiction_scan` | Read Grep Glob | propose_candidate, checkpoint | 证据冲突 → 不确定性候选 |
| `grounding` | Read Grep Glob WebSearch | search_papers, register_paper, open_paper, annotate_grounding, check_dead_ends, checkpoint | 接地结论，只标注 |

- judge 任务另经 `--add-dir $AR_ROOT/library` 读论文全文；`discuss_turn` 新增 `note_prep_request`（记下研究者对“今晚查什么”的回答）。
- **Edit 的白名单必须带路径**：`--allowedTools "Edit(//$STATE/papers/**)"`，其余 State 目录另加 `Edit(...)` deny。
  dontAsk 模式下不在白名单里的 Edit 一律拒绝——cwd 与宿主机其他位置都写不了（Phase 2 spike 3 实测）。
  runner 在 tool_result 后再查一遍：受保护 / 越权路径整文件回滚，论文 frontmatter 被改则**字段级恢复**（保留正文笔记，
  避免快速连续编辑时误伤合法内容），执行层已拦下的尝试只记入 `violations.jsonl`。
- 被挂起（`blocked_on_human`）后重跑的任务，判断“本次做了什么”只看本次 `started` 之后的工具调用。

- 禁用有三层：`--tools` 只加载列出的内置工具（Phase 1 实测：init 事件里的 tools 只剩列出的几个，比 deny-list 更牢，因为 CLI 新版本加的工具不会漏网）；`--disallowedTools` 显式再禁一遍 Bash / Task 等（§0.6，保留作为纵深）；MCP server 按 `AR_TOOLSET` 只注册该任务能用的工具（`--allowedTools` 不移除 MCP 工具，同样不能靠它）。
- 统一加 `--setting-sources ""`（不加载宿主机的用户/项目设置与 hooks）、`--strict-mcp-config`、`--no-session-persistence`、`--permission-mode dontAsk`。**不用 `--bare`**。
- 讨论回合每轮开新 session，只靠 briefing 接续——每一轮都在验收“新 session 从 briefing 能接着谈”。`--resume` 作为之后的省额度优化，不影响正确性。

### 5.4 增量提交协议

State 的每次写入在写入者手里立即提交，没有“任务结束统一写回”：

| 写入者 | 提交时机 |
|---|---|
| MCP 工具 | 工具调用内，写完即提交 |
| 后端（讨论追加、候选确认、交接生成） | 写完即提交 |
| agent 直接编辑文件（Phase 2+） | runner 在 stream 中看到 Write/Edit 的 tool_result 即校验并提交该路径 |
| 人在编辑器里改 | daemon 周期性扫描工作区，脏文件以 `human` 身份提交 |
| 被 SIGKILL 遗留 | daemon 启动时扫描，以 `recovered` 提交 |

- 所有 git 操作经 `run/state.lock`（flock）串行，跨进程（后端与 MCP server）有效。
- 只提交本次写入的路径（`git commit -- <paths>`），不顺手卷走别人的改动。
- 提交作者区分身份：`ar-agent` / `ar-human` / `ar-system`；message 带 `AR-Task:` trailer。`git log --author` 即可回答“谁改的”。
- 讨论回合：人的发言在调用 agent **之前**就已提交；回复被截断则只丢未完成的那条回复，人的话还在，下一班会补答。

### 5.5 Paper 与证据出处（Phase 2，v1.3）

目标：M1.2 的“任何结论都能一路追到论文段落”，且**系统里不可能出现编造的论文**。两条都落在工具上，不落在提示词上。

#### Paper 只能经工具登记，登记即核实

- `papers/P###.md` 的**身份字段**只由 `register_paper` 写：`title` `authors` `year` `venue`，以及 `arxiv` / `doi` / `url` **至少其一**。
  登记时工具去权威源取元数据（arXiv API；DOI 走 Crossref，失败再试 OpenAlex；纯 URL 要求可访问），**取不到就拒绝登记**。
  agent 报一个记错的 arXiv id，结果是“查无此文”，而不是一条虚构的 P###。
- 同一 arXiv id / DOI 重复登记返回已有的 P###（去重归工具）。
- arXiv 自己的 DataCite DOI（`10.48550/arXiv.*`）按 arXiv 论文处理、去 arXiv 取全文——首轮夜间预习里 agent 全用这种写法登记，被当成普通 DOI 取不到全文，只好请研究者上传（已修，`./ar repair-papers` 补救旧登记）。
- 读的时候只有摘要、后来全文到了（上传或补取）的论文，planner 会再安排精读一次。
- 阅读状态字段也归工具：`read`: none / abstract / fulltext（**实际读到了哪一层**，由工具在 agent 取全文时更新，不由 agent 自报）；
  `fulltext`: none / open / uploaded / requested / unavailable；`for`：为哪些 H### / A### 而读（合并写入）；`found_via`：发现它的任务与查询。
- 正文是**阅读笔记**，agent 可以用 Edit/Write 直接写（问题、方法、结论、实验设置、局限，M4.3）。这是系统第一次让 agent 直接编辑 State 文件：
  runner 在看到 tool_result 时校验并提交（§5.4）；**身份字段与阅读状态字段被改动则整文件回滚并记违规**（字段级受保护，比对 HEAD 版本）。
- 凭记忆提到、但没有登记的论文只能出现在讨论文字里，并标“未核对”（讨论协议已有此条）。**State 里不存在“未核对的 Paper”。**

#### 全文不进 State 的 git

```text
$AR_ROOT/library/P###/
  source.pdf | source.html     原件（arXiv 下载或人上传）
  fulltext.md                  转换后的正文，带稳定段落锚点
  meta.json                    来源、sha256、转换方式、时间
```

- 理由：PDF 动辄 10MB+，进 git 会让 State 仓库膨胀且无法瘦身；而研究状态真正需要的是“引用了哪一段”，不是原件字节。
  `papers/P###.md` 记 `fulltext_sha`，原件被替换可以察觉。
- 转换：arXiv 官方 HTML 优先（章节结构可靠，spike 实测 5s/篇），否则 `pdftotext`（宿主已有 poppler，零安装）。
  `fulltext.md` 按章节切分、段落编号，锚点形如 `[s3.2-p4]`（第 3.2 节第 4 段）；摘要固定为 `[abstract]`。
- `library/` 经 `--add-dir` 只读挂给阅读类任务；人上传的 PDF 不可再生（要走清华认证），**归入 M10.1 的备份范围**（备份本身不在 Phase 2）。

#### Evidence 追到段落

`evidence/E###.md` 字段变更（Phase 1 的真实 State 里还没有任何 evidence，无迁移成本）：

| 字段 | 约束 |
|---|---|
| `target` | 替代原 `hypothesis`：H### **或 A###**——前提审视（M5.1b）产出的证据指向前提 |
| `source` | 已登记的 P###，或 X###（Phase 3）；不得是 dead-end（不变） |
| `locator` | source 为 P### 时必填：一个或多个段落锚点，如 `[s4.1-p2, tab3]` 或 `abstract` |
| `quote` | 必填，原文摘录（≤400 字）。**工具校验它确实出现在 locator 指向的段落里**（归一化空白后子串匹配），对不上就拒绝 |
| `basis` | abstract / fulltext，由工具按 locator 推出，不由 agent 填 |
| `stance` `strength` | 不变；**basis=abstract 时 strength 不得为 strong** |

quote 校验是反编造的第二道闸：agent 可以读错，但不能凭空写出论文里没有的话还挂在某一段上。

状态迁移对证据的要求（`transition_hypothesis` 强制）：迁到 supported 至少一条 fulltext 的 support；迁到 refuted 至少一条 fulltext 的 contradict。
只凭摘要不能把一条假设判死或判活。`abandoned` 只有人能做（放弃是方向决定，不是证据结论）。

### 5.6 评判类任务（judge）契约（Phase 2，v1.3）

**哪些是评判类**：凡是读证据、产出证据、据证据改变状态的任务——文献检索、精读与证据抽取、假设评估、前提审视、矛盾扫描、对抗性接地——一律 `judge` profile。
检索也算：挑哪些论文来读本身就会偏（只找支持的），归属可见时这种偏会顺着 origin 走。
非评判类（保留 origin）只有策展与路由：夜间预习的选题、议程、进展报告——Phase 2 里它们由后端机械完成，不派 agent。

**三层屏蔽**：

1. **briefing**：§5.2 的 judge profile（剥离 origin、无候选区与讨论、无 3b 当前理解）。上一班交接只给“被截断的任务与其 checkpoint”，
   不给“本班 State 变更”的提交列表与“待处理”（二者都带归属线索）。
2. **路径封读**（Phase 2 起手 spike 实测成立，见 `spike/phase2/README.md`）：`Read/Grep/Glob(//$STATE/…)` 封
   `provenance.json` `discussions/**` `candidates/**` `handoffs/**` `.git/**`，并加
   **`insights/**`**（评判看不到理解）与 **`decisions/**`**（人推翻前提、选批次的决定都是权威线索）。
   规范化后的路径变体、`/proc/self/root` 前缀、目录级 Grep/Glob 均已实测被挡。
3. **对象正文**：路径规则管不到对象文件本身的文字。实测真实 State 的 H003 `validation` 写着“AI 在第 2 轮提议……研究者尚未认可”。
   因此：(a) 候选确认时，写进正式对象的文字经 `neutralize()`，原话留在候选（judge 读不到）里，对象正文只写“来由见 C###”；
   (b) 校验器对 Q/A/H/U/P/E 的正文与自由文本字段扫描署名线索，给**警告**（不报错——人手写的时候可以有）；
   (c) 派发 judge 任务前若有警告，前端提示一次。已有对象不自动改写：由系统给出中性化 diff，人审过后以 `ar-human` 身份提交（2026-09-23 定）。

**状态迁移只走 MCP**：judge 任务不能 Edit 任何对象的状态字段。可用的迁移工具与各自的硬约束：

| 工具 | 作用 | 硬约束 |
|---|---|---|
| `record_evidence` | 记证据；目标假设若为 proposed 且在当前批次内，自动迁到 investigating（簿记归工具） | §5.5 |
| `transition_hypothesis` | proposed/investigating → supported / refuted / inconclusive | §5.5；不能迁到 abandoned |
| `examine_assumption` | 前提审视结论：`holds`（→ examined）/ `fragile`（→ examined + fragile=true） | 至少一条 target 为该前提的证据 |
| `invalidate_assumption` | 前提被证据推翻 → 触发 M5.6b 对账 | 至少一条 contradict 证据（已实现） |
| `propose_candidate` | 前提可证伪 → 提升为假设的**候选**；矛盾 → 不确定性的候选 | 评判类任务提交的候选 `source` 为任务号、`basis` 必须指向 E/P，origin 固定为 ai；不能提 insight |
| `annotate_grounding` | 对抗性接地的结论，只标注不改写 | 见下 |

**对抗性接地**（M4.6）：Phase 2 还没有 Idea，接地的对象是**假设与待确认的假设/前提候选**（由人在前端点“接地核查”，或批次里的假设自动做一次）。
结论写成独立的 `groundings/GR###.md`（仅工具可写）：`target`、`verdict`（novel / prior_work / contradicted / mixed）、`refs`（P### / E###）、正文。
**被核查对象一个字不改**。Phase 2.5 的 Idea 直接复用这个对象。

**偏差指标**：M8 的按-origin 反驳率由 provenance + hypothesis status 实时计算（已实现），Phase 2 起真实迁移会让它第一次有数据；
`disputed` 的归属单列（前端显示为 “Disputed origin (not counted)”），不计入 human / ai 任一方，直到人裁定。

### 5.7 模式与交棒契约（Phase 2，v1.3）

**模式只有人能切**。`project.md` 的 `mode` 仅由后端经前端操作写（它本就是受保护路径），每次切换写一条 `Decision`（kind=mode）。

**交棒进验证模式**：人在前端从三处挑本轮验证批次——待确认的 hypothesis/assumption 候选（挑中即确认入库）、unexamined 的前提、
proposed/investigating 的假设——并可写一句本轮想弄清什么。确认后：

1. 写 `Decision`（kind=handoff）：`refs` = 批次，正文 = 人的说明；`project.md` 写 `mode: validation`、`batch: DEC###`。
2. 排队中的夜间预习任务取消（文献工作由验证模式接管）；讨论回合照常——验证模式下人仍然可以聊，讨论 agent 的 briefing 会写明当前批次。

**验证模式的调度**（M2.0b 阶梯的 Phase 2 实现，机械规划，不派 agent 决定做什么）。对批次里每个目标 T 推导下一步：

```text
没检索过 T                          → lit_search(T)            ┐
有为 T 登记、可读、未读的论文且未超预算 → read_paper(P, T)        ├ 每个目标一条流水线
T 自上次评估后有新证据                → assess(T)               ┘  （H 迁移状态 / A 审视结论）
批次证据自上次扫描后新增 ≥5 条         → contradiction_scan
以上都没有                           → 第 9 级：收工、留额度
```

- 每个目标的预算：检索 ≤2 次、全文精读 ≤6 篇；到顶即视为该目标饱和。每个任务的 `why` 字段由规划器机械写明
  （“H001 的第 3 篇：由 T00041 的检索‘action chunk contact-rich’找到”），前端“为什么读这篇”直接读它（M2.6 的精神；
  不为每个任务写 Decision，那会淹没真正的方向决定）。
- **窗口份额**：无人值守的验证任务只在 5h 窗口利用率 < 60% 时启动（`AR_UNATTENDED_CAP`），剩下的留给你的交互使用；
  周额度 ≥95% 全面暂停（已实现）。
- 任务 blocked_on_human 时该目标的流水线跳过这篇，继续别的。

**重大结果 → 建议收回，不自己闭环**。以下任一发生，规划器停止派发新的验证任务（在飞的做完提交），前端出横幅“建议回到讨论模式”并写明原因：
批次内任一假设迁到 supported / refuted；任一前提被推翻或判为 fragile；矛盾扫描提交了候选；批次全部饱和。
人选择“收回”（切回讨论）或“继续验证”（写 Decision 说明为何继续）。

hold 分两种：**重大结果**必须由人决定；**饱和**（没有够格的工作了）只是第 9 级的收工——若还有任务挂起在等人取论文，
不算饱和；人上传全文后饱和 hold 自动解除，因为新到的全文就是新的工作。

**收回**：人随时可切回讨论模式。排队中的验证任务取消，在飞的做完并提交（它们本就增量提交，杀掉只会浪费额度）。写 Decision（kind=mode）。

**夜间预习**（讨论模式下）：人最后一次发言后空闲 ≥90 分钟、窗口空闲时启动，每次最多 6 个任务，只做检索与精读（**不做评估、不迁移状态**——那是验证模式的事）。
选题：人在前端 Chat 页“今晚查什么”里写了就用它（讨论 agent 在讨论收尾时会主动问）；没写则规划器从 unexamined 前提、无证据的假设、importance=high 的不确定性里机械挑一个。
启动时写 `Decision`（kind=research）：选了什么、为什么（“你在 Chat 里要求” 或 “A001 是唯一 unexamined 的前提，且支撑 Q001”）。你下次打开前端时第一屏就是这份说明与产出。

### 5.8 blocked_on_human 与 Paper Request Queue（Phase 2，v1.3）

**登记**：精读任务发现全文不可得（非 arXiv、出版商付费墙）时调用 `request_paper(paper, why, blocking)`，工具写 `requests/RQ###.md`（仅工具可写）：
`paper`（P###，已按 DOI 登记，元数据已核实）、`task`、`status`（open / fulfilled / dismissed）、正文写为什么需要它、阻塞了什么。
Paper 的 `fulltext` 置为 requested。

**挂起**：`blocking=true` 时工具告诉 agent“本任务将挂起，记一条 checkpoint 后结束”。任务正常结束后，daemon 从 tools.jsonl 看到这次请求，
把任务置为 `blocked_on_human`（`blocked_on: RQ###`），**不占通道**，loop 继续做别的。挂起不计入被切断次数（MAX_ATTEMPTS）。
`blocking=false` 表示“有了更好，没有也能按摘要级做完”，任务不挂起。

**上传与解阻**：前端 Inbox 的“待取文献”列出 open 的请求（标题、DOI、为什么要、阻塞了哪个任务）。人上传 PDF（≤100MB）→ 后端存进
`library/P###/source.pdf`、转文本、`fulltext: uploaded`、`read` 不变，请求置 fulfilled，挂起的任务带着之前的 checkpoint 重新入队。
人也可以“拿不到”（必须写理由）→ 请求 dismissed，`fulltext: unavailable`，任务重新入队并被告知只能按摘要级处理。

### 5.9 检索关闭的执行环境（Phase 2.5，v1.4 草案）

M11 的全部价值压在“推演时物理上做不到检索”上。Phase 2.5 起手 spike（`spike/phase2.5/README.md`）用金丝雀 + 只看 tool_result + 对照组实测：

| 路径 | 结论 |
|---|---|
| WebSearch / WebFetch / Bash / ToolSearch / Agent / Task / Skill | `--tools` 不列出即在 init 里不存在，按名字调用返回 “No such tool available” |
| MCP 的 search_papers / register_paper / open_paper | server 按 `AR_TOOLSET` 注册后同样不存在；ListMcpResources / ReadMcpResource 不存在；`--strict-mcp-config` 下只有 `state` 一个 server |
| **裸 `Read`（`--allowedTools Read`）** | **宿主机任意文件可读**（对照组读到 `/etc/hostname`、列出 `/home`）。deny-list 枚举不完论文可能在的位置 |
| `--restricted` | 文件工具关进工作目录与 `--add-dir`：`/etc`、`/home`、兄弟任务目录、**工作目录里指向论文库的符号链接**全部被拒，0 泄漏 |

因此推演任务（`incubate`）的执行形态定为：

- cwd = 任务目录，里面只有 briefing.md（= 任务说明 + 基本盘快照）与任务自己的日志；**不 `--add-dir` 任何目录**（State、library 都不挂）；加 **`--restricted`**。
- `--tools Read,Grep,Glob`；deny-list = `ALWAYS_DENY` + WebSearch + WebFetch + 全部非本任务的 `mcp__state__*` + State 与 library 绝对路径的 Read/Grep/Glob 封读（纵深）。
- MCP 只注册 `check_dead_ends` `record_idea` `checkpoint`。server 端改为 **fail-closed**：`AR_TOOLSET` 缺失时只给 checkpoint（原先是全给）。
- 于是**基本盘里给多少文献，推演就只读得到多少**，这是显式决定（§5.11），不是执行层的副作用。
- runner 在开跑前自检 cmd：incubate 任务的 cmd 里出现 `--add-dir`、缺 `--restricted`、`--tools` 含 Read/Grep/Glob 以外的任何工具，直接拒绝启动（配置回归比模型越界更可能发生）。

顺带发现的 Phase 2 缺口：judge 任务同样是裸 `Read`，能读 `$AR_ROOT/run/tasks/*/briefing.md`——讨论 / 蒸馏任务的 briefing 带着“提出者=human”，
是 origin 屏蔽的一条物理旁路。**提议**：judge 与 discuss / distill 任务也加 `--restricted`（它们本就只需要 cwd + State + library），
改前用 `spike/phase2/edit_scope.py` 复测一次 read_paper 的 Edit 白名单在 `--restricted` 下仍成立。（待确认：改的是 Phase 2 已验收的行为。）

### 5.10 Idea 对象与 Assumption Trace（Phase 2.5，v1.4 草案）

`ideas/I###.md`，**只能经 `record_idea` 建**（tool_only），分诊由人经前端做。`provenance.json` 里 origin 恒为 `ai`，source 为推演任务号。

| 字段 | 约束 |
|---|---|
| `status` | `grounding`（待接地）→ `screened_out`（没过闸门，保留）/ `shortlisted`（过关，进 Inbox）→ `accepted` / `rejected`（人） |
| `falsifier` | **必填**：什么情况下这个想法是错的。与陈述不能相同 |
| `premises` | Assumption Trace：推演中**新引入**的前提，每条是一个 `A###`（见下）。`N = len(premises)`，前端显式显示。可以为空，但参数必须显式给出 |
| `challenges` | 对基本盘的显式挑战：被挑战对象的 id（A / H / D / U / E，**必须在本轮基本盘里**），每条在正文“对基本盘的挑战”一节写明挑战什么、为什么 |
| `relates_to` | 与既有假设 / 前提 / 问题 / 不确定性的关系（id 列表），正文写明是细化、对立还是新的解释 |
| `foundation` | 推演时的基本盘 `F###` |
| `chain` | 推演任务 `T#####` |
| `grounding` | 接地结论 `GR###`（工具回填，簿记归工具） |
| `promoted_to` / `decided` | 人分诊后回填：拆出的 `H###`、分诊时间 |

正文固定四节：陈述（直觉层面的断言，不要求工程细节）、推理概要（从基本盘哪几条出发、怎么走到这里）、对基本盘的挑战、与既有假设的关系。
被拒时追加“为什么不要”一节（人写的理由）。

**Assumption Trace**：`record_idea(new_premises=[...])` 里的每条前提由工具在同一次提交里建成 `A###`：`status: unexamined`、
`relied_on_by: [I###]`、`idea: I###`。带 `idea` 字段、且该 Idea 未被接受的前提是**推测性前提**：不进讨论 / 评判 briefing 的前提一章、
不进夜间预习选题、不能进验证批次——否则一晚上的推演会用几十条 AI 发明的前提淹没真正的前提集。Idea 被接受后它们成为普通前提；
被拒后工具把它们置为 `retired`。

**`record_idea` 的机械闸门**（过不了就拒绝，不是警告）：falsifier 非空且不同于陈述；**本任务里已调用过 `check_dead_ends`**（查 tools.jsonl）；
`new_premises` 显式给出；`challenges` / `relates_to` 的 id 存在且挑战对象在本轮基本盘里；当前模式是 incubation；每条推演链最多记 2 条。
工具**不接受任何自评分数**（新颖性、重要性、信心都不收）。

证据与接地的对象范围随之扩展：`evidence.target` 可以是 `I###`（接地找到直接反驳这条想法本身的原文）；`grounding.target` 可以是 `I###`。

### 5.11 Foundation（基本盘）快照（Phase 2.5，v1.4 草案）

`foundations/F###.md`，后端机械生成、进 State 的 git（tool_only）。frontmatter：`session`（进入自演进的 Decision `DEC###`）、`round`（第几轮）、
`question`（Q###）、`parent`（上一版 F###，首版无）、`delta`（相对 parent 新增的 E### / GR###）、`base`（首版取快照时 State 的 commit）。

**内容**（首版 F001 在人切进自演进时从 State 取）：

1. 形式化的研究问题（main question 正文）。
2. 已确立的事实：supported / refuted 的假设（陈述 + 结论 + 支撑它的**全文级**证据）、审视过的前提（holds / fragile）、被推翻的前提。
   证据只给**陈述、立场、强度、≤400 字引文与出处编号**——这是推演能看到的全部文献。
3. 前提集：unexamined / examined 的非推测性前提（带 relied_on_by 与 fragile）。
4. 未定的假设：proposed / investigating / inconclusive 的假设，标明“未定——不是事实，也不是约束”（Idea 要写与它们的关系）。
5. 已关闭方向：**全部** dead-end + 被人否决的 Idea（附理由），不截断。
6. 未决不确定性与待重新审视。
7. 本 session 外部核查带回的结果（首版为空，见下）。

**不含**：论文全文与阅读笔记、论文标题（出处只给编号与年份）、Insight、讨论与摘要、候选区、origin、Decision、交接记录、本 session 产出的 Idea 正文。

> **基本盘给多少文献（要你确认）**：只给证据引文，不给全文、笔记与标题。理由：证据引文就是“已确立事实”本身的依据，删掉它们基本盘就只剩结论；
> 全文与笔记是文献的框架，正是 M11 要摆脱的锚；标题（“Diffusion Policy”这类）本身就是框架的名字。代价：推演不知道某个事实出自哪篇名作，这是有意的。

**更新规则（M11.6b 的落地）**：链内冻结——推演任务拿到的是 F### 的一份拷贝，State 之后怎么变它都看不到。**只在轮与轮之间、只由本 session
接地任务产出的 E### 与 GR### 更新**：一轮的推演与接地全部结束后，后端生成 F(n+1) = F(n) + 第 7 节追加这些证据与接地结论
（接地结论只取接地任务自己的话与出处，不取 Idea 的原文）。提交信息写明 `foundation: F003 ← F002：+E031 E032（GR007 GR008）`，
`git log -p foundations/` 就是“每轮起点变了什么、因为哪条证据”。一轮没有带回任何外部证据，就不出新版，下一轮沿用。

**永不由推演自身更新**：Idea、它的前提、推演任务的 checkpoint 都不进基本盘。人在自演进期间对 State 的改动也**不进本 session 的基本盘**
（下一次进入自演进才会取到）——否则“基本盘只由外部证据推动”就多了一个口子，而且口子在人这边无从审计。

### 5.12 自演进任务契约（Phase 2.5，v1.4 草案）

| kind | profile | 可用工具 | MCP | 产出 |
|---|---|---|---|---|
| `incubate` | incubate | Read Grep Glob（`--restricted`，cwd 只有 briefing） | check_dead_ends, record_idea, checkpoint | 0–2 条 Idea（交白卷合法） |
| `grounding`（对象为 I###） | judge | Read Grep Glob WebSearch（`--add-dir` State + library） | search_papers, register_paper, open_paper, record_evidence, annotate_grounding, check_dead_ends, checkpoint | 一条 GR（只标注），可附针对该 Idea 或其前提的证据 |

**推演任务**（一条短链，timeout 30 分钟）：

- 每轮开 K 条链（默认 2，`AR_INCUBATE_CHAINS`），同一基本盘、**不同切入角度**，角度由规划器机械轮换：挑战被依赖最多的一条前提 /
  从一条已确立事实的反常处出发 / 从某个 dead-end 失败的原因找它没覆盖的变体 / 从一条 high 的不确定性出发。独立链之间的收敛是“有料”的弱证据（M11.6）。
- 协议要点：先 `check_dead_ends`（它同时列出被否决的 Idea 与本 session 已记下的 Idea 的**陈述行**，只为不重复）；推演中每引入一条基本盘里没有的前提就记下来；
  想法站不住（说不出何时是错的、依赖的新前提太多、撞上 dead-end）就不记；**本任务交白卷是合法且常见的结果**，结尾说明为什么。
  briefing 里**不出现“要产出几条”**：推演链根本不知道 3 这个目标，也就没有凑数的压力。
- 被切断：已记的 Idea 已提交；下一班带着 checkpoint 续推（M11.12，半条链是合法的可恢复状态）。

**`incubate` briefing profile**：只有“本次任务”（目标、角度、轮次）与“基本盘”（F### 原文）两章。

- **看不到 origin**：推演要能挑战基本盘里的任何前提。前提若标着“研究者提出”，挑战它的倾向会被压低——这正是 M1 说的慢动作谄媚，
  只是方向从“证据判断”换成了“敢不敢想”。origin 也不是关于世界的事实，对推演没有信息量。
- **看不到 Insight**：理解是解读框架，推演要能离开它；而凡是被拿来剪枝、真正起约束作用的理解，按角色规则已经另建了 Assumption（`derived_from`），
  会以前提的身份进基本盘——约束进来了，框架留在外面。另一个理由：Insight 与 Idea 形态相近，放进去最可能的结果是推演把现成的理解换个说法交回来。
  （反方意见：理解可能是好的出发点。若你想要，可以做成一个“角度”：某条链显式从一条理解出发、必须挑战它，而不是默认背景。）

**Idea 的接地任务**复用 Phase 2 的 `grounding`（judge profile：看不到 origin、看不到 Insight；Idea 恒为 ai 这一点无法屏蔽，但所有 Idea 同源，不构成组间偏差）：

- briefing 另附被核查的 Idea 全文与它的基本盘 F###。任务：找先例（有人做过吗）、找直接反驳、查它是否与基本盘矛盾而没登记为挑战、查它是否依赖了没登记的前提。
- 结论 `annotate_grounding(target=I###, verdict, refs, note, hidden_premises, silent_challenges)`：后两个是计数，是给前端的信号；正文写明是哪几条。
- 找到直接反驳时用 `record_evidence`（target 为该 Idea 或它的某条前提，追到段落，§5.5 规则照旧）。这些 E 是下一轮基本盘的唯一来源。
- **只标注，不改写**：没有 Edit；`ideas/` 是受保护路径；工具只写 GR 与 E。

### 5.13 模式与调度（Phase 2.5，v1.4 草案）

**进入**：只有人能切（前端“Incubate”，写 Decision kind=mode）。闸门：当前是讨论模式（验证模式要先收回）；**main question 的 maturity 必须是 formalized**，
否则拒绝，并说明缺什么（当前成熟度、formalized 的定义“有可测量定义”，以及问题正文里记下的未形式化之处）。通过后后端立刻生成 F001。
maturity 由人改（前端问题卡片上的编辑，或在编辑器里改）；把问题推到 formalized 本身是讨论模式的工作。

**一个 session 内的调度**（规划器机械执行，M2.0b 第 2 级）：

```text
第 r 轮（基本盘 F_r）：K 条推演链 → 每条新 Idea 一个接地任务（优先于下一条链）
全部结束 → 有新的 E/GR 就生成 F_(r+1) → 第 r+1 轮
```

**停止**（开新链之前检查，先到先停）：

1. 过关的 Idea（shortlisted）达到 3 条；
2. 本 session 消耗达到 5h 窗口的 30%（按本 session 各任务开始 / 结束时的 `five_hour` 利用率差累加；没有额度数据时按 30 个任务当量兜底）；
3. 连续两轮一条 Idea 都没有记下——**没有东西可说**；
4. 轮数达到上限（默认 8，`AR_INCUBATE_MAX_ROUNDS`）。

已派出的接地任务做完再停（否则 Idea 悬在 grounding 状态）。无人值守的额度护栏照旧：5h 利用率 ≥ `AR_UNATTENDED_CAP` 不开新任务，周额度 ≥95% 全面暂停。

**收工**：写一条 Decision（kind=research）作为本 session 的交卷：停止原因、轮数、链数、记下几条、每条没过的原因、过关的按下面的顺序列出。
**0 条过关时明确写“本轮没有值得你看的东西”**，并附被挡掉的理由——白卷也要让人看得出不是偷懒。然后 hold（kind=incubation_done），
前端横幅建议回到讨论模式去分诊。模式仍由人切回。

**与其他模式的关系**：自演进与验证互斥（交棒前要先收回）。夜间预习只在讨论模式发生，自演进期间不开。自演进期间人仍然可以在 Chat 里讨论（interactive 通道），
但讨论带来的 State 变化不进本 session 的基本盘（§5.11）。

### 5.14 质量闸门与分诊（Phase 2.5，v1.4 草案）

全部机械化，**没有一道闸门由模型给自己打分**：

| 层 | 内容 | 在哪强制 |
|---|---|---|
| 结构性门槛（M11.8） | 说得出何时是错的（falsifier）；已 check_dead_ends；新前提显式登记；挑战显式登记且指向基本盘 | `record_idea` 拒绝 |
| 接地存活（M11.7） | 接地结论为 novel 或 mixed；prior_work（已被做过）或 contradicted（被直接反驳）→ `screened_out` | 后端在接地任务结束后按 GR 机械置状态 |
| 信号（不是闸门） | **N**（它自己发明的前提数）、接地发现的未登记前提数与未登记挑战数、verdict=mixed | 前端显式显示，并用于排序 |

- **“3”是目标不是配额**：它只出现在规划器的停止条件里；推演链看不到它，闸门不因“还差几条”而放松。一条都没过就交白卷。
- **排序**（M11.10，机械）：N 小的在前；同 N 时 novel 在前于 mixed；再按接地发现的未登记前提数升序。前端 Inbox 的 Ideas 只列 shortlisted；
  screened_out 的折叠在下面，带被挡的原因，可以看但不计入待办。
- **分诊**（人）：
  - **接受**：可以当场拆出一条可证伪的假设（陈述、falsifier 预填 Idea 的、validation 必填）→ 直接建 `H###`（`idea: I###`，provenance origin=ai、source=I###），
    Idea 回填 `promoted_to`；也可以只接受、留到讨论里再拆。它的前提转为普通前提。写 Decision（kind=curation）。
  - **拒绝**：必须写理由。Idea 置 `rejected`，推测性前提置 `retired`。**与 dead-end 同等对待**：之后每次 `check_dead_ends` 与每份 briefing 的已关闭方向一章都全文列出它与理由，不截断。
    （它不写成 D###：dead-end 必须由证据关闭，而“我不要这个想法”是人的判断，不是证据。）
- 自评新颖性不算过关，最终价值只有人能判断：系统的职责是把值得你花十分钟的挑出来，不替你决定什么是好的。

---

## 6. 主要风险与待决问题

| 风险 | 说明 |
|---|---|
| **实验基座是真正瓶颈** | 本方向早期卡点不是想法而是基础设施（见 §2）。不设为显式里程碑，系统会长期停在文献综述阶段。 |
| ~~Agent 产出不可结构化~~ | **Phase 0 已证伪此风险**：15/15 trial schema 全合法，124 次 MCP 调用零失败。§0.3「agent 直接编辑文件」成立，不需要 validator + 重试兜底。 |
| 长期 State 退化 | 跑几个月后摘要失真、briefing 塞不下、检索失准。 |
| 班次切断导致工作丢失 | 增量提交协议不到位的话，5h 截断会反复吃掉进度。 |
| 额度节律未知 | 一个典型任务的消耗与一天能跑的任务数尚未实测，loop 心跳频率因此是猜的。 |
| 自动模式下的“空转” | 持续产出看似合理但无信息量的活动。M8 的进展度量是主要防线。 |
| **自演进的流畅空转** | M11 是空转风险最高的地方：产出读起来总是像洞见。Assumption Trace（M11.5）与对抗性接地（M4.6）是主要防线。 |
| **自演进的产量本身** | 若每晚产出 5 条八成像样的 idea，你的时间会全耗在分诊上。必须排序，且必须允许交白卷。 |
| **周额度被无人值守烧穿** | 单窗口填满不等于划算：真正的约束是周上限，烧穿了你就在想用的时候用不上。靠 M2.0b 第 9 级与窗口份额配置约束。 |
| 推演链的最佳长度未知 | 长程自我推理的边际收益衰减点在哪没有数据。M11.6 的短链累进是对冲，但这条设计本身也是猜的，需跑起来再调。 |
| Blackwell 工具链 | sm_120 较新，部分 robotics / sim 依赖可能尚未适配，环境搭建代价被低估。 |
| 磁盘 | 1.4T 单盘，embodied 数据集是 TB 级，几个数据集即可吃满。 |
| 人长期不在场 | 需明确：无人时系统可自主推进到什么程度必须停下等人。 |

---

## 7. 落地路线

### Phase 0 — Spike（1–2 天，其余全部等它）

只验三件事，不写正式代码：

1. **schema 合法率**：最小 State repo + 3 个 MCP tool + briefing，同一任务跑 10 次，看产出是否稳定合法。
2. **额度消耗**：一个“读 5 篇论文 + 更新假设”的任务实际消耗多少，一个 5h 窗口能跑几个。
3. **事件流粒度**：`stream-json` 是否含足够信息还原“正在读哪篇论文”这类语义。

第 1 项有三种结论对应三条路：合法率够高 → 按 0.3/0.4 走；不稳但可自动修复 → 加 validator + 重试；根本不稳 → 退回“agent 只产建议、系统落库”的保守架构，autonomy 大幅下调。**结论出来前写后续代码都是赌博。**

### Phase 1 — 记忆骨架 + 讨论模式（1–2 周）

State repo + MCP server + task runner + 班次 daemon（含额度耗尽暂停/交接/恢复）+ **前端讨论面板与候选区**。

排期把讨论模式提到了最前面：按 0.2，你的第一个使用场景就是和 agent 讨论方向，自主验证能力反而是后面的事。先交付一个“只会讨论、但不会失忆”的伙伴，比交付一个“能自主跑但你没法跟它谈”的系统有用得多。

**验收标准**：能在前端和 agent 讨论方向，讨论蒸馏出的 assumption 落进 State；开一个完全不知道前情的新 session，从 briefing 能接着谈下去；5h 窗口切断后，下一班能从交接记录接上。这条通过，需求 §8 §9 成立。

### Phase 2 — 文献闭环与交棒（2–3 周）

M4（含 Paper Request Queue 与 Grounding Pass）+ M5（含 Assumption 审视）+ 交棒机制 + M8 观察视图。系统能持续调研、更新假设，你能交棒也能收回。

### Phase 2.5 — 自演进（1 周）

M11。依赖 Phase 1 的讨论模式（形成基本盘）与 Phase 2 的 M4（事后接地），所以排在这里；但它不依赖实验能力，可以早于 Phase 3 交付。

**验收标准**：一个空闲的 5h 窗口能产出若干条带证伪条件与前提清单的 `Idea`，其中至少有你愿意认真读的；且系统在没东西可说时会交白卷，而不是硬凑。

展开（v1.4）：

1. 从前端切到自演进模式（写 Decision），入场闸门生效：问题未达 formalized 时拒绝并说明缺什么；
2. 推演 session 物理上检索不了——有 spike 实测记录（`spike/phase2.5/`），不看自述；
3. 一个空闲窗口能产出若干条带证伪条件与新前提清单的 Idea，每条都经过接地（GR）且接地没有改写它，其中至少有你愿意认真读的；
4. 基本盘只在链之间被外部证据更新，`git log -p foundations/` 能看出每轮起点变了什么、因为哪条证据；
5. 没东西可说时交白卷，不硬凑；30% 预算或 3 条过关先到先停；
6. 前端能看到每条 Idea 的证伪条件、它自己发明的 N 条前提、接地结论，并能确认 / 拒绝（拒绝写理由，与 dead-end 同等对待）。

### Phase 3 — 实验（3–4 周）

M6 + 环境隔离 + Download Broker 与网络分流 + compute backend 抽象。**先做实验基座 bootstrap**（sim benchmark + baseline 可复现训练），再做自动实验循环。

### Phase 4 — 自治与迁移

完整审批流、周期性简报、跑偏检测；系统迁移到公司服务器 + `remote-cluster` backend。
