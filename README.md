# AutoResearch

长期运行的 AI Research Partner。设计见 `DESIGN.md`，进度见 `PROGRESS.md`。

## 运行（Phase 2：讨论 + 文献验证）

### 两个目录

| 目录 | 是什么 | 谁建 |
|---|---|---|
| `~/workspace/AutoResearch` | 代码（本仓库） | 已有 |
| `~/autoresearch` | 运行环境：`state/`（Research State，独立 git 仓库）+ `run/`（任务日志、额度、班次）+ `library/`（论文原件与全文，不进 git） | **第一次 `./ar serve` 时自动创建**，不用手建 |

不需要装任何东西：只用系统 `python3` 的标准库，没有 venv、没有 pip。
想删掉整个系统的运行痕迹：`rm -rf ~/autoresearch`。

### 第一次启动

在服务器上（建议开在 tmux 里，断开 SSH 也不停）：

```bash
cd ~/workspace/AutoResearch
./ar init          # 可选：只建 ~/autoresearch/state，不启动服务。serve 也会自动做这一步
./ar validate      # 可选：确认初始 State 合法，应输出“0 个错误，0 个警告”
./ar serve         # 启动班次 daemon + 前端，监听 127.0.0.1:8765
```

在你自己的电脑上开隧道（服务器的 SSH 对外是 7400 端口，22 端口连不进来），然后用浏览器访问：

```bash
ssh -N -p 7400 -L 8765:localhost:8765 panyz@101.6.48.103
# 浏览器打开 http://localhost:8765
```

`-N` 表示只建隧道、不开远程 shell：终端会停在那里没有输出，这是正常的，保持它开着即可，
`Ctrl+C` 关掉隧道。想放到后台就加 `-f`：`ssh -fN -p 7400 -L 8765:localhost:8765 panyz@101.6.48.103`。

也可以写进 Mac 的 `~/.ssh/config`，之后只需 `ssh -N ar`：

```text
Host ar
    HostName 101.6.48.103
    Port 7400
    User panyz
    LocalForward 8765 localhost:8765
```

前端只绑 `127.0.0.1`，不做鉴权，只能经 SSH 隧道访问（DESIGN.md M9）。

### 停止

在 `./ar serve` 的终端按 `Ctrl+C`（或 `kill <pid>`）。它会切断在飞任务、写一份交接记录后退出；
下次 `./ar serve` 从交接记录接上。

### 前端里怎么用

1. 左栏「＋ 新讨论」→ 输入标题 → 在中间输入框说话，`Ctrl+Enter` 发送。
2. 每 3 轮你的发言会自动蒸馏一次；也可以点「蒸馏到候选区」手动触发。
3. 右栏「候选区」：确认入库 / 修改 / 丢弃（丢弃必须写理由）。标着「归属待你裁定」的要选 human 或 ai。
4. 「研究总览」看当前的问题、前提、假设、偏差指标；「交接」看每个班次结束时的交接记录；「活动」看任务与工具调用。
5. **交棒（Phase 2）**：Chat 顶栏「Hand off →」，从假设、前提、Inbox 里的候选中勾一批，写一句想弄清什么 → 进入验证模式。
   系统自己检索 arXiv、登记（登记即核实，查不到的论文进不来）、读全文、记录追到段落的证据，证据够了才迁移状态。
   做判断的 session 看不到谁提的假设。随时可以点「← Back to discussion」收回。
6. **建议收回**：出现重大结果（某条假设被支持 / 反驳、前提被推翻或判为脆弱、证据冲突）或批次做完，
   系统停下不再开新任务，Chat 顶部出横幅：「Back to discussion」或「Keep validating」（要写理由）。
7. **Reading 页**：Now 看在读哪篇、为什么读、谁在等你；Evidence chains 看一条结论的完整证据链——引文、所在段落原文、
   状态为什么变；Papers 看已登记的论文与笔记。
8. **待取文献**：系统需要付费墙论文时，Inbox「Papers to fetch」里会出现请求（为什么要、哪个任务在等）。
   经清华认证下载 PDF 后在这里上传，挂起的任务自动接着做；拿不到就点「Can't get it」写理由。
9. **夜间预习**：讨论模式下你离开 90 分钟后，系统会读一点文献（只读、不改任何判断）。Chat 里「Tonight, look into」
   可以写你想让它查的；不写它就挑一条未检验的前提，并在下次打开时说明选了什么、为什么。
5. 顶栏：额度条（直接读自 Claude 的 rate_limit_event）、暂停 / 恢复、「模拟切断」（测试用：杀掉在飞任务、写交接、60 秒后自动开下一班）。

### 其他命令

| 命令 | 作用 |
|---|---|
| `./ar brief discuss --discussion DS001` | 打印一个新 session 会看到的 briefing |
| `./ar status` | 班次与额度状态 |
| `git -C ~/autoresearch/state log --format='%h %an %s'` | State 的演化历史（`ar-human` / `ar-agent` / `ar-system` 区分谁改的） |

你也可以直接用编辑器改 `~/autoresearch/state/` 里的文件，daemon 每 30 秒以 `ar-human` 身份提交。

### 环境变量（都可不设）

| 变量 | 默认 | 作用 |
|---|---|---|
| `AR_ROOT` | `~/autoresearch` | 运行环境根目录 |
| `AR_PORT` | `8765` | 前端端口 |
| `AR_MODEL` | Claude Code 默认模型 | agent 用的模型 |
| `AR_WEEKLY_STOP` | `0.95` | 周额度硬闸 |
| `AR_AUTO_RESUME` | `1` | 额度恢复后自动开下一班 |
| `AR_DISTILL_EVERY` | `3` | 每几轮人的发言自动蒸馏一次 |
| `AR_UNATTENDED_CAP` | `0.60` | 无人值守（验证、预习）只在 5h 窗口利用率低于它时开新任务，其余留给你 |
| `AR_SEARCHES_PER_TARGET` | `2` | 验证时每个目标最多检索几次 |
| `AR_READS_PER_TARGET` | `6` | 验证时每个目标最多精读几篇 |
| `AR_PREP_IDLE_MIN` | `90` | 你离开多少分钟后开始夜间预习 |
| `AR_PREP_MAX_TASKS` | `6` | 每次夜间预习最多几个任务 |

例：`AR_PORT=9000 ./ar serve`。

## 编号与术语

State 里每个对象都有一个编号：前缀 + 三位数字，由系统自动分配（C002 → `candidates/C002.md`，DS001 → `discussions/DS001/`）。

**研究内容**（在 `~/autoresearch/state/`，会进 briefing）

| 前缀 | 英文 | 含义 |
|---|---|---|
| Q | Question | 研究问题。成熟度 vague（模糊）→ scoped（边界清楚）→ formalized（有可测量定义） |
| A | Assumption | 前提：研究正在**依赖**、但**没安排验证**的判断。必须写明它支撑着谁（`relied_on_by`） |
| H | Hypothesis | 假设：**已安排验证**的判断。必须写明什么结果会反驳它、怎么验证；状态变化必须附证据 |
| IN | Insight | 理解：研究形成的看法与直觉，可以是描述性的、不要求可证伪，但必须说出根基 |
| U | Uncertainty | 不确定性：影响判断、但暂时无法消除的未知 |
| E | Evidence | 证据：某篇论文对某条假设或前提是支持 / 反对 / 中立。必须写出所在段落（锚点，如 `s4.2-p1`）和原文摘录，工具会对照全文核对 |
| P | Paper | 论文：只能经工具登记，登记时去 arXiv / Crossref 核实；正文是阅读笔记。全文在 `~/autoresearch/library/P###/` |
| D | Dead end | 已关闭的方向，必须引用关闭它的证据；每个 session 展开新想法前都会看到全部 |
| X | Experiment | 实验记录（Phase 3） |

A 与 H 按**当前角色**区分，不按命题本身：同一句话被拿来排除方向、却没人验证，是 A；安排了验证，就是 H。

**过程记录**（也在 State 里）

| 前缀 | 英文 | 含义 |
|---|---|---|
| DS | Discussion | 一场讨论：完整对话记录 + 滚动摘要 |
| C | Candidate | 候选：从讨论蒸馏出、等你在 Inbox 确认的对象。确认后变成正式对象（如 C002 → A002），丢弃的连同理由保留，用于去重 |
| R | Review | 待重新审视：某个对象被推翻后，与它相关的对象各生成一条，由你判断 |
| RQ | Paper Request | 待取文献：系统需要、但拿不到全文的论文，等你上传 |
| GR | Grounding | 接地核查：对一条假设 / 前提“有没有人做过、有没有直接反驳”的结论，只标注不改写 |
| DEC | Decision | 决策记录：做了什么、为什么 |
| HO | Handoff | 交接记录：每个班次结束时机械生成，下一班的每个 briefing 都带着最新一份 |

**运行记录**（在 `~/autoresearch/run/`，不进 State）

| 前缀 | 英文 | 含义 |
|---|---|---|
| SH | Shift | 班次。Claude 额度按 5 小时窗口计、到点强制停止，所以系统按班次工作：开班 → 做事 → 因额度耗尽 / 切断 / 暂停 / 停止服务 / 崩溃而结束 → 写交接记录 → 下一班从交接接上 |
| T | Task | 一次任务：讨论回复（Reply）、蒸馏（Distill），以及验证模式的检索（Search）、精读（Read）、评估（Assess）、矛盾扫描、接地核查。日志在 `run/tasks/T#####/`，含该次的 briefing、完整事件流与 `why`（为什么派它） |

**几个动作**

- **蒸馏（Distill）**：单独的后台任务，读讨论里还没处理的部分，把研究内容提交到候选区，并更新滚动摘要。你每说 3 轮自动触发一次，也可以手动点。
- **确认 / 修改 / 丢弃**：候选只是提议。确认后才成为正式对象；修改后仍待确认；丢弃必须写理由。
- **推翻（Invalidate）**：前提不再成立。所有与它相关的对象进入待重新审视，系统不自动改写任何东西。
- **交棒 / 收回（Hand off / Back to discussion）**：模式只有你能切，每次切换都记一条 Decision。
- **评判类任务看不到归属**：检索、精读、评估、矛盾扫描、接地都用剥离了“谁提的”的 briefing，并在执行层封读
  provenance.json、讨论、候选、交接、理解、决策记录与 `.git`（实测见 `spike/phase2/README.md`）。偏差表按归属统计反驳率兜底。

## 目录

```text
autoresearch/          代码
  schema.py            State schema 的单一定义（DESIGN.md §5.1）
  store.py             State 仓库读写，持锁即提交（§5.4）
  briefing.py          Briefing / Handoff 装配器（§5.2）
  library.py           文献库：元数据核实、全文与段落锚点、引文校验（§5.5）
  papers.py            论文 / 证据 / 迁移 / 审视 / 接地 / 全文请求的状态操作（§5.5–5.8）
  modes.py, planner.py 交棒与收回；验证批次与夜间预习的机械规划（§5.7）
  observe.py           M8 观察视图：在读什么、为什么、证据链
  attribution.py       署名线索的中性化与检测（§5.6）
  mcp_server.py        Research State MCP server（由 spike 长成）
  runner.py            claude -p 执行层（M3）
  daemon.py            班次 daemon（M2）
  server.py, static/   前端（M9）
tests/                 python3 -m unittest discover -s tests -t .
spike/phase0/          Phase 0 的验证代码与结论（保持原样作为记录）
spike/phase2/          Phase 2 起手 spike（judge 封读、arXiv 网络、真实 claude 的 Edit 范围）与验收驱动
```

## 测试

```bash
python3 -m unittest discover -s tests -t .
```

测试用 `tests/fake_claude.py` 代替真实 CLI（输出同形的 stream-json，distill 模式真的
驱动 MCP server），不消耗额度。
