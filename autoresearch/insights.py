"""理解（Insight，DESIGN.md M1）：研究的产出——描述性的看法、直觉，不要求可证伪。

AI 提出的理解只能经候选区（candidates.py）；这里是人直接记下、修订与放弃。
修订不覆盖：新建一条并以 superseded_by 链接，旧的保留，理解的演变本身就是进展记录。
"""
from . import reviews
from .store import today

FIRMNESS = ("hunch", "working", "settled")


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _check(store, statement, firmness, basis, basis_note, informs):
    if not (statement or "").strip():
        raise ValueError("理解的陈述不能为空")
    if firmness not in FIRMNESS:
        raise ValueError("firmness 必须是 hunch（直觉）/ working（工作理解）/ settled（稳固理解）")
    if not basis and not (basis_note or "").strip():
        raise ValueError("必须说出根基：引用 State 里的对象，或在 basis_note 里写来源（如经验、手感）")
    missing = [r for r in basis + informs if not store.exists(r)]
    if missing:
        raise ValueError(f"{missing} 不存在")


def _write(tx, store, iid, statement, firmness, basis, basis_note, informs, change_mind, extra="",
           consolidates=None):
    meta = {"id": iid, "type": "insight", "status": "active", "firmness": firmness,
            "basis": basis or None, "basis_note": (basis_note or "").strip() or None,
            "informs": informs or None, "change_mind": (change_mind or "").strip() or None,
            "consolidates": consolidates or None, "created": today()}
    tx.write_obj(iid, meta, statement.strip() + "\n" + extra)


def create(store, statement, firmness, basis=None, basis_note="", informs=None, change_mind=""):
    """人直接记下一条理解（origin=human）。"""
    basis, informs = _as_list(basis), _as_list(informs)
    _check(store, statement, firmness, basis, basis_note, informs)
    with store.tx("insight: 人记下一条理解", actor="human") as tx:
        iid = tx.new_id("insight")
        _write(tx, store, iid, statement, firmness, basis, basis_note, informs, change_mind)
        tx.set_provenance(iid, {"origin": "human", "source": "direct"})
        tx.note = iid
    return iid


def revise(store, iid, statement, firmness, note="", basis=None, basis_note=None,
           informs=None, change_mind=None, origin="human", source=None, discussion=None, turns=None):
    """修订：新建一条取代旧的；旧的根基与影响默认继承，并把旧理解本身也列为根基。

    经修订候选确认时（§5.15.3）origin / source 记候选的，而不是固定 human。"""
    old, obody = store.read_obj(iid)
    if old is None or old.get("type") != "insight":
        raise KeyError(f"{iid} 不存在")
    if old.get("status") != "active":
        raise ValueError(f"{iid} 已是 {old['status']}")
    basis = list(dict.fromkeys(_as_list(old.get("basis")) + _as_list(basis) + [iid]))
    informs = _as_list(informs) if informs is not None else _as_list(old.get("informs"))
    # 继承来的 informs 只留正式对象（旧数据里可能混进了候选 C###）
    from .candidates import object_refs
    informs, _ = object_refs(store, informs)
    basis_note = old.get("basis_note", "") if basis_note is None else basis_note
    change_mind = old.get("change_mind", "") if change_mind is None else change_mind
    _check(store, statement, firmness, basis, basis_note, informs)
    with store.tx(f"insight {iid}: 修订", actor="human") as tx:
        new = tx.new_id("insight")
        _write(tx, store, new, statement, firmness, basis, basis_note, informs, change_mind,
               f"\n## 由来\n\n修订自 {iid}。{(note or '').strip()}\n")
        prev = store.provenance().get(iid) or {}
        rec = {"origin": origin, "source": source or iid,
               "note": f"修订自 {iid}（原提出者 {prev.get('origin', '未知')}）"}
        if discussion:
            rec.update(discussion=discussion, turns=turns or [])
        tx.set_provenance(new, rec)
        old.update(status="superseded", superseded_by=new)
        tx.write_obj(iid, old, obody.rstrip() + f"\n\n## 被取代（{today()}）\n\n由 {new} 取代。"
                     f"{(note or '').strip()}\n")
        tx.note = f"{iid} → {new}"
    reviews.reconcile(store, actor="human")
    return new


def check_supersedes(store, ids):
    """合并（§5.16.3）：至少两条、各不相同、均为 active 的理解。"""
    ids = list(dict.fromkeys(_as_list(ids)))
    if len(ids) < 2:
        raise ValueError("supersedes 至少要列两条要合并的理解（IN###）；只改一条用修订（revision）。")
    for i in ids:
        m, _ = store.read_obj(i) if i.startswith("IN") else (None, None)
        if not m or m.get("type") != "insight":
            raise ValueError(f"supersedes 中的 {i} 不是已存在的理解 IN###。")
        if m.get("status") != "active":
            raise ValueError(f"{i} 已是 {m.get('status')}：只有 active 的理解能合并。")
    return ids


def consolidate(store, supersedes, statement, firmness, basis=None, basis_note="", informs=None,
                change_mind="", note="", origin="human", source=None, discussion=None, turns=None,
                actor="human"):
    """把几条相近 / 互补的理解合成一条（§5.16.3）。旧的标 superseded、superseded_by 指向新的；
    新的记 consolidates，basis = 各条 basis 的并集 + 被合并的 IN 本身，informs 取并集。

    被合并条目派生的前提不进 Review：理解没有被撤回，只是换了一种说法（reviews.overturned 据 consolidates 跳过）。"""
    ids = check_supersedes(store, supersedes)
    olds = [store.read_obj(i) for i in ids]
    basis = list(dict.fromkeys([b for m, _ in olds for b in _as_list(m.get("basis"))]
                               + _as_list(basis) + ids))
    informs = list(dict.fromkeys([x for m, _ in olds for x in _as_list(m.get("informs"))] + _as_list(informs)))
    notes = [str(m.get("basis_note")).strip() for m, _ in olds if str(m.get("basis_note") or "").strip()]
    basis_note = "；".join(dict.fromkeys(notes + ([basis_note.strip()] if (basis_note or "").strip() else [])))
    _check(store, statement, firmness, basis, basis_note, informs)
    with store.tx(f"insight: 合并 {', '.join(ids)}", actor=actor) as tx:
        new = tx.new_id("insight")
        _write(tx, store, new, statement, firmness, basis, basis_note, informs, change_mind,
               f"\n## 由来\n\n合并自 {', '.join(ids)}。{(note or '').strip()}\n", consolidates=ids)
        prov = store.provenance()
        rec = {"origin": origin, "source": source or "direct",
               "note": f"合并自 {', '.join(ids)}（原提出者 " +
                       "、".join(f"{i}: {(prov.get(i) or {}).get('origin', '未知')}" for i in ids) + "）"}
        if discussion:
            rec.update(discussion=discussion, turns=turns or [])
        tx.set_provenance(new, rec)
        for (om, ob), i in zip(olds, ids):
            om.update(status="superseded", superseded_by=new)
            others = [x for x in ids if x != i]
            tx.write_obj(i, om, ob.rstrip() + f"\n\n## 被合并（{today()}）\n\n与 {', '.join(others)} 一起并入 {new}。"
                         f"{(note or '').strip()}\n")
        tx.note = new
    return new


def abandon(store, iid, reason):
    if not (reason or "").strip():
        raise ValueError("放弃一条理解要写理由")
    meta, body = store.read_obj(iid)
    if meta is None or meta.get("type") != "insight":
        raise KeyError(f"{iid} 不存在")
    if meta.get("status") != "active":
        raise ValueError(f"{iid} 已是 {meta['status']}")
    meta["status"] = "abandoned"
    with store.tx(f"insight {iid}: 放弃", actor="human") as tx:
        tx.write_obj(iid, meta, body.rstrip() + f"\n\n## 放弃（{today()}）\n\n{reason.strip()}\n")
    return reviews.reconcile(store, actor="human")
