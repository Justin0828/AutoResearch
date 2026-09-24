"""推翻的传播（DESIGN.md M5.6b）：被推翻的对象 → 所有相关对象进“待重新审视”。

核心是一个幂等的对账函数 reconcile()：扫描 State 里所有已推翻的对象，为每个
(触发者, 受影响者) 确保有一条 Review。无论状态是谁改的——MCP 工具、前端、
人在编辑器里手改——对账都能补上，不存在绕过的写入路径。
系统只提醒、不改写：受影响的对象保持原样，由人判断。
"""
from . import schema
from .store import today

def _index(store):
    hyps = {m["id"]: m for m, _ in store.list("hypothesis")}
    asms = {m["id"]: m for m, _ in store.list("assumption")}
    pend = [m for m, _ in store.list("candidate") if m.get("status") == "pending"]
    ins = {m["id"]: m for m, _ in store.list("insight")}
    return hyps, asms, pend, ins


def _as_list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def overturned(store):
    hyps, asms, _, ins = _index(store)
    out = [(h, "hypothesis_refuted") for h, m in hyps.items() if m.get("status") == "refuted"]
    out += [(a, "assumption_invalidated") for a, m in asms.items() if m.get("status") == "invalidated"]
    # 被合并的理解没有被撤回，只是换了一种说法：不传播（§5.16.3）
    out += [(i, "insight_withdrawn") for i, m in ins.items()
            if m.get("status") in ("superseded", "abandoned") and not schema.is_consolidation_source(ins, m)]
    return out


def affected(store, trigger, event, idx=None):
    """返回 {target: (depth, path, reason)}，同一目标只保留最短路径。"""
    hyps, asms, pend, ins = idx or _index(store)
    found = {}

    def insights_on(x, depth, path):
        """根基里有 x 的理解：x 被推翻，这种理解的根基就动了。"""
        for iid, m in ins.items():
            if m.get("status") == "active" and x in _as_list(m.get("basis")):
                add(iid, depth, path + [iid], f"它的根基 {x} 已被推翻")

    def add(target, depth, path, reason):
        if target == trigger:
            return
        if target not in found or found[target][0] > depth:
            found[target] = (depth, path, reason)

    def downstream(aid, depth, path, seen):
        """前提 aid 失效 → 依赖它的对象失去前提；沿 assumption 链继续向下游传。"""
        a = asms.get(aid) or {}
        for x in _as_list(a.get("relied_on_by")):
            add(x, depth, path + [x], f"它依赖的前提 {aid} 已不成立" if depth == 1 else
                f"间接受影响：经 {' → '.join(path)} 传递而来")
            if x in asms and x not in seen:
                downstream(x, depth + 1, path + [x], seen | {x})
        if a.get("promoted_to"):
            add(a["promoted_to"], depth, path + [a["promoted_to"]], f"它由前提 {aid} 提升而来")

    if event == "insight_withdrawn":
        # 理解被取代或放弃 → 由它派生的前提失去来由（“凭感觉排除了 X”要重新看）
        # 合成的理解被取代 / 放弃时，派生自它所合并的旧条目的前提同样失去来由
        lineage, todo = {trigger}, [trigger]
        while todo:
            for c in _as_list((ins.get(todo.pop()) or {}).get("consolidates")):
                if c not in lineage:
                    lineage.add(c)
                    todo.append(c)
        for aid, a in asms.items():
            if a.get("derived_from") in lineage and a.get("status") != "invalidated":
                add(aid, 1, [trigger, aid], f"它派生自理解 {trigger}，而这条理解已被"
                    + ("取代" if (ins.get(trigger) or {}).get("status") == "superseded" else "放弃"))
    elif event == "assumption_invalidated":
        downstream(trigger, 1, [trigger], {trigger})
        insights_on(trigger, 1, [trigger])
    else:
        h = hyps.get(trigger) or {}
        for aid, a in asms.items():
            if trigger in _as_list(a.get("relied_on_by")):
                add(aid, 1, [trigger, aid], f"它为已被反驳的 {trigger} 提供前提：是否还有存在意义")
        src = h.get("promoted_from")
        if src:
            add(src, 1, [trigger, src], f"{trigger} 由它提升而来，{trigger} 被反驳即此前提被推翻")
            downstream(src, 2, [trigger, src], {src})
            insights_on(src, 2, [trigger, src])
        insights_on(trigger, 1, [trigger])
        if h.get("group"):
            for hid, other in hyps.items():
                if hid != trigger and other.get("group") == h["group"]:
                    add(hid, 1, [trigger, hid], f"与 {trigger} 同属竞争假设组 {h['group']}")
    for c in pend:
        if trigger in _as_list(c.get("relates_to")):
            add(c["id"], 1, [trigger, c["id"]], f"待确认候选，关联了已被推翻的 {trigger}")
    return found


def reconcile(store, actor="system", task=None):
    """幂等：为每个 (触发者, 受影响者) 确保有一条 Review。返回新建的 id 列表。"""
    new = []
    with store.tx("review: 推翻的传播", actor=actor, task=task) as tx:
        idx = _index(store)
        have = {(m.get("trigger"), m.get("target")) for m, _ in store.list("review")}
        for trig, event in overturned(store):
            for target, (depth, path, reason) in sorted(affected(store, trig, event, idx).items(),
                                                         key=lambda x: (x[1][0], x[0])):
                if (trig, target) in have or not store.exists(target):
                    continue
                rid = tx.new_id("review")
                tx.write_obj(rid, {"id": rid, "type": "review", "trigger": trig, "event": event,
                                   "target": target, "status": "open", "depth": depth,
                                   "created": today()},
                             f"{reason}。\n\n传递路径：{' → '.join(path)}\n")
                have.add((trig, target))
                new.append(rid)
        tx.note = f"新增 {', '.join(new)}" if new else ""
    return new


def list_all(store, status=None):
    out = []
    for m, b in store.list("review"):
        if status and m.get("status") != status:
            continue
        out.append(dict(m, body=b.strip()))
    return out


def resolve(store, rid, status, note, actor="human"):
    if status not in ("resolved", "dismissed"):
        raise ValueError("status 必须是 resolved（已据此调整）或 dismissed（判断不受影响）")
    if not (note or "").strip():
        raise ValueError("必须写明处理结论——它是“为什么这条还站着”的记录")
    meta, body = store.read_obj(rid)
    if meta is None or meta.get("type") != "review":
        raise KeyError(f"{rid} 不存在")
    if meta["status"] != "open":
        raise ValueError(f"{rid} 已是 {meta['status']}")
    meta.update(status=status, decided=today())
    label = "已据此调整" if status == "resolved" else "判断不受影响"
    with store.tx(f"review {rid}: {label}", actor=actor) as tx:
        tx.write_obj(rid, meta, f"{body.rstrip()}\n\n## 处理（{today()}）\n\n{label}：{note.strip()}\n")


def invalidate_assumption(store, aid, by, rationale, actor="agent", task=None):
    """推翻一条前提，随即对账。by 为证据 E### 或人的推翻决定 DEC###。"""
    by = [b for b in _as_list(by) if b]
    if not aid.startswith("A") or not store.exists(aid):
        raise ValueError(f"assumption '{aid}' 不存在")
    if not by:
        raise ValueError("推翻前提必须附依据：evidence id（E###）或人的推翻决定（DEC###）")
    missing = [b for b in by if schema.split_id(b)[0] not in ("E", "DEC") or not store.exists(b)]
    if missing:
        raise ValueError(f"依据 {missing} 不存在或不是 E### / DEC###")
    if not (rationale or "").strip():
        raise ValueError("必须说明为什么这条前提不再成立")
    meta, body = store.read_obj(aid)
    if meta.get("status") == "invalidated":
        raise ValueError(f"{aid} 已被推翻")
    old = meta.get("status")
    meta["status"] = "invalidated"
    meta["invalidated_by"] = by
    with store.tx(f"assumption {aid}: 被推翻", actor=actor, task=task) as tx:
        tx.write_obj(aid, meta, f"{body.rstrip()}\n\n## 被推翻（{today()}）\n\n"
                                f"{old} → invalidated，依据 {', '.join(by)}。\n\n{rationale.strip()}\n")
    return reconcile(store, actor=actor, task=task)


def human_invalidate(store, aid, reason):
    """人从前端推翻一条前提：先留一条 Decision 作为依据（§5 第 5 条：改变方向必须留理由）。"""
    if not (reason or "").strip():
        raise ValueError("必须写明推翻理由")
    if not aid.startswith("A") or not store.exists(aid):
        raise ValueError(f"assumption '{aid}' 不存在")
    with store.tx(f"decision: 人推翻前提 {aid}", actor="human") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "research", "refs": [aid],
                           "created": today()},
                     f"## 做了什么\n\n推翻前提 {aid}。\n\n## 为什么\n\n{reason.strip()}\n")
    return did, invalidate_assumption(store, aid, [did], reason, actor="human")
