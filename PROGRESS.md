# 进度追踪

> 最后更新：2026-09-23 · 当前阶段：**Phase 1 实现完成，三条验收均已用真实 claude 跑通；待用户试用确认**

## 阶段状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 需求澄清 | `CLARIFICATION.md` 15 条 | ✅ 完成 |
| 设计 v1.1 | `DESIGN.md`，11 个模块 + 7 条基础原则 | ✅ 完成 |
| Phase 0 | Spike：验证架构可行性 | ✅ 完成，见 `spike/phase0/README.md` |
| **Phase 1** | **记忆骨架 + 讨论模式（1–2 周）** | 🟨 **实现完成、验收已跑通，待用户试用** |
| Phase 2 | 文献闭环与交棒（2–3 周） | ⬜ |
| Phase 2.5 | 想法自演进 M11（1 周） | ⬜ |
| Phase 3 | 实验 + 网络分流（3–4 周） | ⬜ |
| Phase 4 | 完整自治 + 迁移到公司服务器 | ⬜ |

阶段内容见 `DESIGN.md` §7。

## Phase 0 结果（已完成）

15 个 trial，431s，$5.48，7 日额度窗口 19% → 19%。**四项全部通过。**

| # | 测什么 | 结果 |
|---|---|---|
| 1 | State 文件 schema 合法率 | **15/15 (100%)**，124 次 MCP 调用零失败 |
| 2 | 单任务额度消耗 | evidence $0.45 / 约 1% 的 5h 窗口；一个窗口约 100 个同量级任务 |
| 3 | `stream-json` 事件粒度 | 够；`rate_limit_event` 还额外给出精确窗口状态 |
| 4 | 讨论蒸馏质量 | 5/5 合法，去重正确，闲聊被正确丢弃 |

**结论：按原设计走**，不需要退回保守形态，autonomy 不必下调。

顺带发现 6 个设计问题，已全部改回 `DESIGN.md`。最严重的一条：
**`--allowedTools` 是免批准白名单，不具备禁用能力**——若按原假设实现，
M2.5 与 M11 的安全保证都会是假的。详见 `spike/phase0/README.md`。

## Phase 1 结果

交付「只会讨论、但不会失忆」的伙伴：State 仓库 + MCP server + task runner + 班次 daemon + 前端讨论面板与候选区。
不含自主验证、文献闭环、自演进、实验。

**起手先定了两个关节**，写进 `DESIGN.md` v1.2 §5（再动的代码）：

- §5.1 State schema：目录布局、扁平 frontmatter、全部对象的字段与枚举；§5.2 Briefing/Handoff 契约；
  另补 §5.3 Task 契约与 §5.4 增量提交协议（与前两者缠在一起，拆不开）。

**代码**：`autoresearch/`，零依赖（系统 python3 标准库）。spike 的 MCP server / 旗标组合 / 校验器 /
fixture 都是长成正式版，不是重写（spike 目录保持原样作为记录）。测试 39 个，全部通过，用假 claude 不耗额度。

### 验收（真实 claude，scratch AR_ROOT，4 个任务约 $0.96，5h 窗口 18% → 20%）

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | 前端讨论，蒸馏出的 assumption 落进 State | ✅ 2 轮讨论后自动蒸馏出 3 条候选；"interface 只给子目标"被正确归为 assumption（被依赖、未安排验证），确认后成为 A002，origin 进 provenance.json，校验 0 错误 |
| 2 | 新 session 从 briefing 接着谈 | ✅ 每一轮都是新 session（不 `--resume`），只靠 briefing。第 6 轮的 session 从滚动摘要里读到“第 2 轮凭记忆引用 OpenVLA、待核对”，主动去查证并闭合了这个待办 |
| 3 | 5h 切断后下一班从交接记录接上 | ✅ 回复中途 SIGKILL → 当场机械生成 HO001（列出被截断任务、未回复的发言、本班提交）→ 20s 后自动开 SH0002 → 重试的 briefing 含 HO001，补答成功。daemon 正常停止时也写交接（HO002） |

假 claude 的测试另外覆盖：5h 额度耗尽暂停并在 resetsAt 后自动恢复；周额度 ≥95% 时在飞任务完成提交后全面暂停、不再派发；
daemon 被 SIGKILL 后重启的崩溃恢复（遗留改动以 recovered 提交、写 crash 交接、任务重新入队）；
蒸馏中途被切断后从 checkpoint 接上且不重复提交候选；人在编辑器里直接改 State 被以 `ar-human` 身份提交。

### 行为观察（真实 claude）

- **有立场**：第一轮就反对“直接排除感知”，理由引用 H001/H002/U001，并给出能区分两种解释的对照实验。
- **分类规则生效**：讨论 agent 与蒸馏 agent 都主动按“当前角色”解释分类（“它已经被用来剪枝，却还没有安排验证”）。
- **凭记忆的引用会自我标注**，之后的 session 会去核对——这条链条是跨 session 的，只能靠摘要传递。
- 讨论 agent 在研究者说“那就排那个对照”后自行提交了一条 hypothesis 候选。协议说的是“研究者明确要求记下的才提交”，
  这次算擦边（排了验证 ≈ 要求登记假设）。候选区有人把关，暂不收紧，继续观察。

### 实现中做出的、需要你知道的决定

1. **origin 移出对象文件，统一存 `provenance.json`**（§5.1 规则 1）。理由：agent 有 Read 权限，只在 briefing 里删 origin
   是假屏蔽。已实测 `Read(path)` 拒绝规则能同时让 Grep 搜不到该文件。代价：人手改对象文件时看不到 origin，要看 provenance.json。
2. **禁用多加一层 `--tools`**（§5.3）。实测它真的从 init 的工具列表里移除未列出的内置工具，比 deny-list 牢：CLI 新版本新增的
   工具（Phase 0 的 init 里就有 CronCreate / Monitor / Workflow 等一长串未被禁用）不会漏网。`--disallowedTools` 仍保留作纵深。
3. **初始 State 没有迁入 fixture 的 P001–P003 与 D001**：它们是为 spike 合成的（P 系列自己写着“非真实论文”），进真实 State
   会让后续证据判断建立在虚构文献上。只迁了 Q001 / A001 / H001 / H002，另按 DESIGN §2.4 建议加了 U001。
4. **`$AR_ROOT` 默认 `~/autoresearch`，不在代码仓库下**：agent 的 cwd 若在代码仓库之内会自动加载开发用的 CLAUDE.md。
5. **讨论回合每轮新开 session**：每轮都在验收“不失忆”，代价是每轮重读 briefing（实测每轮 $0.14–0.31）。
   `--resume` 作为之后的省额度优化。

## 待用户拍板

1. **试用 Phase 1**：`./ar serve` + `ssh -L 8765:localhost:8765`。真实 State 会建在 `~/autoresearch`（上面的验收是在 scratch 目录跑的，
   没有动你的真实 State）。
2. **H002 的 origin 争议**：provenance.json 里标了 `disputed`，fixture 记 human，Phase 0 蒸馏多次指出讨论记录里是 AI 先提的。需要你裁定。
3. 上面“实现中做出的决定”第 1、2 条若有异议，在 Phase 2 动 judge 任务前改最便宜。

## Phase 2 起手前的已知缺口

- judge 任务的路径封读用 `Read(//绝对路径)` 语法表达（`tasks.JUDGE_DENY_PATHS`），**只在相对路径上实测过**，绝对路径形式待 Phase 2 实测。
- 交棒按钮已占位（禁用）；模式切换、夜间预习、Memory Compaction（除滚动摘要外）、State 远端备份都未做。
- 讨论 briefing 目前每轮全量装配 State；对象多起来后需要 M1.8 的分层摘要。


## 已定的关键决策（索引）

理由都写在 `DESIGN.md`，这里只做导航：

- State 是 git 仓库、agent 直接编文件 → §0.3
- 需校验的状态迁移走 MCP tool → §0.4
- 三模式（讨论 / 自演进 / 验证），切换权在人 → §0.2
- 5h 窗口是基本节拍，任务必须可被 SIGKILL → §0.5
- autonomy 落在 CLI 权限旗标上，不自造引擎 → §0.6
- 自演进：短链累进循环，基本盘只由外部证据更新 → M11.6 / M11.6b
- 无人值守窗口的九级优先级阶梯，含主动收工 → M2.0b
- 数据集按 UID 分流绕过 sing-box，不按 IP → M10
- 前端绑 127.0.0.1 + SSH 端口转发，不做鉴权 → M9
- 讨论模式提到 Phase 1（用户第一场景是对话，不是自主跑） → §7
- 自演进预算 30% 窗口上限，3 条 hint 与预算先到先停 → M2.0 / M11.7
- 夜间预习保留，选题须显式问人或据进展拟定，次日说明理由 → M2.0
- 周额度 ≥95% 全面暂停（保护项目外的 CC 使用），不单独限额 → M2.1
- `origin` 记录但在证据判断时对 agent 屏蔽，仅用于溯源/路由/偏差检测 → M1
- origin 存 provenance.json 而非对象文件；Assumption/Hypothesis 以 relied_on_by / validation 结构化区分 → §5.1
- 交接记录机械生成、不依赖 agent；与 briefing 共用一个装配器 → §5.2
- 三层禁用：--tools 移除 + --disallowedTools 兜底 + MCP 按 toolset 注册 → §5.3

## 已知风险（完整列表见 `DESIGN.md` §6）

最需要盯的两条：

1. **实验基座是真正瓶颈**。这个方向早期卡点不是想法而是基础设施。不把"建立可复现实验基座"设为显式里程碑，系统会长期停在文献综述阶段，拿不出能区分假设的证据。
2. **自演进的流畅空转**。M11 产出读起来总是像洞见。Assumption Trace 与对抗性接地是主要防线，但有效性未验证。
