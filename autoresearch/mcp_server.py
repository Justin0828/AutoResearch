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

from autoresearch import candidates, config, discussion, schema  # noqa: E402
from autoresearch.store import Store, now, today  # noqa: E402

CFG = config.load()
STORE = Store(CFG.state, CFG.lock)
TASK = os.environ.get("AR_TASK") or None
TASK_DIR = Path(os.environ["AR_TASK_DIR"]) if os.environ.get("AR_TASK_DIR") else None
TOOL_LOG = TASK_DIR / "tools.jsonl" if TASK_DIR else None
TOOLSET = {t.strip() for t in os.environ.get("AR_TOOLSET", "").split(",") if t.strip()}


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


def t_check_dead_ends(description=""):
    """列出已关闭的方向。工具只负责“保证可见”，相关性判断交给 agent（§0.4）。"""
    items = STORE.list("dead-end")
    log({"tool": "check_dead_ends", "ok": True, "query": description[:120], "n": len(items)})
    if not items:
        return "本项目还没有记录任何 dead-end。"
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
    return "\n\n".join(out)


def t_record_evidence(hypothesis_id="", stance="", source="", note="", strength="moderate"):
    k = schema.KINDS["evidence"]
    if not STORE.exists(hypothesis_id) or not hypothesis_id.startswith("H"):
        raise ValueError(f"hypothesis '{hypothesis_id}' 不存在。先用文件列表确认 id。")
    if stance not in k.enums["stance"]:
        raise ValueError(f"stance 必须是 {sorted(k.enums['stance'])} 之一，收到 '{stance}'。")
    if strength not in k.enums["strength"]:
        raise ValueError(f"strength 必须是 {sorted(k.enums['strength'])} 之一，收到 '{strength}'。")
    if schema.split_id(source)[0] not in ("P", "X") or not (
            STORE.exists(source) or (CFG.state / "experiments" / f"{source}.md").exists()):
        raise ValueError(f"source '{source}' 无效：证据出处只能是已存在的 paper（P###）或 "
                         "experiment（X###）。dead-end 不是出处——它引用证据，而不是反过来。")
    if not note.strip():
        raise ValueError("note 不能为空：必须说明这条证据具体说了什么。")

    with STORE.tx("evidence: 记录证据", actor="agent", task=TASK) as tx:
        eid = tx.new_id("evidence")
        tx.write_obj(eid, {"id": eid, "type": "evidence", "hypothesis": hypothesis_id,
                           "stance": stance, "strength": strength, "source": source,
                           "created": today()}, note.strip() + "\n")
        # 回写假设的 evidence 列表：簿记归工具（§0.4）
        hmeta, hbody = STORE.read_obj(hypothesis_id)
        ev = list(hmeta.get("evidence") or [])
        if eid not in ev:
            ev.append(eid)
        hmeta["evidence"] = ev
        tx.write_obj(hypothesis_id, hmeta, hbody)
        tx.note = f"{eid} → {hypothesis_id}"
    log({"tool": "record_evidence", "ok": True, "id": eid,
         "hypothesis": hypothesis_id, "stance": stance, "source": source})
    return (f"已记录证据 {eid}（{hypothesis_id} / {stance} / {strength}，出处 {source}），"
            f"并已自动写入 {hypothesis_id} 的 evidence 列表，你不需要手动编辑。")


def t_transition_hypothesis(hypothesis_id="", new_status="", evidence_ids=None, rationale=""):
    """§0.4 的核心：无证据的状态迁移必须被硬拒绝。"""
    evidence_ids = evidence_ids or []
    if isinstance(evidence_ids, str):
        evidence_ids = [x.strip() for x in evidence_ids.split(",") if x.strip()]
    statuses = schema.KINDS["hypothesis"].enums["status"]
    if not hypothesis_id.startswith("H") or not STORE.exists(hypothesis_id):
        raise ValueError(f"hypothesis '{hypothesis_id}' 不存在。")
    if new_status not in statuses:
        raise ValueError(f"new_status 必须是 {sorted(statuses)} 之一，收到 '{new_status}'。")
    if not evidence_ids:
        raise ValueError("拒绝：状态迁移必须附带至少一条 evidence id。"
                         "先用 record_evidence 记录证据，再用返回的 id 重试。")
    missing = [e for e in evidence_ids if not (e.startswith("E") and STORE.exists(e))]
    if missing:
        raise ValueError(f"拒绝：evidence {missing} 不存在。只能引用 record_evidence 返回的 id。")
    if not rationale.strip():
        raise ValueError("rationale 不能为空：必须说明这些证据为何支持该状态迁移。")

    with STORE.tx("hypothesis: 状态迁移", actor="agent", task=TASK) as tx:
        meta, body = STORE.read_obj(hypothesis_id)
        old = meta.get("status")
        meta["status"] = new_status
        meta["evidence"] = list(dict.fromkeys(list(meta.get("evidence") or []) + evidence_ids))
        body = (f"{body.rstrip()}\n\n## 状态变更 {today()}\n\n"
                f"{old} → {new_status}，依据 {', '.join(evidence_ids)}。\n\n{rationale.strip()}\n")
        tx.write_obj(hypothesis_id, meta, body)
        tx.note = f"{hypothesis_id} {old} → {new_status}"
    log({"tool": "transition_hypothesis", "ok": True, "id": hypothesis_id,
         "from": old, "to": new_status, "evidence": evidence_ids})
    return f"{hypothesis_id}: {old} → {new_status}，已记录依据 {', '.join(evidence_ids)}。"


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
                        relates_to=None):
    cid = candidates.propose(
        STORE, kind=kind, statement=statement, rationale=rationale, origin=origin,
        source=source, turns=turns, origin_note=origin_note, relates_to=relates_to,
        task=TASK, actor="agent", relied_on_by=relied_on_by, falsifier=falsifier,
        validation=validation, confidence=confidence, maturity=maturity,
        importance=importance)
    log({"tool": "propose_candidate", "ok": True, "id": cid, "kind": kind, "origin": origin})
    return (f"已提交候选 {cid}（{kind}）。它在人确认前**不是**正式 State 对象；"
            "不要再为同一内容重复提交。")


def t_update_discussion_summary(discussion_id="", summary="", covers_through=0):
    discussion.write_summary(STORE, discussion_id, summary, int(covers_through), task=TASK)
    log({"tool": "update_discussion_summary", "ok": True, "id": discussion_id,
         "covers_through": int(covers_through)})
    return f"已更新 {discussion_id} 的摘要，覆盖至第 {int(covers_through)} 轮。"


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
    ("record_evidence", t_record_evidence,
     "为某条 hypothesis 记录一条证据。出处必须是已存在的 paper 或 experiment。",
     {"hypothesis_id": (S, "目标假设 id，如 H001", True),
      "stance": (S, "support / contradict / neutral", True),
      "source": (S, "出处：P### 或 X###（不能是 dead-end）", True),
      "note": (S, "这条证据具体说了什么，不能为空", True),
      "strength": (S, "weak / moderate / strong，默认 moderate", False)}),
    ("transition_hypothesis", t_transition_hypothesis,
     "变更某条 hypothesis 的状态。必须附带至少一条已存在的 evidence id，否则会被拒绝。",
     {"hypothesis_id": (S, "目标假设 id", True),
      "new_status": (S, "proposed / investigating / supported / refuted / inconclusive / abandoned", True),
      "evidence_ids": (A, "支撑本次迁移的 evidence id 列表", True),
      "rationale": (S, "这些证据为何支持该迁移，不能为空", True)}),
    ("log_decision", t_log_decision,
     "记录一条研究决策及其理由。",
     {"what": (S, "做了什么决定", True), "why": (S, "为什么", True),
      "kind": (S, "research / curation / mode / handoff，默认 research", False),
      "refs": (A, "相关对象 id", False)}),
    ("propose_candidate", t_propose_candidate,
     "把讨论中出现的研究内容提交到候选区，等人确认。按**当前角色**分类："
     "正在被依赖（支撑其他推理或用来剪枝）而未安排验证的 → assumption（必须给 relied_on_by）；"
     "讨论中已安排/约定了验证方式的 → hypothesis（必须给 falsifier 与 validation）。"
     "闲聊、已被否定的猜测、与已有对象重复的内容不要提交。",
     {"kind": (S, "assumption / hypothesis / question / uncertainty", True),
      "statement": (S, "一句话陈述（可附简短展开）", True),
      "rationale": (S, "为什么这是研究内容、为什么归为这一类、与已有对象的关系", True),
      "origin": (S, "human / ai / unclear：按讨论记录里谁先提出。拿不准或与已有记录冲突就写 unclear", True),
      "origin_note": (S, "origin=unclear 时必填：冲突在哪", False),
      "source": (S, "讨论 id，如 DS001", True),
      "turns": (AI, "依据的讨论轮次号列表", True),
      "relied_on_by": (A, "assumption 必填：它支撑着哪些已存在对象的 id（如 Q001、H002）", False),
      "falsifier": (S, "hypothesis 必填：什么结果会反驳它", False),
      "validation": (S, "hypothesis 必填：讨论中安排的验证方式", False),
      "confidence": (S, "hypothesis 可选：low / medium / high", False),
      "maturity": (S, "question 必填：vague / scoped / formalized", False),
      "importance": (S, "uncertainty 必填：low / medium / high", False),
      "relates_to": (A, "相关的已有对象 id（细化、对立、重叠）", False)}),
    ("update_discussion_summary", t_update_discussion_summary,
     "更新某个讨论的滚动摘要。摘要必须覆盖从第 1 轮到 covers_through 的全部要点"
     "（在旧摘要基础上合并，而不是只写新增部分），因为之后的 session 只会看到摘要与其后的原文。",
     {"discussion_id": (S, "讨论 id，如 DS001", True),
      "summary": (S, "完整的滚动摘要（markdown）", True),
      "covers_through": (I, "摘要覆盖到的最后一轮轮次号", True)}),
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
        else:
            t = {"type": typ}
        props[name] = dict(t, description=desc)
    return {"type": "object", "properties": props,
            "required": [n for n, (_, _, req) in params.items() if req]}


def active_tools():
    return [t for t in TOOLS if not TOOLSET or t[0] in TOOLSET]


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
