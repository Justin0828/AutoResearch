#!/usr/bin/env python3
"""Phase 2.5 真实 claude 管线冒烟（不是验收 3）。

在 scratch 里克隆一份真实 State（只读 ~/autoresearch，不改它），在副本里临时把 Q001 标成 formalized，
用正式的 Daemon 跑 1 轮 × 2 条推演链 + 每条 Idea 的接地，然后只看落盘结果与 stream 里真实的工具调用：
推演链用了哪些工具、有没有尝试检索、record_idea 被拒了几次、接地有没有改写 Idea。

用法：python3 spike/phase2.5/smoke.py <scratch 目录>
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
    root = Path(sys.argv[1]).resolve() / "smoke"
    if root.exists():
        sys.exit(f"{root} 已存在")
    src = Path.home() / "autoresearch"
    root.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(src / "state"), str(root / "state")], check=True)
    shutil.copytree(src / "library", root / "library")
    os.environ.update(AR_ROOT=str(root), AR_INCUBATE_MAX_ROUNDS="1", AR_INCUBATE_CHAINS="2",
                      AR_UNATTENDED_CAP="0.9")
    from autoresearch import config, incubation
    from autoresearch.daemon import Daemon
    from autoresearch.events import Bus
    from autoresearch.store import Store
    cfg = config.load()
    st = Store(cfg.state, cfg.lock)
    m, b = st.read_obj("Q001")
    m["maturity"] = "formalized"
    with st.tx("smoke: 副本里临时把 Q001 标为 formalized（仅管线冒烟，不是真实形式化）", actor="human") as tx:
        tx.write_obj("Q001", m, b)
    d = Daemon(cfg, st, Bus(cfg))
    d.start(background=True)
    did, fid = d.enter_incubation("管线冒烟")
    print(f"进入自演进 {did}，基本盘 {fid}", flush=True)
    t0 = time.time()
    while (d.st.get("hold") or {}).get("kind") != "incubation_done":
        if time.time() - t0 > 3600:
            print("超时")
            break
        time.sleep(10)
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
    from autoresearch import schema
    errs, _ = schema.validate_repo(st.state)
    out.append(f"\n校验错误：{errs}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
