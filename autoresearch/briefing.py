"""Briefing / Handoff 装配器（DESIGN.md §5.2）。

一个装配器、两种用途：给 agent 的 briefing.md，以及班次结束时落进 State 的
交接记录。交接记录必须能在**没有 agent** 的情况下生成（窗口被切断时恰恰
没有额度），所以这里只做机械装配，不调用任何模型。
"""
import re

from . import attribution, candidates, discussion, evolution, frontmatter, modes, reviews, schema

PROFILES = {
    # redact: 是否剥离 origin；sections: 章节（见 §5.2 表），"3b" = 当前理解
    "discuss": {"redact": False, "sections": [1, 2, 3, "3b", *range(4, 9), 12, 9, 10, 11]},
    "distill": {"redact": False, "sections": [1, 2, 3, "3b", *range(4, 9), 12, 9, 10, 11]},
    # 评判类看不到理解：评估证据只看证据，理解是解读框架，会带来锚定
    "judge": {"redact": True, "sections": [*range(1, 9), 12, 11]},
    # 整理（Tidy up，§5.16.3）：同 distill，但讨论上下文换成全部 active 理解与 open 问题的全文
    "tidy": {"redact": False, "sections": [1, 2, 3, "3b", *range(4, 9), 12, 9, "tidy", 11]},
}

BUDGET = {  # 每章字符预算；dead-end、任务、交接、问题不截断
    "3b": 8000, 4: 8000, 5: 12000, 6: 12000, 8: 4000, 9: 6000, 10: 24000, 12: 8000,
}
DISTILL_DISCUSSION_BUDGET = 80000

MODE_RULES = {
    "discussion": "当前是**讨论模式**：研究者主导。你不得自主发起验证、实验或方向变更；"
                  "只允许为当下讨论做低成本的文献查证。讨论中出现的研究内容只能进候选区，"
                  "由研究者确认后才成为正式 State 对象。",
    "validation": "当前是**验证模式**：研究者已把一批前提 / 假设交棒给系统，系统自主检索、阅读、"
                  "记录证据并在证据足够时迁移状态。出现重大结果时系统会停下来建议回到讨论，而不是自己继续闭环。",
}

# 评判类任务的署名线索规范化（§5.2 judge 规则 2）。屏蔽不可能完美，偏差指标兜底。
neutralize = attribution.neutralize


def _clip(parts, budget, more_hint):
    """按顺序拼接 parts，超出预算后剩余的只给首行，并写明去哪看全文。"""
    out, used, clipped = [], 0, 0
    for full, short in parts:
        if used + len(full) <= budget:
            out.append(full)
            used += len(full)
        else:
            out.append(short)
            used += len(short)
            clipped += 1
    if clipped:
        out.append(f"\n（{clipped} 条已截断为摘要行，完整内容见 {more_hint}。）")
    return "\n\n".join(out)


def _fm_line(meta, keys):
    return " · ".join(f"{k}={_v(meta.get(k))}" for k in keys if meta.get(k) not in (None, "", []))


def _v(x):
    return "[" + ", ".join(x) + "]" if isinstance(x, list) else str(x)


class Assembler:
    def __init__(self, store):
        self.store = store
        self.focus = None          # 聚焦讨论的对象：其他章节不重复它的全文（§5.15.2）

    def _live(self, items):
        """撤下的对象不进 briefing 正文（§5.15.6），只在末尾列一行。"""
        return [(m, b) for m, b in items if not schema.is_withdrawn(m)]

    def _pointer(self, head):
        return f"{head}\n\n（本场讨论的聚焦对象，全文见第 2b 章。）"

    def _body(self, text, redact):
        text = (text or "").strip()
        return neutralize(text) if redact else text

    def _origin(self, ident, redact):
        if redact:
            return ""
        rec = self.store.provenance().get(ident)
        if not rec:
            return ""
        s = f" · 提出者={rec.get('origin')}"
        if rec.get("disputed"):
            s += "（归属有争议，待人确认）"
        return s

    # ---------------------------------------------------------------- 章节

    def s_task(self, task):
        mode = self.store.project()[0].get("mode", "discussion")
        lines = [f"- 任务：`{task['id']}`（{task['kind']}）", f"- 目标：{task['goal']}"]
        if task.get("why"):
            lines.append(f"- 为什么做这个：{task['why']}")
        if task.get("expected"):
            lines.append(f"- 预期产出：{task['expected']}")
        lines.append(f"- {MODE_RULES.get(mode, '')}")
        b = modes.batch(self.store)
        if b:
            lines.append(f"- 当前验证批次：{', '.join(b)}")
        if task.get("target"):
            lines.append(f"- 本次目标：**{task['target']}**（全文见下面对应章节）")
        if task.get("paper"):
            lines.append(f"- 本次论文：**{task['paper']}**（先 open_paper）")
        if task.get("resume_note"):
            lines.append(f"- **注意**：{task['resume_note']}")
        if task.get("prior_checkpoints"):
            lines.append("\n**本任务之前被中断过**，上次尝试留下的进度笔记（从这里接着做，"
                         "不要重复已完成的部分；已提交的候选见“候选区”一章）：")
            lines += [f"  - {c['ts']} {c['note']}" for c in task["prior_checkpoints"]]
        return "## 1. 本次任务\n\n" + "\n".join(lines)

    def s_handoff(self, redact=False):
        d = self.store.state / "handoffs"
        files = sorted(d.glob("HO*.md"), key=lambda p: schema.split_id(p.stem)[1] or 0) \
            if d.is_dir() else []
        if not files:
            return "## 2. 上一班交接\n\n（这是第一个班次，没有交接记录。）"
        meta, body = frontmatter.parse(files[-1].read_text(encoding="utf-8"))
        if redact:
            # 评判类只看被截断的任务：提交列表与“待处理”都带归属线索（§5.6）
            m = re.search(r"## 被截断的任务.*?(?=\n## |\Z)", body, re.S)
            return ("## 2. 上一班交接（仅被截断的任务）\n\n" +
                    (neutralize(m.group(0).split("\n", 1)[1].strip()) if m else "（上一班没有被截断的任务。）"))
        return (f"## 2. 上一班交接（{meta.get('id')} · {meta.get('reason')} · "
                f"结束于 {meta.get('ended')}）\n\n{body.strip()}")

    def s_question(self, redact):
        """第 3 章分层（§5.16.2）：主问题与活跃问题给全文，其余 open 问题一行，已结的一行并指向结论。"""
        pmeta, pbody = self.store.project()
        qid = pmeta.get("main_question")
        qmeta, qbody = self.store.read_obj(qid) if qid else (None, None)
        out = [f"## 3. 研究问题\n\n**项目**：{pmeta.get('title', '')}\n\n{self._body(pbody, redact)}"]
        if qmeta:
            h = f"### {qid}（主问题 · 成熟度 {qmeta.get('maturity')}{_rev(qmeta)}）"
            out.append(self._pointer(h) if qid == self.focus else f"{h}\n\n{self._body(qbody, redact)}")
        others = [(m, b) for m, b in self._live(self.store.list("question")) if m.get("id") != qid]
        opened = [(m, b) for m, b in others if schema.question_status(m) == "open"]
        active = [(m, b) for m, b in opened if schema.is_active(m)]
        rest = [(m, b) for m, b in opened if not schema.is_active(m)]
        closed = [(m, b) for m, b in others if schema.question_status(m) in schema.CLOSED_QUESTION]
        if active:
            out.append("### 当前在想的问题（研究者标为活跃）")
            for m, b in active:
                h = f"#### {m['id']}（成熟度 {m.get('maturity')}{_rev(m)}{_parent(m)}）{_fm_line(m, ['relates_to']) and ' · ' + _fm_line(m, ['relates_to'])}"
                out.append(self._pointer(h) if m["id"] == self.focus else f"{h}\n\n{self._body(b, redact)}")
        if rest:
            out.append("### 其余开着的问题（每条一行；全文见 questions/，需要时自行 Read）\n\n" + "\n".join(
                f"- {m['id']} · {m.get('maturity')}{_parent(m, ' · ')}："
                + ("（本场讨论的聚焦对象，全文见第 2b 章。）" if m["id"] == self.focus
                   else _one(self._body(_stmt(b), redact), 120))
                for m, b in rest))
        if closed:
            out.append("### 已有结论的问题（不要重新提出）\n\n这些问题已经结了，作用同已关闭方向：不要把它们当作开放问题重新提出；"
                       "若你认为某条结得太早，明确说出来（研究者可以重开）。\n\n" + "\n".join(
                           f"- {m['id']}（{_CLOSED_LABEL[m['status']]}）：{_one(self._body(_stmt(b), redact), 100)} → "
                           + self._closed_pointer(m, redact) for m, b in closed))
        return "\n\n".join(out)

    def _closed_pointer(self, m, redact):
        st = m.get("status")
        if st == "answered":
            return "答案见 " + "、".join(_as_list(m.get("answered_by")))
        if st == "merged":
            return f"已并入 {m.get('merged_into')}"
        _, db = self.store.read_obj(m.get("decided_by") or "") if m.get("decided_by") else (None, "")
        dec = re.search(r"## 决定\n\n(.*)", db or "", re.S)
        return f"决定（{m.get('decided_by')}）：{_one(self._body(dec.group(1) if dec else '', redact), 200)}"

    def s_tidy(self):
        """整理任务（§5.16.3）的上下文：全部 active 理解与全部 open 问题的全文，不截断。"""
        ins = [(m, b) for m, b in self.store.list("insight") if m.get("status") == "active"]
        qs = [(m, b) for m, b in self._live(self.store.list("question")) if schema.question_status(m) == "open"]
        main = self.store.project()[0].get("main_question")
        out = [f"## 10. 待整理的全部内容（{len(ins)} 条 active 理解、{len(qs)} 个 open 问题）"]
        out.append("### 理解（active）\n\n" + ("\n\n".join(
            f"#### {m['id']} · {m.get('firmness')} · {_fm_line(m, ['basis', 'informs'])}\n\n{b.strip()}"
            + "".join(f"\n- {k}：{m[f]}" for f, k in (("basis_note", "根基说明"), ("change_mind", "什么会让我改观")) if m.get(f))
            for m, b in ins) or "（没有。）"))
        out.append("### 问题（open）\n\n" + ("\n\n".join(
            f"#### {m['id']}{'（主问题，不能结）' if m['id'] == main else ''} · 成熟度 {m.get('maturity')}"
            f"{_parent(m, ' · ')}{' · 活跃' if schema.is_active(m) else ''}{_rev(m, ' · ')}"
            f"{' · ' + _fm_line(m, ['relates_to']) if m.get('relates_to') else ''}\n\n{b.strip()}"
            for m, b in qs) or "（没有。）"))
        return "\n\n".join(out)

    def s_insights(self, redact):
        """第 3b 章分层（§5.17.2）：研究者星标的理解给全文，其余 active 理解每条一行。"""
        items = [(m, b) for m, b in self.store.list("insight") if m.get("status") == "active"]
        head = ("## 3b. 当前理解（是理解，不是证据）\n\n研究至今形成的看法与直觉。它们是解读框架，"
                "不能当作证据引用；拿其中某条来排除方向时，要把这个用法作为 assumption 提出"
                "（derived_from 指向该理解）。")
        if not items:
            return head + "\n\n（暂无。）"
        order = {"settled": 0, "working": 1, "hunch": 2}
        name = {"settled": "稳固理解", "working": "工作理解", "hunch": "直觉"}
        items.sort(key=lambda x: (order.get(x[0].get("firmness"), 3), x[0]["id"]))
        starred = [(m, b) for m, b in items if schema.is_starred(m)]
        rest = [(m, b) for m, b in items if not schema.is_starred(m)]
        out = [head]
        if starred:
            parts = []
            for m, b in starred:
                h = (f"#### {m['id']} · {name.get(m.get('firmness'), m.get('firmness'))} · "
                     f"{_fm_line(m, ['basis', 'informs'])}{self._origin(m['id'], redact)}")
                body = self._body(b, redact)
                extra = "".join(f"\n- {k}：{m[f]}" for f, k in
                                (("basis_note", "根基说明"), ("change_mind", "什么会让我改观")) if m.get(f))
                if m["id"] == self.focus:
                    parts.append((self._pointer(h), h))
                    continue
                parts.append((f"{h}\n\n{body}{extra}", f"{h} — {_one(_stmt(b), 120)}"))
            out.append("### 研究者星标的理解\n\n" + _clip(parts, BUDGET["3b"], "insights/"))
        if rest:
            out.append("### 其余理解（每条一行；全文见 insights/，需要时自行 Read）\n\n" + "\n".join(
                f"- {m['id']} · {name.get(m.get('firmness'), m.get('firmness'))}："
                + ("（本场讨论的聚焦对象，全文见第 2b 章。）" if m["id"] == self.focus
                   else _one(self._body(_stmt(b), redact), 140))
                for m, b in rest))
        return "\n\n".join(out)

    def s_assumptions(self, redact):
        items = self._live(self.store.list("assumption"))
        items.sort(key=lambda x: (x[0].get("status") != "unexamined", x[0]["id"]))
        if not items:
            return "## 4. 前提（Assumption）\n\n（暂无。）"
        parts = []
        for m, b in items:
            head = (f"### {m['id']} · {_fm_line(m, ['status', 'relied_on_by', 'fragile', 'promoted_to', 'relates_to'])}"
                    f"{_rev(m, ' · ')}{self._origin(m['id'], redact)}")
            body = self._body(b, redact)
            if m["id"] == self.focus:
                parts.append((self._pointer(head), head))
                continue
            parts.append((f"{head}\n\n{body}", f"{head} — {body.splitlines()[0] if body else ''}"))
        return "## 4. 前提（Assumption）\n\n" + _clip(parts, BUDGET[4], "assumptions/")

    def s_hypotheses(self, redact):
        items = self._live(self.store.list("hypothesis"))
        if not items:
            return "## 5. 假设（Hypothesis）\n\n（暂无。）"
        parts = []
        for m, b in items:
            head = (f"### {m['id']} · {_fm_line(m, ['status', 'confidence', 'evidence', 'promoted_from', 'group', 'relates_to'])}"
                    f"{_rev(m, ' · ')}{self._origin(m['id'], redact)}")
            if m["id"] == self.focus:
                parts.append((self._pointer(head), head))
                continue
            fal, val = m.get("falsifier", ""), m.get("validation", "")
            if redact:
                fal, val = neutralize(fal), neutralize(val)
            full = (f"{head}\n\n- 证伪条件：{fal}\n- 验证安排：{val}"
                    f"\n\n{self._body(b, redact)}")
            first = self._body(b, redact).splitlines()
            parts.append((full, f"{head} — {first[0] if first else ''}"))
        return "## 5. 假设（Hypothesis）\n\n" + _clip(parts, BUDGET[5], "hypotheses/")

    def s_evidence(self, redact):
        items = self.store.list("evidence")
        if not items:
            return "## 6. 证据\n\n（暂无。）"
        items.reverse()   # 最新优先
        parts = []
        for m, b in items:
            loc = f" {_v(m.get('locator'))}" if m.get("locator") else ""
            loc += f" · 判定时目标为第 {m['revision']} 版" if str(m.get("revision", "1")) not in ("1", "None") else ""
            head = (f"- **{m['id']}** → {m.get('target', m.get('hypothesis'))} · {m.get('stance')} · "
                    f"{m.get('strength')} · 出处 {m.get('source')}{loc}"
                    + (" · 仅摘要" if m.get("basis") == "abstract" else ""))
            quote = f"\n  > {m['quote']}" if m.get("quote") else ""
            parts.append((f"{head}：{self._body(b, redact)}{quote}", head))
        return "## 6. 证据\n\n" + _clip(parts, BUDGET[6], "evidence/")

    def s_dead_ends(self, redact):
        items = self.store.list("dead-end")
        if not items:
            return "## 7. 已关闭方向（禁止重复）\n\n本项目还没有记录任何 dead-end。"
        out = ["## 7. 已关闭方向（禁止重复）\n\n以下**全部**列出、不做截断。展开新思路前逐条对照；"
               "若你认为某条关得太早，明确说出来，而不是悄悄重走。"]
        for m, b in items:
            out.append(f"### {m['id']} · {_fm_line(m, ['status', 'closed_by'])}\n\n{self._body(b, redact)}")
        return "\n\n".join(out)

    def s_uncertainties(self, redact):
        items = [(m, b) for m, b in self._live(self.store.list("uncertainty")) if m.get("status") != "resolved"]
        if not items:
            return "## 8. 未决不确定性\n\n（暂无。）"
        order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda x: order.get(x[0].get("importance"), 3))
        parts = []
        for m, b in items:
            head = f"### {m['id']} · {_fm_line(m, ['status', 'importance', 'relates_to'])}{_rev(m, ' · ')}{self._origin(m['id'], redact)}"
            body = self._body(b, redact)
            if m["id"] == self.focus:
                parts.append((self._pointer(head), head))
                continue
            parts.append((f"{head}\n\n{body}", f"{head} — {body.splitlines()[0] if body else ''}"))
        return "## 8. 未决不确定性\n\n" + _clip(parts, BUDGET[8], "uncertainties/")

    def s_candidates(self):
        cs = candidates.list_all(self.store)
        pending = [c for c in cs if c["status"] == "pending"]
        rejected = [c for c in cs if c["status"] == "rejected"][-15:]
        if not pending and not rejected:
            return "## 9. 候选区\n\n（空。）"
        parts = []
        for c in pending:
            kind = c["kind"]
            if kind == "revision":
                kind = f"修订 {c.get('target')}（基于第 {c.get('base_revision')} 版）"
            elif kind == "resolve":
                kind = f"结 {c.get('target')} 为 {c.get('resolution')}" + (
                    f"，答案 {_v(c.get('answered_by'))}" if c.get("answered_by") else "") + (
                    f"，并入 {c.get('merged_into')}" if c.get("merged_into") else "")
            elif c.get("supersedes"):
                kind = f"insight，合并 {_v(c.get('supersedes'))}"
            src = f"来自 {c['source']} 第 {_v(c.get('turns'))} 轮" if c.get("turns") else f"来自任务 {c['source']}"
            head = f"- **{c['id']}**（待确认 · {kind} · {src}）"
            parts.append((f"{head}：{c['statement']}", head))
        for c in rejected:
            head = f"- **{c['id']}**（已丢弃 · {c['kind']}{' ' + c['target'] if c.get('target') else ''}）"
            parts.append((f"{head}：{c['statement']} —— {c['decision_note']}", head))
        return ("## 9. 候选区\n\n用于去重：待确认的不要重复提交；已丢弃的附有研究者的理由，"
                "不要换个说法再提。\n\n" + _clip(parts, BUDGET[9], "candidates/"))

    def s_discussion(self, ds, budget):
        if not ds:
            return "## 10. 讨论上下文\n\n（本任务不针对某个讨论。）"
        meta, turns = discussion.read(self.store, ds)
        smeta, sbody = discussion.summary(self.store, ds)
        c = int(smeta.get("covers_through", 0) or 0)
        out = [f"## 10. 讨论上下文：{ds}「{meta.get('title')}」（共 {len(turns)} 轮）"]
        if sbody.strip():
            out.append(f"### 第 1–{c} 轮摘要\n\n{sbody.strip()}")
        rest = [t for t in turns if t["n"] > c]
        shown, used = [], 0
        for t in reversed(rest):
            block = f"#### 第 {t['n']} 轮 · {discussion.ROLE_TITLE[t['role']]}\n\n{t['text']}"
            if used + len(block) > budget and shown:
                break
            shown.append(block)
            used += len(block)
        shown.reverse()
        omitted = len(rest) - len(shown)
        if omitted:
            out.append(f"（第 {c + 1}–{c + omitted} 轮原文因篇幅省略，且尚未被摘要覆盖；"
                       f"需要时读 {discussion.rel_transcript(ds)}。）")
        if shown:
            out.append(f"### 摘要之后的原文（第 {rest[-len(shown)]['n']}–{rest[-1]['n']} 轮）")
            out += shown
        return "\n\n".join(out)

    def s_papers(self, task):
        items = self.store.list("paper")
        if not items:
            return "## 12. 文献\n\n（还没有登记任何论文。）"
        focus = task.get("target")
        items.sort(key=lambda x: (focus not in (x[0].get("for") or []), x[0]["id"]))
        parts = []
        for m, b in items:
            ref = m.get("arxiv") and f"arXiv:{m['arxiv']}" or m.get("doi") and f"doi:{m['doi']}" or m.get("url")
            head = (f"- **{m['id']}** {m.get('title')}（{m.get('year') or '?'}，{ref}）· 读到 {m.get('read')} · "
                    f"全文 {m.get('fulltext')}" + (f" · 为 {_v(m['for'])}" if m.get("for") else ""))
            parts.append((head, head))
        return ("## 12. 文献\n\n已登记的论文（登记即已核实存在）。不要重复登记；读它们用 open_paper。\n\n"
                + _clip(parts, BUDGET[12], "papers/"))

    def s_reviews(self):
        rs = reviews.list_all(self.store, "open")
        if not rs:
            return "## 11. 待重新审视\n\n（无。）"
        out = ["## 11. 待重新审视\n\n下列对象与某个**已被推翻**的前提或假设相关，研究者尚未判断它们是否"
               "还站得住。在推理中依赖它们时要明说这一点；不要自行改写它们。"]
        for r in rs:
            out.append(f"- **{r['target']}**（{r['id']}，由 {r['trigger']} 触发，距离 {r['depth']}）："
                       + r["body"].replace("\n\n", " "))
        return "\n".join(out)

    def s_focus(self, ds, redact):
        """第 2b 章：聚焦对象的全文、修订史、与它相关的一切、其他聚焦于它的讨论（§5.15.2）。"""
        from . import objects
        fid = self.focus
        idx = objects.Index(self.store)
        info = objects.links(self.store, fid, idx)
        m = info["meta"]
        rev = info["revision"]
        keys = {"question": ["maturity", "status", "parent", "active", "answered_by", "decided_by", "merged_into"], "assumption": ["status", "relied_on_by", "fragile"],
                "hypothesis": ["status", "confidence", "evidence"], "uncertainty": ["status", "importance"],
                "insight": ["status", "firmness", "basis", "informs"]}.get(m.get("type"), [])
        out = [f"## 2b. 聚焦对象：{fid}（{m.get('type')}，第 {rev} 版）",
               f"本场讨论（{ds}）专门打磨 {fid}。目标是把它想清楚、说准确：可以挑战它本身——包括认为它该拆分、"
               "该合并、该放弃；**不以提升成熟度为目标**，同级打磨（vague → vague，只是说得更准）是完全正当的进展。"
               "当表述确有推进时，明说“这一版比上一版好在哪”；蒸馏会据此提出修订候选"
               f"（kind=revision，target={fid}，base_revision={rev}）。只有问题变成了另一个问题时，才应新建对象并用 relates_to 连回来。"]
        if m.get("type") == "evolution":
            # 演进文档不修订（§5.18.4）：讨论它是为了从里面提炼出理解、问题的修订或新问题
            out[1] = (f"本场讨论（{ds}）围绕自演进文档 {fid}（检索关闭时从 {', '.join(m.get('seeds') or [])} 出发写的一段思考）。"
                      "目标是和研究者一起判断里面哪些想法站得住、值得记下：站得住的可以提成 insight 候选（basis 写 "
                      f"{fid}），让某个问题说得更准的提修订候选，拆出的新问题提 question 候选。文档本身不修订。")
        if info["withdrawn"]:
            out.append(f"**注意：{fid} 已被研究者撤下**（{info['withdrawn']['reason']}）。")
        out.append(f"### 当前全文（第 {rev} 版）\n\n{_fm_line(m, keys)}{_rev(m, ' · ')}")
        if m.get("type") == "hypothesis":
            out.append(f"- 证伪条件：{m.get('falsifier', '')}\n- 验证安排：{m.get('validation', '')}")
        if m.get("type") == "insight":
            out.append("".join(f"- {k}：{m[f]}\n" for f, k in (("basis_note", "根基说明"),
                                                                ("change_mind", "什么会让我改观")) if m.get(f)).strip())
        out.append(self._body(info["body"], redact))
        hist = info["revisions"]
        if hist:
            lines = []
            for h in hist[:6]:
                why = re.search(r"## 为什么\n\n(.*)", h["body"], re.S)
                before = re.search(r"## 修订前（第 \d+ 版）\n\n(.*?)\n\n字段", h["body"], re.S)
                lines.append(f"- 第 {h['from']} → {h['to']} 版（{h['created']}，{h['id']}，候选 {h.get('candidate')}）："
                             f"{_one(why.group(1) if why else '', 300)}\n  修订前：{_one(before.group(1) if before else '', 300)}")
            out.append("### 修订史（最近在前）\n\n" + "\n".join(lines))
        else:
            out.append("### 修订史\n\n（还没有修订过。）")
        rel = []
        for e in info["evidence"]:
            tag = f" · 判定时为第 {e['revision']} 版" if e.get("revision") and e["revision"] != rev else ""
            rel.append(f"- 证据 {e['id']} · {e['stance']} · {e['strength']} · 出处 {e['source']}{tag}"
                       + (f"（{e['via']}）" if e.get("via") else "") + f"：{_one(e['note'], 200)}")
        for r in info["related"]:
            arrow = f"{r['id']} 的 {r['field']} 指向它" if r["dir"] == "in" else f"它的 {r['field']} 指向 {r['id']}"
            rel.append(f"- {r['id']}（{r['type']}{'，已撤下' if r['withdrawn'] else ''}）· {arrow}：{r['text']}")
        for r in info["reviews"]:
            if r.get("status") == "open":
                rel.append(f"- 待重新审视 {r['id']}：{r['target']}（因 {r['trigger']}）")
        for c in info["candidates"]:
            if c.get("status") == "pending":
                what = f"修订（基于第 {c.get('base_revision')} 版）" if c.get("kind") == "revision" else c.get("kind")
                rel.append(f"- 待确认候选 {c['id']}（{what}）：{_one(c['statement'], 200)}")
        out.append("### 与它相关的一切\n\n" + ("\n".join(rel) or "（暂无。）"))
        others = [d for d in info["discussions"] if d["id"] != ds and d.get("relation") == "focus"]
        if others:
            parts = []
            for d in others:
                smeta, sbody = discussion.summary(self.store, d["id"])
                parts.append(f"#### {d['id']}「{d.get('title')}」（{d.get('status')}，{d.get('turns')} 轮）\n\n"
                             + (sbody.strip() or f"（尚无摘要；原文见 {discussion.rel_transcript(d['id'])}。）"))
            out.append("### 此前其他聚焦于它的讨论\n\n" + "\n\n".join(parts))
        return "\n\n".join(x for x in out if x)

    def s_withdrawn(self, redact):
        """撤下的对象在末尾各列一行，防止被当成新想法重新提出（§5.15.6）。"""
        lines = []
        for t in schema.WITHDRAWN_STATUS:
            for m, b in self.store.list(t):
                if not schema.is_withdrawn(m):
                    continue
                _, db = self.store.read_obj(m["withdrawn_by"])
                why = re.search(r"## 为什么\n\n(.*)", db or "", re.S)
                lines.append(f"- {m['id']}：{_one(self._body(b, redact), 120)} —— 撤下理由："
                             f"{_one(self._body(why.group(1) if why else '', redact), 200)}")
        if not lines:
            return ""
        return ("## 已撤下（不再相关，不是被推翻）\n\n研究者认为这些不再是关心的方向。不要把它们当作新想法重新提出；"
                "若你认为某条应当回来，明确说出来。\n\n" + "\n".join(lines))

    # ---------------------------------------------------------------- 装配

    def briefing(self, profile, task):
        if profile == "evolve":
            # 自演进（§5.18.3）：任务读不到任何文件，briefing 就是全部上下文
            return evolution.briefing(self.store, task)
        p = PROFILES[profile]
        redact = p["redact"]
        ds = task.get("discussion")
        self.focus = None
        if ds and profile in ("discuss", "distill"):
            dm = (discussion.read(self.store, ds)[0] or {}) if self.store.exists(ds) else {}
            if dm.get("focus") and self.store.exists(dm["focus"]):
                self.focus = dm["focus"]
        disc_budget = DISTILL_DISCUSSION_BUDGET if profile == "distill" else BUDGET[10]
        builders = {
            1: lambda: self.s_task(task),
            2: lambda: self.s_handoff(redact),
            3: lambda: self.s_question(redact),
            "3b": lambda: self.s_insights(redact),
            4: lambda: self.s_assumptions(redact),
            5: lambda: self.s_hypotheses(redact),
            6: lambda: self.s_evidence(redact),
            7: lambda: self.s_dead_ends(redact),
            8: lambda: self.s_uncertainties(redact),
            9: self.s_candidates,
            10: lambda: self.s_discussion(ds, disc_budget),
            "tidy": self.s_tidy,
            11: self.s_reviews,
            12: lambda: self.s_papers(task),
        }
        head = ("# Briefing\n\n这是你本次任务的全部上下文，由 Research State 装配而来。"
                "你没有任何先前的记忆——这里写的就是研究至今的全部共识；"
                "细节可以去 State 仓库读原文件。")
        secs = [builders[i]() for i in p["sections"]]
        if self.focus:
            secs.insert(p["sections"].index(2) + 1, self.s_focus(ds, redact))
        secs.append(self.s_withdrawn(redact))
        return "\n\n".join([head] + [s for s in secs if s]) + "\n"

    def handoff(self, shift, tasks, queue):
        """交接记录正文（§5.2 handoff profile）。纯机械，不依赖 agent。"""
        out = []
        q = shift.get("quota_end") or {}
        out.append("## 班次概况\n\n"
                   f"- 班次：{shift['id']}，{shift['started']} → {shift['ended']}\n"
                   f"- 结束原因：{shift['reason']}"
                   + (f"（{shift['reason_detail']}）" if shift.get("reason_detail") else "") + "\n"
                   f"- 额度（结束时）：5h {_pct(q.get('five_hour'))} · 7d {_pct(q.get('seven_day'))}"
                   + (f" · 5h 窗口重置于 {q['five_hour_resets']}" if q.get("five_hour_resets") else ""))

        done = [t for t in tasks if t["status"] == "done"]
        cut = [t for t in tasks if t["status"] in ("interrupted", "running")]
        failed = [t for t in tasks if t["status"] == "failed"]
        out.append("## 本班完成\n\n" + ("\n".join(
            f"- {t['id']} {t['kind']}：{t['goal']}" + (f" → {t['result_brief']}" if t.get("result_brief") else "")
            for t in done) or "（无）"))
        if cut:
            lines = []
            for t in cut:
                lines.append(f"- **{t['id']} {t['kind']}**：{t['goal']}（第 {t.get('attempts', 1)} 次尝试被切断）")
                for c in t.get("checkpoints", []):
                    lines.append(f"  - 进度 {c['ts']}：{c['note']}")
                if not t.get("checkpoints"):
                    lines.append("  - 没有留下进度笔记，下一班从头做。")
            out.append("## 被截断的任务（下一班优先闭合）\n\n" + "\n".join(lines))
        if failed:
            out.append("## 执行失败\n\n" + "\n".join(
                f"- {t['id']} {t['kind']}：{t.get('error', '')[:200]}" for t in failed))
        if queue:
            out.append("## 队列中等待的任务\n\n" + "\n".join(
                f"- {t['id']} {t['kind']}：{t['goal']}" for t in queue))

        commits = self.store.log(since=shift.get("head_start")) if shift.get("head_start") else []
        if commits:
            out.append("## 本班 State 变更\n\n" + "\n".join(
                f"- `{c['sha']}` {c['author']}：{c['subject']}" for c in reversed(commits[:60])))

        waiting = []
        for d in discussion.list_all(self.store):
            if d.get("status") == "open" and d.get("awaiting_reply"):
                waiting.append(f"- {d['id']}「{d.get('title')}」最后一轮是研究者的发言，尚未回复")
        open_r = reviews.list_all(self.store, "open")
        if open_r:
            waiting.append(f"- 待重新审视 {len(open_r)} 项：" + "、".join(
                f"{r['target']}（因 {r['trigger']}）" for r in open_r[:20]))
        pend = candidates.list_all(self.store, "pending")
        if pend:
            waiting.append(f"- 候选区有 {len(pend)} 条待确认：" + "、".join(c["id"] for c in pend[:20]))
        out.append("## 待处理\n\n" + ("\n".join(waiting) or "（无）"))
        return "\n\n".join(out) + "\n"


_CLOSED_LABEL = {"answered": "已回答", "decided": "已拍板", "merged": "已合并"}


def _as_list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def _parent(m, sep="，"):
    return f"{sep}母问题 {m['parent']}" if m.get("parent") else ""


def _stmt(body):
    """对象正文去掉标题、来由注记与状态史各节（## 结 / ## 撤下 ……），只留陈述。"""
    from .objects import statement_of
    return statement_of(body)


def _rev(meta, sep="，"):
    r = schema.revision_of(meta)
    return f"{sep}第 {r} 版" if r > 1 else ""


def _one(text, n):
    s = " ".join((text or "").split())
    return s[:n] + ("…" if len(s) > n else "")


def _pct(x):
    return "未知" if x is None else f"{round(float(x) * 100)}%"
