"""M8 观察视图（Phase 2）：在读什么、为什么读、结论的完整证据链（M1.2 / M8.2 / M8.8）。

全部机械装配自 State + 任务台账 + 文献库，不调用任何模型——“为什么”必须来自记录，
而不是事后让模型编解释（M7.7）。
"""
import re

from . import planner
from .library import Library

JUDGE = {"lit_search", "read_paper", "assess", "contradiction_scan", "grounding"}


def _as_list(v):
    if v in (None, ""):
        return []
    return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]


def _paper_brief(store, pid):
    m, _ = store.read_obj(pid)
    if not m:
        return {"id": pid}
    return {k: m.get(k) for k in ("id", "title", "year", "arxiv", "doi", "url", "read", "fulltext",
                                  "venue")}


def _evidence_tasks(ledger):
    idx = {}
    for t in ledger.all():
        if t["kind"] in JUDGE:
            for c in planner.tool_calls(ledger, t["id"], "record_evidence"):
                idx[c["id"]] = t["id"]
    return idx


def chain(store, ledger, cfg, target):
    meta, body = store.read_obj(target)
    if meta is None:
        raise KeyError(f"{target} 不存在")
    lib = Library(cfg.library)
    by_task = _evidence_tasks(ledger)
    evs = []
    for m, b in store.list("evidence"):
        if m.get("target") != target:
            continue
        src = m.get("source")
        paras = {}
        if src and src.startswith("P"):
            found, _ = lib.resolve(src, _as_list(m.get("locator")))
            paras = dict(list(found.items())[:4])
        evs.append({**m, "note": b.strip(), "paper": _paper_brief(store, src) if src else None,
                    "paragraphs": paras, "task": by_task.get(m["id"])})
    changes = re.findall(r"## (状态变更|审视|被推翻|提升)[^\n]*\n\n(.*?)(?=\n## |\Z)", body or "", re.S)
    tasks = [{k: t.get(k) for k in ("id", "kind", "status", "why", "goal", "result_brief", "paper",
                                     "ended", "blocked_on")}
             for t in ledger.all() if t.get("target") == target]
    return {
        "target": dict(meta, body=(body or "").strip()),
        "evidence": evs,
        "changes": [{"kind": k, "text": v.strip()} for k, v in changes],
        "papers": [_paper_brief(store, m["id"]) for m, _ in store.list("paper")
                   if target in _as_list(m.get("for"))],
        "groundings": [dict(m, body=b.strip()) for m, b in store.list("grounding")
                       if m.get("target") == target],
        "reviews": [dict(m, body=b.strip()) for m, b in store.list("review")
                    if m.get("trigger") == target or m.get("target") == target],
        "tasks": tasks,
    }


def reading(store, ledger):
    """当前在读什么、为什么读；排队、挂起与最近完成的评判类任务。"""
    out = {"running": [], "queued": [], "blocked": [], "recent": []}
    for t in reversed(ledger.all()):
        if t["kind"] not in JUDGE:
            continue
        row = {k: t.get(k) for k in ("id", "kind", "status", "goal", "why", "target", "paper",
                                     "batch", "prep", "blocked_on", "result_brief", "started", "ended",
                                     "cost_usd")}
        if t.get("paper"):
            row["paper_info"] = _paper_brief(store, t["paper"])
        key = {"running": "running", "queued": "queued", "blocked_on_human": "blocked"}.get(t["status"])
        if key:
            out[key].append(row)
        elif len(out["recent"]) < 15:
            out["recent"].append(row)
    return out


def requests(store, ledger):
    out = []
    for m, b in store.list("request"):
        t = ledger.get(m.get("task")) if str(m.get("task", "")).startswith("T") else None
        out.append(dict(m, body=b.strip(), paper_info=_paper_brief(store, m["paper"]),
                        task_info={k: t.get(k) for k in ("id", "kind", "goal", "status")} if t else None))
    return sorted(out, key=lambda r: (r["status"] != "open", r["id"]))


def paper(store, ledger, cfg, pid):
    meta, body = store.read_obj(pid)
    if meta is None or meta.get("type") != "paper":
        raise KeyError(f"{pid} 不存在")
    lib = Library(cfg.library)
    return {"meta": meta, "body": body, "library": lib.meta(pid),
            "evidence": [dict(m, note=b.strip()) for m, b in store.list("evidence")
                         if m.get("source") == pid],
            "fulltext_available": lib.has_fulltext(pid)}
