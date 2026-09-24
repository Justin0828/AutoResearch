"""无人值守的规划器（M2.0b 阶梯的 Phase 2 实现，DESIGN.md §5.7）。

机械规划，不派 agent 决定做什么：对验证批次里的每个目标，从 State 与任务台账推出
流水线的下一步（检索 → 精读 → 评估），外加矛盾扫描；都没有就是第 9 级——收工、留额度。
每个任务的 `why` 在这里写明，前端“为什么读这篇”直接读它。
"""
import json

from . import incubation, modes

DONE_H = {"supported", "refuted", "abandoned"}
DONE_A = {"invalidated", "promoted", "retired"}
LIVE = {"queued", "running"}
SCAN_EVERY = 5


def _as_list(v):
    if v in (None, ""):
        return []
    return v if isinstance(v, list) else [x.strip() for x in str(v).split(",") if x.strip()]


def tool_lines(ledger, tid):
    p = ledger.path(tid) / "tools.jsonl"
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def tool_calls(ledger, tid, name=None, since=None):
    """任务的成功工具调用。since 只取本次尝试的——被挂起、切断后重跑的任务，tools.jsonl 里还留着
    上一次尝试的记录。since 为整数时是开跑时 tools.jsonl 的行数（精确）；为 ISO 时间时按秒比较
    （旧任务的兼容写法：两次尝试落在同一秒会分不开）。"""
    p = ledger.path(tid) / "tools.jsonl"
    if not p.exists():
        return []
    out = []
    lines = p.read_text(encoding="utf-8").splitlines()
    if isinstance(since, int):
        lines, since = lines[since:], None
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("ok") and (name is None or e.get("tool") == name) and \
                (since is None or e.get("ts", "") >= since):
            out.append(e)
    return out


def target_done(store, t):
    m, _ = store.read_obj(t)
    if not m:
        return True
    if t.startswith("H"):
        return m.get("status") in DONE_H
    return m.get("status") in DONE_A or (m.get("status") == "examined" and "fragile" in m)


def evidence_of(store, t):
    return [m["id"] for m, _ in store.list("evidence") if m.get("target") == t]


def next_step(store, ledger, cfg, targets, tasks, tag):
    """给定目标列表与台账，返回下一个要建的任务 dict（kind, goal, fields），或 None。

    tag 区分验证批次（batch=DEC###）与夜间预习（prep=DEC###），各自只数自己的任务。
    """
    mine = [t for t in tasks if t.get(tag[0]) == tag[1]]
    read_papers = {t.get("paper") for t in tasks if t["kind"] == "read_paper"
                   and t["status"] in LIVE | {"done", "blocked_on_human"}}
    # 读的时候只有摘要、后来全文到了（上传或补取）：值得再读一遍
    for m, _ in store.list("paper"):
        if m["id"] in read_papers and m.get("fulltext") in ("open", "uploaded") \
                and m.get("read") != "fulltext" and not any(
                    t.get("paper") == m["id"] and t["status"] in LIVE for t in tasks):
            read_papers.discard(m["id"])
    # 前提在前（阶梯第 3 级：未检验前提的核查直接喂养基本盘），假设在后（第 4 级）
    for target in sorted(targets, key=lambda x: (not x.startswith("A"), x)):
        if target_done(store, target):
            continue
        tt = [t for t in mine if t.get("target") == target]
        searches = [t for t in tt if t["kind"] == "lit_search"]
        reads = [t for t in tt if t["kind"] == "read_paper"]
        if any(t["status"] in LIVE for t in searches):
            continue
        pending_reads = [t for t in reads if t["status"] in LIVE]
        # 1. 评估：自上次评估后有新证据，且这个目标没有在读的论文
        if tag[0] == "batch" and not pending_reads:
            ev = evidence_of(store, target)
            last = max((t for t in tt if t["kind"] == "assess" and t["status"] in LIVE | {"done"}),
                       key=lambda t: t["id"], default=None)
            if last and last["status"] in LIVE:
                continue
            seen = set(last.get("evidence_seen", [])) if last else set()
            if set(ev) - seen:
                return {"kind": "assess", "target": target, "evidence_seen": ev, "priority": 3,
                        "goal": f"评估 {target}：综合它的全部 {len(ev)} 条证据，决定状态是否改变",
                        "why": f"{target} 自上次评估后新增证据 {', '.join(sorted(set(ev) - seen))}"}
        # 2. 精读：为这个目标登记、还没人读过的论文
        if len(reads) < cfg.reads_per_target:
            for m, _ in store.list("paper"):
                if target not in _as_list(m.get("for")) or m["id"] in read_papers:
                    continue
                if m.get("fulltext") == "requested":
                    continue
                via = m.get("found_via") or "登记"
                return {"kind": "read_paper", "target": target, "paper": m["id"], "priority": 4,
                        "goal": f"精读 {m['id']}「{m.get('title')}」，抽取关于 {target}（以及其他相关假设 / 前提）的证据",
                        "why": f"{target} 的第 {len(reads) + 1} 篇（上限 {cfg.reads_per_target}）："
                               f"{m['id']} 由 {via} 找到，为 {target} 登记"}
        elif pending_reads:
            continue
        # 3. 检索：还没检索过，或检索过但没找到可读的论文
        if len(searches) < cfg.searches_per_target and not pending_reads:
            unread = [m for m, _ in store.list("paper")
                      if target in _as_list(m.get("for")) and m["id"] not in read_papers]
            if not searches or (not unread and len(reads) < cfg.reads_per_target):
                return {"kind": "lit_search", "target": target, "priority": 4,
                        "goal": f"为 {target} 检索最能检验它的论文（第 {len(searches) + 1} 次，"
                                f"上限 {cfg.searches_per_target}）",
                        "why": (f"{target} 还没有检索过" if not searches else
                                f"{target} 已登记的论文都读完了，证据仍不足以下结论")}
    # 4. 矛盾扫描（阶梯第 5 级）
    if tag[0] == "batch":
        total = len(store.list("evidence"))
        last = max((t for t in mine if t["kind"] == "contradiction_scan"), key=lambda t: t["id"],
                   default=None)
        if last and last["status"] in LIVE:
            return None
        base = last.get("evidence_count", 0) if last else 0
        if total - base >= SCAN_EVERY:
            return {"kind": "contradiction_scan", "priority": 5, "evidence_count": total,
                    "goal": "扫描全部证据之间、证据与前提 / 假设之间的冲突",
                    "why": f"上次扫描后新增 {total - base} 条证据"}
    return None


def pick_prep_target(store):
    """夜间预习的机械选题（人没写“今晚查什么”时）。返回 (target, why) 或 (None, None)。"""
    # 推演中新引入、所属想法未被接受的前提不参与选题（§5.10）
    asm = [m for m, _ in incubation.real_assumptions(store) if m.get("status") == "unexamined"]
    if asm:
        a = max(asm, key=lambda m: (len(_as_list(m.get("relied_on_by"))), m["id"]))
        return a["id"], (f"{a['id']} 是未检验的前提，支撑着 {', '.join(_as_list(a.get('relied_on_by')))}"
                         "——整条方向建立在它上面，却还没人查过")
    hyp = [m for m, _ in store.list("hypothesis")
           if m.get("status") == "proposed" and not _as_list(m.get("evidence"))]
    if hyp:
        h = hyp[0]
        return h["id"], f"{h['id']} 还没有任何证据"
    # 活跃问题（§5.16.2）：研究者标为“正在想的”，以“调研 Q00x”为题
    from . import schema
    act = [m for m, _ in store.list("question") if schema.is_active(m)
           and schema.question_status(m) == "open" and not schema.is_withdrawn(m)]
    if act:
        q = act[0]
        return q["id"], f"{q['id']} 是你标为活跃的问题（正在想的），先替你调研相关文献"
    # 不确定性不是 H/A，做不成证据目标；Phase 2 不自动挑，等人在“今晚查什么”里点名
    return None, None


def question_line(store, qid, n=80):
    from .objects import statement_of
    _, b = store.read_obj(qid)
    t = " ".join(statement_of(b).split())
    return t[:n] + ("…" if len(t) > n else "")


def batch_saturated(store, ledger, cfg, tasks):
    b = modes.batch(store)
    return bool(b) and all(target_done(store, t) for t in b)
