#!/usr/bin/env python3
"""Phase 0 spike: 最小 Research State MCP server（stdio JSON-RPC，零依赖）。

验证 DESIGN.md §0.4：需要校验的状态迁移走 MCP tool，agent 没有绕过的路径。
核心是 transition_hypothesis 必须在证据不足时硬拒绝。

State 仓库路径由 STATE_ROOT 给出，调用日志写到 TOOL_LOG。
"""
import json
import os
import re
import sys
import datetime

STATE = os.environ["STATE_ROOT"]
TOOL_LOG = os.environ.get("TOOL_LOG")

VALID_STANCE = {"support", "contradict", "neutral"}
VALID_STRENGTH = {"weak", "moderate", "strong"}
VALID_STATUS = {"proposed", "investigating", "supported",
                "refuted", "inconclusive", "abandoned"}


def log(entry):
    if not TOOL_LOG:
        return
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(TOOL_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def split_front(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else (None, text)


def read_front(path):
    fm, _ = split_front(open(path, encoding="utf-8").read())
    out = {}
    for line in (fm or "").splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def next_id(sub, prefix):
    d = os.path.join(STATE, sub)
    os.makedirs(d, exist_ok=True)
    n = [int(m.group(1)) for f in os.listdir(d)
         if (m := re.match(rf"{prefix}(\d+)\.md$", f))]
    return f"{prefix}{max(n) + 1 if n else 1:03d}"


def exists(sub, ident):
    return bool(ident) and os.path.isfile(os.path.join(STATE, sub, f"{ident}.md"))


def today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- tools

def tokens(text):
    """中英混合分词：拉丁词 + CJK 字符 bigram。

    不能用 \\W+ 切中文——整段中文会变成单个 token，overlap 恒为空，
    check_dead_ends 会静默返回“没找到”。那比没有这个工具更危险。
    """
    text = text.lower()
    out = set(re.findall(r"[a-z0-9]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def t_check_dead_ends(description=""):
    """列出已关闭的方向。

    设计决定：工具只负责“保证可见”，相关性判断交给调用它的 agent。
    早先版本用词汇重叠过滤，结果是英文查询打不中中文 dead-end——而
    State 里论文是英文、讨论是中文，这种混合必然发生。静默返回“没找到”
    比没有这个工具更危险：agent 会理直气壮地重走死路。
    dead-end 现实量级是几十条，全列出来的成本可以忽略。
    """
    d = os.path.join(STATE, "dead-ends")
    files = sorted(os.listdir(d)) if os.path.isdir(d) else []
    if not files:
        log({"tool": "check_dead_ends", "ok": True, "query": description[:120], "n": 0})
        return "本项目还没有记录任何 dead-end。"

    log({"tool": "check_dead_ends", "ok": True, "query": description[:120]})
    q = tokens(description)
    items = []
    for f in files:
        body = open(os.path.join(d, f), encoding="utf-8").read()
        _, text = split_front(body)
        score = len(q & tokens(body)) / len(q) if q else 0.0
        items.append((score, f[:-3], text.strip()))
    items.sort(key=lambda x: -x[0])

    out = [f"本项目共有 {len(items)} 条已关闭方向。**由你判断**你的思路是否与它们重合——"
           "工具只负责把它们摆出来，不替你做相关性判断。\n"]
    for score, ident, text in items:
        full = score >= 0.2 or len(items) <= 8
        shown = text[:600] if full else (text.splitlines()[0] if text else "")
        out.append(f"### [{ident}] 词汇相关度 {score:.2f}\n{shown}")
    return "\n\n".join(out)


def t_record_evidence(hypothesis_id="", stance="", source="", note="", strength="moderate"):
    if not exists("hypotheses", hypothesis_id):
        raise ValueError(f"hypothesis '{hypothesis_id}' 不存在。先用文件列表确认 id。")
    if stance not in VALID_STANCE:
        raise ValueError(f"stance 必须是 {sorted(VALID_STANCE)} 之一，收到 '{stance}'。")
    if strength not in VALID_STRENGTH:
        raise ValueError(f"strength 必须是 {sorted(VALID_STRENGTH)} 之一，收到 '{strength}'。")
    if not source.strip():
        raise ValueError("source 不能为空：证据必须有出处（paper id 或 experiment id）。")
    if not note.strip():
        raise ValueError("note 不能为空：必须说明这条证据具体说了什么。")

    eid = next_id("evidence", "E")
    with open(os.path.join(STATE, "evidence", f"{eid}.md"), "w", encoding="utf-8") as f:
        f.write(f"""---
id: {eid}
type: evidence
hypothesis: {hypothesis_id}
stance: {stance}
strength: {strength}
source: {source}
created: {today()}
---

{note.strip()}
""")
    # 回写假设的 evidence 列表：这种簿记应由工具承担，不该让 agent 手动编辑
    hp = os.path.join(STATE, "hypotheses", f"{hypothesis_id}.md")
    htext = open(hp, encoding="utf-8").read()
    hfm, hbody = split_front(htext)
    cur = re.search(r"^evidence:\s*\[(.*?)\]", hfm, re.M)
    ids = [x.strip() for x in (cur.group(1) if cur else "").split(",") if x.strip()]
    if eid not in ids:
        ids.append(eid)
    hfm = re.sub(r"^evidence:.*$", f"evidence: [{', '.join(ids)}]", hfm, flags=re.M)
    open(hp, "w", encoding="utf-8").write(f"---\n{hfm}\n---\n{hbody}")

    log({"tool": "record_evidence", "ok": True, "id": eid,
         "hypothesis": hypothesis_id, "stance": stance, "source": source})
    return (f"已记录证据 {eid}（{hypothesis_id} / {stance} / {strength}，出处 {source}），"
            f"并已自动写入 {hypothesis_id} 的 evidence 列表，你不需要手动编辑。")


def t_transition_hypothesis(hypothesis_id="", new_status="", evidence_ids=None, rationale=""):
    """§0.4 的核心：无证据的状态迁移必须被硬拒绝。"""
    evidence_ids = evidence_ids or []
    if isinstance(evidence_ids, str):
        evidence_ids = [x.strip() for x in evidence_ids.split(",") if x.strip()]

    if not exists("hypotheses", hypothesis_id):
        raise ValueError(f"hypothesis '{hypothesis_id}' 不存在。")
    if new_status not in VALID_STATUS:
        raise ValueError(f"new_status 必须是 {sorted(VALID_STATUS)} 之一，收到 '{new_status}'。")
    if not evidence_ids:
        raise ValueError(
            "拒绝：状态迁移必须附带至少一条 evidence id。"
            "先用 record_evidence 记录证据，再用返回的 id 重试。")
    missing = [e for e in evidence_ids if not exists("evidence", e)]
    if missing:
        raise ValueError(f"拒绝：evidence {missing} 不存在。只能引用 record_evidence 返回的 id。")
    if not rationale.strip():
        raise ValueError("rationale 不能为空：必须说明这些证据为何支持该状态迁移。")

    path = os.path.join(STATE, "hypotheses", f"{hypothesis_id}.md")
    text = open(path, encoding="utf-8").read()
    fm, body = split_front(text)
    old = re.search(r"^status:\s*(\S+)", fm, re.M).group(1)
    fm = re.sub(r"^status:.*$", f"status: {new_status}", fm, flags=re.M)
    fm = re.sub(r"^evidence:.*$", f"evidence: [{', '.join(evidence_ids)}]", fm, flags=re.M)
    open(path, "w", encoding="utf-8").write(
        f"---\n{fm}\n---\n{body}\n\n## 状态变更 {today()}\n\n"
        f"{old} → {new_status}，依据 {', '.join(evidence_ids)}。\n\n{rationale.strip()}\n")

    log({"tool": "transition_hypothesis", "ok": True, "id": hypothesis_id,
         "from": old, "to": new_status, "evidence": evidence_ids})
    return f"{hypothesis_id}: {old} → {new_status}，已记录依据 {', '.join(evidence_ids)}。"


def t_log_decision(what="", why=""):
    if not what.strip() or not why.strip():
        raise ValueError("what 与 why 都不能为空。")
    did = next_id("decisions", "DEC")
    open(os.path.join(STATE, "decisions", f"{did}.md"), "w", encoding="utf-8").write(
        f"---\nid: {did}\ntype: decision\ncreated: {today()}\n---\n\n"
        f"## 做了什么\n\n{what.strip()}\n\n## 为什么\n\n{why.strip()}\n")
    log({"tool": "log_decision", "ok": True, "id": did})
    return f"已记录决策 {did}。"


TOOLS = [
    ("check_dead_ends", t_check_dead_ends,
     "检索本项目已关闭的研究方向，避免重复已经失败过的尝试。展开任何新方向前应先调用。",
     {"description": ("要检查的思路描述", True)}),
    ("record_evidence", t_record_evidence,
     "为某条 hypothesis 记录一条证据。证据必须有出处，且必须说明具体内容。",
     {"hypothesis_id": ("目标假设 id，如 H001", True),
      "stance": ("support / contradict / neutral", True),
      "source": ("出处：paper id 或 experiment id，不能为空", True),
      "note": ("这条证据具体说了什么，不能为空", True),
      "strength": ("weak / moderate / strong，默认 moderate", False)}),
    ("transition_hypothesis", t_transition_hypothesis,
     "变更某条 hypothesis 的状态。必须附带至少一条已存在的 evidence id，否则会被拒绝。",
     {"hypothesis_id": ("目标假设 id", True),
      "new_status": ("proposed / investigating / supported / refuted / inconclusive / abandoned", True),
      "evidence_ids": ("支撑本次迁移的 evidence id 列表", True),
      "rationale": ("这些证据为何支持该迁移，不能为空", True)}),
    ("log_decision", t_log_decision,
     "记录一条研究决策及其理由。",
     {"what": ("做了什么决定", True), "why": ("为什么", True)}),
]


def schema_of(params):
    props = {}
    for name, (desc, _) in params.items():
        typ = {"type": "array", "items": {"type": "string"}} if name.endswith("_ids") else {"type": "string"}
        props[name] = dict(typ, description=desc)
    return {"type": "object", "properties": props,
            "required": [n for n, (_, req) in params.items() if req]}


REGISTRY = {name: fn for name, fn, _, _ in TOOLS}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        ver = (msg.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return {"protocolVersion": ver, "capabilities": {"tools": {}},
                "serverInfo": {"name": "state", "version": "0.1.0"}}
    if method == "tools/list":
        return {"tools": [{"name": n, "description": d, "inputSchema": schema_of(p)}
                          for n, _, d, p in TOOLS]}
    if method == "tools/call":
        p = msg.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        fn = REGISTRY.get(name)
        if fn is None:
            return {"content": [{"type": "text", "text": f"未知工具 {name}"}], "isError": True}
        try:
            return {"content": [{"type": "text", "text": fn(**args)}], "isError": False}
        except Exception as e:
            log({"tool": name, "ok": False, "error": str(e), "args": args})
            return {"content": [{"type": "text", "text": f"错误：{e}"}], "isError": True}
    if method in ("ping",):
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
