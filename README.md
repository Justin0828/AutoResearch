# AutoResearch

长期运行的 AI Research Partner。设计见 `DESIGN.md`，进度见 `PROGRESS.md`。

## 运行（Phase 1：讨论模式）

只用系统 `python3` 的标准库，不安装任何东西。

```bash
./ar serve                 # 首次运行会在 ~/autoresearch 建 State 仓库
```

然后在本地机器上：

```bash
ssh -L 8765:localhost:8765 <这台服务器>
# 浏览器打开 http://localhost:8765
```

前端只绑 `127.0.0.1`，不做鉴权，靠 SSH 隧道访问（DESIGN.md M9）。

| 命令 | 作用 |
|---|---|
| `./ar serve` | 启动班次 daemon 与前端 |
| `./ar init` | 只建 State 仓库（从 Phase 0 fixture 播种） |
| `./ar validate` | 校验 State 仓库 |
| `./ar brief discuss --discussion DS001` | 打印一个新 session 会看到的 briefing |
| `./ar status` | 班次与额度状态 |

环境变量：`AR_ROOT`（默认 `~/autoresearch`，删掉即零残留）、`AR_PORT`（默认 8765）、
`AR_MODEL`（默认用 Claude Code 的默认模型）、`AR_WEEKLY_STOP`（周额度硬闸，默认 0.95）、
`AR_AUTO_RESUME`（窗口恢复后自动开下一班，默认 1）、`AR_DISTILL_EVERY`（每几轮人的发言
自动蒸馏一次，默认 3）。

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
