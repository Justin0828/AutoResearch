"""Briefing / Handoff 装配器（DESIGN.md §5.2）。

一个装配器、两种用途：给 agent 的 briefing.md，以及班次结束时落进 State 的
交接记录。交接记录必须能在**没有 agent** 的情况下生成（窗口被切断时恰恰
没有额度），所以这里只做机械装配，不调用任何模型。
"""
import re

from . import candidates, discussion, frontmatter, reviews, schema

PROFILES = {
    # redact: 是否剥离 origin；sections: 章节编号（见 §5.2 表）
    "discuss": {"redact": False, "sections": range(1, 12)},
    "distill": {"redact": False, "sections": range(1, 12)},
    "judge": {"redact": True, "sections": [*range(1, 9), 11]},
}

BUDGET = {  # 每章字符预算；dead-end、任务、交接、问题不截断
    4: 8000, 5: 12000, 6: 8000, 8: 4000, 9: 6000, 10: 24000,
}
DISTILL_DISCUSSION_BUDGET = 80000

MODE_RULES = {
    "discussion": "当前是**讨论模式**：研究者主导。你不得自主发起验证、实验或方向变更；"
                  "只允许为当下讨论做低成本的文献查证。讨论中出现的研究内容只能进候选区，"
                  "由研究者确认后才成为正式 State 对象。",
    "incubation": "当前是**自演进模式**：检索关闭，从冻结的基本盘推演。",
    "validation": "当前是**验证模式**。",
}

# 评判类任务的署名线索规范化（§5.2 judge 规则 2）。屏蔽不可能完美，偏差指标兜底。
_ATTRIBUTION = [
    (r"(研究者|人类?|用户|我们|我|你|AI|agent|模型|系统)\s*(最早|首先|先)?\s*(认为|觉得|提出|怀疑|倾向于|猜测|主张)",
     "有观点\\3"),
    (r"(由|经)\s*(研究者|人类?|用户|AI|agent|模型)\s*(提出|确认)", "被\\3"),
    (r"\borigin\s*[:：]\s*(human|ai|unclear)\b", ""),
    (r"（经候选 C\d+ 确认，出自讨论 DS\d+ 第 [\d, ]+ 轮。）", ""),
]


def neutralize(text):
    for pat, rep in _ATTRIBUTION:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


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
        if task.get("expected"):
            lines.append(f"- 预期产出：{task['expected']}")
        lines.append(f"- {MODE_RULES.get(mode, '')}")
        if task.get("prior_checkpoints"):
            lines.append("\n**本任务之前被中断过**，上次尝试留下的进度笔记（从这里接着做，"
                         "不要重复已完成的部分；已提交的候选见“候选区”一章）：")
            lines += [f"  - {c['ts']} {c['note']}" for c in task["prior_checkpoints"]]
        return "## 1. 本次任务\n\n" + "\n".join(lines)

    def s_handoff(self):
        d = self.store.state / "handoffs"
        files = sorted(d.glob("HO*.md"), key=lambda p: schema.split_id(p.stem)[1] or 0) \
            if d.is_dir() else []
        if not files:
            return "## 2. 上一班交接\n\n（这是第一个班次，没有交接记录。）"
        meta, body = frontmatter.parse(files[-1].read_text(encoding="utf-8"))
        return (f"## 2. 上一班交接（{meta.get('id')} · {meta.get('reason')} · "
                f"结束于 {meta.get('ended')}）\n\n{body.strip()}")

    def s_question(self, redact):
        pmeta, pbody = self.store.project()
        qid = pmeta.get("main_question")
        qmeta, qbody = self.store.read_obj(qid) if qid else (None, None)
        out = [f"## 3. 研究问题\n\n**项目**：{pmeta.get('title', '')}\n\n{self._body(pbody, redact)}"]
        if qmeta:
            out.append(f"### {qid}（成熟度 {qmeta.get('maturity')}）\n\n{self._body(qbody, redact)}")
        others = [(m, b) for m, b in self.store.list("question") if m.get("id") != qid]
        for m, b in others:
            out.append(f"### {m['id']}（成熟度 {m.get('maturity')}）\n\n{self._body(b, redact)}")
        return "\n\n".join(out)

    def s_assumptions(self, redact):
        items = self.store.list("assumption")
        items.sort(key=lambda x: (x[0].get("status") != "unexamined", x[0]["id"]))
        if not items:
            return "## 4. 前提（Assumption）\n\n（暂无。）"
        parts = []
        for m, b in items:
            head = (f"### {m['id']} · {_fm_line(m, ['status', 'relied_on_by', 'fragile', 'promoted_to'])}"
                    f"{self._origin(m['id'], redact)}")
            body = self._body(b, redact)
            parts.append((f"{head}\n\n{body}", f"{head} — {body.splitlines()[0] if body else ''}"))
        return "## 4. 前提（Assumption）\n\n" + _clip(parts, BUDGET[4], "assumptions/")

    def s_hypotheses(self, redact):
        items = self.store.list("hypothesis")
        if not items:
            return "## 5. 假设（Hypothesis）\n\n（暂无。）"
        parts = []
        for m, b in items:
            head = (f"### {m['id']} · {_fm_line(m, ['status', 'confidence', 'evidence', 'promoted_from', 'group'])}"
                    f"{self._origin(m['id'], redact)}")
            full = (f"{head}\n\n- 证伪条件：{m.get('falsifier', '')}\n- 验证安排：{m.get('validation', '')}"
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
            head = f"- **{m['id']}** → {m.get('hypothesis')} · {m.get('stance')} · {m.get('strength')} · 出处 {m.get('source')}"
            parts.append((f"{head}：{self._body(b, redact)}", head))
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
        items = [(m, b) for m, b in self.store.list("uncertainty") if m.get("status") != "resolved"]
        if not items:
            return "## 8. 未决不确定性\n\n（暂无。）"
        order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda x: order.get(x[0].get("importance"), 3))
        parts = []
        for m, b in items:
            head = f"### {m['id']} · {_fm_line(m, ['status', 'importance'])}{self._origin(m['id'], redact)}"
            body = self._body(b, redact)
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
            head = f"- **{c['id']}**（待确认 · {c['kind']} · 来自 {c['source']} 第 {_v(c.get('turns'))} 轮）"
            parts.append((f"{head}：{c['statement']}", head))
        for c in rejected:
            head = f"- **{c['id']}**（已丢弃 · {c['kind']}）"
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

    # ---------------------------------------------------------------- 装配

    def briefing(self, profile, task):
        p = PROFILES[profile]
        redact = p["redact"]
        ds = task.get("discussion")
        disc_budget = DISTILL_DISCUSSION_BUDGET if profile == "distill" else BUDGET[10]
        builders = {
            1: lambda: self.s_task(task),
            2: self.s_handoff,
            3: lambda: self.s_question(redact),
            4: lambda: self.s_assumptions(redact),
            5: lambda: self.s_hypotheses(redact),
            6: lambda: self.s_evidence(redact),
            7: lambda: self.s_dead_ends(redact),
            8: lambda: self.s_uncertainties(redact),
            9: self.s_candidates,
            10: lambda: self.s_discussion(ds, disc_budget),
            11: self.s_reviews,
        }
        head = ("# Briefing\n\n这是你本次任务的全部上下文，由 Research State 装配而来。"
                "你没有任何先前的记忆——这里写的就是研究至今的全部共识；"
                "细节可以去 State 仓库读原文件。")
        return "\n\n".join([head] + [builders[i]() for i in p["sections"]]) + "\n"

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


def _pct(x):
    return "未知" if x is None else f"{round(float(x) * 100)}%"
