# 进度追踪

> 最后更新：2026-09-24 · 当前阶段：**Phase 2.5 完成（用户已确认）；Phase 3 下一步**

## 阶段状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 需求澄清 | `CLARIFICATION.md` 15 条 | ✅ 完成 |
| 设计 v1.1 | `DESIGN.md`，11 个模块 + 7 条基础原则 | ✅ 完成 |
| Phase 0 | Spike：验证架构可行性 | ✅ 完成，见 `spike/phase0/README.md` |
| Phase 1 | 记忆骨架 + 讨论模式（1–2 周） | ✅ 完成（2026-09-23 用户确认） |
| Phase 2 | 文献闭环与交棒（2–3 周） | ✅ 完成（2026-09-23 用户确认） |
| Phase 2.5 | 想法自演进 M11（1 周） | ✅ 完成（2026-09-24 用户确认） |
| **Phase 3** | **实验 + 网络分流（3–4 周）** | ⬜ **下一步** |
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

### 验收之后、试用中追加的（均已提交，测试 49 个全过）

- **推翻的传播**（M5.6b）：假设被反驳 / 前提被推翻时，所有结构化相关对象进“待重新审视”，只提醒不改写。
- **Insight（理解）**（M1）：描述性的直觉与看法也算研究结论，不要求可证伪，但必须说出根基。
- **UI 重做**：Claude 暖橙配色、英文界面、Chat / Inbox / Research / System 四页拆分、侧边栏可收起。
- **试用中发现并修掉的**：新讨论时把问题写在标题里、以为已经发出去了（弹窗加了“第一句话”）；
  SSH 实际端口是 7400、需要 `-N`（README 已改）。
- **注意**：前端文件刷新即生效，后端改动必须重启 `./ar serve`；只刷新会出现新前端调旧接口的 404。

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

## Phase 2 结果

**起手 spike**（`spike/phase2/README.md`）：
1. judge 的绝对路径封读 `Read(//$STATE/…)` **成立**：规范化变体、相对路径、`/proc/self/root` 前缀、目录级 Grep/Glob 全部被挡（金丝雀判定，不看自述；对照组全漏，证明测试有效）。**发现并补上一个缺口：`.git/` 原先可读**，提交信息会带出归属线索。
2. 经 sing-box 访问 arXiv 可用（API 2–5s，PDF ~580KB/s，官方 HTML 全文 5s）；Semantic Scholar 无 key 429，不依赖。
3. 真实 claude 下 runner 的 Edit 提交 / 回滚成立；Edit 必须给带路径的白名单，dontAsk 下其余位置（含 cwd、宿主机）一律拒绝。

**契约**写进 DESIGN §5.5–5.8（v1.3，已确认），§5.3 补了 Phase 2 任务表。

**代码**：`library.py`（核实 + 全文 + 段落锚点 + 引文校验）、`papers.py`、`modes.py`、`planner.py`、`observe.py`、`attribution.py`，
daemon / runner / briefing / MCP server / 前端的相应扩展。测试 72 个全过（新增 17 个，假 claude + 离线网络夹具）。

### 验收（真实 claude，临时 AR_ROOT，$2.70）

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | 前端挑批次交棒、随时收回，留 Decision | ✅ Hand off 对话框（候选在交棒时确认）；交棒 / 继续验证 / 收回各写 Decision。验收脚本全程只走前端同一套 API |
| 2 | 自主检索、读真实论文，证据追到段落；证据足够才迁移；迁移的 session 看不到 origin | ✅ 7 条证据全部带锚点与逐字引文且经工具核对；证据冲突时 agent 选择不动 H001，证据充分时把 H004 判为 refuted；做迁移的 briefing 无归属字样，封读规则在 cmd 里 |
| 3 | 拿不到全文 → Paper Request，任务挂起、loop 不停；上传后解阻 | 🟡 真实 claude 对只有 DOI 的论文确实登记了请求且没有编造证据，但它判断该文只间接相关、选了不挂起，所以**挂起 → 上传 → 续跑在真实链路上没走到**；假 claude 测试与 API 冒烟（上传真 PDF → 解阻 → 续跑出证据）覆盖了它。建议你在前端验收时亲手传一次 |
| 4 | 假设被真实证据反驳 → 相关对象进“待重新审视” | ✅ H004 refuted → R001（依赖它的 A002）、R002（关联它的候选） |
| 5 | 前端看到在读哪篇、为什么、完整证据链 | ✅ Reading 页：Now（每条带 why）/ Evidence chains（引文 + 段落原文 + 状态变更理由 + 接地 + 待重新审视）/ Papers；API 已冒烟，**页面未在浏览器里看过**（我这边没有浏览器） |

### 实现中做出的、需要你知道的决定

1. **全文不进 State 的 git**，放 `$AR_ROOT/library/P###/`；Paper 只记 sha。你上传的 PDF 不可再生，将来 State 备份（M10.1）要把 library 一起带上。
2. **PDF 的锚点按页编**（`pg3-p4`），不按节：pdftotext 输出里节号和标题常被拆开、混着页码与图中文字，按节编不可靠。arXiv HTML 仍按节（`s3.2-p4`）。
3. **judge 另封 `insights/**` 与 `decisions/**`**：前者是你定的“评判看不到理解”，后者因为“人推翻前提 / 选批次”本身是权威线索。
4. **精读与评估拆成两个任务**：读一篇就迁移状态太草率；评估任务综合全部证据再决定。前提的“提升为假设”只提候选，由你决定。
5. **hold 分两种**：重大结果必须你决定；“饱和”只是第 9 级收工，若有任务在等你取论文则不算饱和，上传后自动解除。
6. **Paper frontmatter 字段级受保护**：agent 改了标题之类的字段，runner 只恢复这些字段、保留同文件里的笔记（整文件回滚在快速连续编辑时会误伤合法笔记——假 claude 测出来的竞态）。

## 验收之后

- H003 中性化已按用户确认提交（`ar-human`，ca48e49）。
- 用户重启服务后，首轮夜间预习（A001）自己跑了起来；用户上传 RT-Trajectory 走通了“请求 → 上传 → 解阻 → 精读 → 证据”（验收 3 的真实链路补上了）。
- **发现缺陷**：agent 用 arXiv 的 DOI 形式（`10.48550/arxiv.*`）登记，被当成普通 DOI，6 篇 arXiv 论文都没取到全文，其中 3 篇向研究者发了本不该发的请求。已修（按 arXiv 处理 + 406 重试），`./ar repair-papers` 已在真实 State 上补取全文并关闭那 3 条请求。只有摘要时读过、后来全文到了的论文，planner 会再读一次。

## Phase 2.5 进展

- 分支 `phase2.5`。Q001 仍是 scoped：用户选择**在讨论模式里一起把它形式化**，不放宽闸门、不建测试问题（2026-09-23）。
- **起手 spike**（`spike/phase2.5/README.md`，$0.95）：网络一侧三层禁用成立（WebSearch / WebFetch / Bash / ToolSearch / Agent / Skill / MCP 检索工具全部 “No such tool”）；
  **裸 `Read` 能读宿主机任意文件**，加 `--restricted` 后文件读取被关进工作目录，符号链接、兄弟任务目录、/etc、/home 全挡，0 泄漏。
  顺带发现 Phase 2 judge 任务可经 `run/tasks/*/briefing.md` 看到 origin（物理旁路），提议 judge 也加 `--restricted`。
- **契约**写进 DESIGN §5.9–5.14（v1.4），用户确认并修改（2026-09-23）：
  Phase 2 judge 不加 `--restricted`（旁路记为已知缺口）；推演看得到 Insight、看不到 origin；切入角度不固定，由提示词引导模型自选；
  prior_work 不淘汰（要的是思想不是实现），只有被直接反驳才筛掉；自演进内容（推演记录、全部 Idea）都留存，人分诊时可送验证或记成理解。

- **代码**：`incubation.py`（入场闸门、基本盘装配与轮间更新、record_idea 机械闸门、推演记录、停止与交卷、分诊）；
  tasks / runner（推演任务 `--restricted`、不挂目录、开跑自检）/ MCP（fail-closed、record_idea、接地可记证据）/ briefing / daemon（按轮调度）/ server / 前端
  （Ideas 页、Inbox 的 Ideas 分诊、Incubate 入口、问题成熟度编辑）。测试 90 个全过（新增 16 个，假 claude）。
  顺带修了两个 Phase 1/2 就有的竞态（人在回复写入的瞬间发言会被漏掉；按秒区分重跑的工具调用）。
- **真实 claude 管线冒烟**（$2.37，副本里临时 formalized，**不算验收**）：推演链只调用了 Read / checkpoint / check_dead_ends / record_idea，无检索尝试；
  两条链自选了不同角度，各出 1 条带证伪实验、N=4 的 Idea；接地把相近工作当线索（prior_work）、没改写 Idea；F002 由 4 条外部证据 + 2 条接地结论推出。
  发现基本盘里混进论文名，已修。详见 `spike/phase2.5/README.md`。
- **验收（副本，2026-09-24，$5.00）**：真实 Q001 仍是 scoped（形式化草案 C005 待研究者拍板三点），验收在 scratch 副本里用 C005 草案作 Q001 正文跑完整 session：
  2 轮 4 条推演链、4 条 Idea 全部过关、因 ≥3 条停止；基本盘 F001→F002→F003 各因外部证据更新；推演链零检索尝试；接地未改写 Idea；State 校验 0 错误。
  4 条里 3 条在挑战 Q001 的形式化本身。详见 `spike/phase2.5/README.md` 与 `runs/acceptance.md`。
  验收中修掉：`challenges` 不收 Q###；**arXiv 检索对 Python 一律 406（Phase 2 以来 search_papers 实际一直失败）**，改用 curl 兜底，补跑接地确认可用。
- **待研究者**：拍板 C005 并把 Q001 改为 formalized → 在真实 State 上跑一次自演进；读 `runs/acceptance.md` 的 4 条 Idea（验收 3 的“愿意认真读”只有你能判断）；
  浏览器里过一遍 Ideas 页与 Inbox 分诊。后端有改动，需要重启 `./ar serve`。

- **用户确认完成（2026-09-24）**：用户只验证功能、暂不做真实研究，Q001 保持 scoped（C005 形式化草案留在候选区）。
  确认前修掉：同一个 AR_ROOT 可同时起两个 `./ar serve`（第二个把第一个的在飞任务当崩溃遗留、两边一起派活）→ 单实例锁。
  验收副本留在 `~/autoresearch/demo-incubation`（可删）。

## 开始真实使用（2026-09-24）

- 用户决定清空全部研究进度、从头讨论（重点是讨论想法，不是实验）。`~/autoresearch` 已由用户删除（不备份，含 State、论文库与上传的 PDF、验收副本）。
- **新项目改由研究者在前端建立**（`DESIGN.md` §5「新项目的建立」）：State 默认建成空仓库，首次打开前端填写标题 / 描述 / 主问题 → project.md + Q001；
  建立前 daemon 不规划任务、不能开讨论，已有项目时拒绝覆盖。Phase 0 fixture 降为测试夹具（`seed=True` / `./ar init --fixture`）。
  理由：重置后旧的 Q001/A001/H001/H002/U001 会被 serve 自动种回，以“已有前提与假设”的身份影响新讨论。测试 96 个全过。
- **下一步（研究者）**：`./ar serve` → 前端建项目 → 开始讨论。
- **Phase 2.6 设计已写入 `DESIGN.md` §5.15（2026-09-24，待实现）**：用户真实使用一轮后提出——对已确认对象发起聚焦讨论、在讨论中原地修订（同级打磨也算进展，id 不变、证据保留）、
  对象详情页（点击跳转 + 折叠区块）、讨论改名 / 分组 / 置顶、对象撤下与撤回确认（§5.15.6）。必须兼容当前真实 State（DS001 一场讨论、C001–C007 均 pending，尚无已确认对象）。

## Phase 2.6 实现（2026-09-24，分支 `phase2.6`，待用户确认）

- **后端**：新模块 `objects.py`——反向索引（`Index`，每次按需扫描 State，不存储）、原地修订（`apply_revision`：一个事务里改写对象
  revision+1 / revised、写 `Decision kind=revision`（正文含修订前全文与字段）、provenance 追加 `revisions`、候选回填 `promoted_to`）、
  撤下 / 恢复（写 `Decision kind=curation`，对象记 `withdrawn_by` / `withdrawn_from`，不对账、不触发 Review）、撤回确认（唯一的真删）。
  `candidates` 支持 `kind: revision`（propose 时 `base_revision` 必须等于当前版本；确认时过时 → `StaleRevision` → HTTP 409 带回当前版本，前端显式 force），
  普通候选确认时保留 `relates_to`。`record_evidence` 自动记 `revision`。讨论 frontmatter 加 `focus` / `group` / `pinned`，经 `/api/discussions/<ds>/meta`
  与 `/api/discussion-groups/rename` 写。briefing：聚焦讨论的 discuss / distill 在第 1 章后加 2b（对象全文、修订史、反向索引、其他聚焦讨论的摘要），
  其他章节对该对象只给指针；撤下的对象不进正文，末尾各列一行。discuss / distill 协议加修订规则与成熟度定义（“不以提升成熟度为目标”）。
  schema：question.status、uncertainty 的 withdrawn、decision kind=revision、候选 kind=revision / target、通用可选 `relates_to` `withdrawn_by` `revision`；
  hypothesis 的 abandoned 不再要求 evidence 非空；讨论的 focus 须存在；引用已撤下对象的 pending 候选给警告。自演进基本盘排除 withdrawn 的不确定性
  （retired / abandoned 本来就不进）；验证批次对它们本来就拒收。
- **前端**：对象详情页 `#/obj/<id>`（区块折叠 + 计数，默认只展开“待确认的修订”，折叠状态存 localStorage 且全部 try/catch）；Research 页改紧凑行；
  讨论正文、候选、证据、Ideas 里的对象 id 自动成为链接；修订确认对话框新旧对照、可改文字、question 在此选成熟度、改证伪条件且已有证据时显式警告、
  过时候选需“Confirm anyway”；Inbox 修订卡与 “Accept & discuss”；讨论列表 置顶 → 分组（可折叠）→ 未分组，聚焦讨论带目标 id 标签；
  撤下 / 恢复 / 撤回确认按钮，撤回不可用时说明被谁用到（英文，由后端给结构化原因）。
- **实现中对 §5.15 的补充**（已写回 DESIGN §5.15.3 / §5.15.6）：`base_revision` 在提交时就必须等于当前版本；修订候选只出自讨论；
  insight 修订候选确认时新 IN 的 provenance 记候选的 origin；被撤回的 id 不复用（Decision 记 `undid`，`next_id` 跳过）；
  “被用到”= 任何对象 / 候选 / 决策的任何字段引用了它、讨论聚焦它、证据指向它（出处讨论与产生它的候选除外）——所以撤下再恢复过的对象也不能再撤回。
- **测试 118 个全过**（新增 `tests/test_phase26.py` 22 个；假 claude 加 `distill_revise` 模式，回复里带 `[聚焦:<id>]` 标记）。
- **兼容验证（真实 State 的 scratch 副本，假 claude，端口 8799，`~/autoresearch` 未动）**：新代码下校验 0 错误 0 警告；DS001 继续对话正常；
  C001–C007 全部照常确认（C002 / C003 归属 unclear，选定后确认），确认后都带 `relates_to: [Q001]`。
  在副本上用 jsdom 跑真实 `app.js` 走完：Q002 → Discuss this（DS002，briefing 含 2b）→ 蒸馏出修订候选 C008 → 对话框确认 → Q002 v2，
  修订史、DS002 / DS001、Q001 关联都在；Q003 撤回确认 → C002 回 pending，再确认得 Q006（不复用 Q003）；Q004（被 DS003 聚焦）撤回被拒并列出原因；
  Q004 撤下 → Research 折进 Withdrawn → 恢复；讨论改名 / 分组 / 置顶 / 组改名；两条同基版本的修订先确认一条，另一条走“Confirm anyway”→ v3。全程无 JS 错误。
  没有跑真实 claude：修订候选能否被真实 agent 产出，留给用户在真实使用中观察。
- **待用户**：浏览器里过一遍（对象页、修订对话框、讨论列表）；后端有改动，需要重启 `./ar serve`。

## 已知缺口（Phase 2 之后）

- 挂起 → 上传 → 续跑待真实链路验证一次（见验收 3）。
- Memory Compaction（除滚动摘要外）、State 远端备份未做；讨论 briefing 仍每轮全量装配，对象多了需要 M1.8 分层摘要。
- 讨论 agent “收尾时问今晚查什么”只写进了协议，真实对话里还没观察到。
- 对象正文的署名线索只能靠写入时中性化 + 警告，屏蔽依然不完美；偏差表按 origin 统计兜底。


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
- 自演进契约（检索关闭 = --tools + MCP 按 toolset + --restricted；基本盘只给证据引文；只由接地带回的 E/GR 在轮间更新；只有被直接反驳才筛掉）→ §5.9–5.14（2026-09-23 用户确认）
- 夜间预习保留，选题须显式问人或据进展拟定，次日说明理由 → M2.0
- 周额度 ≥95% 全面暂停（保护项目外的 CC 使用），不单独限额 → M2.1
- `origin` 记录但在证据判断时对 agent 屏蔽，仅用于溯源/路由/偏差检测 → M1
- origin 存 provenance.json 而非对象文件；Assumption/Hypothesis 以 relied_on_by / validation 结构化区分 → §5.1
- 交接记录机械生成、不依赖 agent；与 briefing 共用一个装配器 → §5.2
- 三层禁用：--tools 移除 + --disallowedTools 兜底 + MCP 按 toolset 注册 → §5.3
- 新增 Insight（理解）：研究的产出，可以是描述性直觉、不要求可证伪，但必须说出根基；AI 可提（经候选区、根基须指向 State）；评判类任务看不到；凭理解剪枝须另建 Assumption（derived_from）→ M1（2026-09-23 用户确认）
- 推翻的传播：假设被反驳 / 前提被推翻时，所有结构化相关对象进“待重新审视”（Review），只提醒不改写，幂等对账覆盖所有写入路径 → M5.6b（2026-09-23 用户同意）
- Phase 2 四个契约（Paper 与证据出处、评判类任务、模式与交棒、Paper Request Queue）→ §5.5–5.8（2026-09-23 用户确认）
- 摘要级证据不能 strong、不能单独判活判死；无人值守只用 5h 窗口的 60%，每目标检索 ≤2、精读 ≤6 → §5.5 / §5.7（用户确认）
- H002 归属保持 disputed，偏差表单列不计入 → §5.6（用户决定）

## 已知风险（完整列表见 `DESIGN.md` §6）

最需要盯的两条：

1. **实验基座是真正瓶颈**。这个方向早期卡点不是想法而是基础设施。不把"建立可复现实验基座"设为显式里程碑，系统会长期停在文献综述阶段，拿不出能区分假设的证据。
2. **自演进的流畅空转**。M11 产出读起来总是像洞见。Assumption Trace 与对抗性接地是主要防线，但有效性未验证。
