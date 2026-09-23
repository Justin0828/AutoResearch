#!/usr/bin/env python3
"""Research State MCP server（stdio JSON-RPC，零依赖）。

由 spike/phase0/mcp_state_server.py 长成（124 次调用零失败的那一版）。变化：
- 所有写入经 Store.tx：持锁、只提交本次路径、作者 ar-agent、带 AR-Task trailer（§5.4）
- 按 AR_TOOLSET 只注册本任务能用的工具（--allowedTools 不移除 MCP 工具，§5.3）
- 新增 propose_candidate / update_discussion_summary / checkpoint
- record_evidence 的 source 只接受已存在的 P### / X###（dead-end 不能当出处，M1）
- transition_hypothesis 合并而非覆盖 evidence 列表（spike 版会丢掉已有证据）

环境变量：AR_ROOT（State 根）、AR_TASK、AR_TASK_DIR、AR_TOOLSET（逗号分隔）。
"""
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "autoresearch"

from autoresearch import (candidates, config, discussion, incubation, library, modes, papers,  # noqa: E402
                          reviews, schema)
from autoresearch.store import Store, now, today  # noqa: E402

CFG = config.load()
STORE = Store(CFG.state, CFG.lock)
TASK = os.environ.get("AR_TASK") or None
TASK_DIR = Path(os.environ["AR_TASK_DIR"]) if os.environ.get("AR_TASK_DIR") else None
TOOL_LOG = TASK_DIR / "tools.jsonl" if TASK_DIR else None
TOOLSET = {t.strip() for t in os.environ.get("AR_TOOLSET", "").split(",") if t.strip()}
PROFILE = os.environ.get("AR_PROFILE", "")
LIB = library.Library(CFG.library)


def log(entry):
    if not TOOL_LOG:
        return
    entry["ts"] = now()
    with open(TOOL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- tools

def tokens(text):
    """中英混合分词：拉丁词 + CJK 字符 bigram（只用于排序，不用于过滤）。"""
    text = text.lower()
    out = set(re.findall(r"[a-z0-9]{2,}", text))
    for run in re.findall(r"[一-鿿]+", text):
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _idea_lines():
    """被否决的想法（与 dead-end 同等对待，§5.14）与本 session 已记下的想法（只为不重复）。"""
    out = []
    rej = incubation.rejected_ideas(STORE)
    if rej:
        out.append(f"\n\n## 被研究者否决的想法（{len(rej)} 条，与已关闭方向同等对待）")
        for m, b in rej:
            out.append(f"- [{m['id']}] {incubation._first(incubation._section(b, '陈述'), 300)}\n"
                       f"  否决理由：{incubation._first(incubation._section(b, '为什么不要'), 400)}")
    did = incubation.session(STORE)
    mine = [(m, b) for m, b in incubation.session_ideas(STORE, did) if m.get("status") != "rejected"] if did else []
    if mine:
        out.append(f"\n\n## 本次自演进已经记下的想法（{len(mine)} 条；不是事实，列出只为不换说法重提）")
        for m, b in mine:
            out.append(f"- [{m['id']}] {incubation._first(incubation._section(b, '陈述'), 200)}")
    return "\n".join(out)


def t_check_dead_ends(description=""):
    """列出已关闭的方向。工具只负责“保证可见”，相关性判断交给 agent（§0.4）。"""
    items = STORE.list("dead-end")
    extra = _idea_lines()
    log({"tool": "check_dead_ends", "ok": True, "query": description[:120], "n": len(items)})
    if not items:
        return "本项目还没有记录任何 dead-end。" + extra
    q = tokens(description)
    ranked = []
    for meta, body in items:
        score = len(q & tokens(body)) / len(q) if q else 0.0
        ranked.append((score, meta, body.strip()))
    ranked.sort(key=lambda x: -x[0])
    out = [f"本项目共有 {len(ranked)} 条已关闭方向。**由你判断**你的思路是否与它们重合——"
           "工具只负责把它们摆出来，不替你做相关性判断。\n"]
    for score, meta, text in ranked:
        full = score >= 0.2 or len(ranked) <= 8
        shown = text[:800] if full else (text.splitlines()[0] if text else "")
        out.append(f"### [{meta['id']}] status={meta.get('status')} "
                   f"closed_by={meta.get('closed_by')} 词汇相关度 {score:.2f}\n{shown}")
    return "\n\n".join(out) + extra


def t_search_papers(query="", max_results=10):
    """arXiv 检索。结果标出已登记的论文，避免重复登记。"""
    if not query.strip():
        raise ValueError("query 不能为空。")
    try:
        hits = library.arxiv_search(query, max_results=max_results)
    except library.NetError as e:
        raise ValueError(f"检索失败：{e}。稍后重试，或换个查询。") from e
    log({"tool": "search_papers", "ok": True, "query": query[:200], "n": len(hits)})
    if not hits:
        return f"arXiv 检索「{query}」没有结果。换个说法，或用更宽的词。"
    out = [f"arXiv 检索「{query}」前 {len(hits)} 条（按相关度）。登记要读的用 register_paper(ref=arXiv id)。"]
    for h in hits:
        pid = papers.find_paper(STORE, arxiv=h["arxiv"])
        tag = f"【已登记 {pid}】" if pid else ""
        au = ", ".join(h["authors"][:3]) + (" 等" if len(h["authors"]) > 3 else "")
        out.append(f"- {tag}arXiv:{h['arxiv']}（{h['year']}）{h['title']} —— {au}\n  "
                   f"{h['abstract'][:420]}{'…' if len(h['abstract']) > 420 else ''}")
    return "\n".join(out)


def t_register_paper(ref="", why="", for_targets=None):
    pid, new, msg = papers.register(STORE, LIB, ref, why=why, for_targets=for_targets,
                                    found_via=f"任务 {TASK}" if TASK else "", task=TASK)
    log({"tool": "register_paper", "ok": True, "id": pid, "ref": ref, "new": new})
    m, _ = STORE.read_obj(pid)
    return (f"{'已登记' if new else '已存在'} {pid}：{m.get('title')}（{m.get('year') or '?'}）。"
            f"{msg}。读它之前先调 open_paper({pid})。")


def t_open_paper(paper_id=""):
    text = papers.open_paper(STORE, LIB, paper_id, task=TASK)
    log({"tool": "open_paper", "ok": True, "id": paper_id})
    return text


def t_record_evidence(target_id="", stance="", source="", note="", quote="", locator=None,
                      strength="moderate", hypothesis_id=""):
    target_id = target_id or hypothesis_id
    eid, basis, moved = papers.record_evidence(
        STORE, LIB, target_id, stance, source, note, quote=quote, locator=locator,
        strength=strength, batch=modes.batch(STORE), task=TASK)
    log({"tool": "record_evidence", "ok": True, "id": eid, "target": target_id,
         "stance": stance, "source": source, "basis": basis})
    return (f"已记录证据 {eid}（{target_id} / {stance} / {strength}，出处 {source}，"
            f"依据{'全文' if basis == 'fulltext' else '摘要'}），引文已对照原文核实，"
            f"并已写入 {target_id} 的 evidence 列表{moved}。")


def t_transition_hypothesis(hypothesis_id="", new_status="", evidence_ids=None, rationale=""):
    """§0.4 的核心：无证据的状态迁移必须被硬拒绝。"""
    old, new_reviews = papers.transition_hypothesis(STORE, hypothesis_id, new_status,
                                                    evidence_ids or [], rationale, task=TASK)
    log({"tool": "transition_hypothesis", "ok": True, "id": hypothesis_id,
         "from": old, "to": new_status, "evidence": evidence_ids, "reviews": new_reviews})
    msg = f"{hypothesis_id}: {old} → {new_status}，已记录依据 {', '.join(papers._as_list(evidence_ids))}。"
    if new_reviews:
        msg += f"相关对象已列入待重新审视：{', '.join(new_reviews)}（由人判断，不要自行改写它们）。"
    return msg


def t_examine_assumption(assumption_id="", verdict="", evidence_ids=None, note=""):
    old = papers.examine_assumption(STORE, assumption_id, verdict, evidence_ids or [], note,
                                    task=TASK)
    log({"tool": "examine_assumption", "ok": True, "id": assumption_id, "verdict": verdict})
    return f"{assumption_id}: {old} → examined（{verdict}）。"


def t_invalidate_assumption(assumption_id="", evidence_ids=None, rationale=""):
    evidence_ids = evidence_ids or []
    if any(not e.startswith("E") for e in evidence_ids):
        raise ValueError("agent 推翻前提只能依据 evidence（E###）。")
    new_reviews = papers.invalidate_by_evidence(STORE, assumption_id, evidence_ids, rationale,
                                                task=TASK)
    log({"tool": "invalidate_assumption", "ok": True, "id": assumption_id, "reviews": new_reviews})
    return (f"{assumption_id} 已标记为被推翻。依赖它的对象已列入待重新审视："
            f"{', '.join(new_reviews) or '（无）'}。由人判断，不要自行改写它们。")


def t_request_paper(paper_id="", why="", blocking=True):
    rid, new = papers.request_paper(STORE, paper_id, why, blocking=bool(blocking), task=TASK)
    log({"tool": "request_paper", "ok": True, "id": rid, "paper": paper_id,
         "blocking": bool(blocking), "new": new})
    if blocking:
        return (f"已登记全文请求 {rid}（{paper_id}），研究者会经清华认证取回后上传。"
                "**本任务将挂起等待全文**：现在用 checkpoint 记下做到哪、拿到全文后要做什么，然后结束本任务。"
                "上传后任务会带着你的 checkpoint 重新开始。")
    return f"已登记全文请求 {rid}（{paper_id}）。本任务不挂起，继续按摘要级完成。"


def t_annotate_grounding(target_id="", verdict="", refs=None, note="", hidden_premises=None,
                        silent_challenges=None):
    gid = papers.annotate_grounding(STORE, target_id, verdict, refs or [], note, task=TASK,
                                    hidden_premises=hidden_premises,
                                    silent_challenges=silent_challenges)
    log({"tool": "annotate_grounding", "ok": True, "id": gid, "target": target_id,
         "verdict": verdict})
    return f"已记录接地结论 {gid}（{target_id}：{verdict}）。被核查对象保持原样，由研究者判断。"


def _task():
    p = TASK_DIR / "task.json" if TASK_DIR else None
    return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else {}


def _called(tool):
    if not TOOL_LOG or not TOOL_LOG.exists():
        return False
    for line in TOOL_LOG.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("tool") == tool and e.get("ok"):
            return True
    return False


def t_record_idea(statement="", falsifier="", new_premises=None, reasoning="", challenges=None,
                  challenge_notes="", builds_on=None, relates_to=None, relation_note=""):
    iid, aids = incubation.record_idea(
        STORE, task=TASK, chain=_task(), statement=statement, falsifier=falsifier,
        new_premises=new_premises, reasoning=reasoning, challenges=challenges,
        challenge_notes=challenge_notes, builds_on=builds_on, relates_to=relates_to,
        relation_note=relation_note, called_dead_ends=_called("check_dead_ends"))
    log({"tool": "record_idea", "ok": True, "id": iid, "premises": aids})
    return (f"已登记 {iid}（新前提 {len(aids)} 条{'：' + ', '.join(aids) if aids else ''}）。"
            "它会由另一个开着检索的任务做外部核查，核查只标注、不改写它。")


def t_log_decision(what="", why="", kind="research", refs=None):
    if not what.strip() or not why.strip():
        raise ValueError("what 与 why 都不能为空。")
    kinds = schema.KINDS["decision"].enums["kind"]
    if kind not in kinds:
        raise ValueError(f"kind 必须是 {sorted(kinds)} 之一。")
    refs = [r for r in (refs or []) if r]
    missing = [r for r in refs if not STORE.exists(r)]
    if missing:
        raise ValueError(f"refs 中 {missing} 不存在。")
    with STORE.tx("decision: 记录决策", actor="agent", task=TASK) as tx:
        did = tx.new_id("decision")
        tx.write_obj(did, {"id": did, "type": "decision", "kind": kind, "refs": refs,
                           "task": TASK, "created": today()},
                     f"## 做了什么\n\n{what.strip()}\n\n## 为什么\n\n{why.strip()}\n")
        tx.note = did
    log({"tool": "log_decision", "ok": True, "id": did})
    return f"已记录决策 {did}。"


def t_propose_candidate(kind="", statement="", rationale="", origin="", source="",
                        turns=None, origin_note="", relied_on_by=None, falsifier="",
                        validation="", confidence="", maturity="", importance="",
                        relates_to=None, basis=None, firmness="", change_mind="",
                        informs=None, derived_from="", promoted_from=""):
    if PROFILE == "judge":        # 评判类任务：候选出自本任务，origin 固定 ai（§5.6）
        source, origin, turns, origin_note = TASK, "ai", [], ""
    cid = candidates.propose(
        STORE, kind=kind, statement=statement, rationale=rationale, origin=origin,
        source=source, turns=turns, origin_note=origin_note, relates_to=relates_to,
        task=TASK, actor="agent", relied_on_by=relied_on_by, falsifier=falsifier,
        validation=validation, confidence=confidence, maturity=maturity,
        importance=importance, basis=basis, firmness=firmness, change_mind=change_mind,
        informs=informs, derived_from=derived_from, promoted_from=promoted_from)
    log({"tool": "propose_candidate", "ok": True, "id": cid, "kind": kind, "origin": origin})
    return (f"已提交候选 {cid}（{kind}）。它在人确认前**不是**正式 State 对象；"
            "不要再为同一内容重复提交。")


def t_update_discussion_summary(discussion_id="", summary="", covers_through=0):
    discussion.write_summary(STORE, discussion_id, summary, int(covers_through), task=TASK)
    log({"tool": "update_discussion_summary", "ok": True, "id": discussion_id,
         "covers_through": int(covers_through)})
    return f"已更新 {discussion_id} 的摘要，覆盖至第 {int(covers_through)} 轮。"


def t_note_prep_request(topic="", turn=0):
    """研究者在讨论里回答了“今晚查什么”：记下原话，夜间预习据此选题（M2.0）。"""
    if not topic.strip():
        raise ValueError("topic 不能为空：写研究者的原话或忠实转述。")
    rec = {"text": topic.strip(), "ts": now(), "source": f"讨论第 {int(turn)} 轮" if turn else "讨论中",
           "task": TASK}
    (CFG.run / "prep_request.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    log({"tool": "note_prep_request", "ok": True, "topic": topic[:200]})
    return "已记下。研究者离开后窗口空闲时，夜间预习会先查这个，次日在前端说明。"


def t_checkpoint(note=""):
    """进度笔记：交接记录的原料（§5.2）。写入任务台账，不进 State。"""
    if not note.strip():
        raise ValueError("note 不能为空。")
    if not TASK_DIR:
        raise ValueError("当前不在任务上下文中。")
    with open(TASK_DIR / "checkpoints.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "note": note.strip()}, ensure_ascii=False) + "\n")
    log({"tool": "checkpoint", "ok": True})
    return "已记录进度。若本任务被中途切断，下一班会从这条笔记接上。"


S, A, AI, I = "string", "array", "array_int", "integer"
TOOLS = [
    ("check_dead_ends", t_check_dead_ends,
     "列出本项目全部已关闭的研究方向，避免重复已经失败过的尝试。展开任何新方向前应先调用。",
     {"description": (S, "要检查的思路描述（只用于排序，不会过滤掉任何条目）", True)}),
    ("search_papers", t_search_papers,
     "在 arXiv 检索论文（标题、摘要、年份）。结果会标出已登记的论文。"
     "支持 arXiv 查询语法（ti: abs: au: cat:），否则按关键词 AND 检索。",
     {"query": (S, "检索词，英文效果最好", True),
      "max_results": (I, "返回条数，默认 10，最多 30", False)}),
    ("register_paper", t_register_paper,
     "登记一篇真实论文：工具会去 arXiv / Crossref 核实元数据，查不到就拒绝；arXiv 论文同时取回全文。"
     "只能登记能核实的论文——不要凭记忆写编号。",
     {"ref": (S, "arXiv id（如 2406.09246）、DOI 或 URL", True),
      "why": (S, "为什么要读它：它和哪条假设 / 前提有关", True),
      "for_targets": (A, "为哪些 H### / A### 而读", False)}),
    ("open_paper", t_open_paper,
     "打开一篇已登记的论文：返回全文文件路径与目录。读完用 Read 读这个文件。",
     {"paper_id": (S, "P###", True)}),
    ("record_evidence", t_record_evidence,
     "为某条假设或前提记录一条证据。出处必须是已登记的论文（或实验）；"
     "quote 必须是 locator 所指段落里的逐字原文，工具会核对，对不上就拒绝。",
     {"target_id": (S, "目标：假设 H###、前提 A###，或（接地时）想法 I###", True),
      "stance": (S, "support / contradict / neutral", True),
      "source": (S, "出处：P### 或 X###（不能是 dead-end）", True),
      "locator": (A, "段落锚点列表，取自全文每段开头的方括号，如 [\"s4.1-p2\"]、[\"tab2\"]、[\"abstract-p1\"]", True),
      "quote": (S, "所引段落里的逐字原文摘录（英文原文，一两句，≤400 字符）", True),
      "note": (S, "这条证据具体说明了什么、为什么是这个立场（中文）", True),
      "strength": (S, "weak / moderate / strong，默认 moderate；只读了摘要不能是 strong", False)}),
    ("transition_hypothesis", t_transition_hypothesis,
     "变更某条假设的状态。必须附带关于它的 evidence id；迁到 supported / refuted 至少要一条读过全文的"
     "对应立场证据。abandoned 只有研究者能做。",
     {"hypothesis_id": (S, "目标假设 id", True),
      "new_status": (S, "investigating / supported / refuted / inconclusive", True),
      "evidence_ids": (A, "支撑本次迁移的 evidence id 列表", True),
      "rationale": (S, "这些证据为何支持该迁移，不能为空", True)}),
    ("examine_assumption", t_examine_assumption,
     "给出前提审视的结论：holds（经核查站得住）或 fragile（站得住但脆弱）。必须附关于该前提的证据。"
     "若证据表明它不成立，改用 invalidate_assumption。",
     {"assumption_id": (S, "A###", True), "verdict": (S, "holds / fragile", True),
      "evidence_ids": (A, "target 为该前提的 evidence id", True),
      "note": (S, "审视了什么、为什么得出这个结论", True)}),
    ("invalidate_assumption", t_invalidate_assumption,
     "推翻一条前提。必须附至少一条 target 为它、stance 为 contradict 的证据；"
     "所有依赖它的对象会自动列入待重新审视，由人判断。",
     {"assumption_id": (S, "目标前提 id，如 A001", True),
      "evidence_ids": (A, "证明它不成立的 evidence id 列表", True),
      "rationale": (S, "为什么这些证据推翻了它", True)}),
    ("request_paper", t_request_paper,
     "某篇已登记论文的全文拿不到、但判断必须读全文时，请研究者经清华认证取回。",
     {"paper_id": (S, "P###（先用 register_paper 按 DOI 登记）", True),
      "why": (S, "为什么必须读全文：它可能改变哪条判断", True),
      "blocking": ("boolean", "true = 本任务挂起等全文；false = 不等，按摘要级做完", False)}),
    ("annotate_grounding", t_annotate_grounding,
     "对抗性接地的结论：这个想法有人做过吗、有没有直接反驳。只标注，不改被核查的对象。",
     {"target_id": (S, "被核查对象：H### / A### / C### / I###", True),
      "verdict": (S, "novel（没找到先例）/ prior_work（已有人做过）/ contradicted（被直接反驳）/ mixed", True),
      "refs": (A, "支撑结论的 P### / E###（novel 以外必填）", False),
      "note": (S, "核查了什么、找到了什么", True),
      "hidden_premises": (I, "（对 Idea）它依赖但没有登记的前提有几条", False),
      "silent_challenges": (I, "（对 Idea）它与基本盘矛盾但没有登记为挑战的有几处", False)}),
    ("record_idea", t_record_idea,
     "登记一条推演出的想法（Idea）。工具会拒绝：说不出何时是错的、还没调用过 check_dead_ends、"
     "没有显式给出新前提清单、挑战的对象不在本轮基本盘里。不收任何自评分数。",
     {"statement": (S, "直觉层面的断言（有内容，不要工程细节）", True),
      "falsifier": (S, "什么情况下它是错的：什么观察 / 结果会说明它不成立", True),
      "new_premises": (A, "推演中引入的、基本盘里没有的前提，逐条一句话；没有就传 []", True),
      "reasoning": (S, "从基本盘哪几条出发、怎么走到这里（几句话）", True),
      "challenges": (A, "它挑战了基本盘里的哪些对象（A/H/D/U/E/IN 的 id），没有就不传", False),
      "challenge_notes": (S, "有挑战时必填：挑战的是什么、为什么", False),
      "builds_on": (A, "它建立在哪些已有对象上（含理解 IN###）", False),
      "relates_to": (A, "与哪些已有假设 / 前提 / 问题 / 不确定性 / 想法相关", False),
      "relation_note": (S, "与它们是什么关系：细化、对立、新的解释……", False)}),
    ("log_decision", t_log_decision,
     "记录一条研究决策及其理由。",
     {"what": (S, "做了什么决定", True), "why": (S, "为什么", True),
      "kind": (S, "research / curation / mode / handoff，默认 research", False),
      "refs": (A, "相关对象 id", False)}),
    ("propose_candidate", t_propose_candidate,
     "把讨论中出现的研究内容提交到候选区，等人确认。按**当前角色**分类："
     "正在被依赖（支撑其他推理或用来剪枝）而未安排验证的 → assumption（必须给 relied_on_by）；"
     "讨论中已安排/约定了验证方式的 → hypothesis（必须给 falsifier 与 validation）。"
     "研究中形成的看法、直觉 → insight（不要求可证伪，但必须给 basis 与 firmness）。"
     "闲聊、已被否定的猜测、与已有对象重复的内容不要提交。",
     {"kind": (S, "assumption / hypothesis / question / uncertainty / insight", True),
      "statement": (S, "一句话陈述（可附简短展开）", True),
      "rationale": (S, "为什么这是研究内容、为什么归为这一类、与已有对象的关系", True),
      "origin": (S, "human / ai / unclear：按讨论记录里谁先提出。拿不准或与已有记录冲突就写 unclear（验证任务不用填）", False),
      "origin_note": (S, "origin=unclear 时必填：冲突在哪", False),
      "source": (S, "讨论 id，如 DS001（验证任务不用填）", False),
      "turns": (AI, "依据的讨论轮次号列表（验证任务不用填）", False),
      "relied_on_by": (A, "assumption 必填：它支撑着哪些已存在对象的 id（如 Q001、H002）", False),
      "falsifier": (S, "hypothesis 必填：什么结果会反驳它", False),
      "validation": (S, "hypothesis 必填：讨论中安排的验证方式", False),
      "confidence": (S, "hypothesis 可选：low / medium / high", False),
      "maturity": (S, "question 必填：vague / scoped / formalized", False),
      "importance": (S, "uncertainty 必填：low / medium / high", False),
      "relates_to": (A, "相关的已有对象 id（细化、对立、重叠）", False),
      "basis": (A, "insight 必填：这条理解的根基——State 里真实存在的对象 id（DS###、E###、H###、P###……）；验证任务提出的候选必填，指向 E### / P###", False),
      "firmness": (S, "insight 必填：hunch（直觉）/ working（工作理解）/ settled（稳固理解）", False),
      "change_mind": (S, "insight 可选：什么会让这个看法改变", False),
      "informs": (A, "insight 可选：它影响了哪些对象的判断", False),
      "derived_from": (S, "assumption 可选：若这条前提来自“凭某条理解排除方向”，写那条理解的 id（IN###）", False),
      "promoted_from": (S, "hypothesis 可选：由哪条前提（A###）提升而来——前提审视发现它可证伪、值得安排验证时", False)}),
    ("update_discussion_summary", t_update_discussion_summary,
     "更新某个讨论的滚动摘要。摘要必须覆盖从第 1 轮到 covers_through 的全部要点"
     "（在旧摘要基础上合并，而不是只写新增部分），因为之后的 session 只会看到摘要与其后的原文。",
     {"discussion_id": (S, "讨论 id，如 DS001", True),
      "summary": (S, "完整的滚动摘要（markdown）", True),
      "covers_through": (I, "摘要覆盖到的最后一轮轮次号", True)}),
    ("note_prep_request", t_note_prep_request,
     "研究者明确回答了“今晚（他不在时）去查什么”时，记下他的要求。只记研究者自己说的，不要替他拟题。",
     {"topic": (S, "研究者要你去查的内容（原话或忠实转述）", True),
      "turn": (I, "研究者说这话的轮次", False)}),
    ("checkpoint", t_checkpoint,
     "记录本任务的进度笔记（做到哪、下一步是什么）。任务可能随时被切断，"
     "每完成一个有意义的步骤就记一条，下一班从这里接上。",
     {"note": (S, "进度笔记", True)}),
]


def schema_of(params):
    props = {}
    for name, (typ, desc, _) in params.items():
        if typ == A:
            t = {"type": "array", "items": {"type": "string"}}
        elif typ == AI:
            t = {"type": "array", "items": {"type": "integer"}}
        elif typ == "boolean":
            t = {"type": "boolean"}
        else:
            t = {"type": typ}
        props[name] = dict(t, description=desc)
    return {"type": "object", "properties": props,
            "required": [n for n, (_, _, req) in params.items() if req]}


def active_tools():
    """fail-closed（§5.9）：没说给什么就只给 checkpoint；"*" 显式表示全部（测试与调试用）。"""
    if "*" in TOOLSET:
        return list(TOOLS)
    return [t for t in TOOLS if t[0] in (TOOLSET or {"checkpoint"})]


def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"protocolVersion": ver, "capabilities": {"tools": {}},
                "serverInfo": {"name": "state", "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": schema_of(p)}
                          for n, _, d, p in active_tools()]}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        fn = {n: f for n, f, _, _ in active_tools()}.get(name)
        if fn is None:
            return {"content": [{"type": "text", "text": f"工具 {name} 在本任务中不可用"}],
                    "isError": True}
        try:
            return {"content": [{"type": "text", "text": fn(**args)}], "isError": False}
        except Exception as e:
            log({"tool": name, "ok": False, "error": str(e), "args": args})
            return {"content": [{"type": "text", "text": f"错误：{e}"}], "isError": True}
    if method == "ping":
        return {}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") is None:           # notification：不回复
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as e:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": f"Method not found: {e}"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
