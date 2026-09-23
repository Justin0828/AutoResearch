"""模式门与交棒（M2.0 / M7.3，DESIGN.md §5.7）。

模式只有人能切：这里的函数只由后端（前端操作）调用，agent 没有任何路径能触达。
每次切换都写 Decision——“为什么现在在验证这几条”必须能追溯。
"""
from . import candidates, frontmatter, schema
from .store import today

BATCH_KINDS = {"C": "candidate", "A": "assumption", "H": "hypothesis"}


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


def mode(store):
    return store.project()[0].get("mode", "discussion")


def batch_decision(store):
    pm, _ = store.project()
    return pm.get("batch") if pm.get("mode") == "validation" else None


def batch(store):
    """当前验证批次的对象 id（不在验证模式时为空）。"""
    did = batch_decision(store)
    if not did:
        return []
    dm, _ = store.read_obj(did)
    return _as_list((dm or {}).get("refs"))


def _set_project(tx, store, **fields):
    pm, pb = store.project()
    for k, v in fields.items():
        if v is None:
            pm.pop(k, None)
        else:
            pm[k] = v
    tx.write("project.md", frontmatter.dump(pm, pb))


def check_item(store, ident):
    p = schema.split_id(ident)[0]
    if p not in BATCH_KINDS or not store.exists(ident):
        raise ValueError(f"{ident} 不存在，或不是候选 / 前提 / 假设")
    meta, _ = store.read_obj(ident)
    if p == "C":
        if meta.get("status") != "pending" or meta.get("kind") not in ("hypothesis", "assumption"):
            raise ValueError(f"{ident} 不是待确认的假设 / 前提候选")
        if meta.get("origin") == "unclear":
            raise ValueError(f"{ident} 的归属还没裁定：先在 Inbox 里选定 human / ai")
    elif p == "A" and meta.get("idea"):
        im, _ = store.read_obj(meta["idea"])
        if not im or im.get("status") != "accepted":
            raise ValueError(f"{ident} 是想法 {meta['idea']} 推演中引入的前提，想法被接受前不能送去验证")
    if p == "A" and meta.get("status") not in ("unexamined", "examined"):
        raise ValueError(f"{ident} 是 {meta.get('status')}，不能再送去验证")
    elif p == "H" and meta.get("status") not in ("proposed", "investigating", "inconclusive"):
        raise ValueError(f"{ident} 是 {meta.get('status')}，不能再送去验证")
    return meta


def handoff(store, items, note=""):
    """人交棒：挑一批进验证模式。候选在这里一并确认入库。返回 (decision id, 批次)。"""
    items = list(dict.fromkeys(_as_list(items)))
    if not items:
        raise ValueError("批次不能为空：至少挑一条前提或假设")
    if mode(store) == "validation":
        raise ValueError("已经在验证模式：先收回，再交一个新批次")
    if mode(store) == "incubation":
        raise ValueError("正在自演进：先回到讨论模式，再交棒验证（两者互斥，§5.13）")
    for i in items:
        check_item(store, i)
    final = []
    for i in items:
        final.append(candidates.accept(store, i) if i.startswith("C") else i)
    with store.tx("mode: 交棒进验证模式", actor="human") as tx:
        did = tx.new_id("decision")
        lines = []
        for i in final:
            m, b = store.read_obj(i)
            first = (b or "").strip().splitlines()[0] if (b or "").strip() else ""
            lines.append(f"- {i}（{m.get('status')}）：{first[:120]}")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "handoff", "refs": final,
                           "created": today()},
                     "## 做了什么\n\n交棒进验证模式，本轮批次：\n\n" + "\n".join(lines) +
                     "\n\n## 为什么\n\n" + (note.strip() or "（研究者没有写说明。）") + "\n")
        _set_project(tx, store, mode="validation", batch=did)
        tx.note = f"{did}：{', '.join(final)}"
    return did, final


def recall(store, reason=""):
    """人收回：切回讨论模式。"""
    if mode(store) != "validation":
        raise ValueError("当前不在验证模式")
    prev = batch_decision(store)
    with store.tx("mode: 收回讨论模式", actor="human") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "mode",
                           "refs": [prev] if prev else [], "created": today()},
                     f"## 做了什么\n\n从验证模式收回讨论模式（结束批次 {prev}）。\n\n## 为什么\n\n"
                     + (reason.strip() or "（研究者没有写说明。）") + "\n")
        _set_project(tx, store, mode="discussion", batch=None)
        tx.note = did
    return did


def continue_validation(store, reason, suggestion):
    """系统建议收回、人选择继续验证：必须写为什么（M2.0：不让系统自己闭环）。"""
    if not (reason or "").strip():
        raise ValueError("选择继续验证必须写明理由")
    with store.tx("mode: 研究者选择继续验证", actor="human") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "mode",
                           "refs": [x for x in [batch_decision(store)] if x], "created": today()},
                     f"## 做了什么\n\n系统建议回到讨论模式（{suggestion}），研究者选择继续验证。"
                     f"\n\n## 为什么\n\n{reason.strip()}\n")
        tx.note = did
    return did


def record_prep(store, topic, why, targets):
    """夜间预习开始时写一条 Decision：选了什么、为什么（M2.0：次日说明理由）。"""
    with store.tx("decision: 夜间预习选题", actor="system") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "research",
                           "refs": [t for t in targets if store.exists(t)], "prep": True,
                           "created": today()},
                     f"## 做了什么\n\n夜间预习：{topic}\n\n## 为什么选它\n\n{why}\n")
        tx.note = did
    return did
