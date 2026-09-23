"""候选区（M1.10 / M7.2，DESIGN.md §5.1 规则 4）。

agent 只能提出候选；成为正式对象必须经人确认。确认时由后端建对象、写
provenance、回填 promoted_to——这些簿记归代码，不归 agent（§0.4）。
"""
import re

from . import discussion, frontmatter, schema
from .store import today

TARGET_TYPE = {"assumption": "assumption", "hypothesis": "hypothesis",
               "question": "question", "uncertainty": "uncertainty"}

# 候选 frontmatter 中属于目标对象的字段
TARGET_FIELDS = {
    "assumption": ("relied_on_by", "fragile"),
    "hypothesis": ("falsifier", "validation", "confidence", "promoted_from"),
    "question": ("maturity",),
    "uncertainty": ("importance",),
}


def _sections(body):
    """候选正文 → {'陈述': ..., '理由': ...}"""
    out, cur = {}, None
    for line in (body or "").splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _body(statement, rationale, extra=None):
    parts = [f"## 陈述\n\n{statement.strip()}", f"## 理由\n\n{rationale.strip()}"]
    for k, v in (extra or {}).items():
        if v:
            parts.append(f"## {k}\n\n{v.strip()}")
    return "\n\n".join(parts) + "\n"


def _as_list(v):
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def load(store, cid):
    meta, body = store.read_obj(cid)
    if meta is None or meta.get("type") != "candidate":
        raise KeyError(f"候选 {cid} 不存在")
    return meta, body


def as_dict(meta, body):
    s = _sections(body)
    return {**meta, "statement": s.get("陈述", ""), "rationale": s.get("理由", ""),
            "decision_note": s.get("人的处理意见", "")}


def list_all(store, status=None):
    out = [as_dict(m, b) for m, b in store.list("candidate")]
    return [c for c in out if status is None or c.get("status") == status]


def validate_proposal(store, kind, statement, rationale, origin, origin_note, source,
                      turns, fields):
    if kind not in TARGET_TYPE:
        raise ValueError(f"kind 必须是 {sorted(TARGET_TYPE)} 之一，收到 '{kind}'。")
    if not statement.strip():
        raise ValueError("statement 不能为空。")
    if not rationale.strip():
        raise ValueError("rationale 不能为空：说明为什么这是研究内容、为什么归为这一类。")
    if origin not in ("human", "ai", "unclear"):
        raise ValueError("origin 必须是 human / ai / unclear。")
    if origin == "unclear" and not origin_note.strip():
        raise ValueError("origin=unclear 时必须在 origin_note 说明归属冲突在哪。")
    if not store.exists(source):
        raise ValueError(f"source 讨论 {source} 不存在。")
    _, ts = discussion.read(store, source)
    valid = {t["n"] for t in ts}
    if not turns:
        raise ValueError("turns 不能为空：写出依据的讨论轮次号。")
    bad = [t for t in turns if t not in valid]
    if bad:
        raise ValueError(f"turns 中 {bad} 不是 {source} 的轮次（现有 1..{max(valid or [0])}）。")
    for f in schema.CANDIDATE_FIELDS[kind]:
        if not fields.get(f):
            raise ValueError(_missing_hint(kind, f))
    if kind == "assumption":
        missing = [r for r in fields["relied_on_by"] if not store.exists(r)]
        if missing:
            raise ValueError(f"relied_on_by 中 {missing} 不存在；只能引用已存在的对象 id。")
    if kind == "question" and fields.get("maturity") not in ("vague", "scoped", "formalized"):
        raise ValueError("maturity 必须是 vague / scoped / formalized。")
    if kind == "uncertainty" and fields.get("importance") not in ("low", "medium", "high"):
        raise ValueError("importance 必须是 low / medium / high。")
    if fields.get("confidence") and fields["confidence"] not in ("low", "medium", "high"):
        raise ValueError("confidence 必须是 low / medium / high。")


def _missing_hint(kind, f):
    if kind == "assumption" and f == "relied_on_by":
        return ("assumption 必须给出 relied_on_by：它正在支撑哪些对象（id 列表）。"
                "说不出它被谁依赖，它就不是当前的 assumption。")
    if kind == "hypothesis" and f == "validation":
        return ("hypothesis 必须给出 validation：讨论中已经安排/约定了怎样验证它。"
                "如果还没有安排任何验证、只是被当作前提在用，它应当是 assumption（按当前角色分类）。")
    if kind == "hypothesis" and f == "falsifier":
        return "hypothesis 必须给出 falsifier：什么结果会反驳它。"
    return f"kind={kind} 的候选必须带 {f}。"


def propose(store, *, kind, statement, rationale, origin, source, turns,
            origin_note="", relates_to=None, task=None, actor="human", **fields):
    turns = [int(t) for t in _as_list(turns)]
    fields = {k: v for k, v in fields.items() if v not in (None, "", [])}
    if "relied_on_by" in fields:
        fields["relied_on_by"] = _as_list(fields["relied_on_by"])
    validate_proposal(store, kind, statement, rationale, origin, origin_note, source,
                      turns, fields)
    rel = _as_list(relates_to)
    missing = [r for r in rel if not store.exists(r)]
    if missing:
        raise ValueError(f"relates_to 中 {missing} 不存在。")
    with store.tx("candidate: 新候选", actor=actor, task=task) as tx:
        cid = tx.new_id("candidate")
        meta = {"id": cid, "type": "candidate", "kind": kind, "status": "pending",
                "origin": origin, "origin_note": origin_note.strip() or None,
                "source": source, "turns": turns, "relates_to": rel or None}
        for f in TARGET_FIELDS[kind]:
            if f in fields:
                meta[f] = fields[f]
        meta["created"] = today()
        tx.write_obj(cid, meta, _body(statement, rationale))
        tx.note = f"{cid}（{kind}）"
    return cid


def update(store, cid, changes, actor="human"):
    """人在确认前修改候选（字段或陈述/理由）。"""
    meta, body = load(store, cid)
    if meta["status"] != "pending":
        raise ValueError(f"{cid} 已是 {meta['status']}，不能再改。")
    s = _sections(body)
    statement = changes.pop("statement", s.get("陈述", ""))
    rationale = changes.pop("rationale", s.get("理由", ""))
    for k, v in changes.items():
        if k in ("kind", "origin", "origin_note", *sum(TARGET_FIELDS.values(), ())):
            meta[k] = _as_list(v) if k == "relied_on_by" else v
    meta = {k: v for k, v in meta.items() if v not in (None, "")}
    with store.tx(f"candidate {cid}: 人修改", actor=actor) as tx:
        tx.write_obj(cid, meta, _body(statement, rationale))


def accept(store, cid, origin=None, changes=None, actor="human"):
    """确认候选 → 正式对象。返回新对象 id。"""
    if changes:
        update(store, cid, dict(changes), actor=actor)
    meta, body = load(store, cid)
    if meta["status"] != "pending":
        raise ValueError(f"{cid} 已是 {meta['status']}。")
    origin = origin or meta.get("origin")
    if origin not in ("human", "ai"):
        raise ValueError("归属不明（unclear）的候选必须由人选定 origin（human / ai）后才能确认。")
    kind = meta["kind"]
    s = _sections(body)
    fields = {k: meta[k] for k in TARGET_FIELDS[kind] if meta.get(k) not in (None, "", [])}
    validate_proposal(store, kind, s.get("陈述", ""), s.get("理由", "") or "-", origin, "",
                      meta["source"], [int(t) for t in _as_list(meta.get("turns"))], fields)

    with store.tx(f"candidate {cid}: 确认", actor=actor) as tx:
        ttype = TARGET_TYPE[kind]
        new = tx.new_id(ttype)
        obj = {"id": new, "type": ttype}
        if ttype == "assumption":
            obj.update(status="unexamined", relied_on_by=fields["relied_on_by"],
                       fragile=fields.get("fragile"))
        elif ttype == "hypothesis":
            obj.update(status="proposed", confidence=fields.get("confidence", "low"),
                       falsifier=fields["falsifier"], validation=fields["validation"],
                       evidence=[], promoted_from=fields.get("promoted_from"))
        elif ttype == "question":
            obj.update(maturity=fields["maturity"])
        elif ttype == "uncertainty":
            obj.update(status="open", importance=fields["importance"])
        obj["created"] = today()
        text = s.get("陈述", "").strip() + "\n\n## 来由\n\n" + s.get("理由", "").strip() + \
            f"\n\n（经候选 {cid} 确认，出自讨论 {meta['source']} 第 {', '.join(map(str, _as_list(meta.get('turns'))))} 轮。）\n"
        tx.write_obj(new, obj, text)
        rec = {"origin": origin, "source": cid, "discussion": meta["source"],
               "turns": [int(t) for t in _as_list(meta.get("turns"))]}
        if meta.get("origin") == "unclear":
            rec["note"] = f"蒸馏时归属不明（{meta.get('origin_note', '')}），由人裁定为 {origin}"
        tx.set_provenance(new, rec)
        meta.update(status="accepted", promoted_to=new, decided=today())
        if origin != meta.get("origin"):
            meta["origin"] = origin
        tx.write_obj(cid, meta, body)
        tx.note = f"→ {new}"
    return new


def reject(store, cid, reason, actor="human"):
    meta, body = load(store, cid)
    if meta["status"] != "pending":
        raise ValueError(f"{cid} 已是 {meta['status']}。")
    if not (reason or "").strip():
        raise ValueError("丢弃必须写理由——它会进入后续蒸馏的去重依据。")
    meta.update(status="rejected", decided=today())
    body = body.rstrip() + f"\n\n## 人的处理意见\n\n丢弃：{reason.strip()}\n"
    with store.tx(f"candidate {cid}: 丢弃", actor=actor) as tx:
        tx.write_obj(cid, meta, body)
