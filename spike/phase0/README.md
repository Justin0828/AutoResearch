# Phase 0 Spike — 结果

验证 `DESIGN.md` 赖以成立、但此前全是猜测的四个假设。**四项全部通过**，
并顺带发现一个会让安全保证失效的设计错误。

规模：15 个 trial（10 × evidence 任务，5 × distill 任务），耗时 431s，
成本 $5.48，7 日额度窗口 19% → 19%（未动）。

## 结果

| # | 测什么 | 结果 |
|---|---|---|
| 1 | State 文件 schema 合法率 | **15/15 (100%)**，124 次 MCP 调用零失败 |
| 2 | 单任务额度消耗 | evidence $0.45 / 约 1% 的 5h 窗口；distill $0.20。一个窗口约 100 个同量级任务 |
| 3 | `stream-json` 事件粒度 | 够。`tool_use` 块可还原"正在读哪篇论文"；`rate_limit_event` 额外给出精确的窗口状态 |
| 4 | 讨论蒸馏质量 | 5/5 合法。去重正确（未重复建已存在的 H001/H002），闲聊被正确丢弃 |

**结论：按 `DESIGN.md` §0.2 / §0.3 / §0.4 的原设计走**，不需要退回
"agent 只产建议、系统落库"的保守形态，autonomy 不必下调。

## 发现的问题（已改回 DESIGN.md）

1. **`--allowedTools` 不是禁用机制**（最严重）。它是免批准白名单，不移除
   未列出的工具——smoke test 中 agent 在白名单外调用了 `Bash` 与 `ToolSearch`。
   真正的禁用要靠 `--disallowedTools` / `--restricted`。若按原假设实现，
   M2.5 的"自动档不给 Bash"与 M11 的"物理上做不到检索"都会是假的保证。
   改用 deny-list 后，10+5 次 trial 的越界工具为零。→ `DESIGN.md` §0.6

2. **`rate_limit_event` 让窗口管理不用估算**。直接给出 `five_hour.resetsAt`
   （精确时间戳）、`five_hour.utilization`、`seven_day.utilization`。
   M2.1 原写的"记录窗口起点、估算重置时间"可以删掉；M2.0b 第 9 级"留额度"
   的判断也因此有了真实依据。→ `DESIGN.md` M2.1

3. **Assumption / Hypothesis 的分类不稳定**。5 次 trial 对同一条命题
   （"感知不是瓶颈"）给出 3 种分类：2 次当假设、1 次当前提、2 次只标记。
   原定义（前提 vs 待验证命题）在这条上两边都像。改为按**当前角色**区分：
   被依赖而未安排验证 → Assumption；已安排验证 → Hypothesis。
   → `DESIGN.md` M1

4. **校验类工具应"保证可见"，不替 agent 做相关性判断**。`check_dead_ends`
   最初用词汇重叠过滤，英文查询打不中中文 dead-end，静默返回"没找到"——
   比没有这个工具更危险。改为全部列出。→ `DESIGN.md` §0.4

5. **簿记归工具**。`record_evidence` 最初不回写假设的 `evidence` 字段，
   agent 只能手动编辑补上。→ `DESIGN.md` §0.4

6. **dead-end 不应内嵌实验结果**。6/10 次 trial 把证据 `source` 写成
   dead-end id，因为 fixture 的 D001 内嵌了一份没有独立实验记录的复现结果，
   导致出处链断在 dead-end 上。→ `DESIGN.md` M1

## 值得记下的行为观察

这些不是通过/失败，是关于 agent 判断力的观察，影响 Phase 1 的协议设计：

- **没有机械套用证伪条件**。P003 字面上满足了 H001 预注册的 falsifier，
  agent 拒绝据此判 refuted，理由是 P003 用仿真脚本数据、与内部结果 D001
  冲突、且只有一种粒度无法区分两种解释。10 次 trial 中 9 次把 H001 迁到
  `investigating` 而非 `refuted`/`supported`，判断一致。
- **主动指出我们自己的 dead-end 可能关早了**（D001 只扩了 4 倍，P003 扩了
  20 倍，D001 的平台期可能只是局部平台）。
- **去重优先于产出**。distill 任务中 3/5 次一条假设都不建，理由是讨论里的
  两条解释仓库里已有，重复建会让证据被拆散。这是对的。
- **发现了 fixture 自身的不一致**：H002 记 `origin: human`，但讨论记录里
  是 AI 先提的；多次 trial 独立标记了这一点并留给人确认。

## 文件

| 文件 | 作用 |
|---|---|
| `fixture/state/` | 最小 State 仓库模板，内容用真实研究方向 |
| `mcp_state_server.py` | 手写 stdio JSON-RPC MCP server，零依赖 |
| `run_trial.py` | 跑一次 trial |
| `validate.py` | 校验产出的 State 是否合法 |
| `batch.py` / `analyze.py` | 批量与汇总 |
| `runs/` | 产出（未入库，见 .gitignore） |

复现：`python3 batch.py --evidence 10 --distill 5 --jobs 3 && python3 analyze.py`
