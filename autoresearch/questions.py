"""让研究收敛（DESIGN.md §5.16）：问题的生命周期、活跃集、问题树。

结一个问题和撤下一样，不删任何东西，只改变它在 briefing 与界面中的位置；结了的可以重开。
所有“结”都由人确认（agent 只能经 resolve 候选提议），每次结 / 重开都写 Decision，正文追加状态史，陈述不动。
活跃标记与母问题只由人经前端改，每次一次提交（actor human）。
"""
from . import attribution, candidates, schema
from .store import today

CLOSED = tuple(schema.CLOSED_QUESTION)          # answered / decided / merged
RES_LABEL = {"answered": "已回答", "decided": "已拍板", "merged": "已合并"}


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _question(store, qid):
    meta, body = store.read_obj(qid) if schema.kind_of_id(qid or "") else (None, None)
    if meta is None or meta.get("type") != "question":
        raise ValueError(f"{qid or '（空）'} 不是已存在的问题")
    return meta, body


def main_question(store):
    return store.project()[0].get("main_question")


# ================================================================ 结 / 重开（§5.16.1）

def check_resolve(store, qid, resolution, answered_by=None, merged_into=None, *, allow_candidates=False):
    """结一个问题前的校验（候选提交时与确认时共用）。返回问题的 (meta, body)。"""
    meta, body = _question(store, qid)
    if resolution not in CLOSED:
        raise ValueError("resolution 必须是 answered（已被回答）/ decided（本质是选择，已拍板）/ merged（与另一个问题重复）。")
    if qid == main_question(store):
        raise ValueError(f"{qid} 是项目的主问题，不能结：要结它先把主问题改指向别的问题。")
    st = schema.question_status(meta)
    if st != "open":
        raise ValueError(f"{qid} 已是 {st}，只有 open 的问题能结（已结的要先重开）。")
    if resolution == "answered":
        ids = _as_list(answered_by)
        if not ids:
            raise ValueError("answered 必须给 answered_by：回答它的理解 IN### 或假设 H###。")
        for r in ids:
            p = schema.split_id(r)[0]
            if p == "C" and allow_candidates:
                cm, _ = store.read_obj(r)
                if not cm or cm.get("type") != "candidate" or cm.get("kind") not in ("insight", "hypothesis"):
                    raise ValueError(f"answered_by 中的 {r} 不是 insight / hypothesis 候选。")
                if cm.get("status") == "rejected":
                    raise ValueError(f"answered_by 中的 {r} 已被丢弃，不能用它结问题。")
                continue
            if p not in ("IN", "H") or not store.exists(r):
                raise ValueError(f"answered_by 中的 {r} 不存在或不是 IN### / H###"
                                 + ("（也可以引用同批提交的 insight / hypothesis 候选 C###）" if allow_candidates else "") + "。")
            m, _ = store.read_obj(r)
            if schema.is_withdrawn(m) or m.get("status") == "abandoned":
                raise ValueError(f"{r} 已被放弃 / 撤下，不能拿它当答案。")
    if resolution == "merged":
        if not merged_into:
            raise ValueError("merged 必须给 merged_into：并入哪个问题 Q###。")
        if merged_into == qid:
            raise ValueError("不能把问题并入它自己。")
        tm, _ = _question(store, merged_into)
        if schema.question_status(tm) != "open":
            raise ValueError(f"{merged_into} 已是 {schema.question_status(tm)}：只能并入 open 的问题。")
    return meta, body


def resolve(store, qid, resolution, *, reason, answered_by=None, merged_into=None, decision=None,
            candidate=None, actor="human"):
    """结问题（只有人能做）。返回 (curation Decision, research Decision 或 None)。"""
    meta, body = _check_write(store, qid, resolution, reason, answered_by, merged_into, decision)
    with store.tx(f"question {qid}: 结为 {resolution}", actor=actor) as tx:
        out = _write_resolve(tx, qid, meta, body, resolution, reason, _as_list(answered_by), merged_into,
                             decision, candidate)
        tx.note = out[0]
    return out


def _check_write(store, qid, resolution, reason, answered_by, merged_into, decision):
    if not (reason or "").strip():
        raise ValueError("结一个问题要写理由：依据什么、为什么认为可以结了。")
    if resolution == "decided" and not (decision or "").strip():
        raise ValueError("decided 要写决定本身：选了什么、为什么、什么情况下重议。")
    return check_resolve(store, qid, resolution, answered_by, merged_into)


def _write_resolve(tx, qid, meta, body, resolution, reason, answered_by, merged_into, decision, candidate):
    research = None
    if resolution == "decided":
        research = tx.new_id("decision")
        tx.write_obj(research, {"id": research, "type": "decision", "kind": "research",
                                "refs": [qid] + ([candidate] if candidate else []), "created": today()},
                     f"## 做了什么\n\n就 {qid} 拍板。\n\n## 决定\n\n{decision.strip()}\n")
    target = {"answered": answered_by, "decided": [research], "merged": [merged_into]}[resolution]
    did = tx.new_id("decision")
    what = {"answered": f"由 {', '.join(answered_by)} 回答",
            "decided": f"已作决定，见 {research}",
            "merged": f"并入 {merged_into}"}[resolution]
    tx.write_obj(did, {"id": did, "type": "decision", "kind": "curation",
                       "refs": [qid] + target + ([candidate] if candidate else []),
                       "resolution": resolution, "candidate": candidate, "created": today()},
                 f"## 做了什么\n\n结 {qid}（open → {resolution}）：{what}。"
                 + (f"来自候选 {candidate}。" if candidate else "")
                 + "不删任何东西，可以重开。\n\n## 为什么\n\n" + reason.strip() + "\n")
    meta["status"] = resolution
    meta[schema.CLOSED_QUESTION[resolution]] = target if resolution == "answered" else target[0]
    meta.pop("active", None)            # 结了就不再是“正在想的”
    tx.write_obj(qid, meta, body.rstrip() + f"\n\n## 结（{today()}）\n\nopen → {resolution}：{what}，见 {did}。\n")
    return did, research


def reopen(store, qid, reason, actor="human"):
    """重开：回到 open，清掉 answered_by / decided_by / merged_into（旧值留在 Decision 与正文状态史里）。"""
    if not (reason or "").strip():
        raise ValueError("重开要写理由")
    meta, body = _question(store, qid)
    st = schema.question_status(meta)
    if st not in CLOSED:
        raise ValueError(f"{qid} 是 {st}，没有结，不需要重开" + ("（撤下的用恢复）" if st == "withdrawn" else "") + "。")
    f = schema.CLOSED_QUESTION[st]
    old = ", ".join(_as_list(meta.get(f)))
    with store.tx(f"question {qid}: 重开", actor=actor) as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "curation",
                           "refs": [qid] + _as_list(meta.get(f)), "reopened": st, "created": today()},
                     f"## 做了什么\n\n重开 {qid}（{st} → open）。此前 {f}: {old}。\n\n## 为什么\n\n{reason.strip()}\n")
        meta.pop("status", None)
        for k in schema.CLOSED_QUESTION.values():
            meta.pop(k, None)
        # 正文里的理由中性化：评判类任务会直接读问题文件（§5.6）
        tx.write_obj(qid, meta, body.rstrip() + f"\n\n## 重开（{today()}）\n\n{st} → open（此前 {f}: {old}），见 {did}。"
                                                f"{attribution.neutralize(reason.strip())}\n")
        tx.note = did
    return did


def accept_resolve(store, cid, *, origin=None, decision=None, actor="human"):
    """确认 resolve 候选。answered_by 里引用的候选须已确认（用其 promoted_to 替换）；被拒的不能确认。"""
    cm, cb = candidates.load(store, cid)
    if cm.get("status") != "pending":
        raise ValueError(f"{cid} 已是 {cm['status']}。")
    origin = origin or cm.get("origin")
    if origin not in ("human", "ai"):
        raise ValueError("归属不明（unclear）的候选必须由人选定 origin（human / ai）后才能确认。")
    sec = candidates._sections(cb)
    res = cm.get("resolution")
    answered = []
    for r in _as_list(cm.get("answered_by")):
        if schema.split_id(r)[0] != "C":
            answered.append(r)
            continue
        rm, _ = store.read_obj(r)
        st = (rm or {}).get("status")
        if st == "accepted" and rm.get("promoted_to"):
            answered.append(rm["promoted_to"])
        elif st == "pending":
            raise PendingRefs(f"{cid} 引用的候选 {r} 还没确认：先确认它（一并确认），再确认这条。", [r])
        else:
            raise ValueError(f"{cid} 引用的候选 {r} 已是 {st}，不能用它结问题；可以丢弃这条，或编辑后再确认。")
    if res == "decided":
        decision = (decision or sec.get("陈述", "")).strip()
    qid, merged = cm["target"], cm.get("merged_into")
    reason = (sec.get("理由", "") or sec.get("陈述", "")).strip()
    if res != "decided" and sec.get("陈述", "").strip():
        reason = sec["陈述"].strip() + "\n\n" + reason
    meta, body = _check_write(store, qid, res, reason, answered, merged, decision)
    with store.tx(f"candidate {cid}: 确认结 {qid}（{res}）", actor=actor) as tx:
        did, _ = _write_resolve(tx, qid, meta, body, res, reason, answered, merged, decision, cid)
        if answered != _as_list(cm.get("answered_by")):
            cm["answered_by"] = answered          # 引用的候选已确认：换成它建出的对象
        cm.update(status="accepted", promoted_to=qid, decided=today(), origin=origin)
        tx.write_obj(cid, cm, cb)
        tx.note = did
    return qid


class PendingRefs(ValueError):
    """resolve 候选引用的候选还 pending：前端给“一并确认”。"""

    def __init__(self, msg, refs):
        super().__init__(msg)
        self.refs = refs


# ================================================================ 活跃集与问题树（§5.16.2 / §5.16.4）

def set_active(store, qid, active, actor="human"):
    meta, body = _question(store, qid)
    active = active in (True, "true", "1", 1)
    if active and schema.question_status(meta) != "open":
        raise ValueError(f"{qid} 是 {schema.question_status(meta)}：只有 open 的问题能标为活跃。")
    if active == schema.is_active(meta):
        return False
    if active:
        meta["active"] = "true"
    else:
        meta.pop("active", None)
    with store.tx(f"question {qid}: {'标为活跃' if active else '取消活跃'}", actor=actor) as tx:
        tx.write_obj(qid, meta, body)
    return True


def check_parent(store, qid, parent):
    """parent 须是已存在的问题、不是自己、不成环；主问题是树根，不挂在别处。"""
    if not parent:
        return
    pm, _ = _question(store, parent)
    if qid and parent == qid:
        raise ValueError("问题不能挂在自己下面。")
    if qid and qid == main_question(store):
        raise ValueError(f"{qid} 是主问题，是问题树的根，不挂在别的问题下。")
    seen, cur = {parent}, pm.get("parent")
    while cur:
        if cur == qid:
            raise ValueError(f"把 {qid} 挂到 {parent} 下会成环（{parent} 本身在 {qid} 之下）。")
        if cur in seen:
            break
        seen.add(cur)
        m, _ = store.read_obj(cur)
        cur = (m or {}).get("parent")


def set_parent(store, qid, parent, actor="human"):
    meta, body = _question(store, qid)
    parent = (parent or "").strip() or None
    check_parent(store, qid, parent)
    if parent == meta.get("parent"):
        return False
    old = meta.get("parent")
    if parent:
        meta["parent"] = parent
    else:
        meta.pop("parent", None)
    with store.tx(f"question {qid}: 母问题 {old or '（未归位）'} → {parent or '（未归位）'}", actor=actor) as tx:
        tx.write_obj(qid, meta, body)
    return True


def _first(body, n=160):
    from .objects import _first as f, statement_of
    return f(statement_of(body), n)


def tree(store, idx=None):
    """Research 页的问题树：根是主问题，无 parent 的非主问题归“未归位”，撤下的另列。"""
    from . import objects
    idx = idx or objects.Index(store)
    main = idx.main_question
    qs = {i: m for i, m in idx.meta.items() if m.get("type") == "question"}

    def hangs(q):
        """挂在它上面的理解与假设（经 answered_by、relates_to、informs 的反向索引）。"""
        ins, hyp = set(), set()
        for src, f in idx.incoming.get(q, []):
            if f not in ("relates_to", "informs"):
                continue
            m = idx.meta.get(src) or {}
            if m.get("type") == "insight" and m.get("status") == "active":
                ins.add(src)
            elif m.get("type") == "hypothesis" and not schema.is_withdrawn(m):
                hyp.add(src)
        for r in _as_list(qs[q].get("answered_by")):
            (ins if r.startswith("IN") else hyp).add(r)
        return sorted(ins), sorted(hyp)

    nodes = {}
    for q, m in qs.items():
        ins, hyp = hangs(q)
        rel_q = [r for r in _as_list(m.get("relates_to")) if r in qs and r != q]
        nodes[q] = {
            "id": q, "status": schema.question_status(m), "maturity": m.get("maturity"),
            "active": schema.is_active(m), "parent": m.get("parent") if m.get("parent") in qs else None,
            "text": _first(idx.body.get(q, "")), "revision": schema.revision_of(m),
            "answered_by": _as_list(m.get("answered_by")), "decided_by": m.get("decided_by"),
            "merged_into": m.get("merged_into"),
            "merged_from": sorted(x for x, xm in qs.items() if xm.get("merged_into") == q),
            "insights": ins, "hypotheses": hyp, "withdrawn": schema.is_withdrawn(m),
            "main": q == main,
            # relates_to 恰好只含一个问题：给“挂到它下面”的一键建议，不自动写入（§5.16.4）
            "suggest_parent": rel_q[0] if len(rel_q) == 1 and not m.get("parent") and q != main else None,
            "children": [],
        }
    for q, n in sorted(nodes.items()):
        if n["parent"] and not n["withdrawn"]:
            nodes[n["parent"]]["children"].append(q)
    live = [q for q, n in sorted(nodes.items()) if not n["withdrawn"]]
    return {
        "main": main if main in nodes else None,
        "nodes": nodes,
        "unplaced": [q for q in live if q != main and not nodes[q]["parent"]],
        "withdrawn": [q for q, n in sorted(nodes.items()) if n["withdrawn"]],
        "active": [q for q in live if nodes[q]["active"] and nodes[q]["status"] == "open"],
        "open": [q for q in live if nodes[q]["status"] == "open"],
    }
