"""候选区（M1.10 / M7.2，DESIGN.md §5.1 规则 4）。

agent 只能提出候选；成为正式对象必须经人确认。确认时由后端建对象、写
provenance、回填 promoted_to——这些簿记归代码，不归 agent（§0.4）。
"""
import re

from . import attribution, discussion, frontmatter, schema
from .store import today

TARGET_TYPE = {"assumption": "assumption", "hypothesis": "hypothesis",
               "question": "question", "uncertainty": "uncertainty", "insight": "insight"}

# 候选 frontmatter 中属于目标对象的字段
TARGET_FIELDS = {
    "assumption": ("relied_on_by", "fragile", "derived_from"),
    "hypothesis": ("falsifier", "validation", "confidence", "promoted_from"),
    "question": ("maturity", "parent"),
    "uncertainty": ("importance",),
    # supersedes：提议把几条 active 的理解合成这一条（§5.16.3）
    "insight": ("firmness", "basis", "basis_note", "informs", "change_mind", "supersedes"),
    # 修订（§5.15.3）：target / base_revision 另存，这里是按目标类型可改的字段（由 objects.REVISABLE 再筛）
    "revision": ("maturity", "relied_on_by", "fragile", "falsifier", "validation", "importance",
                 "firmness", "change_mind"),
    # 结一个问题（§5.16.1）：target / resolution 另存；decided 的建议决定写在陈述里
    "resolve": ("answered_by", "merged_into"),
}
LIST_FIELDS = ("relied_on_by", "basis", "informs", "supersedes", "answered_by")
SPECIAL = ("revision", "resolve")        # 针对已有对象，不新建；不能与新建类互改类别


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
    out = [c for c in out if status is None or c.get("status") == status]
    revs = {}
    for c in out:
        if c.get("kind") == "revision" and c.get("target"):
            if c["target"] not in revs:
                tm, _ = store.read_obj(c["target"])
                revs[c["target"]] = tm
            tm = revs[c["target"]]
            c["target_revision"] = schema.revision_of(tm) if tm else None
            c["target_type"] = (tm or {}).get("type")
            # 同一目标的另一条修订先被确认 → 这条“基于旧版本”，确认需显式 force（§5.15.3）
            c["stale"] = bool(tm) and c.get("status") == "pending" and \
                str(c.get("base_revision")) != str(c["target_revision"])
    byid = {m.get("id"): m for m, _ in store.list("candidate")} if any(
        c.get("kind") == "resolve" for c in out) else {}
    for c in out:
        if c.get("kind") != "resolve":
            continue
        tm, _ = store.read_obj(c["target"]) if schema.kind_of_id(c.get("target") or "") else (None, None)
        c["target_status"] = schema.question_status(tm) if tm else None
        # 目标已不是 open（期间被别的途径结了 / 撤下）：这条已过时，确认会被拒
        c["stale"] = c.get("status") == "pending" and c["target_status"] != "open"
        # 引用的同批候选：前端据此给“一并确认”（§5.16.1）
        c["refs"] = [{"id": r, "status": (byid.get(r) or {}).get("status"), "kind": (byid.get(r) or {}).get("kind"),
                      "origin": (byid.get(r) or {}).get("origin"), "promoted_to": (byid.get(r) or {}).get("promoted_to")}
                     for r in _as_list(c.get("answered_by")) if r.startswith("C")]
    return out


def withdrawn_refs(store, ids):
    """引用了哪些已撤下的对象（只警告，§5.15.6）。"""
    out = []
    for r in ids:
        m, _ = store.read_obj(r) if schema.kind_of_id(r) else (None, None)
        if m and schema.is_withdrawn(m):
            out.append(r)
    return out


def validate_proposal(store, kind, statement, rationale, origin, origin_note, source,
                      turns, fields, target=None, base_revision=None, at_propose=True, tidy=False,
                      resolution=None):
    if kind not in TARGET_TYPE and kind not in SPECIAL:
        raise ValueError(f"kind 必须是 {sorted(TARGET_TYPE) + list(SPECIAL)} 之一，收到 '{kind}'。")
    if kind == "revision" and schema.TASK_ID.match(source or ""):
        raise ValueError("修订候选只出自讨论：评判类任务的产出是证据与状态迁移，不提修订。")
    merging = kind == "insight" and bool(fields.get("supersedes"))
    if tidy and not (kind == "resolve" or merging):
        raise ValueError("整理任务只提两种候选：合并理解（kind=insight 带 supersedes）与结问题（kind=resolve）。"
                         "不产新想法。")
    if schema.TASK_ID.match(source or "") and not tidy and (kind == "resolve" or merging):
        raise ValueError("结问题与合并理解只出自讨论或整理任务（Tidy up）。")
    if not statement.strip():
        raise ValueError("statement 不能为空。")
    if not rationale.strip():
        raise ValueError("rationale 不能为空：说明为什么这是研究内容、为什么归为这一类。")
    if origin not in ("human", "ai", "unclear"):
        raise ValueError("origin 必须是 human / ai / unclear。")
    if origin == "unclear" and not origin_note.strip():
        raise ValueError("origin=unclear 时必须在 origin_note 说明归属冲突在哪。")
    if schema.TASK_ID.match(source or ""):
        # 验证模式的任务提出的候选（§5.6）：没有讨论轮次，根基必须指向证据或论文
        if origin != "ai":
            raise ValueError("验证任务提出的候选 origin 只能是 ai。")
        if tidy:
            pass                     # 整理任务：根基就是被合并 / 被引用的对象本身
        elif kind == "insight":
            raise ValueError("评判类任务不提 insight：理解是研究者与讨论的产物。")
        else:
            basis = fields.get("basis") or []
            if not basis or any(schema.split_id(b)[0] not in ("E", "P") for b in basis):
                raise ValueError("basis 必须给出支撑这个候选的证据或论文（E### / P###）。")
    else:
        if not store.exists(source):
            raise ValueError(f"source 讨论 {source} 不存在。")
        _, ts = discussion.read(store, source)
        valid = {t["n"] for t in ts}
        if not turns:
            raise ValueError("turns 不能为空：写出依据的讨论轮次号。")
        bad = [t for t in turns if t not in valid]
        if bad:
            raise ValueError(f"turns 中 {bad} 不是 {source} 的轮次（现有 1..{max(valid or [0])}）。")
    if kind == "revision":
        from . import objects
        if at_propose:
            objects.check_revision(store, target, base_revision, statement, fields, at_propose=True)
        return
    if kind == "resolve":
        from . import questions
        if resolution == "decided" and tidy:
            raise ValueError("拍板（decided）只能由研究者在讨论里做：整理任务只提 answered 与 merged。")
        questions.check_resolve(store, target, resolution, fields.get("answered_by"),
                                fields.get("merged_into"), allow_candidates=True)
        return
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
    if kind == "insight":
        if fields.get("firmness") not in ("hunch", "working", "settled"):
            raise ValueError("firmness 必须是 hunch（直觉）/ working（工作理解）/ settled（稳固理解）。")
        if not fields.get("basis") and not (fields.get("basis_note") or "").strip() and not merging:
            raise ValueError("理解必须说出根基：basis（State 里的对象 id）或 basis_note。")
        if merging:
            from . import insights
            insights.check_supersedes(store, fields["supersedes"])
    for f in ("basis", "informs"):
        missing = [r for r in fields.get(f) or [] if not store.exists(r)]
        if missing:
            raise ValueError(f"{f} 中 {missing} 不存在；只能引用 State 里真实存在的对象。")
    if fields.get("derived_from") and not store.exists(fields["derived_from"]):
        raise ValueError(f"derived_from {fields['derived_from']} 不存在。")
    if kind == "question" and fields.get("parent"):
        from . import questions
        questions.check_parent(store, None, fields["parent"])


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
            origin_note="", relates_to=None, task=None, actor="human", target=None,
            base_revision=None, resolution=None, tidy=False, **fields):
    turns = [int(t) for t in _as_list(turns)]
    fields = {k: v for k, v in fields.items() if v not in (None, "", [])}
    for f in LIST_FIELDS:
        if f in fields:
            fields[f] = _as_list(fields[f])
    if kind == "question" and not fields.get("parent") and str(source or "").startswith("DS") \
            and store.exists(source):
        # 聚焦于某个问题的讨论里产出的问题候选，默认挂在它下面（§5.16.4）
        focus = (discussion.read(store, source)[0] or {}).get("focus")
        if schema.split_id(focus or "")[0] == "Q" and store.exists(focus):
            fields["parent"] = focus
    if kind == "insight" and actor == "agent" and not fields.get("basis") and not fields.get("supersedes"):
        raise ValueError("AI 提出的理解，basis 必须指向 State 里真实存在的对象（讨论、证据、假设、论文……），"
                         "说不出根基的“洞见”不收。")
    if kind == "revision" and base_revision in (None, ""):
        base_revision = 1 if not target or not store.exists(target) else \
            schema.revision_of(store.read_obj(target)[0])
        if actor == "agent":
            raise ValueError(f"修订候选必须给 base_revision：起草时 {target} 的版本号"
                             f"（当前是第 {base_revision} 版）。")
    validate_proposal(store, kind, statement, rationale, origin, origin_note, source,
                      turns, fields, target=target, base_revision=base_revision, tidy=tidy,
                      resolution=resolution)
    rel = _as_list(relates_to)
    missing = [r for r in rel if not store.exists(r)]
    if missing:
        raise ValueError(f"relates_to 中 {missing} 不存在。")
    with store.tx("candidate: 新候选", actor=actor, task=task) as tx:
        cid = tx.new_id("candidate")
        meta = {"id": cid, "type": "candidate", "kind": kind, "status": "pending",
                "origin": origin, "origin_note": origin_note.strip() or None,
                "source": source, "turns": turns, "relates_to": rel or None}
        if kind == "revision":
            meta.update(target=target, base_revision=int(base_revision))
        if kind == "resolve":
            meta.update(target=target, resolution=resolution)
            if resolution != "answered":
                fields.pop("answered_by", None)
            if resolution != "merged":
                fields.pop("merged_into", None)
        for f in TARGET_FIELDS[kind]:
            if f in fields:
                meta[f] = fields[f]
        if kind != "insight" and fields.get("basis"):
            meta["basis"] = fields["basis"]
        if schema.TASK_ID.match(source or ""):
            meta.pop("turns", None)
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
    if "kind" in changes and changes["kind"] != meta["kind"] and \
            (changes["kind"] in SPECIAL or meta["kind"] in SPECIAL):
        raise ValueError("修订 / 结问题候选与新建候选不能互相改类别：前者针对已有对象，后者产生新对象。")
    for k, v in changes.items():
        if k in ("kind", "origin", "origin_note", "resolution", *sum(TARGET_FIELDS.values(), ())):
            meta[k] = _as_list(v) if k in LIST_FIELDS else v
    meta = {k: v for k, v in meta.items() if v not in (None, "")}
    with store.tx(f"candidate {cid}: 人修改", actor=actor) as tx:
        tx.write_obj(cid, meta, _body(statement, rationale))


def accept(store, cid, origin=None, changes=None, actor="human", force=False, maturity=None,
           decision=None):
    """确认候选 → 正式对象。返回新对象 id（修订候选返回被修订的对象 id，§5.15.3）。"""
    if changes:
        update(store, cid, dict(changes), actor=actor)
    meta, body = load(store, cid)
    if meta["status"] != "pending":
        raise ValueError(f"{cid} 已是 {meta['status']}。")
    if meta["kind"] == "revision":
        from . import objects
        return objects.apply_revision(store, cid, origin=origin, force=force, maturity=maturity,
                                      actor=actor)
    if meta["kind"] == "resolve":
        from . import questions
        return questions.accept_resolve(store, cid, origin=origin, decision=decision, actor=actor)
    origin = origin or meta.get("origin")
    if origin not in ("human", "ai"):
        raise ValueError("归属不明（unclear）的候选必须由人选定 origin（human / ai）后才能确认。")
    kind = meta["kind"]
    s = _sections(body)
    fields = {k: meta[k] for k in TARGET_FIELDS[kind] if meta.get(k) not in (None, "", [])}
    if meta.get("basis") and "basis" not in fields:
        fields["basis"] = _as_list(meta["basis"])
    tidy = schema.TASK_ID.match(meta["source"]) and bool(fields.get("supersedes"))
    validate_proposal(store, kind, s.get("陈述", ""), s.get("理由", "") or "-", origin, "",
                      meta["source"], [int(t) for t in _as_list(meta.get("turns"))], fields, tidy=tidy)
    if kind == "insight" and fields.get("supersedes"):
        return _accept_merge(store, cid, meta, body, s, fields, origin, actor)

    with store.tx(f"candidate {cid}: 确认", actor=actor) as tx:
        ttype = TARGET_TYPE[kind]
        new = tx.new_id(ttype)
        obj = {"id": new, "type": ttype}
        if ttype == "assumption":
            obj.update(status="unexamined", relied_on_by=fields["relied_on_by"],
                       fragile=fields.get("fragile"))
        elif ttype == "hypothesis":
            obj.update(status="proposed", confidence=fields.get("confidence", "low"),
                       falsifier=attribution.neutralize(fields["falsifier"]),
                       validation=attribution.neutralize(fields["validation"]),
                       evidence=[], promoted_from=fields.get("promoted_from"))
        elif ttype == "question":
            obj.update(maturity=fields["maturity"])
            if fields.get("parent") and store.exists(fields["parent"]):
                obj["parent"] = fields["parent"]
        elif ttype == "uncertainty":
            obj.update(status="open", importance=fields["importance"])
        elif ttype == "insight":
            basis = list(dict.fromkeys(_as_list(fields.get("basis")) +
                                       ([meta["source"]] if meta["source"].startswith("DS") else [])))
            obj.update(status="active", firmness=fields["firmness"], basis=basis,
                       basis_note=fields.get("basis_note"),
                       informs=_as_list(fields.get("informs")) or None,
                       change_mind=fields.get("change_mind"))
        if ttype == "assumption" and fields.get("derived_from"):
            obj["derived_from"] = fields["derived_from"]
        rel = [r for r in _as_list(meta.get("relates_to")) if store.exists(r)]
        if rel:                               # 确认后保留关联（§5.15.4）
            obj["relates_to"] = rel
        obj["created"] = today()
        # 正式对象只写中性化的陈述；理由（常带“研究者认为 / AI 提议”）留在候选里，
        # 评判类任务读不到候选（§5.6 第 3 层）
        text = attribution.neutralize(s.get("陈述", "")).strip() + f"\n\n（来由见候选 {cid}。）\n"
        tx.write_obj(new, obj, text)
        if ttype == "hypothesis" and fields.get("promoted_from"):
            # 前提被安排验证 → 提升为假设（M5.1b），双向链接由这里维护
            am, ab = store.read_obj(fields["promoted_from"])
            if am and am.get("type") == "assumption":
                am.update(status="promoted", promoted_to=new)
                tx.write_obj(am["id"], am, ab.rstrip() + f"\n\n## 提升（{today()}）\n\n已安排验证，提升为 {new}。\n")
        if meta["source"].startswith("DS"):
            rec = {"origin": origin, "source": cid, "discussion": meta["source"],
                   "turns": [int(t) for t in _as_list(meta.get("turns"))]}
        else:
            rec = {"origin": origin, "source": cid, "task": meta["source"]}
        if meta.get("origin") == "unclear":
            rec["note"] = f"蒸馏时归属不明（{meta.get('origin_note', '')}），由人裁定为 {origin}"
        tx.set_provenance(new, rec)
        meta.update(status="accepted", promoted_to=new, decided=today())
        if origin != meta.get("origin"):
            meta["origin"] = origin
        tx.write_obj(cid, meta, body)
        tx.note = f"→ {new}"
    return new


def _accept_merge(store, cid, meta, body, s, fields, origin, actor):
    """合并理解的候选（§5.16.3）：firmness 由人在确认时选定（经 changes 改候选），其余取并集。"""
    from . import insights
    src = meta["source"]
    extra = {"discussion": src, "turns": [int(t) for t in _as_list(meta.get("turns"))]} \
        if src.startswith("DS") else {}
    basis = [b for b in _as_list(fields.get("basis")) if b != src] + ([src] if src.startswith("DS") else [])
    new = insights.consolidate(store, fields["supersedes"], attribution.neutralize(s.get("陈述", "")),
                               fields["firmness"], basis=basis, basis_note=fields.get("basis_note", ""),
                               informs=fields.get("informs"), change_mind=fields.get("change_mind", ""),
                               note=f"经候选 {cid} 合并。", origin=origin, source=cid, actor=actor, **extra)
    with store.tx(f"candidate {cid}: 确认合并", actor=actor) as tx:
        meta.update(status="accepted", promoted_to=new, decided=today(), origin=origin)
        tx.write_obj(cid, meta, body)
        tx.note = f"{', '.join(fields['supersedes'])} → {new}"
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
