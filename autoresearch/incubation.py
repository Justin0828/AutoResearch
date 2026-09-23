"""想法自演进（M11，DESIGN.md §5.10–5.14）。

检索关闭的推演链从冻结的基本盘出发产出 Idea；另一个开着检索的接地任务只标注不改写；
基本盘只在轮与轮之间、只由接地带回的外部证据（E / GR）更新，永不由推演自身更新。

这里全是机械操作：入场闸门、基本盘装配与更新、record_idea 的闸门、推演记录、
接地后的状态、停止条件、分诊。**没有一处让模型给自己打分**（§5.14）。
"""
import re

from . import attribution, frontmatter, schema
from .store import today

SHORTLIST_TARGET = 3          # M11.7：目标，不是配额——只出现在停止条件里，推演链看不到它
BUDGET_SHARE = 0.30           # M2.0：自演进占 5h 窗口的 30% 上限
TASK_EQUIV = 0.01             # Phase 0 实测：一个任务约 1% 窗口；没有额度数据时按它兜底
MAX_IDEAS_PER_CHAIN = 2
EMPTY_ROUNDS_STOP = 2
LIVE_IDEA = ("grounding", "screened_out", "shortlisted")
TRIAGEABLE = ("grounding", "screened_out", "shortlisted")


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _first(body, n=200):
    t = " ".join((body or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def _section(body, title):
    m = re.search(rf"^## {re.escape(title)}\n\n(.*?)(?=\n## |\Z)", body or "", re.S | re.M)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------- 模式

def session(store):
    """当前自演进 session（进入时写的 Decision），不在自演进模式时为 None。"""
    pm, _ = store.project()
    return pm.get("incubation") if pm.get("mode") == "incubation" else None


def entry_problems(store):
    """入场闸门（M11.1）。返回不满足的条件（中文、可直接展示给人），空列表表示放行。"""
    pm, _ = store.project()
    out = []
    if pm.get("mode") == "validation":
        out.append("当前在验证模式：先收回讨论模式，再进入自演进。")
    elif pm.get("mode") == "incubation":
        out.append("已经在自演进模式。")
    qid = pm.get("main_question")
    qm, qb = store.read_obj(qid) if qid else (None, None)
    if not qm:
        out.append("项目没有主问题（project.md 的 main_question）。")
    elif qm.get("maturity") != "formalized":
        gap = _section(qb, "当前的形式化程度")
        out.append(f"{qid} 的成熟度是 {qm.get('maturity')}，自演进要求 formalized——问题要有可测量的定义，"
                   "否则基本盘没有成形，推演只会空转（M11.1）。"
                   + (f"\n\n{qid} 里记下的未形式化之处：\n{gap}" if gap else "")
                   + "\n\n在讨论模式里把它推到 formalized，然后在 Research 页把成熟度改为 formalized。")
    return out


def enter(store, note=""):
    """人切进自演进：写 Decision（kind=mode），立刻取首版基本盘。返回 (DEC, F)。"""
    problems = entry_problems(store)
    if problems:
        raise ValueError("\n\n".join(problems))
    base = store.head()
    with store.tx("mode: 进入自演进", actor="human") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "mode", "refs": [],
                           "created": today()},
                     "## 做了什么\n\n进入自演进模式：检索关闭，从冻结的基本盘推演（M11）。\n\n## 为什么\n\n"
                     + ((note or "").strip() or "（研究者没有写说明。）") + "\n")
        pm, pb = store.project()
        pm.update(mode="incubation", incubation=did)
        tx.write("project.md", frontmatter.dump(pm, pb))
        tx.note = did
    fid = build_first(store, did, base)
    return did, fid


def leave(store, reason=""):
    did0 = session(store)
    if not did0:
        raise ValueError("当前不在自演进模式")
    with store.tx("mode: 离开自演进，回到讨论模式", actor="human") as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": "mode", "refs": [did0],
                           "created": today()},
                     f"## 做了什么\n\n从自演进模式回到讨论模式（结束 session {did0}）。\n\n## 为什么\n\n"
                     + ((reason or "").strip() or "（研究者没有写说明。）") + "\n")
        pm, pb = store.project()
        pm["mode"] = "discussion"
        pm.pop("incubation", None)
        tx.write("project.md", frontmatter.dump(pm, pb))
        tx.note = did
    return did


# ---------------------------------------------------------------- 推测性前提

def speculative(store, meta, _cache=None):
    """推演中新引入、所属 Idea 还没被人接受的前提（§5.10）：不进讨论 / 评判的前提一章、
    不进夜间预习选题、不能进验证批次。"""
    iid = (meta or {}).get("idea")
    if not iid:
        return False
    im, _ = store.read_obj(iid)
    return not im or im.get("status") != "accepted"


def real_assumptions(store):
    return [(m, b) for m, b in store.list("assumption") if not speculative(store, m)]


# ---------------------------------------------------------------- 基本盘（§5.11）

def redact_titles(store, text):
    """基本盘不给论文标题（§5.11，用户确认）：标题本身就是文献框架的名字。接地任务写的话、证据的 note 里常带着
    论文名，这里按已登记论文的标题与冒号前的简称机械替换成 P###。作者名、未登记的论文名管不到——屏蔽不完美，
    但推演读不到任何全文，漏出的只是名字。"""
    names = []
    for m, _ in store.list("paper"):
        t = " ".join(str(m.get("title") or "").split())
        if not t:
            continue
        names.append((t, m["id"]))
        short = re.split(r"[:：]", t, 1)[0].strip()
        if short != t and len(short) >= 4:
            names.append((short, m["id"]))
    for name, pid in sorted(names, key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", pid, text, flags=re.I)
    return text

def _ev_line(store, m, b):
    pm, _ = store.read_obj(m.get("source", "")) if str(m.get("source", "")).startswith("P") else (None, None)
    year = f"（{pm.get('year')}）" if pm and pm.get("year") else ""
    loc = f" {'[' + ', '.join(_as_list(m.get('locator'))) + ']'}" if m.get("locator") else ""
    q = (m.get("quote") or "")[:400]
    return (f"  - {m['id']} · {m.get('stance')} · {m.get('strength')} · 出处 {m.get('source')}{year}{loc}"
            + (" · 仅摘要" if m.get("basis") == "abstract" else "")
            + f"：{_first(attribution.neutralize(b), 240)}" + (f"\n    > {q}" if q else ""))


def _evidence_of(store, target, fulltext_only=False):
    out = []
    for m, b in store.list("evidence"):
        if m.get("target") == target and (not fulltext_only or m.get("basis") == "fulltext"):
            out.append(_ev_line(store, m, b))
    return out


def rejected_ideas(store):
    return [(m, b) for m, b in store.list("idea") if m.get("status") == "rejected"]


def _rejected_block(store):
    out = []
    for m, b in rejected_ideas(store):
        out.append(f"- **{m['id']}**（被研究者否决）：{_first(_section(b, '陈述'), 300)}\n"
                   f"  否决理由：{_first(_section(b, '为什么不要'), 400)}")
    return out


def foundation_body(store):
    """首版基本盘的正文：只由 State 机械装配，不含 origin、论文全文 / 笔记 / 标题、讨论、候选、决策。"""
    n = attribution.neutralize
    pm, _ = store.project()
    qid = pm.get("main_question")
    qm, qb = store.read_obj(qid)
    parts = [f"## 1. 研究问题\n\n**{qid}** · maturity={qm.get('maturity')}\n\n{n(qb.strip())}"]

    ins = [(m, b) for m, b in store.list("insight") if m.get("status") == "active"]
    order = {"settled": 0, "working": 1, "hunch": 2}
    ins.sort(key=lambda x: order.get(x[0].get("firmness"), 3))
    parts.append("## 2. 当前理解\n\n以下是研究至今形成的看法与直觉——**是理解，不是证据**。你可以在上面更进一步，"
                 "也可以批评它；不必二选一。\n\n" + ("\n\n".join(
                     f"- **{m['id']}** · {m.get('firmness')}：{n(b.strip())}" for m, b in ins) or "（暂无。）"))

    facts = []
    for m, b in store.list("hypothesis"):
        if m.get("status") in ("supported", "refuted"):
            facts.append(f"- **{m['id']}**（{m['status']}）：{n(b.strip())}\n"
                         + "\n".join(_evidence_of(store, m["id"], fulltext_only=True)))
    for m, b in real_assumptions(store):
        if m.get("status") == "examined":
            tag = "脆弱" if str(m.get("fragile")).lower() == "true" else "站得住"
            facts.append(f"- **{m['id']}**（前提，审视结论：{tag}）：{n(b.strip())}\n"
                         + "\n".join(_evidence_of(store, m["id"])))
        elif m.get("status") == "invalidated":
            facts.append(f"- **{m['id']}**（前提，**已被推翻**，依据 {', '.join(_as_list(m.get('invalidated_by')))}）："
                         f"{n(b.strip())}\n" + "\n".join(_evidence_of(store, m["id"])))
    parts.append("## 3. 已确立的事实\n\n证据只给陈述、立场与引文——这是你能看到的全部文献。\n\n"
                 + ("\n\n".join(facts) or "（还没有经证据确立的事实。）"))

    prem = [(m, b) for m, b in real_assumptions(store) if m.get("status") == "unexamined"]
    parts.append("## 4. 前提集（未检验，正被依赖）\n\n" + ("\n\n".join(
        f"- **{m['id']}** · 支撑 {', '.join(_as_list(m.get('relied_on_by')))}：{n(b.strip())}"
        for m, b in prem) or "（暂无。）"))

    hyp = [(m, b) for m, b in store.list("hypothesis")
           if m.get("status") in ("proposed", "investigating", "inconclusive")]
    parts.append("## 5. 未定的假设\n\n**未定——不是事实，也不是约束。**\n\n" + ("\n\n".join(
        f"- **{m['id']}**（{m.get('status')}）：{n(b.strip())}\n  证伪条件：{n(str(m.get('falsifier') or ''))}"
        for m, b in hyp) or "（暂无。）"))

    de = [f"- **{m['id']}**（{m.get('status')}，关闭依据 {', '.join(_as_list(m.get('closed_by')))}）：{n(b.strip())}"
          for m, b in store.list("dead-end")]
    parts.append("## 6. 已关闭方向（全部，不截断）\n\n" + ("\n\n".join(de + _rejected_block(store))
                                                   or "（暂无。）"))

    unc = [f"- **{m['id']}** · {m.get('importance')}：{n(b.strip())}"
           for m, b in store.list("uncertainty") if m.get("status") != "resolved"]
    from . import reviews
    rv = [f"- {r['target']}（由 {r['trigger']} 被推翻而待重新审视）" for r in reviews.list_all(store, "open")]
    parts.append("## 7. 未决不确定性与待重新审视\n\n" + ("\n".join(unc + rv) or "（暂无。）"))
    parts.append("## 8. 本 session 外部核查带回的结果\n\n（暂无。）")
    return redact_titles(store, "\n\n".join(parts) + "\n")


def build_first(store, did, base=None):
    pm, _ = store.project()
    body = foundation_body(store)
    with store.tx("foundation: 进入自演进时取首版基本盘", actor="system") as tx:
        fid = tx.new_id("foundation")
        tx.write_obj(fid, {"id": fid, "type": "foundation", "session": did, "round": 1,
                           "question": pm.get("main_question"), "base": base, "created": today()},
                     f"# 基本盘 {fid}\n\n{body}")
        tx.note = fid
    return fid


def current_foundation(store, did):
    fs = [m for m, _ in store.list("foundation") if m.get("session") == did]
    return max(fs, key=lambda m: schema.split_id(m["id"])[1])["id"] if fs else None


def _delta_lines(store, ids):
    out = []
    for i in ids:
        m, b = store.read_obj(i)
        if not m:
            continue
        if i.startswith("GR"):
            refs = ", ".join(_as_list(m.get("refs")))
            out.append(f"- **{i}**（对 {m.get('target')} 的外部核查：{m.get('verdict')}"
                       + (f"，出处 {refs}" if refs else "") + f"）：{_first(attribution.neutralize(b), 700)}")
        else:
            out.append(_ev_line(store, m, b).replace("  - ", "- ", 1))
    return out


def next_foundation(store, did, round_no, delta):
    """轮与轮之间：F(n+1) = F(n) + 本轮接地带回的 E / GR（§5.11）。没有外部证据就不出新版。"""
    delta = [d for d in dict.fromkeys(delta) if store.exists(d)]
    parent = current_foundation(store, did)
    if not delta or not parent:
        return None
    pm, pb = store.read_obj(parent)
    head = "## 8. 本 session 外部核查带回的结果"
    before, _, after = pb.partition(head)
    after = after.strip()
    after = "" if after == "（暂无。）" else after
    lines = [redact_titles(store, x) for x in _delta_lines(store, delta)]
    body = (before + head + "\n\n" + (after + "\n\n" if after else "")
            + f"第 {round_no} 轮之后：\n\n" + "\n\n".join(lines) + "\n")
    with store.tx("foundation", actor="system") as tx:
        fid = tx.new_id("foundation")
        body = re.sub(r"^# 基本盘 F\d+", f"# 基本盘 {fid}", body, count=1, flags=re.M)
        tx.write_obj(fid, {"id": fid, "type": "foundation", "session": did,
                           "round": int(round_no) + 1, "question": pm.get("question"),
                           "parent": parent, "delta": delta, "created": today()}, body)
        ev = [d for d in delta if d.startswith("E")]
        gr = [d for d in delta if d.startswith("GR")]
        tx.note = f"{fid} ← {parent}：+{' '.join(ev) or '（无新证据）'}（{' '.join(gr) or '无接地结论'}）"
    return fid


# ---------------------------------------------------------------- Idea（§5.10）

def record_idea(store, *, task, chain, statement, falsifier, new_premises, reasoning="",
                challenges=None, challenge_notes="", builds_on=None, relates_to=None,
                relation_note="", called_dead_ends=False):
    """推演链登记一条 Idea。机械闸门过不了就拒绝（§5.10），不接受任何自评分数。"""
    did = session(store)
    if not did:
        raise ValueError("当前不是自演进模式，不能登记 Idea。")
    fid = chain.get("foundation")
    if not fid or chain.get("incubation") != did:
        raise ValueError("本任务不是当前自演进 session 的推演链。")
    statement, falsifier = (statement or "").strip(), (falsifier or "").strip()
    if not statement:
        raise ValueError("statement 不能为空。")
    if not falsifier:
        raise ValueError("falsifier 不能为空：说不出这个想法在什么情况下是错的，就还不是一个想法（M11.8）。")
    if " ".join(falsifier.split()) == " ".join(statement.split()):
        raise ValueError("falsifier 不能与陈述相同：写出什么观察 / 结果会说明它是错的。")
    if not called_dead_ends:
        raise ValueError("登记前必须先调用 check_dead_ends，与已关闭方向、被否决的想法、本 session 已记下的想法比对（M11.8）。")
    if new_premises is None:
        raise ValueError("new_premises 必须显式给出：推演中引入的、基本盘里没有的前提逐条列出；确实没有就传空列表 []。")
    premises = [" ".join(str(p).split()) for p in new_premises if str(p).strip()]
    challenges, builds_on, relates_to = _as_list(challenges), _as_list(builds_on), _as_list(relates_to)
    fbody = store.read_obj(fid)[1] or ""
    for f, ids in (("challenges", challenges), ("builds_on", builds_on), ("relates_to", relates_to)):
        allowed = schema.KINDS["idea"].refs[f]
        bad = [i for i in ids if schema.split_id(i)[0] not in allowed or not store.exists(i)]
        if bad:
            raise ValueError(f"{f} 中 {bad} 不存在或类型不对（允许 {'/'.join(allowed)}）。")
    outside = [i for i in challenges if not re.search(rf"\b{re.escape(i)}\b", fbody)]
    if outside:
        raise ValueError(f"challenges 中 {outside} 不在本轮基本盘 {fid} 里——只能挑战基本盘里的东西。")
    if challenges and not (challenge_notes or "").strip():
        raise ValueError("有挑战就要写 challenge_notes：挑战的是什么、为什么（M11.4：挑战可以，悄悄漂移不行）。")
    mine = [m for m, _ in store.list("idea") if m.get("chain") == task]
    if len(mine) >= MAX_IDEAS_PER_CHAIN:
        raise ValueError(f"一条推演链最多登记 {MAX_IDEAS_PER_CHAIN} 条 Idea（本链已有 {', '.join(m['id'] for m in mine)}）。")

    with store.tx("idea: 推演链登记 Idea", actor="agent", task=task) as tx:
        iid = tx.new_id("idea")
        aids = []
        for p in premises:
            aid = tx.new_id("assumption")
            tx.write_obj(aid, {"id": aid, "type": "assumption", "status": "unexamined",
                               "relied_on_by": [iid], "idea": iid, "created": today()},
                         f"{p}\n\n（推演 {task} 为 {iid} 引入的新前提，未检验。）\n")
            tx.set_provenance(aid, {"origin": "ai", "source": iid, "task": task})
            aids.append(aid)
        body = (f"## 陈述\n\n{statement}\n\n## 推理概要\n\n{(reasoning or '').strip() or '（未写。）'}\n\n"
                f"## 对基本盘的挑战\n\n{(challenge_notes or '').strip() or '无'}\n\n"
                f"## 与既有对象的关系\n\n{(relation_note or '').strip() or '（未写。）'}\n")
        tx.write_obj(iid, {"id": iid, "type": "idea", "status": "grounding", "falsifier": falsifier,
                           "premises": aids, "challenges": challenges or None,
                           "builds_on": builds_on or None, "relates_to": relates_to or None,
                           "foundation": fid, "chain": task, "session": did,
                           "round": chain.get("round"), "created": today()}, body)
        tx.set_provenance(iid, {"origin": "ai", "source": task})
        tx.note = f"{iid}（新前提 {len(aids)} 条：{', '.join(aids) or '无'}）"
    return iid, aids


def on_grounding(store, iid, gid, verdict, actor="agent", task=None):
    """接地结论到了：回填 grounding，并按 §5.14 机械置状态——只有被直接反驳才筛掉。
    在 annotate_grounding 的同一把锁外调用（它自己开事务）。"""
    m, b = store.read_obj(iid)
    if not m or m.get("status") not in ("grounding", "screened_out", "shortlisted"):
        return None
    g = _as_list(m.get("grounding"))
    if gid not in g:
        g.append(gid)
    m["grounding"] = g
    m["status"] = "screened_out" if verdict == "contradicted" else "shortlisted"
    with store.tx(f"idea {iid}: 接地 {gid} → {m['status']}", actor=actor, task=task) as tx:
        tx.write_obj(iid, m, b)
    return m["status"]


def signals(store, m):
    """前端与排序用的信号（不是闸门）：N、接地发现的未登记前提 / 挑战、相近工作。"""
    g = [store.read_obj(x)[0] or {} for x in _as_list(m.get("grounding"))]
    last = g[-1] if g else {}
    return {"n": len(_as_list(m.get("premises"))),
            "hidden_premises": int(last.get("hidden_premises") or 0),
            "silent_challenges": int(last.get("silent_challenges") or 0),
            "verdict": last.get("verdict"),
            "prior_work": last.get("verdict") in ("prior_work", "mixed")}


def ranked(store, metas):
    """M11.10 的机械排序：N 小的在前，再按接地发现的未登记前提数。"""
    return sorted(metas, key=lambda m: (signals(store, m)["n"], signals(store, m)["hidden_premises"],
                                        m["id"]))


def write_chain(store, task, status, result="", checkpoints=(), ideas=()):
    """推演记录（§5.10）：每条链的推理原文都进 State，交白卷的也留着。"""
    angle = next((c["note"].split("：", 1)[-1].strip() for c in checkpoints
                  if c.get("note", "").startswith(("角度：", "角度:"))), "")
    cps = "\n".join(f"- {c['ts']} {c['note']}" for c in checkpoints) or "（无。）"
    body = (f"# 推演记录 {task['id']}\n\n## 切入角度\n\n{angle or '（没有写明。）'}\n\n"
            f"## 过程笔记（checkpoint）\n\n{cps}\n\n## 最终回复\n\n{(result or '').strip() or '（无。）'}\n")
    with store.tx(f"chain {task['id']}: 推演记录（{status}）", actor="system", task=task["id"]) as tx:
        tx.write_obj(task["id"], {"id": task["id"], "type": "chain", "session": task["incubation"],
                                  "round": task.get("round"), "foundation": task["foundation"],
                                  "angle": angle or None, "ideas": list(ideas) or None,
                                  "status": status, "created": today()}, body)
    return angle


def angles(store, did):
    return [(m["id"], m.get("round"), m.get("angle")) for m, _ in store.list("chain")
            if m.get("session") == did and m.get("angle")]


def session_ideas(store, did):
    return [(m, b) for m, b in store.list("idea") if m.get("session") == did]


# ---------------------------------------------------------------- 停止与交卷（§5.13）

def spent(tasks):
    """本 session 用掉的 5h 窗口份额：各任务开始 / 结束时 five_hour 利用率之差累加；没数据按任务数兜底。"""
    got = [t for t in tasks if t.get("q5_start") is not None and t.get("q5_end") is not None]
    if got:
        return sum(max(0.0, t["q5_end"] - t["q5_start"]) for t in got) + \
            TASK_EQUIV * len([t for t in tasks if t not in got and t.get("ended")])
    return TASK_EQUIV * len([t for t in tasks if t.get("ended")])


def stop_reason(store, did, tasks, empty_rounds, round_no, max_rounds):
    ideas = [m for m, _ in session_ideas(store, did)]
    ok = [m for m in ideas if m.get("status") == "shortlisted"]
    if len(ok) >= SHORTLIST_TARGET:
        return f"{len(ok)} idea(s) passed the gates"
    s = spent(tasks)
    if s >= BUDGET_SHARE:
        return f"used {round(s * 100)}% of the 5-hour window (budget {round(BUDGET_SHARE * 100)}%)"
    if empty_rounds >= EMPTY_ROUNDS_STOP:
        return f"{empty_rounds} rounds in a row produced nothing worth recording"
    if round_no >= max_rounds:
        return f"reached the round limit ({max_rounds})"
    return None


def conclude(store, did, reason, rounds, chains):
    """收工：写一条 Decision 作为本 session 的交卷。0 条过关时明说“本轮没有值得你看的东西”。"""
    ideas = session_ideas(store, did)
    ok = ranked(store, [m for m, _ in ideas if m.get("status") == "shortlisted"])
    lines = [f"停止原因：{reason}。共 {rounds} 轮、{chains} 条推演链，记下 {len(ideas)} 条 Idea，"
             f"过关 {len(ok)} 条。"]
    if not ok:
        lines.append("\n**本轮没有值得你看的东西。**")
    else:
        lines.append("\n过关的（按新前提数 N 从少到多）：")
        for m in ok:
            sg = signals(store, m)
            _, b = store.read_obj(m["id"])
            lines.append(f"- {m['id']}（N={sg['n']}，接地 {sg['verdict']}）：{_first(_section(b, '陈述'), 160)}")
    out = [(m, b) for m, b in ideas if m.get("status") != "shortlisted"]
    if out:
        lines.append("\n没过的：")
        for m, b in out:
            why = {"screened_out": "接地找到了直接反驳",
                   "grounding": "接地没有完成"}.get(m.get("status"), m.get("status"))
            lines.append(f"- {m['id']}：{why}——{_first(_section(b, '陈述'), 120)}")
    with store.tx("decision: 自演进交卷", actor="system") as tx:
        dec = tx.new_id("decision")
        tx.write_obj(dec, {"id": dec, "type": "decision", "kind": "research",
                           "refs": [did] + [m["id"] for m, _ in ideas], "incubation_summary": did,
                           "created": today()},
                     "## 做了什么\n\n自演进 session " + did + " 收工。\n\n" + "\n".join(lines) +
                     "\n\n## 为什么\n\n停止条件先到先停（M11.7 / §5.13）：3 条过关、30% 窗口预算、"
                     "连续两轮没东西可说、轮数上限。\n")
        tx.note = dec
    return dec, len(ok)


# ---------------------------------------------------------------- 分诊（§5.14）

def accept(store, iid, hypothesis=None, insight=None, note=""):
    """人接受一条 Idea：可当场拆出假设、记成理解，或二者都不做（留到讨论里再说）。"""
    m, b = store.read_obj(iid)
    if not m or m.get("type") != "idea":
        raise KeyError(f"{iid} 不存在")
    if m.get("status") not in TRIAGEABLE:
        raise ValueError(f"{iid} 已是 {m['status']}")
    made = []
    with store.tx(f"idea {iid}: 研究者接受", actor="human") as tx:
        if hypothesis:
            st = (hypothesis.get("statement") or "").strip() or _section(b, "陈述")
            fals = (hypothesis.get("falsifier") or "").strip() or m.get("falsifier")
            val = (hypothesis.get("validation") or "").strip()
            if not val:
                raise ValueError("拆成假设要写 validation：怎么验证它")
            conf = hypothesis.get("confidence") or "low"
            hid = tx.new_id("hypothesis")
            tx.write_obj(hid, {"id": hid, "type": "hypothesis", "status": "proposed",
                               "confidence": conf, "falsifier": fals, "validation": val,
                               "evidence": [], "idea": iid, "created": today()},
                         f"{st}\n\n（由想法 {iid} 拆出。）\n")
            tx.set_provenance(hid, {"origin": "ai", "source": iid})
            made.append(hid)
        if insight:
            st = (insight.get("statement") or "").strip() or _section(b, "陈述")
            firm = insight.get("firmness") or "hunch"
            if firm not in ("hunch", "working", "settled"):
                raise ValueError("firmness 必须是 hunch / working / settled")
            nid = tx.new_id("insight")
            tx.write_obj(nid, {"id": nid, "type": "insight", "status": "active", "firmness": firm,
                               "basis": [iid], "created": today()},
                         f"{st}\n\n（来自自演进的想法 {iid}。）\n")
            tx.set_provenance(nid, {"origin": "ai", "source": iid})
            made.append(nid)
        m.update(status="accepted", decided=today(), promoted_to=made or None)
        tx.write_obj(iid, m, b.rstrip() + f"\n\n## 研究者接受（{today()}）\n\n"
                     + ((note or "").strip() or "（没有写说明。）")
                     + (f"\n\n由它建出：{', '.join(made)}。" if made else "") + "\n")
        dec = tx.new_id("decision")
        tx.write_obj(dec, {"id": dec, "type": "decision", "kind": "curation", "refs": [iid] + made,
                           "created": today()},
                     f"## 做了什么\n\n接受自演进的想法 {iid}" + (f"，建出 {', '.join(made)}" if made else "")
                     + f"。它的 {len(_as_list(m.get('premises')))} 条前提转为普通前提。\n\n## 为什么\n\n"
                     + ((note or "").strip() or "（研究者没有写说明。）") + "\n")
        tx.note = f"→ {', '.join(made) or '仅接受'}"
    return made


def reject(store, iid, reason):
    """人否决：必须写理由；推测性前提退役；之后与 dead-end 同等对待（全文列出、不截断）。"""
    if not (reason or "").strip():
        raise ValueError("否决一条想法必须写理由——它会和 dead-end 一样，之后每次推演都会看到")
    m, b = store.read_obj(iid)
    if not m or m.get("type") != "idea":
        raise KeyError(f"{iid} 不存在")
    if m.get("status") not in TRIAGEABLE:
        raise ValueError(f"{iid} 已是 {m['status']}")
    with store.tx(f"idea {iid}: 研究者否决", actor="human") as tx:
        for aid in _as_list(m.get("premises")):
            am, ab = store.read_obj(aid)
            if am and am.get("status") == "unexamined":
                am["status"] = "retired"
                tx.write_obj(aid, am, ab.rstrip() + f"\n\n## 退役（{today()}）\n\n所属想法 {iid} 被否决。\n")
        m.update(status="rejected", decided=today())
        tx.write_obj(iid, m, b.rstrip() + f"\n\n## 为什么不要\n\n{reason.strip()}\n")
    return iid


def as_dict(store, m, b):
    sg = signals(store, m)
    prem = []
    for a in _as_list(m.get("premises")):
        am, ab = store.read_obj(a)
        prem.append({"id": a, "status": (am or {}).get("status"),
                     "text": (ab or "").split("\n\n（推演")[0].strip()})
    gr = []
    for g in _as_list(m.get("grounding")):
        gm, gb = store.read_obj(g)
        if gm:
            gr.append({"id": g, "verdict": gm.get("verdict"), "refs": _as_list(gm.get("refs")),
                       "hidden_premises": gm.get("hidden_premises"),
                       "silent_challenges": gm.get("silent_challenges"), "note": (gb or "").strip()})
    return {"id": m["id"], "status": m.get("status"), "falsifier": m.get("falsifier"),
            "statement": _section(b, "陈述"), "reasoning": _section(b, "推理概要"),
            "challenge_note": _section(b, "对基本盘的挑战"), "relation": _section(b, "与既有对象的关系"),
            "rejected_because": _section(b, "为什么不要"),
            "challenges": _as_list(m.get("challenges")), "builds_on": _as_list(m.get("builds_on")),
            "relates_to": _as_list(m.get("relates_to")), "premises": prem, "grounding": gr,
            "signals": sg, "foundation": m.get("foundation"), "chain": m.get("chain"),
            "session": m.get("session"), "round": m.get("round"),
            "promoted_to": _as_list(m.get("promoted_to")), "created": m.get("created")}
