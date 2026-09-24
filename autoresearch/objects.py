"""对象的持续打磨（DESIGN.md §5.15）：反向索引、原地修订、撤下 / 恢复、撤回确认。

反向索引不存储，每次按需从 State 算（State 是唯一真相来源，存反链会产生两处真相）。
所有改动都由人经后端触发，每次写一条 Decision——“为什么这个对象变成这样”必须能追溯。
"""
import re

from . import attribution, candidates, discussion, frontmatter, insights, schema
from .store import today

REVISABLE = {                      # 目标类型 → 可经修订改动的字段（§5.15.3 表）
    "question": ("maturity",),
    "assumption": ("relied_on_by", "fragile"),
    "hypothesis": ("falsifier", "validation"),
    "uncertainty": ("importance",),
    "insight": ("firmness", "change_mind"),     # 走 insights.revise（supersede），不原地改
}
LIST_FIELDS = ("relied_on_by",)
# 对象自身出现、但不算“关联对象”的字段（证据与决定各有区块）
OWN_SKIP = {"id", "type", "evidence", "withdrawn_by", "invalidated_by", "superseded_by", "created",
            "revised", "revision", "idea"}
_NOTE = re.compile(r"^（(来由见候选|第 \d+ 版).*）$")


class StaleRevision(ValueError):
    """修订候选基于旧版本起草：拒绝并带回当前版本，由人对照后决定是否 force。"""

    def __init__(self, msg, current):
        super().__init__(msg)
        self.current = current


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _first(text, n=160):
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not _NOTE.match(s):
            return s[:n] + ("…" if len(s) > n else "")
    return ""


def split_body(body):
    """对象正文 → (陈述部分, 其后的状态史各节)。状态史（## 提升 / ## 状态变更 / ## 撤下……）修订时原样保留。"""
    m = re.search(r"^## ", body or "", re.M)
    return ((body or "")[:m.start()], (body or "")[m.start():]) if m else (body or "", "")


def statement_of(body):
    """去掉 H1 标题与“来由见候选 / 第 N 版”注记后的陈述。"""
    main, _ = split_body(body)
    lines = main.strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(l for l in lines if not _NOTE.match(l.strip())).strip()


def load(store, ident):
    meta, body = store.read_obj(ident)
    if meta is None:
        raise KeyError(f"{ident} 不存在")
    return meta, body


# ================================================================ 反向索引（§5.15.4）

class Index:
    """一次扫描整个 State：谁引用了谁。"""

    def __init__(self, store):
        self.store = store
        self.meta, self.body = {}, {}
        self.incoming = {}             # id -> [(src_id, field)]
        for k in schema.KINDS.values():
            for m, b in store.list(k.type):
                if not m.get("id"):
                    continue
                m.setdefault("type", k.type)
                self.meta[m["id"]], self.body[m["id"]] = m, b
                self._scan(m["id"], m)
        self.discussions = discussion.list_all(store)
        for d in self.discussions:
            if d.get("focus"):
                self.incoming.setdefault(d["focus"], []).append((d["id"], "focus"))
        self.prov = store.provenance()
        self.main_question = store.project()[0].get("main_question")

    def _scan(self, src, meta):
        for f, v in meta.items():
            if f == "id":
                continue
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and x != src and schema.split_id(x)[0]:
                    self.incoming.setdefault(x, []).append((src, f))

    def type_of(self, ident):
        if ident.startswith("DS"):
            return "discussion"
        return (self.meta.get(ident) or {}).get("type")

    def source_candidate(self, ident):
        src = (self.prov.get(ident) or {}).get("source")
        return src if schema.split_id(src or "")[0] == "C" else None

    def users(self, ident):
        """谁在用它（结构化）：[{id, type, field}]。出处讨论与产生它的那条候选不算。"""
        own = self.source_candidate(ident)
        out = [{"id": "project", "type": "project", "field": "main_question"}] if ident == self.main_question else []
        for src, f in self.incoming.get(ident, []):
            if src != own and {"id": src, "field": f} not in [{"id": u["id"], "field": u["field"]} for u in out]:
                out.append({"id": src, "type": self.type_of(src) or "?", "field": f})
        return out

    def usage(self, ident):
        """谁在用它（撤回确认的判据），中文说明。"""
        own = self.source_candidate(ident)
        out = []
        if ident == self.main_question:
            out.append("它是项目的主问题")
        for src, f in self.incoming.get(ident, []):
            if src == own:
                continue
            t = self.type_of(src) or "?"
            if t == "discussion":
                out.append(f"讨论 {src} 聚焦于它")
            elif t == "evidence":
                out.append(f"证据 {src} 指向它")
            elif t == "decision":
                out.append(f"决策 {src} 引用了它")
            elif t == "candidate":
                out.append(f"候选 {src} 引用了它（{f}）")
            else:
                out.append(f"{src} 的 {f} 引用了它")
        return list(dict.fromkeys(out))

    def counts(self, ident):
        inc = self.incoming.get(ident, [])
        t = self.type_of
        m = self.meta.get(ident) or {}
        ev = {s for s, _ in inc if t(s) == "evidence"} | set(_as_list(m.get("evidence")))
        related = {s for s, _ in inc if t(s) not in ("evidence", "decision", "candidate", "discussion",
                                                      "review", None)}
        related |= {r for f, v in m.items() if f not in OWN_SKIP
                    for r in _as_list(v if isinstance(v, list) else [v]) if isinstance(r, str)
                    and r in self.meta and self.type_of(r) not in ("evidence", "decision", "candidate")}
        return {
            "evidence": len(ev),
            "discussions": len({s for s, _ in inc if t(s) == "discussion"}),
            "related": len(related),
            "candidates": sum(1 for s, _ in inc if t(s) == "candidate"
                              and (self.meta[s].get("status") == "pending")),
            "reviews": sum(1 for s, _ in inc if t(s) == "review" and self.meta[s].get("status") == "open"),
            "revision": schema.revision_of(m),
        }


def _brief(idx, ident):
    m = idx.meta.get(ident) or {}
    t = idx.type_of(ident)
    text = idx.body.get(ident, "")
    if t == "candidate":
        text = candidates._sections(text).get("陈述", "")
    return {"id": ident, "type": t, "status": m.get("status") or m.get("maturity"),
            "withdrawn": schema.is_withdrawn(m), "text": _first(text)}


def revision_history(store, ident):
    out = []
    for m, b in store.list("decision"):
        if m.get("kind") == "revision" and ident in _as_list(m.get("refs")):
            out.append({"id": m["id"], "created": m.get("created"), "from": m.get("from_revision"),
                        "to": m.get("to_revision"), "candidate": m.get("candidate"), "body": b.strip()})
    out.sort(key=lambda x: int(x["to"] or 0), reverse=True)
    return out


def links(store, ident, idx=None):
    """对象详情页需要的一切（§5.15.4）。"""
    idx = idx or Index(store)
    if ident not in idx.meta:
        raise KeyError(f"{ident} 不存在")
    m, body = idx.meta[ident], idx.body[ident]
    t = idx.type_of
    inc = idx.incoming.get(ident, [])
    prov = idx.prov.get(ident)
    own = idx.source_candidate(ident)

    # 证据：直接指向它的 + 它自己列的；前提另经 promoted_to / invalidated_by 间接得到
    ev_ids = list(dict.fromkeys([s for s, f in inc if t(s) == "evidence"] + _as_list(m.get("evidence"))))
    indirect = {}
    if m.get("type") == "assumption":
        h = idx.meta.get(m.get("promoted_to") or "") or {}
        for e in _as_list(h.get("evidence")):
            indirect.setdefault(e, f"经 {h['id']}（由它提升）")
        for e in _as_list(m.get("invalidated_by")):
            if e.startswith("E"):
                indirect.setdefault(e, "推翻它的依据")
    evidence = []
    for e in ev_ids + [e for e in indirect if e not in ev_ids]:
        em = idx.meta.get(e)
        if not em:
            continue
        evidence.append({"id": e, "stance": em.get("stance"), "strength": em.get("strength"),
                         "source": em.get("source"), "target": em.get("target"), "basis": em.get("basis"),
                         "quote": em.get("quote"), "locator": em.get("locator"),
                         "revision": int(em["revision"]) if str(em.get("revision", "")).isdigit() else None,
                         "note": idx.body.get(e, "").strip(), "created": em.get("created"),
                         "via": indirect.get(e) if e not in ev_ids else None})

    focus = [d for d in idx.discussions if d.get("focus") == ident]
    origin_ds = (prov or {}).get("discussion")
    discussions = [dict(d, relation="focus") for d in focus]
    if origin_ds and origin_ds not in {d["id"] for d in focus}:
        d = next((x for x in idx.discussions if x["id"] == origin_ds), None)
        if d:
            discussions.append(dict(d, relation="origin"))

    related, seen = [], set()
    for s, f in inc:
        if t(s) in ("evidence", "decision", "candidate", "discussion", "review", None) or (s, f) in seen:
            continue
        seen.add((s, f))
        related.append(dict(_brief(idx, s), dir="in", field=f))
    for f, v in m.items():
        if f in OWN_SKIP:
            continue
        for r in _as_list(v if isinstance(v, list) else [v]):
            if isinstance(r, str) and r in idx.meta and t(r) not in ("evidence", "decision", "candidate"):
                related.append(dict(_brief(idx, r), dir="out", field=f))

    reviews = [dict(idx.meta[s], body=idx.body[s].strip()) for s, _ in inc if t(s) == "review"]
    cands = []
    for s, f in inc:
        if t(s) != "candidate" or s in {c["id"] for c in cands}:
            continue
        cm = idx.meta[s]
        sec = candidates._sections(idx.body[s])
        c = dict(cm, statement=sec.get("陈述", ""), rationale=sec.get("理由", ""), field=f,
                 own=(s == own))
        if cm.get("kind") == "revision":
            c["stale"] = cm.get("status") == "pending" and \
                str(cm.get("base_revision")) != str(schema.revision_of(m))
        cands.append(c)
    if own and own in idx.meta and own not in {c["id"] for c in cands}:
        cm = idx.meta[own]
        sec = candidates._sections(idx.body[own])
        cands.append(dict(cm, statement=sec.get("陈述", ""), rationale=sec.get("理由", ""),
                          field="promoted_to", own=True))
    decisions = [dict(idx.meta[s], body=idx.body[s].strip()) for s, _ in inc
                 if t(s) == "decision" and idx.meta[s].get("kind") != "revision"]

    # 合并（§5.16.1）：被并入问题的讨论、关联、子问题一并显示在目标问题页，标来源
    merged_from = sorted(q for q, qm in idx.meta.items()
                         if qm.get("type") == "question" and qm.get("merged_into") == ident)
    for q in merged_from:
        have = {d["id"] for d in discussions}
        for d in idx.discussions:
            if d.get("focus") == q and d["id"] not in have:
                discussions.append(dict(d, relation="merged", via=q))
        qprov = idx.prov.get(q) or {}
        if qprov.get("discussion") and qprov["discussion"] not in have | {d["id"] for d in discussions}:
            d = next((x for x in idx.discussions if x["id"] == qprov["discussion"]), None)
            if d:
                discussions.append(dict(d, relation="merged", via=q))
        for s_, f in idx.incoming.get(q, []):
            if s_ == ident or t(s_) in ("evidence", "decision", "candidate", "discussion", "review", None):
                continue
            related.append(dict(_brief(idx, s_), dir="in", field=f, via=q))

    usage = idx.usage(ident)
    rev = schema.revision_of(m)
    undo, undo_code = None, None
    if not own or (idx.meta.get(own) or {}).get("promoted_to") != ident:
        undo, undo_code = "它不是经候选确认而来（没有可退回的候选）", "not_from_candidate"
    elif rev > 1:
        undo, undo_code = f"它已修订过（第 {rev} 版）", "revised"
    elif usage:
        undo, undo_code = "；".join(usage), "used"
    withdrawn = None
    if schema.is_withdrawn(m):
        dm, db = store.read_obj(m["withdrawn_by"])
        withdrawn = {"decision": m["withdrawn_by"], "from": m.get("withdrawn_from"),
                     "reason": _section(db, "为什么"), "date": (dm or {}).get("created")}
    return {
        "id": ident, "meta": m, "body": body,
        "statement": body.strip() if m.get("type") == "evolution" else statement_of(body),
        "provenance": prov, "revision": rev, "withdrawn": withdrawn,
        "revisions": revision_history(store, ident),
        "discussions": discussions, "evidence": evidence, "related": related,
        "reviews": reviews, "candidates": cands, "decisions": decisions,
        "usage": usage, "users": idx.users(ident), "can_undo": undo is None, "undo_blocked": undo,
        "undo_code": undo_code,
        "can_withdraw": _withdraw_problem(idx, m), "withdraw_code": _withdraw_code(idx, m),
        "is_main_question": ident == idx.main_question,
        **_convergence(idx, ident, m, merged_from),
    }


def _convergence(idx, ident, m, merged_from):
    """问题的状态 / 活跃 / 树位置，理解的合并关系（§5.16），给对象页用。"""
    t = m.get("type")
    out = {}
    if t == "question":
        st = schema.question_status(m)
        problem = None
        if ident == idx.main_question:
            problem = "它是项目的主问题：要结它先把主问题改指向别的问题"
        elif st != "open":
            problem = f"已是 {st}"
        out["question"] = {
            "status": st, "active": schema.is_active(m), "parent": m.get("parent"),
            "children": sorted(q for q, qm in idx.meta.items() if qm.get("type") == "question"
                               and qm.get("parent") == ident),
            "merged_from": merged_from, "can_resolve": problem is None, "resolve_blocked": problem,
            "answered_by": [_brief(idx, r) for r in _as_list(m.get("answered_by")) if r in idx.meta],
            "decided_by": m.get("decided_by"), "merged_into": m.get("merged_into"),
            "decision": _section(idx.body.get(m.get("decided_by") or "", ""), "决定") if m.get("decided_by") else None,
            "suggest_parent": None,
        }
        rel_q = [r for r in _as_list(m.get("relates_to")) if (idx.meta.get(r) or {}).get("type") == "question"
                 and r != ident]
        if len(rel_q) == 1 and not m.get("parent") and ident != idx.main_question:
            out["question"]["suggest_parent"] = rel_q[0]
    if t == "insight":
        sup = idx.meta.get(m.get("superseded_by") or "") or {}
        out["merged_into"] = m.get("superseded_by") if ident in _as_list(sup.get("consolidates")) else None
    if t == "assumption" and m.get("derived_from"):
        src = idx.meta.get(m["derived_from"]) or {}
        new = idx.meta.get(src.get("superseded_by") or "") or {}
        if m["derived_from"] in _as_list(new.get("consolidates")):
            out["derived_merged"] = {"from": m["derived_from"], "into": new["id"]}
    return out


def _section(body, title):
    m = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", body or "", re.S | re.M)
    return m.group(1).strip() if m else ""


# ================================================================ 修订（§5.15.3）

def check_revision(store, target, base_revision, statement, fields, *, at_propose):
    """修订候选的校验。返回目标的 (meta, body)。"""
    if not target or not store.exists(target):
        raise ValueError(f"target {target or '（空）'} 不存在：修订只能针对已存在的对象。")
    meta, body = load(store, target)
    _check_target(meta, target)
    _check_fields(store, meta.get("type"), fields)
    try:
        base = int(base_revision)
    except (TypeError, ValueError):
        raise ValueError("base_revision 必须是整数：起草时目标对象的版本号（briefing 里写着，缺省为 1）。")
    cur = schema.revision_of(meta)
    if at_propose and base != cur:
        raise ValueError(f"base_revision={base} 与 {target} 当前版本 {cur} 不符：请基于当前版本起草。")
    if not (statement or "").strip():
        raise ValueError("statement 必须写修订后的**完整**表述（不是 diff）。")
    if not _changes(meta, body, statement, fields):
        raise ValueError(f"修订后与 {target} 当前版本完全相同，没有可修订的内容。")
    return meta, body


def _check_target(meta, target):
    ttype = meta.get("type")
    if ttype not in REVISABLE:
        raise ValueError(f"{target} 是 {ttype}，不能修订（只能修订问题 / 前提 / 假设 / 不确定性 / 理解）。")
    if ttype == "insight" and meta.get("status") != "active":
        raise ValueError(f"{target} 已是 {meta.get('status')}，只有 active 的理解能修订。")
    if ttype == "assumption" and meta.get("status") == "invalidated":
        raise ValueError(f"{target} 已被推翻，不能修订；若认为它其实成立，请研究者先处理。")
    if ttype == "hypothesis" and meta.get("status") == "abandoned":
        raise ValueError(f"{target} 已被放弃，不能修订。")


def _check_fields(store, ttype, fields):
    stray = [f for f, v in fields.items() if v not in (None, "", []) and f not in REVISABLE[ttype]]
    if stray:
        hint = {"hypothesis": "status / confidence / evidence 只经评判类任务与 transition_hypothesis 改动",
                "assumption": "status 有审视 / 推翻的专门流程", "uncertainty": "status 不经修订改动"}
        raise ValueError(f"{ttype} 的修订不能改 {stray}；可改的只有正文与 {list(REVISABLE[ttype])}。"
                         + (hint.get(ttype, "") and f"（{hint[ttype]}）"))
    if fields.get("maturity") and fields["maturity"] not in ("vague", "scoped", "formalized"):
        raise ValueError("maturity 必须是 vague / scoped / formalized。")
    if fields.get("importance") and fields["importance"] not in ("low", "medium", "high"):
        raise ValueError("importance 必须是 low / medium / high。")
    if fields.get("firmness") and fields["firmness"] not in ("hunch", "working", "settled"):
        raise ValueError("firmness 必须是 hunch / working / settled。")
    if fields.get("fragile") not in (None, "", "true", "false", True, False):
        raise ValueError("fragile 必须是 true / false。")
    missing = [r for r in _as_list(fields.get("relied_on_by")) if not store.exists(r)]
    if missing:
        raise ValueError(f"relied_on_by 中 {missing} 不存在。")


def _norm(v):
    if isinstance(v, list):
        return tuple(v)
    return "" if v is None else str(v).strip()


def _changes(meta, body, statement, fields):
    """[(字段, 旧值, 新值)]。maturity 只是建议，不算变化（人确认时才定）。"""
    out = []
    old_stmt = statement_of(body)
    if " ".join((statement or "").split()) != " ".join(old_stmt.split()):
        out.append(("statement", old_stmt, statement.strip()))
    for f, v in fields.items():
        if f == "maturity" or v in (None, "", []):
            continue
        new = _as_list(v) if f in LIST_FIELDS else str(v).strip()
        if _norm(meta.get(f)) != _norm(new):
            out.append((f, meta.get(f), new))
    return out


def preview(store, cid):
    """确认对话框用：新旧对照、是否基于旧版本、改证伪条件时已有几条证据。"""
    cm, cb = candidates.load(store, cid)
    if cm.get("kind") != "revision":
        raise ValueError(f"{cid} 不是修订候选")
    target = cm.get("target")
    meta, body = load(store, target)
    sec = candidates._sections(cb)
    fields = {f: cm.get(f) for f in REVISABLE.get(meta.get("type"), ()) if cm.get(f) not in (None, "")}
    cur = schema.revision_of(meta)
    changes = _changes(meta, body, sec.get("陈述", ""), fields)
    ev = _as_list(meta.get("evidence"))
    return {"candidate": cid, "target": target, "type": meta.get("type"), "revision": cur,
            "base_revision": int(cm.get("base_revision") or 1),
            "stale": str(cm.get("base_revision")) != str(cur),
            "current": {"statement": statement_of(body), "meta": meta},
            "proposed": {"statement": sec.get("陈述", ""), "fields": fields,
                         "rationale": sec.get("理由", "")},
            "changes": [{"field": f, "old": o, "new": n} for f, o, n in changes],
            "falsifier_evidence": len(ev) if any(f == "falsifier" for f, _, _ in changes) else 0,
            "withdrawn": schema.is_withdrawn(meta)}


def _fields_md(meta, ttype):
    keys = {"question": ("maturity", "status"), "assumption": ("status", "relied_on_by", "fragile"),
            "hypothesis": ("status", "confidence", "falsifier", "validation", "evidence"),
            "uncertainty": ("status", "importance")}.get(ttype, ())
    lines = [f"- {k}: {', '.join(v) if isinstance(v, list) else v}" for k in keys
             if (v := meta.get(k)) not in (None, "", [])]
    return "\n".join(lines) or "（无）"


def apply_revision(store, cid, *, origin=None, force=False, maturity=None, actor="human"):
    """确认修订候选（只有人能做）。insight 走 insights.revise；其余原地改写，id 不变、版本 +1。"""
    cm, cb = candidates.load(store, cid)
    if cm.get("status") != "pending":
        raise ValueError(f"{cid} 已是 {cm['status']}。")
    origin = origin or cm.get("origin")
    if origin not in ("human", "ai"):
        raise ValueError("归属不明（unclear）的候选必须由人选定 origin（human / ai）后才能确认。")
    target = cm.get("target")
    sec = candidates._sections(cb)
    statement = sec.get("陈述", "")
    meta, body = load(store, target)
    ttype = meta.get("type")
    fields = {f: cm.get(f) for f in REVISABLE.get(ttype, ()) if cm.get(f) not in (None, "")}
    cur = schema.revision_of(meta)
    base = int(cm.get("base_revision") or 1)
    if base != cur and not force:
        raise StaleRevision(f"{cid} 基于 {target} 第 {base} 版起草，而 {target} 现在是第 {cur} 版"
                            "（期间有别的修订被确认）。对照当前版本后可以仍然确认（force），或丢弃。",
                            {"id": target, "revision": cur, "statement": statement_of(body), "meta": meta})
    if ttype == "question":
        mat = maturity or meta.get("maturity")
        if mat not in ("vague", "scoped", "formalized"):
            raise ValueError("maturity 必须是 vague / scoped / formalized。")
        fields["maturity"] = mat
    _check_target(meta, target)
    _check_fields(store, ttype, fields)
    if not statement.strip():
        raise ValueError("修订后的表述不能为空。")
    changes = _changes(meta, body, statement, fields)
    if ttype == "question" and fields["maturity"] != meta.get("maturity"):
        changes.append(("maturity", meta.get("maturity"), fields["maturity"]))
    if not changes:
        raise ValueError(f"修订后与 {target} 当前版本完全相同。")
    # 修订多出自讨论；整理任务的理解改写（§5.17.1）出自任务号，没有讨论与轮次
    src = {"discussion": cm.get("source"), "turns": [int(t) for t in _as_list(cm.get("turns"))]} \
        if str(cm.get("source") or "").startswith("DS") else {}

    if ttype == "insight":
        new = insights.revise(store, target, statement, fields.get("firmness") or meta.get("firmness"),
                              note=f"经候选 {cid} 修订。", change_mind=fields.get("change_mind"),
                              origin=origin, source=cid, **src)
        with store.tx(f"candidate {cid}: 确认修订", actor=actor) as tx:
            cm.update(status="accepted", promoted_to=new, decided=today(), origin=origin)
            tx.write_obj(cid, cm, cb)
            tx.note = f"{target} → {new}"
        return new

    ev = _as_list(meta.get("evidence"))
    warn = []
    if any(f == "falsifier" for f, _, _ in changes) and ev:
        warn.append(f"**改动了证伪条件，而 {target} 已有 {len(ev)} 条证据（{', '.join(ev)}）是按旧证伪条件判定的。**"
                    "这些证据保留在对象上，按版本标记。")
    if base != cur:
        warn.append(f"该候选基于第 {base} 版起草，确认时对象已是第 {cur} 版；研究者对照后显式确认（force）。")
    new_rev = cur + 1
    old_fields = _fields_md(meta, ttype)
    for f, v in fields.items():
        if f == "fragile":
            meta[f] = str(v).lower()
        elif f in LIST_FIELDS:
            meta[f] = _as_list(v)
        elif f in ("falsifier", "validation"):
            meta[f] = attribution.neutralize(str(v).strip())
        else:
            meta[f] = v
    meta["revision"] = new_rev
    meta["revised"] = today()
    main, tail = split_body(body)
    h1 = main.strip().splitlines()[0] if main.strip().startswith("# ") else ""
    with store.tx(f"{ttype} {target}: 修订至第 {new_rev} 版", actor=actor) as tx:
        did = tx.new_id("decision")
        new_main = ((h1 + "\n\n") if h1 else "") + attribution.neutralize(statement).strip() + \
            f"\n\n（第 {new_rev} 版，{today()} 经候选 {cid} 修订，修订史见 {did}。）\n"
        tx.write_obj(target, meta, new_main + ("\n" + tail if tail else ""))
        diff = "\n".join(f"- {f}：{_short(o)} → {_short(n)}" for f, o, n in changes)
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "revision", "refs": [target, cid],
                           "candidate": cid, "from_revision": cur, "to_revision": new_rev,
                           "created": today()},
                     f"## 做了什么\n\n修订 {target}：第 {cur} 版 → 第 {new_rev} 版（候选 {cid}，"
                     f"出自 {cm.get('source')} 第 {', '.join(map(str, src.get('turns', []))) or '?'} 轮）。\n\n"
                     + ("## 注意\n\n" + "\n\n".join(warn) + "\n\n" if warn else "")
                     + f"## 修订前（第 {cur} 版）\n\n{statement_of(body)}\n\n字段：\n\n{old_fields}\n\n"
                     f"## 修订后（第 {new_rev} 版）\n\n{statement.strip()}\n\n字段：\n\n{_fields_md(meta, ttype)}\n\n"
                     f"## 变化\n\n{diff}\n\n## 为什么\n\n{sec.get('理由', '').strip() or '（候选没有写理由。）'}\n")
        prov = store.provenance().get(target) or {"origin": "human", "source": "unknown"}
        prov.setdefault("revisions", []).append(
            {"rev": new_rev, "origin": origin, "source": cid, **src})
        tx.set_provenance(target, prov)
        cm.update(status="accepted", promoted_to=target, decided=today(), origin=origin)
        tx.write_obj(cid, cm, cb)
        tx.note = f"{target} v{new_rev}（{did}）"
    return target


def _short(v, n=120):
    s = ", ".join(v) if isinstance(v, (list, tuple)) else str(v if v not in (None, "") else "（空）")
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


# ================================================================ 撤下 / 恢复 / 撤回确认（§5.15.6）

def _withdraw_problem(idx, meta):
    """能否撤下；能则返回 None，否则返回原因。"""
    t, st = meta.get("type"), meta.get("status")
    if t == "insight":
        return None if st == "active" else f"已是 {st}"
    if t not in schema.WITHDRAWN_STATUS:
        return "这类对象不能撤下"
    if schema.is_withdrawn(meta):
        return "已撤下"
    if t == "question" and meta["id"] == idx.main_question:
        return "它是项目的主问题：要撤下先把主问题换成别的"
    if t == "question" and schema.question_status(meta) in schema.CLOSED_QUESTION:
        return f"它已结（{meta.get('status')}）：要撤下先重开"
    if t == "assumption" and st in ("retired", "invalidated"):
        return f"已是 {st}"
    if t == "hypothesis" and st == "abandoned":
        return "已放弃"
    return None


def _withdraw_code(idx, meta):
    """_withdraw_problem 的机器可读版（前端用英文呈现）。"""
    if _withdraw_problem(idx, meta) is None:
        return None
    if meta.get("type") == "question" and meta.get("id") == idx.main_question:
        return "main_question"
    if meta.get("type") == "question" and schema.question_status(meta) in schema.CLOSED_QUESTION:
        return "closed"
    return "not_withdrawable" if meta.get("type") not in schema.WITHDRAWN_STATUS and meta.get("type") != "insight" \
        else "already"


def _decision(tx, refs, what, why, **extra):
    did = tx.new_id("decision")
    tx.write_obj(did, {"id": did, "type": "decision", "kind": "curation", "refs": refs, **extra,
                       "created": today()}, f"## 做了什么\n\n{what}\n\n## 为什么\n\n{why.strip()}\n")
    return did


def withdraw(store, ident, reason, actor="human"):
    """撤下：不再相关（不是推翻）。链接全部保留、不触发 Review、可恢复。"""
    if not (reason or "").strip():
        raise ValueError("撤下必须写理由")
    meta, body = load(store, ident)
    t = meta.get("type")
    if t == "insight":                         # 沿用既有的 Abandon（行为不变）
        insights.abandon(store, ident, reason)
        return None
    problem = _withdraw_problem(Index(store), meta)
    if problem:
        raise ValueError(f"{ident} 不能撤下：{problem}")
    old = meta.get("status") or "open"
    new = schema.WITHDRAWN_STATUS[t]
    with store.tx(f"{t} {ident}: 撤下", actor=actor) as tx:
        did = _decision(tx, [ident], f"撤下 {ident}（{old} → {new}）：不再相关，不是推翻。"
                        "引用它的链接全部保留，不触发待重新审视；可恢复。", reason)
        meta.update(status=new, withdrawn_by=did, withdrawn_from=old)
        tx.write_obj(ident, meta, body.rstrip() + f"\n\n## 撤下（{today()}）\n\n{old} → {new}，见 {did}。"
                                                  f"{reason.strip()}\n")
        tx.note = did
    return did


def restore(store, ident, reason="", actor="human"):
    meta, body = load(store, ident)
    if not schema.is_withdrawn(meta):
        raise ValueError(f"{ident} 没有被撤下")
    back = meta.get("withdrawn_from")
    t = meta.get("type")
    if back not in schema.KINDS[t].enums.get("status", {"open"}) and not (t == "question" and back == "open"):
        raise ValueError(f"{ident} 的 withdrawn_from='{back}' 不是合法状态，无法恢复")
    with store.tx(f"{t} {ident}: 恢复", actor=actor) as tx:
        did = _decision(tx, [ident], f"恢复 {ident}（{meta['status']} → {back}），撤下决定见 {meta['withdrawn_by']}。",
                        reason or "（研究者没有写说明。）")
        if t == "question" and back == "open":
            meta.pop("status", None)
        else:
            meta["status"] = back
        meta.pop("withdrawn_by", None)
        meta.pop("withdrawn_from", None)
        tx.write_obj(ident, meta, body.rstrip() + f"\n\n## 恢复（{today()}）\n\n回到 {back}，见 {did}。\n")
        tx.note = did
    return did


def undo_accept(store, ident, reason="", actor="human"):
    """撤回确认：只对确认后还没被任何东西用到、也没修订过的对象开放。系统里唯一的真删。"""
    idx = Index(store)
    info = links(store, ident, idx)
    if not info["can_undo"]:
        raise ValueError(f"{ident} 不能撤回确认：{info['undo_blocked']}。可以改用撤下。")
    cid = idx.source_candidate(ident)
    cm, cb = candidates.load(store, cid)
    meta = info["meta"]
    with store.tx(f"{meta.get('type')} {ident}: 撤回确认（{cid} 退回待确认）", actor=actor) as tx:
        did = _decision(tx, [cid], f"撤回对 {cid} 的确认：删除由它建立的 {ident}（此前未被任何对象、候选、"
                        f"讨论或证据用到，也未修订过），{cid} 退回待确认。{ident} 这个编号不再复用。\n\n"
                        f"被删除的 {ident} 原文：\n\n{statement_of(info['body'])}",
                        reason or "（研究者没有写说明。）", undid=ident)
        tx.remove(f"{schema.KINDS[meta['type']].dir}/{ident}.md")
        tx.del_provenance(ident)
        for k in ("promoted_to", "decided"):
            cm.pop(k, None)
        cm["status"] = "pending"
        tx.write_obj(cid, cm, cb)
        tx.note = did
    return did, cid
