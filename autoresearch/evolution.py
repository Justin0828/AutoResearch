"""自演进（M11 v1.8，DESIGN.md §5.18）：从一个问题或一条理解出发，在检索关闭的条件下演进一段思考，写成演进文档。

越是 vague 的问题越要演进。文档只是文档：不改任何研究对象、不进候选区、不进 briefing（聚焦讨论除外），
研究者读后自己决定记成理解或带进讨论。这里全是机械操作：选题、装配 briefing、把最终回复写成 EV###。
"""
import re

from . import attribution, schema
from .store import today

MATURITY_RANK = {"vague": 0, "scoped": 1, "formalized": 2}
MATURITY_MEANING = {
    "vague": "有方向，但边界不清——说不出什么不算在这个问题里",
    "scoped": "边界清楚——写得出研究什么、不研究什么、哪些视为给定",
    "formalized": "有可测量定义——写得出用什么量衡量、在什么设定下、什么结果算回答了它",
}


def _as_list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _stmt(body):
    from .objects import statement_of
    return statement_of(body)


def _one(text, n=160):
    t = " ".join((text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def docs(store, seed=None):
    """全部演进文档（旧→新）；给 seed 时只要以它为出发点的。"""
    out = [(m, b) for m, b in store.list("evolution")]
    if seed:
        out = [(m, b) for m, b in out if seed in _as_list(m.get("seeds"))]
    return sorted(out, key=lambda x: schema.split_id(x[0]["id"])[1] or 0)


def _open_questions(store):
    return [(m, b) for m, b in store.list("question")
            if schema.question_status(m) == "open" and not schema.is_withdrawn(m)]


def pick_seed(store):
    """自动选题（§5.18.1）：越 vague 越优先 → 活跃优先 → 演进过的越少越优先 → 越久没演进越优先。返回 (seed, why)。"""
    qs = _open_questions(store)
    if qs:
        def key(x):
            m = x[0]
            hist = docs(store, m["id"])
            last = hist[-1][0].get("created") if hist else ""
            return (MATURITY_RANK.get(m.get("maturity"), 0), not schema.is_active(m), len(hist), last or "",
                    schema.split_id(m["id"])[1] or 0)
        m = min(qs, key=key)[0]
        n = len(docs(store, m["id"]))
        why = (f"{m['id']} 是 {m.get('maturity')} 的开放问题" + ("、你标为活跃" if schema.is_active(m) else "")
               + (f"，此前演进过 {n} 次" if n else "，还没有演进过") + "——越 vague 的问题越需要演进")
        return m["id"], why
    ins = [m for m, _ in store.list("insight") if m.get("status") == "active"]
    fresh = [m for m in ins if schema.is_starred(m) and not docs(store, m["id"])]
    if fresh:
        return fresh[0]["id"], f"没有开放问题；{fresh[0]['id']} 是你星标、还没演进过的理解"
    if ins:
        m = max(ins, key=lambda m: schema.split_id(m["id"])[1] or 0)
        return m["id"], f"没有开放问题；{m['id']} 是最新的理解"
    return None, None


def check_seed(store, seed):
    m, _ = store.read_obj(seed) if schema.kind_of_id(seed or "") else (None, None)
    if not m or m.get("type") not in ("question", "insight"):
        raise ValueError(f"{seed or '（空）'} 不是已存在的问题或理解：自演进的出发点只能是 Q### 或 IN###。")
    if m.get("type") == "question" and schema.is_withdrawn(m):
        raise ValueError(f"{seed} 已撤下。")
    if m.get("type") == "insight" and m.get("status") != "active":
        raise ValueError(f"{seed} 已是 {m.get('status')}：只有 active 的理解能作出发点。")
    return m


def related_insights(store, seed):
    """以问题为出发点时同时带上的理解（§5.18.1）：指向它的、回答它的、星标的、上次演进后新形成的。返回 {IN: 原因}。"""
    m, _ = store.read_obj(seed)
    out = {}
    last = docs(store)
    # 上次演进时已有哪些理解：记在文档的 seen 字段（不作引用校验——理解可能被撤回确认删掉）
    seen = set(_as_list(last[-1][0].get("seen"))) if last else None
    for im, _ in store.list("insight"):
        if im.get("status") != "active" or im["id"] == seed:
            continue
        why = []
        if m.get("type") == "question" and seed in _as_list(im.get("informs")) + _as_list(im.get("relates_to")):
            why.append(f"与 {seed} 相关")
        if im["id"] in _as_list(m.get("answered_by")):
            why.append(f"回答过 {seed}")
        if schema.is_starred(im):
            why.append("研究者星标")
        if seen is not None and im["id"] not in seen:
            why.append("上次演进之后新形成")
        if why:
            out[im["id"]] = "、".join(why)
    return out


# ---------------------------------------------------------------- briefing（profile evolve，剥离 origin）

def briefing(store, task):
    n = attribution.neutralize
    seed = task["seed"]
    sm, sb = store.read_obj(seed)
    pm, pb = store.project()
    lines = [f"- 任务：`{task['id']}`（自演进）", f"- 出发点：**{seed}**（{'问题' if sm.get('type') == 'question' else '理解'}）",
             f"- 为什么选它：{task.get('why') or '研究者点名'}",
             "- 检索关闭：你没有联网工具，也读不到任何文件。这份 briefing 就是全部上下文。"]
    if task.get("prior_checkpoints"):
        lines.append("\n**本任务之前被切断过**，接着你上次的笔记往下想，然后重写整篇文档：")
        lines += [f"  - {c['ts']} {c['note']}" for c in task["prior_checkpoints"]]
    out = ["# Briefing\n\n这是你本次自演进的全部上下文。", "## 1. 本次任务\n\n" + "\n".join(lines)]

    rel = related_insights(store, seed)
    head = [f"## 2. 出发点：{seed}"]
    if sm.get("type") == "question":
        mat = sm.get("maturity")
        head.append(f"**成熟度 {mat}**：{MATURITY_MEANING.get(mat, '')}。"
                    + ("越 vague 越需要你替它想：它到底在问什么、有几种问法、藏着什么前提、可以怎么拆。" if mat != "formalized" else ""))
        head.append(n(_stmt(sb)))
        if sm.get("parent"):
            _, ppb = store.read_obj(sm["parent"])
            head.append(f"母问题 {sm['parent']}：{n(_one(_stmt(ppb), 200))}")
        kids = [(m, b) for m, b in store.list("question") if m.get("parent") == seed and not schema.is_withdrawn(m)]
        if kids:
            head.append("子问题：\n" + "\n".join(f"- {m['id']}（{schema.question_status(m)}）：{n(_one(_stmt(b), 160))}"
                                              for m, b in kids))
    else:
        head.append(f"牢固程度 {sm.get('firmness')}。\n\n{n(_stmt(sb))}")
        qs = [q for q in _as_list(sm.get("informs")) if schema.split_id(q)[0] == "Q" and store.exists(q)]
        for q in qs:
            _, qb = store.read_obj(q)
            head.append(f"它影响的问题 {q}：{n(_one(_stmt(qb), 300))}")
    if rel:
        parts = []
        for iid, why in rel.items():
            im, ib = store.read_obj(iid)
            parts.append(f"#### {iid} · {im.get('firmness')} · {why}\n\n{n(_stmt(ib))}")
        head.append("### 与出发点相关的理解（是理解，不是证据）\n\n" + "\n\n".join(parts))
    out.append("\n\n".join(head))

    hist = docs(store, seed)
    if hist:
        lm, lb = hist[-1]
        older = "".join(f"\n- {m['id']}（{m.get('created')}）：{m.get('title') or ''}" for m, _ in hist[:-1])
        out.append(f"## 3. 此前以 {seed} 为出发点的演进\n\n演进是接着往前走，不是从头再来：先看上一次推到了哪里、"
                   f"留下了什么没想通的。\n\n### 最近一篇 {lm['id']}（{lm.get('created')}）\n\n"
                   + re.sub(r"^(#+) ", lambda x: "#" * min(len(x.group(1)) + 3, 6) + " ", n(lb.strip()), flags=re.M)
                   + (f"\n\n### 更早的{older}" if older else ""))
    else:
        out.append(f"## 3. 此前以 {seed} 为出发点的演进\n\n（没有，这是第一次。）")

    other_ins = [(m, b) for m, b in store.list("insight")
                 if m.get("status") == "active" and m["id"] != seed and m["id"] not in rel]
    qs = [(m, b) for m, b in store.list("question") if m["id"] != seed and not schema.is_withdrawn(m)]
    land = [f"## 4. 研究全景\n\n**项目**：{pm.get('title', '')}\n\n{n((pb or '').strip())}"]
    if other_ins:
        land.append("### 其余理解\n\n" + "\n\n".join(f"- **{m['id']}** · {m.get('firmness')}：{n(_stmt(b))}"
                                                  for m, b in other_ins))
    opened = [(m, b) for m, b in qs if schema.question_status(m) == "open"]
    closed = [(m, b) for m, b in qs if schema.question_status(m) in schema.CLOSED_QUESTION]
    if opened:
        land.append("### 其余开着的问题\n\n" + "\n".join(
            f"- {m['id']} · {m.get('maturity')}{' · 母问题 ' + m['parent'] if m.get('parent') else ''}：{n(_one(_stmt(b), 160))}"
            for m, b in opened))
    if closed:
        land.append("### 已有结论的问题（不要当作开放问题重新推）\n\n" + "\n".join(
            f"- {m['id']}（{m['status']}）：{n(_one(_stmt(b), 120))}" for m, b in closed))
    gone = [(m, b) for m, b in store.list("question") if schema.is_withdrawn(m)]
    if gone:
        land.append("### 研究者撤下的问题（不再相关的方向，不要当作新想法重推；认为该回来就明说）\n\n" + "\n".join(
            f"- {m['id']}：{n(_one(_stmt(b), 120))}" for m, b in gone))
    asm = [(m, b) for m, b in store.list("assumption") if not schema.is_withdrawn(m)]
    if asm:
        land.append("### 前提\n\n" + "\n".join(f"- {m['id']}（{m.get('status')}）：{n(_one(_stmt(b), 160))}"
                                              for m, b in asm))
    hyp = [(m, b) for m, b in store.list("hypothesis") if not schema.is_withdrawn(m)]
    if hyp:
        land.append("### 假设\n\n" + "\n".join(f"- {m['id']}（{m.get('status')}）：{n(_one(_stmt(b), 160))}"
                                              for m, b in hyp))
    de = store.list("dead-end")
    land.append("### 已关闭方向（全部，不要换个说法重走）\n\n" + ("\n\n".join(
        f"- **{m['id']}**（{m.get('status')}）：{n(b.strip())}" for m, b in de) or "（暂无。）"))
    out.append("\n\n".join(land))
    return _redact_titles(store, "\n\n".join(out)) + "\n"


def _redact_titles(store, text):
    """不给论文标题：标题本身就是文献框架的名字（沿用 v1.4 基本盘的屏蔽）。"""
    names = []
    for m, _ in store.list("paper"):
        t = " ".join(str(m.get("title") or "").split())
        if t:
            names.append((t, m["id"]))
            short = re.split(r"[:：]", t, 1)[0].strip()
            if short != t and len(short) >= 4:
                names.append((short, m["id"]))
    for name, pid in sorted(names, key=lambda x: -len(x[0])):
        text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", pid, text, flags=re.I)
    return text


# ---------------------------------------------------------------- 产出

def record(store, task, text):
    """把演进任务的最终回复写成 EV###（§5.18.4）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("演进任务没有给出文档")
    seed = task["seed"]
    sm, _ = store.read_obj(seed)
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), "") or f"演进：{seed}"
    q = seed if (sm or {}).get("type") == "question" else next(
        (x for x in _as_list((sm or {}).get("informs")) if x.startswith("Q") and store.exists(x)), None)
    with store.tx(f"evolution: 自演进文档（出发点 {seed}）", actor="system", task=task["id"]) as tx:
        eid = tx.new_id("evolution")
        tx.write_obj(eid, {"id": eid, "type": "evolution", "seeds": [seed], "question": q,
                           "task": task["id"], "trigger": task.get("trigger") or "manual", "title": title,
                           "seen": [m["id"] for m, _ in store.list("insight") if m.get("status") == "active"] or None,
                           "created": today()}, text + "\n")
        tx.note = eid
    return eid


def as_dict(m, b):
    return {**m, "body": (b or "").strip()}
