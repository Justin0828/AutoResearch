#!/usr/bin/env python3
"""Phase 2.5 真实 claude 管线冒烟（不是验收 3）。

在 scratch 里克隆一份真实 State（只读 ~/autoresearch，不改它），在副本里临时把 Q001 标成 formalized，
用正式的 Daemon 跑 1 轮 × 2 条推演链 + 每条 Idea 的接地，然后只看落盘结果与 stream 里真实的工具调用：
推演链用了哪些工具、有没有尝试检索、record_idea 被拒了几次、接地有没有改写 Idea。

用法：python3 spike/phase2.5/smoke.py <scratch 目录> [--full] [--question-from C###]
  --full               按默认参数跑完整个 session（最多 8 轮、30% 窗口、3 条过关 / 连续两轮没东西先到先停）
  --question-from C### 用候选区里的形式化草案作为 Q001 正文（仍只改副本）
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE))


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("scratch")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--question-from", default=None)
    ap.add_argument("--name", default="smoke")
    a = ap.parse_args()
    root = Path(a.scratch).resolve() / a.name
    if root.exists():
        sys.exit(f"{root} 已存在")
    src = Path.home() / "autoresearch"
    root.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(src / "state"), str(root / "state")], check=True)
    shutil.copytree(src / "library", root / "library")
    os.environ["AR_ROOT"] = str(root)
    if not a.full:
        os.environ.update(AR_INCUBATE_MAX_ROUNDS="1", AR_INCUBATE_CHAINS="2", AR_UNATTENDED_CAP="0.9")
    from autoresearch import config, incubation
    from autoresearch.daemon import Daemon
    from autoresearch.events import Bus
    from autoresearch.store import Store
    cfg = config.load()
    st = Store(cfg.state, cfg.lock)
    m, b = st.read_obj("Q001")
    m["maturity"] = "formalized"
    if a.question_from:
        cm, cb = st.read_obj(a.question_from)
        draft = cb.split("## 陈述", 1)[1].split("## 理由", 1)[0].strip()
        b = (b.split("## 当前的形式化程度")[0].rstrip() + "\n\n## 形式化（取自候选 " + a.question_from +
             " 的草案，仅用于验收副本）\n\n" + draft + "\n")
    with st.tx("验收副本: 临时把 Q001 标为 formalized" + (f"（正文取自 {a.question_from} 草案）" if a.question_from else "")
               + "——研究者尚未确认，真实 State 未改", actor="human") as tx:
        tx.write_obj("Q001", m, b)
    d = Daemon(cfg, st, Bus(cfg))
    did, fid = d.enter_incubation("验收" if a.full else "管线冒烟")   # 先切模式再启动，避免先开夜间预习
    d.start(background=True)
    print(f"进入自演进 {did}，基本盘 {fid}", flush=True)
    t0 = time.time()
    while (d.st.get("hold") or {}).get("kind") != "incubation_done":
        if time.time() - t0 > 4 * 3600:
            print("超时")
            break
        time.sleep(30)
        print(f"{int(time.time() - t0)}s", [(t["id"], t["kind"], t["status"]) for t in d.ledger.all()
                                          if t["status"] in ("running", "queued")], flush=True)
    d.stop()
    report(cfg, st, d)


def report(cfg, st, d):
    from autoresearch import incubation
    out = ["## 任务"]
    total = 0
    for t in d.ledger.all():
        total += t.get("cost_usd") or 0
        out.append(f"- {t['id']} {t['kind']} {t['status']} ${t.get('cost_usd') or 0:.2f} "
                   f"q5 {t.get('q5_start')}→{t.get('q5_end')} {t.get('angle') or ''} {(t.get('result_brief') or '')[:120]}")
    out.append(f"\n总花费 ${total:.2f}")
    out.append("\n## 推演链实际调用的工具（只看 stream）")
    for t in d.ledger.all():
        if t["kind"] != "incubate":
            continue
        tools, errors = {}, []
        uses = {}
        for line in open(cfg.tasks / t["id"] / "stream.jsonl", encoding="utf-8"):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            c = (ev.get("message") or {}).get("content") if isinstance(ev.get("message"), dict) else None
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                out.append(f"- {t['id']} init tools: {ev.get('tools')}")
            if ev.get("type") == "assistant" and isinstance(c, list):
                for x in c:
                    if x.get("type") == "tool_use":
                        tools[x["name"]] = tools.get(x["name"], 0) + 1
                        uses[x["id"]] = (x["name"], json.dumps(x.get("input"), ensure_ascii=False)[:160])
            if ev.get("type") == "user" and isinstance(c, list):
                for x in c:
                    if x.get("type") == "tool_result" and x.get("is_error"):
                        txt = x.get("content") if isinstance(x.get("content"), str) else json.dumps(x.get("content"), ensure_ascii=False)
                        errors.append((uses.get(x.get("tool_use_id")), txt[:200]))
        out.append(f"- {t['id']} 调用：{tools}")
        for e in errors:
            out.append(f"  - 被拒 / 出错：{e}")
        cmd = json.loads((cfg.tasks / t["id"] / "cmd.json").read_text())
        out.append(f"  - --restricted={'--restricted' in cmd} --add-dir={'--add-dir' in cmd}")
    out.append("\n## Ideas")
    for m, b in st.list("idea"):
        i = incubation.as_dict(st, m, b)
        out.append(f"### {i['id']}（{i['status']}，N={i['signals']['n']}，接地 {i['signals']['verdict']}，"
                   f"未登记前提 {i['signals']['hidden_premises']}）\n\n{i['statement']}\n\n- 证伪：{i['falsifier']}\n"
                   + "".join(f"- 前提 {p['id']}：{p['text']}\n" for p in i["premises"])
                   + f"- 挑战：{i['challenges']} {i['challenge_note']}\n- 关系：{i['builds_on']} {i['relates_to']}\n"
                   + "".join(f"- 接地 {g['id']}（{g['verdict']}，refs {g['refs']}）：{g['note'][:600]}\n" for g in i["grounding"]))
        log = subprocess.run(["git", "log", "--format=%an %s", "--", f"ideas/{i['id']}.md"], cwd=st.state,
                             capture_output=True, text=True).stdout
        out.append("提交历史（看接地是否改写正文）：\n" + log)
    out.append("\n## 基本盘")
    out.append(subprocess.run(["git", "log", "--format=%an %s", "--", "foundations/"], cwd=st.state,
                              capture_output=True, text=True).stdout)
    for m, b in st.list("chain"):
        out.append(f"### 推演记录 {m['id']}（{m['status']}）\n{b[:3000]}")
    hold = d.st.get("hold") or {}
    if hold.get("summary"):
        out.append(f"\n## 交卷 {hold['summary']}（{hold.get('reason')}）\n" + st.read_obj(hold["summary"])[1])
    from autoresearch import schema
    errs, _ = schema.validate_repo(st.state)
    out.append(f"\n校验错误：{errs}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
