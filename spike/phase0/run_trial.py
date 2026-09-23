#!/usr/bin/env python3
"""Phase 0 spike：跑一次 trial —— 用 claude -p 驱动一个研究任务，落到独立的 State 副本上。

测四件事（见 PROGRESS.md）：
  1. agent 直接编辑 State 文件的 schema 合法率
  2. 一个典型任务的额度/token 消耗
  3. stream-json 事件粒度够不够还原“正在做什么”
  4. （另一个任务变体）讨论蒸馏成候选对象的质量
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture" / "state"

# 关键发现（smoke test）：--allowedTools 只是"免批准白名单"，不阻止其他工具。
# smoke test 里 agent 在白名单外调用了 Bash 和 ToolSearch。真正禁用要靠
# --disallowedTools。这直接影响 DESIGN.md M2.5 的自治分级与 M11 的"物理上
# 无法检索"——若只靠 allowedTools，那些保证都是假的。
DISALLOWED = ["Bash", "ToolSearch", "WebSearch", "WebFetch", "Task", "NotebookEdit"]

ALLOWED = [
    "Read", "Glob", "Grep", "Write", "Edit",
    "mcp__state__check_dead_ends",
    "mcp__state__record_evidence",
    "mcp__state__transition_hypothesis",
    "mcp__state__log_decision",
]

PROTOCOL = """你在一个持久的研究状态仓库（Research State）里工作。这个仓库是整个研究项目的
唯一真相来源，你这次 session 结束后会被丢弃，但仓库会留下来给下一个 session。

仓库结构：
  problem.md      当前研究问题
  hypotheses/     假设，H###.md
  assumptions/    前提，A###.md
  evidence/       证据，E###.md（**只能由 record_evidence 工具创建，不要手写**）
  papers/         论文笔记，P###.md
  dead-ends/      已关闭的方向，D###.md
  decisions/      决策记录（只能由 log_decision 工具创建）

文件格式：YAML frontmatter + markdown 正文。新建 hypotheses/ 文件必须包含这些字段：
  id / type / origin / status / confidence / falsifier / evidence / created
其中 status 取 proposed|investigating|supported|refuted|inconclusive|abandoned，
origin 取 human|ai，confidence 取 low|medium|high，
falsifier 必须写明“什么结果会反驳这条假设”，evidence 是列表如 [] 或 [E001]。

规则：
- 假设的状态变更只能通过 transition_hypothesis 工具，且必须附带真实的 evidence id。
- 证据只能通过 record_evidence 工具记录，必须有出处。
- 展开任何新方向前先调用 check_dead_ends。
- 不要编造 id。不要修改 papers/ 和 dead-ends/ 下的文件。
"""

TASKS = {
    # 任务 A：State 操作与 schema 合规（测 1/2/3）
    "evidence": """阅读 papers/ 下的全部论文笔记，以及 problem.md、hypotheses/ 和 assumptions/。

然后：
1. 先调用 check_dead_ends，确认你接下来的思路没有重走已关闭的方向。
2. 为 H001 和 H002 分别记录你能从论文里找到的证据（支持和反对都要记），用 record_evidence。
3. 在证据足够时，用 transition_hypothesis 更新假设状态；证据不足就不要动状态。
4. 如果你在阅读中发现了一条现有假设没有覆盖的新假设，在 hypotheses/ 下按上面的格式新建一个文件。
5. 用 log_decision 记录你这次做了什么判断、为什么。

注意 papers/ 里存在互相冲突的结果，这是真实情况，你需要处理它而不是回避。""",

    # 任务 B：讨论蒸馏（测 4）
    "distill": """下面是我（研究者）和你之前的一段讨论记录。请把它蒸馏成候选的结构化对象。

<讨论记录>
我：我总觉得现在这些 VLA 模型在精细操作上差得远，但说不清差在哪。
你：可以先区分几种可能——是感知不够、表示不够、还是数据不够？
我：感知我觉得不是主要问题，现在的视觉编码器已经很强了。哦对了昨天那个 demo
    视频你看了吗，那个机器人叠衣服叠得还挺像样的。
你：叠衣服是柔性物体，和刚体精密装配的难点不太一样。
我：也是。我怀疑是动作表示的问题，就是说模型输出的动作太粗了，接触的那一瞬间
    需要很精细的调整，但 chunk 一大就糊过去了。
你：那如果是这样，单纯加数据应该没用。
我：对，我们之前试过加数据，确实到一定程度就上不去了。
你：另一种解释是上层传下来的信息里根本没有接触相关的语义。
我：这个也有可能。不过我更倾向于前一个。
我：今天先聊到这，我去开会了。
</讨论记录>

把其中真正构成研究内容的部分蒸馏出来，写成 hypotheses/ 下的候选假设文件（格式见上，
origin 写 ai 或 human，按谁先提出来算）。注意：**不是每句话都该变成一条假设**——
闲聊、已经被否定的猜测、以及跑题的内容不要收进去。最后用 log_decision 说明你收了
哪些、丢了哪些、为什么。

本任务不要调用 record_evidence 和 transition_hypothesis。""",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(TASKS), default="evidence")
    ap.add_argument("--out", required=True, help="本次 trial 的输出目录")
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    out = Path(a.out).resolve()   # 必须绝对：cwd 会被切到 state
    out.mkdir(parents=True, exist_ok=True)
    state = out / "state"
    shutil.copytree(FIXTURE, state)

    tool_log = out / "tools.jsonl"
    mcp_cfg = out / "mcp.json"
    mcp_cfg.write_text(json.dumps({"mcpServers": {"state": {
        "command": sys.executable,
        "args": [str(HERE / "mcp_state_server.py")],
        "env": {"STATE_ROOT": str(state), "TOOL_LOG": str(tool_log)},
    }}}, ensure_ascii=False, indent=2), encoding="utf-8")

    sid = str(uuid.uuid4())
    cmd = [
        "claude", "-p", TASKS[a.task],
        "--output-format", "stream-json", "--include-partial-messages", "--verbose",
        "--session-id", sid,
        "--mcp-config", str(mcp_cfg), "--strict-mcp-config",
        "--allowedTools", *ALLOWED,
        "--disallowedTools", *DISALLOWED,
        "--permission-mode", "acceptEdits",
        "--add-dir", str(state),
        "--append-system-prompt", PROTOCOL,
    ]
    if a.model:
        cmd += ["--model", a.model]

    t0 = time.time()
    with open(out / "stream.jsonl", "w", encoding="utf-8") as f:
        p = subprocess.run(cmd, cwd=state, stdout=f,
                           stderr=subprocess.PIPE, text=True, timeout=a.timeout)
    dt = time.time() - t0

    (out / "meta.json").write_text(json.dumps({
        "task": a.task, "session_id": sid, "returncode": p.returncode,
        "wall_seconds": round(dt, 1), "stderr": p.stderr[-4000:],
        "cmd": cmd,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{a.task}] rc={p.returncode} {dt:.0f}s -> {out}")
    if p.returncode != 0:
        print(p.stderr[-1500:], file=sys.stderr)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
