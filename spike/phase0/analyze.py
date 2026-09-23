#!/usr/bin/env python3
"""Phase 0 spike：汇总全部 trial，回答 PROGRESS.md 里的四个问题。"""
import json
import collections
from pathlib import Path

HERE = Path(__file__).resolve().parent
runs = sorted(p for p in (HERE / "runs").iterdir()
              if p.is_dir() and (p / "validation.json").exists() and p.name != "smoke")


def stream_stats(run):
    cost = util0 = util1 = None
    week = None
    ev = collections.Counter()
    tools = []
    for line in (run / "stream.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ev[d.get("type", "?")] += 1
        if d.get("type") == "rate_limit_event":
            w = d["rate_limit_info"]["unifiedWindows"]
            u = w["five_hour"]["utilization"]
            util0 = u if util0 is None else util0
            util1 = u
            week = w["seven_day"]["utilization"]
        if d.get("type") == "result":
            cost = d.get("total_cost_usd")
        m = d.get("message")
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools.append(b["name"])
    return cost, util0, util1, week, ev, tools


by_task = collections.defaultdict(list)
for r in runs:
    v = json.loads((r / "validation.json").read_text())
    cost, u0, u1, week, ev, tools = stream_stats(r)
    v.update(cost=cost, util_start=u0, util_end=u1, week=week,
             events=sum(ev.values()), stream_tools=tools)
    by_task[v["task"]].append(v)

print("=" * 78)
for task, vs in sorted(by_task.items()):
    ok = sum(1 for v in vs if v["valid"])
    costs = [v["cost"] for v in vs if v["cost"]]
    walls = [v["wall_s"] for v in vs if v["wall_s"]]
    print(f"\n### {task}  n={len(vs)}")
    print(f"  schema 合法率 : {ok}/{len(vs)}  ({100*ok/len(vs):.0f}%)")
    print(f"  MCP 工具失败  : {sum(v['tool_failures'] for v in vs)} 次 / "
          f"{sum(v['tool_calls'] for v in vs)} 次调用")
    if costs:
        print(f"  单次成本      : 均 ${sum(costs)/len(costs):.3f}  "
              f"区间 ${min(costs):.3f}–${max(costs):.3f}")
    if walls:
        print(f"  单次耗时      : 均 {sum(walls)/len(walls):.0f}s  "
              f"区间 {min(walls):.0f}–{max(walls):.0f}s")
    nh = collections.Counter(len(v["new_hypotheses"]) for v in vs)
    print(f"  新建假设数分布: {dict(sorted(nh.items()))}")
    warn = collections.Counter(w.split(":")[1].strip()[:50] for v in vs for w in v["warnings"])
    if warn:
        print(f"  软警告        : {dict(warn)}")
    errs = [e for v in vs for e in v["errors"]]
    if errs:
        print(f"  !! 错误       : {errs}")
    tc = collections.Counter(t for v in vs for t in v["stream_tools"])
    print(f"  工具使用(总)  : {dict(tc.most_common())}")
    # 越界工具：不在白名单也不在 deny-list 预期内
    expected = {"Read", "Glob", "Grep", "Write", "Edit", "TodoWrite"} | \
               {f"mcp__state__{x}" for x in ("check_dead_ends", "record_evidence",
                                             "transition_hypothesis", "log_decision")}
    stray = {t: c for t, c in tc.items() if t not in expected}
    print(f"  越界工具      : {stray if stray else '无 ✓'}")

allv = [v for vs in by_task.values() for v in vs]
weeks = [v["week"] for v in allv if v["week"] is not None]
utils = [(v["util_end"] - v["util_start"]) for v in allv
         if v["util_start"] is not None and v["util_end"] is not None]
print("\n" + "=" * 78)
print(f"额度：7 日窗口 {min(weeks):.0%} → {max(weeks):.0%}（{len(allv)} 个 trial 期间）")
if utils:
    print(f"      单 trial 的 5h 窗口增量中位数 {sorted(utils)[len(utils)//2]:.3%}")
print(f"总成本：${sum(v['cost'] for v in allv if v['cost']):.2f}")
