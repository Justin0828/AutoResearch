"""M4 / M5 的状态操作：论文登记、证据、状态迁移、前提审视、接地、Paper Request（§5.5–5.8）。

MCP server 与后端都调这里——规则只写一处。簿记归工具（§0.4）：论文的身份与阅读状态、
假设 / 前提的 evidence 列表、proposed → investigating，都由这里确定性维护，不交给 agent。
"""
import re

from . import library as L
from . import reviews, schema
from .store import today

MAX_AUTHORS = 8
AGENT_TRANSITIONS = {"investigating", "supported", "refuted", "inconclusive"}


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


# ---------------------------------------------------------------- 论文登记

def find_paper(store, arxiv=None, doi=None, url=None):
    for m, _ in store.list("paper"):
        if (arxiv and m.get("arxiv") == arxiv) or (doi and m.get("doi") == doi) or \
                (url and m.get("url") == url):
            return m["id"]
    return None


def _resolve_ref(ref):
    """arXiv id / DOI / URL → (kind, 规范化值)。arxiv.org 链接当作 arXiv id。"""
    ref = (ref or "").strip()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", ref)
    if m and L.norm_arxiv(m.group(1)):
        return "arxiv", L.norm_arxiv(m.group(1))
    if L.norm_arxiv(ref):
        return "arxiv", L.norm_arxiv(ref)
    if L.norm_doi(ref):
        return "doi", L.norm_doi(ref)
    if ref.startswith(("http://", "https://")):
        return "url", ref
    return None, None


def register(store, lib, ref, why="", for_targets=None, found_via="", actor="agent", task=None):
    """登记一篇真实论文。返回 (pid, 是否新建, 说明)。取不到权威元数据就拒绝。"""
    kind, val = _resolve_ref(ref)
    if not kind:
        raise ValueError(f"'{ref}' 不是 arXiv id、DOI 或 http(s) URL。只登记能核实的真实论文。")
    targets = _as_list(for_targets)
    bad = [t for t in targets if schema.split_id(t)[0] not in ("H", "A") or not store.exists(t)]
    if bad:
        raise ValueError(f"for_targets 中 {bad} 不存在或不是 H### / A###")
    existing = find_paper(store, **{kind: val})
    if existing:
        _merge_for(store, existing, targets, actor, task)
        return existing, False, "已登记过，未重复建档"

    try:
        meta = {"arxiv": L.arxiv_meta, "doi": L.doi_meta, "url": L.url_meta}[kind](val)
    except L.NetError as e:
        raise ValueError(f"无法核实 {kind}={val}：{e}。网络问题可稍后重试；不要凭记忆登记。") from e
    if not meta or not meta.get("title"):
        raise ValueError(f"查无此文：{kind}={val} 在权威源里不存在。可能是记错了编号——"
                         "用 search_papers 按标题找到真实编号再登记，不要猜。")
    if kind == "arxiv" and meta.get("doi"):
        dup = find_paper(store, doi=meta["doi"])
        if dup:
            _merge_for(store, dup, targets, actor, task)
            return dup, False, f"已以 DOI 登记为 {dup}"

    authors = meta.get("authors") or []
    if len(authors) > MAX_AUTHORS:
        authors = authors[:MAX_AUTHORS] + ["et al."]
    authors = [a.replace(",", " ") for a in authors if a]
    abstract = meta.get("abstract") or ""
    with store.tx("paper: 登记论文", actor=actor, task=task) as tx:
        pid = tx.new_id("paper")
        fm = {"id": pid, "type": "paper", "title": meta["title"], "authors": authors or None,
              "year": meta.get("year") or None, "venue": meta.get("venue") or None,
              "arxiv": val if kind == "arxiv" else None,
              "doi": (val if kind == "doi" else meta.get("doi")) or None,
              "url": val if kind == "url" else None,
              "read": "none", "fulltext": "none", "for": targets or None,
              "found_via": found_via or (task and f"任务 {task}") or None, "created": today()}
        body = (f"## 摘要\n\n{abstract or '（元数据源没有摘要。）'}\n\n"
                + (f"## 为什么读\n\n{why.strip()}\n\n" if why.strip() else "")
                + "## 阅读笔记\n\n（尚未精读。）\n")
        tx.write_obj(pid, fm, body)
        tx.note = f"{pid} {meta['title'][:60]}"

    # 全文获取在锁外做（下载可能要几十秒）
    if kind == "arxiv":
        ok, msg = lib.fetch_arxiv(pid, val, meta["title"], abstract)
    else:
        lib.write(pid, meta["title"], None, "仅摘要", abstract)
        ok, msg = False, ("非 arXiv 论文，没有开放全文。需要全文时用 request_paper 请研究者取回。"
                          if kind == "doi" else "只登记了链接，全文需要研究者上传。")
    set_paper_fields(store, pid, {"fulltext": "open" if ok else "none",
                                  "fulltext_sha": lib.meta(pid).get("sha")},
                     f"paper {pid}: 全文{'已取得' if ok else '不可得'}", actor, task)
    return pid, True, msg


def read_paper_locked(store, pid):
    """在锁内读论文：frontmatter 取 HEAD 版本（字段归工具，agent 在工作区里对它的改动一律作废），
    正文取工作区版本（agent 刚用 Edit 写的笔记可能还没被 runner 提交）。"""
    meta, body = store.read_obj(pid)
    head = store.git("show", f"HEAD:papers/{pid}.md", check=False)
    if head:
        from . import frontmatter
        hm, _ = frontmatter.parse(head)
        if hm:
            meta = hm
    return meta, body


def set_paper_fields(store, pid, fields, message, actor="agent", task=None):
    with store.tx(message, actor=actor, task=task) as tx:
        meta, body = read_paper_locked(store, pid)
        meta.update({k: v for k, v in fields.items()})
        meta = {k: v for k, v in meta.items() if v is not None}
        tx.write_obj(pid, meta, body)


def _merge_for(store, pid, targets, actor, task):
    meta, _ = store.read_obj(pid)
    cur = _as_list(meta.get("for"))
    new = [t for t in targets if t not in cur]
    if new:
        set_paper_fields(store, pid, {"for": cur + new}, f"paper {pid}: 关联 {', '.join(new)}",
                         actor, task)


def open_paper(store, lib, pid, actor="agent", task=None):
    """给 agent 全文路径与目录；阅读状态由这里更新，不由 agent 自报。"""
    meta, _ = store.read_obj(pid)
    if meta is None or meta.get("type") != "paper":
        raise ValueError(f"论文 {pid} 不存在")
    path = lib.fulltext_path(pid)
    if not path.exists():
        raise ValueError(f"{pid} 在文献库里没有任何文本（登记时获取失败）。")
    full = lib.has_fulltext(pid)
    level = "fulltext" if full else "abstract"
    if {"none": 0, "abstract": 1, "fulltext": 2}[meta.get("read", "none")] < (2 if full else 1):
        set_paper_fields(store, pid, {"read": level}, f"paper {pid}: 开始阅读（{level}）", actor, task)
    toc = {}
    for a in lib.anchors(pid):
        g = a.rsplit("-p", 1)[0] if "-p" in a else "图表"
        toc[g] = toc.get(g, 0) + 1
    lines = [f"{pid}「{meta.get('title')}」——{'全文' if full else '只有摘要'}：{path}",
             "目录（锚点前缀 · 段落数）：" + "，".join(f"{g}·{n}" for g, n in toc.items())]
    if not full:
        lines.append("没有全文：只能引用摘要（abstract-p1），这类证据 strength 不能是 strong，"
                     "也不能单独支撑把假设判为 supported / refuted。必要时用 request_paper 请研究者取回。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 证据与迁移

def record_evidence(store, lib, target, stance, source, note, quote="", locator=None,
                    strength="moderate", batch=(), actor="agent", task=None):
    k = schema.KINDS["evidence"]
    tkind = schema.split_id(target)[0]
    if tkind not in ("H", "A") or not store.exists(target):
        raise ValueError(f"target '{target}' 不存在或不是假设 H### / 前提 A###。")
    if stance not in k.enums["stance"]:
        raise ValueError(f"stance 必须是 {sorted(k.enums['stance'])} 之一，收到 '{stance}'。")
    if strength not in k.enums["strength"]:
        raise ValueError(f"strength 必须是 {sorted(k.enums['strength'])} 之一，收到 '{strength}'。")
    sp = schema.split_id(source)[0]
    if sp not in ("P", "X") or not (store.exists(source) or
                                    (store.state / "experiments" / f"{source}.md").exists()):
        raise ValueError(f"source '{source}' 无效：证据出处只能是已存在的 paper（P###）或 "
                         "experiment（X###）。dead-end 不是出处——它引用证据，而不是反过来。")
    if not (note or "").strip():
        raise ValueError("note 不能为空：必须说明这条证据具体说了什么、为什么是 support/contradict。")
    locator = [x.strip().strip("[]") for x in _as_list(locator)]
    basis = "fulltext"
    if sp == "P":
        ok, why, basis = lib.verify_quote(source, locator, quote or "")
        if not ok:
            raise ValueError(why)
        if basis == "abstract" and strength == "strong":
            raise ValueError("只凭摘要的证据 strength 不能是 strong。读全文后再定，或降为 moderate / weak。")

    with store.tx("evidence: 记录证据", actor=actor, task=task) as tx:
        eid = tx.new_id("evidence")
        tx.write_obj(eid, {"id": eid, "type": "evidence", "target": target, "stance": stance,
                           "strength": strength, "source": source, "locator": locator or None,
                           "basis": basis, "quote": " ".join((quote or "").split()) or None,
                           "created": today()}, note.strip() + "\n")
        tmeta, tbody = store.read_obj(target)
        ev = _as_list(tmeta.get("evidence"))
        if eid not in ev:
            ev.append(eid)
        tmeta["evidence"] = ev
        moved = ""
        if tkind == "H" and tmeta.get("status") == "proposed" and target in batch:
            tmeta["status"] = "investigating"          # 簿记归工具（§5.6）
            tbody = tbody.rstrip() + f"\n\n## 状态变更 {today()}\n\nproposed → investigating（{eid} 是第一条证据）。\n"
            moved = "，并已迁到 investigating"
        tx.write_obj(target, tmeta, tbody)
        if sp == "P":
            pm, pb = read_paper_locked(store, source)
            rank = {"none": 0, "abstract": 1, "fulltext": 2}
            if rank.get(pm.get("read", "none"), 0) < rank[basis]:
                pm["read"] = basis
                tx.write_obj(source, pm, pb)
        tx.note = f"{eid} → {target}"
    return eid, basis, moved


def _evidence(store, ids):
    out = []
    for e in ids:
        m, _ = store.read_obj(e)
        if m is None or m.get("type") != "evidence":
            raise ValueError(f"evidence {e} 不存在。只能引用 record_evidence 返回的 id。")
        out.append(m)
    return out


def transition_hypothesis(store, hid, new_status, evidence_ids, rationale, actor="agent", task=None):
    evidence_ids = _as_list(evidence_ids)
    statuses = schema.KINDS["hypothesis"].enums["status"]
    if not hid.startswith("H") or not store.exists(hid):
        raise ValueError(f"hypothesis '{hid}' 不存在。")
    if new_status not in statuses:
        raise ValueError(f"new_status 必须是 {sorted(statuses)} 之一，收到 '{new_status}'。")
    if actor == "agent" and new_status not in AGENT_TRANSITIONS:
        raise ValueError(f"agent 只能迁到 {sorted(AGENT_TRANSITIONS)}。abandoned（放弃）是方向决定，只有研究者能做。")
    if not evidence_ids:
        raise ValueError("拒绝：状态迁移必须附带至少一条 evidence id。"
                         "先用 record_evidence 记录证据，再用返回的 id 重试。")
    evs = _evidence(store, evidence_ids)
    stray = [e["id"] for e in evs if e.get("target", e.get("hypothesis")) != hid]
    if stray:
        raise ValueError(f"拒绝：evidence {stray} 不是关于 {hid} 的证据。")
    need = {"supported": "support", "refuted": "contradict"}.get(new_status)
    if need and not any(e.get("stance") == need and e.get("basis", "fulltext") == "fulltext"
                        for e in evs):
        raise ValueError(f"拒绝：迁到 {new_status} 至少要一条读过全文的 {need} 证据。"
                         "只凭摘要不能把一条假设判活或判死（§5.5）。")
    if not (rationale or "").strip():
        raise ValueError("rationale 不能为空：必须说明这些证据为何支持该状态迁移。")
    with store.tx("hypothesis: 状态迁移", actor=actor, task=task) as tx:
        meta, body = store.read_obj(hid)
        old = meta.get("status")
        if old == new_status:
            raise ValueError(f"{hid} 已经是 {new_status}。")
        meta["status"] = new_status
        meta["evidence"] = list(dict.fromkeys(_as_list(meta.get("evidence")) + evidence_ids))
        body = (f"{body.rstrip()}\n\n## 状态变更 {today()}\n\n"
                f"{old} → {new_status}，依据 {', '.join(evidence_ids)}。\n\n{rationale.strip()}\n")
        tx.write_obj(hid, meta, body)
        tx.note = f"{hid} {old} → {new_status}"
    new_reviews = reviews.reconcile(store, actor=actor, task=task)   # M5.6b 推翻的传播
    return old, new_reviews


def examine_assumption(store, aid, verdict, evidence_ids, note, actor="agent", task=None):
    """前提审视（M5.1b）的结论：holds → examined；fragile → examined + fragile。推翻走 invalidate。"""
    if not aid.startswith("A") or not store.exists(aid):
        raise ValueError(f"assumption '{aid}' 不存在。")
    if verdict not in ("holds", "fragile"):
        raise ValueError("verdict 必须是 holds（经核查站得住）或 fragile（站得住但脆弱）。"
                         "若证据表明它不成立，用 invalidate_assumption。")
    evs = _evidence(store, _as_list(evidence_ids))
    if not evs:
        raise ValueError("审视结论必须附证据（target 为该前提的 evidence）。")
    stray = [e["id"] for e in evs if e.get("target") != aid]
    if stray:
        raise ValueError(f"evidence {stray} 不是关于 {aid} 的证据。")
    if not (note or "").strip():
        raise ValueError("note 不能为空：说明审视了什么、为什么得出这个结论。")
    with store.tx(f"assumption {aid}: 审视", actor=actor, task=task) as tx:
        meta, body = store.read_obj(aid)
        if meta.get("status") in ("invalidated", "promoted", "retired"):
            raise ValueError(f"{aid} 已是 {meta['status']}，不再审视。")
        old = meta.get("status")
        meta["status"] = "examined"
        meta["fragile"] = verdict == "fragile"
        meta["evidence"] = list(dict.fromkeys(_as_list(meta.get("evidence")) +
                                              [e["id"] for e in evs]))
        tx.write_obj(aid, meta, f"{body.rstrip()}\n\n## 审视 {today()}\n\n{old} → examined"
                                f"（{'脆弱' if verdict == 'fragile' else '站得住'}），依据 "
                                f"{', '.join(e['id'] for e in evs)}。\n\n{note.strip()}\n")
        tx.note = f"{verdict}"
    return old


def invalidate_by_evidence(store, aid, evidence_ids, rationale, actor="agent", task=None):
    evs = _evidence(store, _as_list(evidence_ids))
    if not any(e.get("target") == aid and e.get("stance") == "contradict" for e in evs):
        raise ValueError(f"推翻 {aid} 至少要一条 target 为 {aid}、stance 为 contradict 的证据。")
    return reviews.invalidate_assumption(store, aid, [e["id"] for e in evs], rationale,
                                         actor=actor, task=task)


# ---------------------------------------------------------------- 接地与请求

def annotate_grounding(store, target, verdict, refs, note, actor="agent", task=None):
    k = schema.KINDS["grounding"]
    if schema.split_id(target)[0] not in ("H", "A", "C") or not store.exists(target):
        raise ValueError(f"target '{target}' 不存在或不是 H### / A### / C###。")
    if verdict not in k.enums["verdict"]:
        raise ValueError(f"verdict 必须是 {sorted(k.enums['verdict'])} 之一。")
    refs = _as_list(refs)
    bad = [r for r in refs if schema.split_id(r)[0] not in ("P", "E") or not store.exists(r)]
    if bad:
        raise ValueError(f"refs 中 {bad} 不存在或不是 P### / E###。")
    if verdict != "novel" and not refs:
        raise ValueError("除 novel 外，接地结论必须引用具体论文或证据（refs）。")
    if not (note or "").strip():
        raise ValueError("note 不能为空。")
    with store.tx("grounding: 对抗性接地", actor=actor, task=task) as tx:
        gid = tx.new_id("grounding")
        tx.write_obj(gid, {"id": gid, "type": "grounding", "target": target, "verdict": verdict,
                           "refs": refs or None, "task": task, "created": today()},
                     note.strip() + "\n")
        tx.note = f"{gid} → {target}（{verdict}）"
    return gid


def request_paper(store, pid, why, blocking=True, actor="agent", task=None):
    meta, _ = store.read_obj(pid)
    if meta is None or meta.get("type") != "paper":
        raise ValueError(f"论文 {pid} 不存在：先用 register_paper 按 DOI 登记（登记会核实它确实存在）。")
    if meta.get("fulltext") in ("open", "uploaded"):
        raise ValueError(f"{pid} 已有全文，用 open_paper 读。")
    if not (why or "").strip():
        raise ValueError("why 不能为空：研究者要据此判断值不值得去取。")
    for m, _ in store.list("request"):
        if m.get("paper") == pid and m.get("status") == "open":
            return m["id"], False
    with store.tx("request: 请求全文", actor=actor, task=task) as tx:
        rid = tx.new_id("request")
        tx.write_obj(rid, {"id": rid, "type": "request", "paper": pid, "status": "open",
                           "task": task or "-", "blocking": bool(blocking), "created": today()},
                     f"## 为什么需要\n\n{why.strip()}\n")
        meta, body = read_paper_locked(store, pid)
        meta["fulltext"] = "requested"
        tx.write_obj(pid, meta, body)
        tx.note = f"{rid} → {pid}"
    return rid, True


def fulfill_request(store, lib, rid, raw, actor="human"):
    meta, body = store.read_obj(rid)
    if meta is None or meta.get("type") != "request":
        raise KeyError(f"{rid} 不存在")
    if meta["status"] != "open":
        raise ValueError(f"{rid} 已是 {meta['status']}")
    pid = meta["paper"]
    pm, _ = store.read_obj(pid)
    abstract = (lib.anchors(pid) or {}).get("abstract-p1")
    info = lib.ingest_pdf(pid, pm.get("title"), raw, abstract)
    with store.tx(f"request {rid}: 研究者上传全文", actor=actor) as tx:
        meta.update(status="fulfilled", decided=today())
        tx.write_obj(rid, meta, body.rstrip() + f"\n\n## 处理（{today()}）\n\n已上传 PDF，{info['anchors']} 个锚点。\n")
        pm, pb = read_paper_locked(store, pid)
        pm.update(fulltext="uploaded", fulltext_sha=info["sha"])
        tx.write_obj(pid, pm, pb)
    return meta.get("task"), info


def dismiss_request(store, rid, reason, actor="human"):
    if not (reason or "").strip():
        raise ValueError("必须写明为什么拿不到——它会告诉任务只能按摘要级处理。")
    meta, body = store.read_obj(rid)
    if meta is None or meta.get("type") != "request":
        raise KeyError(f"{rid} 不存在")
    if meta["status"] != "open":
        raise ValueError(f"{rid} 已是 {meta['status']}")
    with store.tx(f"request {rid}: 拿不到", actor=actor) as tx:
        meta.update(status="dismissed", decided=today())
        tx.write_obj(rid, meta, body.rstrip() + f"\n\n## 处理（{today()}）\n\n拿不到：{reason.strip()}\n")
        pm, pb = read_paper_locked(store, meta["paper"])
        pm["fulltext"] = "unavailable"
        tx.write_obj(meta["paper"], pm, pb)
    return meta.get("task")
