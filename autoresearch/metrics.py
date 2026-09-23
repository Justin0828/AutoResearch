"""M8 的 Phase 1 子集：按 origin 的反驳率偏差指标（必需项，DESIGN.md M1 / §5.2）。

屏蔽不可能完美，这是检测“人提的假设活得更久”这种慢动作谄媚的唯一仪器。
数据只来自 provenance.json + hypothesis status，不依赖任何 agent。
"""
RESOLVED = ("supported", "refuted", "inconclusive", "abandoned")


def bias_by_origin(store):
    prov = store.provenance()
    rows = {}
    for meta, _ in store.list("hypothesis"):
        origin = (prov.get(meta["id"]) or {}).get("origin", "unknown")
        r = rows.setdefault(origin, {"origin": origin, "total": 0, "resolved": 0,
                                     **{s: 0 for s in RESOLVED}, "open": 0})
        r["total"] += 1
        st = meta.get("status")
        if st in RESOLVED:
            r["resolved"] += 1
            r[st] += 1
        else:
            r["open"] += 1
    for r in rows.values():
        r["refute_rate"] = round(r["refuted"] / r["resolved"], 3) if r["resolved"] else None
    return sorted(rows.values(), key=lambda r: r["origin"])
