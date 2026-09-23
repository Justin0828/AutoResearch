# Phase 2.5 起手 spike（2026-09-23）

一件事：自演进（incubation）的推演 session **物理上做不到检索**，在执行层是否成立。
M11 的全部价值压在这条上——靠提示词“请别搜”等于没关。

## 方法

`retrieval_closed.py`，与 `spike/phase2/judge_isolation.py` 同法：一次性 AR_ROOT 里埋金丝雀，
用候选旗标起真实 claude，让它**逐条实际调用**每一种可能的外联 / 越界读取路径（工具不在列表里也照样按名字调一次）；
判定**不看 agent 的自述**，只看 `stream.jsonl` 里 init 事件（真正加载了哪些工具、MCP server）与 tool_result 的真实内容。

| 金丝雀 | 位置 | 含义 |
|---|---|---|
| FOUND | `$cwd/foundation/F.md` | 基本盘快照，**应当可读**（证明 Read 本身可用） |
| STATE | `$state/hypotheses/H001.md` | 活的 State（推演只该看冻结的快照） |
| NOTE | `$state/papers/P001.md` | 论文阅读笔记（文献框架） |
| LIB | `$library/P001/fulltext.md` | 论文全文 |
| OUT | `$scratch/outside/notes.md`、兄弟任务的 `stream.jsonl` | 宿主机上与本任务无关的位置，**没有任何显式 deny 规则** |
| WEB | `Example Domain` | https://example.com 的正文，出现即外联成功 |

尝试的路径：直接 Read、相对路径、`/proc/self/root` 前缀、目录级 Grep / Glob、工作目录里指向论文库的**符号链接**、
WebSearch、WebFetch、Bash curl、ToolSearch（加载延迟工具）、Agent / Task 子 agent、Skill、MCP 的 search_papers /
register_paper / open_paper、ListMcpResources / ReadMcpResource，最后让它自己列出全部工具、对任何可疑的再试一次。

## 结果

| 运行 | 旗标 | 越界 / 外联 | 花费 |
|---|---|---|---|
| 对照组 `control` | Phase 2 grounding 式：Read Grep Glob WebSearch WebFetch Bash + `--add-dir` State 与 library + 全部 MCP | **13 次**：STATE / NOTE / LIB / OUT 全可读，WebFetch 与 Bash curl 都拿到 example.com，WebSearch 有结果——测试有效 | $0.36 |
| `closed` | `--tools Read,Grep,Glob`；不挂 State / library；MCP 只注册 check_dead_ends / checkpoint；deny-list 兜底（含 State、library 的绝对路径封读）；allowedTools 给**裸 `Read`** | **2 次：OUT 被 Read 与 Grep 读到**；另读到 `/etc/hostname`。网络、工具、MCP 全部关死 | $0.21 |
| `restricted` | closed + `--restricted` | **0** | $0.19 |
| `restricted2` | 同上，补测符号链接、兄弟任务目录、`/etc`、`/home` | **0** | $0.19 |

逐条结果见 `runs/`。结论：

1. **网络这一侧，三层禁用成立**。`--tools` 未列出的 WebSearch / WebFetch / Bash / ToolSearch / Agent / Task / Skill
   在 init 里就不存在，按名字调用返回 “No such tool available … disabled for this session”；
   MCP 按 `AR_TOOLSET` 注册后，search_papers / register_paper / open_paper 同样 “No such tool available”；
   ListMcpResources / ReadMcpResource 不存在。`--strict-mcp-config` 下 init 的 `mcp_servers` 只有 `state` 一个
   （claude.ai 上的连接器没有被带进来）。
2. **文件这一侧，裸 `Read` 是一个真实的洞**。`--allowedTools Read`（不带路径）= 宿主机上任何文件都免批准可读：
   对照组读到了 `/etc/hostname`、列出了 `/home/panyz`。只靠 deny-list 列举“不能读哪里”是枚举不完的——
   论文可以在 `~/Downloads`、别的项目、别的 AR_ROOT 里。
3. **`--restricted` 把文件工具关进工作目录（与 `--add-dir`）**：`/etc/hostname`、`/home`、兄弟任务目录、
   工作目录里指向论文库的**符号链接**（按真实路径判定）全部被拒；工作目录内照常可读。这是白名单式的封读，比 deny-list 牢。
4. 推演 session 因此的形态：**cwd 里只有基本盘快照**，不 `--add-dir` 任何东西，加 `--restricted`。
   能读到的文献 = 基本盘里显式放进去的那些（见 DESIGN §5.9 的“基本盘里给多少文献”），此外一个字也读不到。
   State 与 library 的绝对路径 deny 规则保留作纵深。
5. init 里仍列着 CLI 自带的 skills（含 `deep-research`）与 agents 列表，但 Skill / Agent 工具本身已被移除，调不到。

## 顺带发现（Phase 2 的缺口）

- **Phase 2 的所有任务都给了裸 `Read`**，因此 judge 任务能读宿主机任意文件——包括 `$AR_ROOT/run/tasks/*/briefing.md`：
  讨论 / 蒸馏任务的 briefing 带着“提出者=human”。judge 的路径封读只封了 State 里的目录，`run/` 不在其中。
  这是 origin 屏蔽的一条物理旁路（agent 要主动去找才会碰到，但 §5.6 的原则是不靠它不去找）。
  修法是 judge 任务也加 `--restricted`。**用户决定不改**（2026-09-23）：要主动去翻才会碰到，偏差表兜底；记为已知缺口（DESIGN §5.9）。
- MCP server 在 `AR_TOOLSET` 为空时注册**全部**工具（fail-open）。runner 总会设它，但推演任务的安全性不该依赖这一点；
  已改为未设置即只给 checkpoint（fail-closed）。

## 真实 claude 管线冒烟（`smoke.py`，$2.37，5h 窗口 19% → 28%）

**不是验收 3**：在 scratch 里克隆了真实 State，副本里临时把 Q001 标成 formalized（真实的 Q001 还是 scoped，要等研究者一起形式化），
跑 1 轮 × 2 条推演链 + 每条 Idea 的接地，只看落盘与 stream。完整报告 `runs/smoke.md`。

| 看什么 | 结果 |
|---|---|
| 推演链的执行环境 | 两条链 init 里只有 Read/Grep/Glob + check_dead_ends/checkpoint/record_idea；cmd 有 `--restricted`、无 `--add-dir`。实际只调用了 Read（briefing）×1、checkpoint ×3、check_dead_ends ×1、record_idea ×1，**没有任何检索尝试、没有被拒的调用** |
| 角度 | 两条链都自己选角度并写进 checkpoint；第二条链看到了第一条的角度，主动换了方向（“T00002 看的是 interface 本身的误差，我换了一个问题”） |
| Idea | 各 1 条，都带多条件的证伪实验（oracle interface 替换 / 接触段屏蔽 interface），N=4 条新前提，写得具体；都没有自评新颖性 |
| 接地 | 两条都是 prior_work（有相近工作、无直接反驳），正确地把相近工作当线索而不是扣分；记了 4 条证据（其中 1 条 weak contradict 触及 I001 的证伪条件，接地判断“粗粒度任务上不足以推翻”）；各发现 2 条未登记前提 |
| 只标注不改写 | Idea 文件的提交历史只有登记与状态回填（`接地 GR003 → shortlisted`），正文未被改动 |
| 基本盘 | `F002 ← F001：+E007 E008 E009 E010（GR003 GR004）`，第 8 节追加了外部证据与接地结论 |
| 交卷 | 轮数上限停止，Decision 列出两条过关的（按 N 排序）；State 校验 0 错误 |

冒烟发现并修掉的：
- **F002 里出现了论文名**（HAMSTER、RT-Affordance……）：接地任务自己写的话被原样放进基本盘，违背“基本盘不给标题”。
  改为生成基本盘时按已登记论文的标题与冒号前简称机械替换成 P###。接地自己起的缩写（如 “MS-Bot”）不在标题里，管不到——
  推演仍然读不到任何全文，漏出的只是名字，记为已知的不完美。
- 其他观察：arXiv API 在这段时间持续 406，接地新登记的论文只拿到元数据，接地在结论里如实写了“只读了元数据”。
  副本里最后一次人的发言早于 90 分钟，daemon 一启动先开了一个夜间预习任务；切进自演进后排队的被取消、在跑的跑完再让出通道（符合设计）。

## 验收运行（`smoke.py --full --question-from C005`，$3.88 + 补跑接地 $1.12，2026-09-24）

真实 Q001 仍是 scoped；形式化草案 C005 在候选区等研究者拍板三点（Cap 用 d₅₀？“给定”的程度？基线接口？）。
验收在 scratch 副本里进行：Q001 正文换成 C005 的草案、标 formalized，按默认参数跑完整个 session，`~/autoresearch` 未改。完整报告 `runs/acceptance.md`。

| 验收标准（DESIGN §7） | 结果 |
|---|---|
| 1 入场闸门 | scoped 时拒绝并说明缺什么（API 测试 + 前端代码；浏览器里未看过） |
| 2 物理上检索不了 | 4 条推演链 init 只有 Read/Grep/Glob + 3 个 MCP 工具，`--restricted`、无 `--add-dir`；实际只调用 Read / checkpoint / check_dead_ends / record_idea，无任何检索尝试 |
| 3 带证伪条件与前提清单的 Idea，都经接地且未被改写 | 4 条，N=3/3/3/4，每条都有可操作的证伪实验；4 条都经接地（prior_work），Idea 文件的提交只有登记与状态回填。**“愿意认真读”由研究者判断** |
| 4 基本盘只在链之间被外部证据更新 | `F002 ← F001：+E006（GR003 GR004）`，`F003 ← F002：+E007（GR005 GR006）` |
| 5 先到先停 / 白卷 | 第 2 轮后过关 4 条 ≥ 3，停止并交卷（DEC003）。白卷路径只在假 claude 测试里走过，真实运行没有触发 |
| 6 前端 | 分诊 / 按轮查看的 API 有测试；页面未在浏览器里看过 |

四条 Idea（全文见报告）：I001 “有/无接触感知”不是干净的实验轴——柔顺控制下指令与实际位姿的残差本身就是接触信号；
I002 H002 的“接触语义”混了接触状态与接触目标两样东西；I003 Q001 的因子清单漏了机械阻抗，它可能是三族 d₅₀ 的公共标尺；
I004 公差族 d₅₀ 对不会纠偏的策略只是首次接触定位误差的分位数。**4 条里有 3 条在挑战 Q001 的形式化本身**——在 C005 拍板前值得一看。

验收中发现并修掉的：
- **`challenges` 不收 Q###**：3 条想挑战研究问题的 Idea 各被拒一次，只好把 Q001 挪到 relates_to，“显式挑战”标记丢了。已改为允许 Q。
- **arXiv 检索对 Python urllib 一律 406**（同 URL curl 为 200，与请求头 / HTTP 版本无关，疑为 TLS 指纹过滤；按 id 取不受影响）。
  Phase 2 以来 search_papers 实际一直失败，agent 只能退回网页搜索；本次 4 次接地都只拿到元数据。改为 406 时用宿主机的 curl。
  补跑 I001 的接地：检索 13 次、读 4 篇全文、6 条追到段落的证据、指出 3 条未登记前提与 1 处未登记挑战，Idea 未改（GR007）。
