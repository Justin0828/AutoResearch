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


def _write(tx, store, iid, statement, firmness, basis, basis_note, informs, change_mind, extra=""):
    meta = {"id": iid, "type": "insight", "status": "active", "firmness": firmness,
            "basis": basis or None, "basis_note": (basis_note or "").strip() or None,
            "informs": informs or None, "change_mind": (change_mind or "").strip() or None,
            "created": today()}
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
           informs=None, change_mind=None):
    """修订：新建一条取代旧的；旧的根基与影响默认继承，并把旧理解本身也列为根基。"""
    old, obody = store.read_obj(iid)
    if old is None or old.get("type") != "insight":
        raise KeyError(f"{iid} 不存在")
    if old.get("status") != "active":
        raise ValueError(f"{iid} 已是 {old['status']}")
    basis = list(dict.fromkeys(_as_list(old.get("basis")) + _as_list(basis) + [iid]))
    informs = _as_list(informs) if informs is not None else _as_list(old.get("informs"))
    basis_note = old.get("basis_note", "") if basis_note is None else basis_note
    change_mind = old.get("change_mind", "") if change_mind is None else change_mind
    _check(store, statement, firmness, basis, basis_note, informs)
    with store.tx(f"insight {iid}: 修订", actor="human") as tx:
        new = tx.new_id("insight")
        _write(tx, store, new, statement, firmness, basis, basis_note, informs, change_mind,
               f"\n## 由来\n\n修订自 {iid}。{(note or '').strip()}\n")
        prev = store.provenance().get(iid) or {}
        tx.set_provenance(new, {"origin": "human", "source": iid,
                                "note": f"修订自 {iid}（原提出者 {prev.get('origin', '未知')}）"})
        old.update(status="superseded", superseded_by=new)
        tx.write_obj(iid, old, obody.rstrip() + f"\n\n## 被取代（{today()}）\n\n由 {new} 取代。"
                     f"{(note or '').strip()}\n")
        tx.note = f"{iid} → {new}"
    reviews.reconcile(store, actor="human")
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
