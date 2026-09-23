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
