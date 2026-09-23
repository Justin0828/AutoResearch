#!/usr/bin/env python3
"""Phase 2 验收驱动（真实 claude、真实网络、临时 AR_ROOT）。

像前端一样只经 HTTP API 操作：交棒 → 观察自主检索 / 精读 / 评估 → 处理 Paper Request →
遇到“建议收回”时记录并继续 → 最后收回讨论模式。判定看 State、台账与 git，不看 agent 自述。

State 里除了种子，加两样东西：
- H004「OpenVLA 在 LIBERO 上的平均成功率低于 50%」+ 依赖它的 A002：一条能被全文证据明确反驳的假设，
  用来在真实链路上检验“反驳 → 待重新审视”（验收 4）；
- 为 H001 预先登记一篇只有 DOI 的期刊论文（RA-L，付费墙），用来触发 Paper Request（验收 3）。

用法：python3 spike/phase2/acceptance.py <scratch 目录>   （端口 8798；预算闸 $12 / 5h 利用率 55%）
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
ROOT = Path(sys.argv[1]).resolve() / "accept"
PORT = 8798
B = f"http://127.0.0.1:{PORT}"
LOG = ROOT.parent / "accept.log"
BUDGET_USD, BUDGET_5H = 12.0, 0.55


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def seed():
    os.environ["AR_ROOT"] = str(ROOT)
    sys.path.insert(0, str(CODE))
    from autoresearch import bootstrap, config, papers
    from autoresearch.library import Library
    cfg = config.load()
    st, _ = bootstrap.init(cfg)
    with st.tx("acceptance: 加一条可被全文反驳的假设与依赖它的前提", actor="human") as tx:
        tx.write_obj("H004", {"id": "H004", "type": "hypothesis", "status": "proposed", "confidence": "low",
                              "falsifier": "若 OpenVLA 原论文报告其在 LIBERO 四个任务套件上的平均成功率 ≥ 50%，则本假设被反驳。",
                              "validation": "文献核查：读 OpenVLA 原论文的 LIBERO 实验", "evidence": [],
                              "created": "2026-09-23"},
                     "OpenVLA 在 LIBERO 仿真基准上的平均成功率低于 50%，即通用 VLA 在该精细操作基准上尚不可用。\n")
        tx.write_obj("A002", {"id": "A002", "type": "assumption", "status": "unexamined",
                              "relied_on_by": ["H004", "Q001"], "created": "2026-09-23"},
                     "现成的通用 VLA 在 LIBERO 这类仿真操作基准上表现很差，因此不值得拿来做本项目的 baseline。\n")
        prov = st.provenance()
        prov["H004"] = {"origin": "ai", "source": "acceptance"}
        prov["A002"] = {"origin": "human", "source": "acceptance"}
        tx.write("provenance.json", json.dumps(dict(sorted(prov.items())), ensure_ascii=False, indent=1) + "\n")
    pid, _, msg = papers.register(st, Library(cfg.library), "10.1109/lra.2024.3497713",
                                  why="力/力矩反馈对接触密集操作的作用，直接检验 H001", for_targets=["H001"],
                                  found_via="研究者预先登记", actor="human")
    log("seeded H004, A002;", pid, msg)


def main():
    seed()
    env = dict(os.environ, AR_ROOT=str(ROOT), AR_PORT=str(PORT), AR_READS_PER_TARGET="2",
               AR_SEARCHES_PER_TARGET="1", PYTHONPATH=str(CODE))
    env.pop("AR_NET_FIXTURES", None)
    srv = subprocess.Popen([sys.executable, "-m", "autoresearch", "serve"], cwd=CODE, env=env,
                           stdout=open(ROOT.parent / "accept-serve.log", "w"), stderr=subprocess.STDOUT)
    log("serve pid", srv.pid)
    time.sleep(3)
    try:
        r = api("POST", "/api/mode/handoff", {"items": ["H001", "H004"],
                                               "note": "验收：H001 是开放问题；H004 应被 OpenVLA 原文反驳"})
        log("handoff", r)
        seen, holds, t0 = set(), [], time.time()
        while time.time() - t0 < 3 * 3600:
            time.sleep(20)
            tasks = api("GET", "/api/tasks?limit=100")
            cost = sum(t.get("cost_usd") or 0 for t in tasks)
            for t in reversed(tasks):
                key = (t["id"], t["status"])
                if key not in seen:
                    seen.add(key)
                    log(f"{t['id']} {t['kind']} {t.get('target') or ''} {t.get('paper') or ''} → {t['status']}"
                        f" ${t.get('cost_usd') or 0:.2f} | why: {t.get('why') or ''} | {t.get('result_brief') or t.get('error') or ''}"[:600])
            q = api("GET", "/api/shift")["quota"]
            if cost > BUDGET_USD or (q.get("five_hour") or 0) > BUDGET_5H:
                log("预算闸触发", cost, q.get("five_hour"))
                break
            for rq in api("GET", "/api/requests"):
                if rq["status"] == "open":
                    log("Paper Request", rq["id"], rq["paper"], rq["task"], rq["body"][:300])
                    time.sleep(5)
                    log("dismiss", api("POST", f"/api/requests/{rq['id']}/dismiss",
                                       {"reason": "验收脚本模拟：本次拿不到全文"}))
            m = api("GET", "/api/mode")
            if m.get("hold"):
                holds.append(m["hold"])
                log("HOLD", m["hold"])
                if m["hold"].get("kind") == "saturated" or len(holds) >= 3:
                    break
                log("continue", api("POST", "/api/mode/continue", {"reason": "验收：看批次里的其他目标"}))
        log("recall", api("POST", "/api/mode/recall", {"reason": "验收结束"}))
        log("total cost $%.2f" % sum(t.get("cost_usd") or 0 for t in api("GET", "/api/tasks?limit=100")))
        log("quota", api("GET", "/api/shift")["quota"])
    finally:
        srv.terminate()
        srv.wait(30)
        log("server stopped")


if __name__ == "__main__":
    main()
