# AutoResearch

长期运行的 AI Research Partner。设计见 `DESIGN.md`，进度见 `PROGRESS.md`。

## 运行（Phase 1：讨论模式）

### 两个目录

| 目录 | 是什么 | 谁建 |
|---|---|---|
| `~/workspace/AutoResearch` | 代码（本仓库） | 已有 |
| `~/autoresearch` | 运行环境：`state/`（Research State，独立 git 仓库）+ `run/`（任务日志、额度、班次） | **第一次 `./ar serve` 时自动创建**，不用手建 |

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
| E | Evidence | 证据：某篇论文或某次实验对某条假设是支持 / 反对 / 中立 |
| P | Paper | 论文笔记 |
| D | Dead end | 已关闭的方向，必须引用关闭它的证据；每个 session 展开新想法前都会看到全部 |
| X | Experiment | 实验记录（Phase 3） |

A 与 H 按**当前角色**区分，不按命题本身：同一句话被拿来排除方向、却没人验证，是 A；安排了验证，就是 H。

**过程记录**（也在 State 里）

| 前缀 | 英文 | 含义 |
|---|---|---|
| DS | Discussion | 一场讨论：完整对话记录 + 滚动摘要 |
| C | Candidate | 候选：从讨论蒸馏出、等你在 Inbox 确认的对象。确认后变成正式对象（如 C002 → A002），丢弃的连同理由保留，用于去重 |
| R | Review | 待重新审视：某个对象被推翻后，与它相关的对象各生成一条，由你判断 |
| DEC | Decision | 决策记录：做了什么、为什么 |
| HO | Handoff | 交接记录：每个班次结束时机械生成，下一班的每个 briefing 都带着最新一份 |

**运行记录**（在 `~/autoresearch/run/`，不进 State）

| 前缀 | 英文 | 含义 |
|---|---|---|
| SH | Shift | 班次。Claude 额度按 5 小时窗口计、到点强制停止，所以系统按班次工作：开班 → 做事 → 因额度耗尽 / 切断 / 暂停 / 停止服务 / 崩溃而结束 → 写交接记录 → 下一班从交接接上 |
| T | Task | 一次任务：一轮讨论回复（Reply）或一次蒸馏（Distill）。日志在 `run/tasks/T#####/`，含该次的 briefing 与完整事件流 |

**几个动作**

- **蒸馏（Distill）**：单独的后台任务，读讨论里还没处理的部分，把研究内容提交到候选区，并更新滚动摘要。你每说 3 轮自动触发一次，也可以手动点。
- **确认 / 修改 / 丢弃**：候选只是提议。确认后才成为正式对象；修改后仍待确认；丢弃必须写理由。
- **推翻（Invalidate）**：前提不再成立。所有与它相关的对象进入待重新审视，系统不自动改写任何东西。

## 目录

```text
autoresearch/          代码
  schema.py            State schema 的单一定义（DESIGN.md §5.1）
  store.py             State 仓库读写，持锁即提交（§5.4）
  briefing.py          Briefing / Handoff 装配器（§5.2）
  mcp_server.py        Research State MCP server（由 spike 长成）
  runner.py            claude -p 执行层（M3）
  daemon.py            班次 daemon（M2）
  server.py, static/   前端（M9）
tests/                 python3 -m unittest discover -s tests -t .
spike/phase0/          Phase 0 的验证代码与结论（保持原样作为记录）
```

## 测试

```bash
python3 -m unittest discover -s tests -t .
```

测试用 `tests/fake_claude.py` 代替真实 CLI（输出同形的 stream-json，distill 模式真的
驱动 MCP server），不消耗额度。
