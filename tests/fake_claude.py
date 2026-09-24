#!/usr/bin/env python3
"""测试用的假 claude：按 FAKE_MODE 输出与真实 stream-json 同形的事件。

reply          读 briefing.md，回复里带上它看到的交接与摘要（证明“从 briefing 接上”）
sleep          吐一半回复后挂住，等着被 SIGKILL
exhaust        rate_limit_event status=rejected，随后 is_error 的 result
distill        真的启动 MCP server，提交一个候选并更新摘要
distill_sleep  提交一个候选、记一条 checkpoint 后挂住
distill_resolve 聚焦讨论的蒸馏：提一条 insight 候选，再提一条引用它（C###）结掉聚焦问题的 resolve 候选
整理任务（协议里有“整理（Tidy up）”时自动进入）：把第 10 章的前两条理解提成合并候选；
               FAKE_TIDY=blank（或 run/fake_mode 为 tidy_blank）交白卷；rewrite / tidy_rewrite 把第一条理解改写成独立表述
judge          按 briefing 里的任务种类驱动 Phase 2 的 MCP 工具（检索 / 精读 / 评估 / 扫描 / 接地）。
               FAKE_BAD_EDIT=1 时精读任务会越界改论文的 title（应被 runner 回滚）
自演进（协议里有“做一次**自演进**”时自动进入）：最终回复是一篇演进文档；FAKE_EVOLVE=sleep / empty
"""
import json
import os
import re
import subprocess
import sys
import time


def emit(ev):
    sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def arg(name):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else None


def rate(status="allowed", resets=None):
    resets = resets or int(time.time()) + 3600
    emit({"type": "rate_limit_event", "rate_limit_info": {
        "status": status, "resetsAt": resets, "rateLimitType": "five_hour",
        "unifiedWindows": {
            "five_hour": {"utilization": float(os.environ.get("FAKE_5H", "0.1")), "resetsAt": resets},
            "seven_day": {"utilization": float(os.environ.get("FAKE_7D", "0.2")),
                          "resetsAt": int(time.time()) + 86400}}}})


def text(t):
    emit({"type": "stream_event", "event": {"type": "message_start"}})
    for i in range(0, len(t), 8):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                                "delta": {"type": "text_delta", "text": t[i:i + 8]}}})


def result(t, error=False):
    emit({"type": "result", "subtype": "success" if not error else "error_during_execution",
          "is_error": error, "result": t, "total_cost_usd": 0.01, "usage": {}})


class Mcp:
    def __init__(self):
        cfg = json.load(open(arg("--mcp-config")))["mcpServers"]["state"]
        self.p = subprocess.Popen([cfg["command"], *cfg["args"]], env=dict(os.environ, **cfg["env"]),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0
        self.req("initialize", {})

    def req(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def call(self, name, **args):
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"tu{self.n}", "name": f"mcp__state__{name}", "input": args}]}})
        r = self.req("tools/call", {"name": name, "arguments": args})["result"]
        if r.get("isError"):
            raise SystemExit("MCP 调用失败：" + r["content"][0]["text"])
        return r["content"][0]["text"]


def main():
    mode = os.environ.get("FAKE_MODE", "reply")
    override = os.path.join(os.environ.get("AR_ROOT", ""), "run", "fake_mode")
    if os.environ.get("AR_ROOT") and os.path.exists(override):   # 端到端手测时不重启 serve 就能切模式
        mode = open(override).read().strip() or mode
    prompt = arg("-p") or ""
    emit({"type": "system", "subtype": "init", "tools": (arg("--tools") or "").split(","),
          "session_id": arg("--session-id")})
    brief = open("briefing.md", encoding="utf-8").read() if os.path.exists("briefing.md") else ""

    if mode == "exhaust":
        rate("rejected", int(time.time()) + int(os.environ.get("FAKE_RESET_IN", "2")))
        result("Claude usage limit reached", error=True)
        return
    rate()

    if "做一次**自演进**" in (arg("--append-system-prompt") or ""):
        evolve(brief)
        return

    if "整理（Tidy up）" in (arg("--append-system-prompt") or ""):
        tidy(brief, blank=mode == "tidy_blank" or os.environ.get("FAKE_TIDY") == "blank",
             rewrite=mode == "tidy_rewrite" or os.environ.get("FAKE_TIDY") == "rewrite")
        return

    if mode in ("reply", "sleep"):
        handoff = re.search(r"## 2\. 上一班交接（(HO\d+)", brief)
        summ = re.search(r"### 第 1–(\d+) 轮摘要\n\n(.+)", brief)
        reply = ("我接着上次的讨论说。"
                 + (f"[交接:{handoff.group(1)}]" if handoff else "[交接:无]")
                 + (f"[摘要至:{summ.group(1)}:{summ.group(2)[:40]}]" if summ else "[摘要:无]")
                 + ("[补答]" if "被切断" in prompt else "")
                 + (f"[聚焦:{f.group(1)}]" if (f := re.search(r"## 2b\. 聚焦对象：(\S+?)（", brief)) else ""))
        text(reply[: len(reply) // 2])
        if mode == "sleep":
            time.sleep(120)
        text(reply)
        result(reply)
        return

    if mode == "judge" or (mode == "reply" and "证据评判" in (arg("--append-system-prompt") or "")):
        judge(brief, prompt)
        return

    if mode == "distill_revise":
        # 聚焦讨论的蒸馏：针对聚焦对象提一条修订候选（同级打磨，成熟度只作建议）
        m = re.search(r"讨论 (DS\d+) 的第 (\d+)–(\d+) 轮", prompt)
        ds, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        f = re.search(r"## 2b\. 聚焦对象：(\S+?)（(\w+)，第 (\d+) 版）", brief)
        mcp = Mcp()
        if f:
            mcp.call("propose_candidate", kind="revision", target=f.group(1), base_revision=int(f.group(3)),
                     statement=os.environ.get("FAKE_REVISION", "打磨后的表述：边界更清楚了。"),
                     rationale="第 %d 轮把“不研究什么”说清楚了，这一版比上一版边界更明确。" % lo,
                     origin="human", source=ds, turns=[lo], maturity="vague")
        mcp.call("update_discussion_summary", discussion_id=ds,
                 summary=f"打磨 {f.group(1) if f else '?'}。覆盖到第 {hi} 轮。", covers_through=hi)
        result("提了 1 条修订。")
        return

    if mode == "distill_resolve":
        m = re.search(r"讨论 (DS\d+) 的第 (\d+)–(\d+) 轮", prompt)
        ds, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        f = re.search(r"## 2b\. 聚焦对象：(Q\d+)（", brief)
        mcp = Mcp()
        out = mcp.call("propose_candidate", kind="insight", statement="CoT 只需几何子目标，不需要语言。",
                       rationale="第 %d 轮聊透了" % lo, origin="human", source=ds, turns=[lo],
                       firmness="working", basis=[ds])
        cid = re.search(r"候选 (C\d+)", out).group(1)
        mcp.call("propose_candidate", kind="resolve", target=f.group(1), resolution="answered",
                 answered_by=[cid], statement=f"{f.group(1)} 已被 {cid} 回答。",
                 rationale="第 %d 轮研究者明确说“这个问题就这样了”" % lo, origin="human", source=ds, turns=[lo])
        mcp.call("update_discussion_summary", discussion_id=ds, summary=f"聊透了，覆盖到第 {hi} 轮。",
                 covers_through=hi)
        result("提了 1 条理解与 1 条结问题。")
        return

    if mode in ("distill", "distill_sleep"):
        m = re.search(r"讨论 (DS\d+) 的第 (\d+)–(\d+) 轮", prompt)
        ds, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        mcp = Mcp()
        if "C001" not in brief:     # 被切断重试时，从 briefing 看到已提交过就不重复
            mcp.call("propose_candidate", kind="assumption", statement="感知不是精细操作的主要瓶颈",
                     rationale="被用来排除感知方向但无人安排验证", origin="human", source=ds,
                     turns=[lo], relied_on_by=["Q001"])
        mcp.call("checkpoint", note="已提交 C001，下一步更新摘要")
        if mode == "distill_sleep":
            time.sleep(120)
        mcp.call("update_discussion_summary", discussion_id=ds,
                 summary=f"研究者认为感知不是瓶颈（C001）。覆盖到第 {hi} 轮。", covers_through=hi)
        result("收了 1 条 assumption。")
        return


def tidy(brief, blank=False, rewrite=False):
    mcp = Mcp()
    ch = brief.split("## 10. 待整理的全部内容", 1)[-1]
    ins = re.findall(r"^#### (IN\d+)", ch, re.M)
    if rewrite and ins:
        mcp.call("propose_candidate", kind="revision", target=ins[0], base_revision=1,
                 statement="接口宜在时间上稀疏、信息上稠密：上层隔一段时间才下发一次指导，但每次给出足够完整的几何目标。",
                 rationale="原文用了“上面那个方案”，离开讨论读不懂；只改措辞")
        result("改写了 1 条理解。")
        return
    if blank or len(ins) < 2:
        result("看过了全部理解与问题，没有值得合并或结掉的。")
        return
    mcp.call("propose_candidate", kind="insight", supersedes=ins[:2], firmness="working",
             statement=f"合并 {ins[0]} 与 {ins[1]}：接口在时间上稀疏、信息上稠密。",
             rationale="两条在说同一件事的两面")
    mcp.call("checkpoint", note="提了 1 条合并")
    result("提了 1 条合并。")


def evolve(brief):
    """自演进（§5.18）：最终回复就是文档。FAKE_EVOLVE=sleep 时记一条进度后挂住（等着被切断），empty 时回复为空。"""
    mcp = Mcp()
    seed = re.search(r"- 出发点：\*\*(\w+)\*\*", brief).group(1)
    prev = re.search(r"### 最近一篇 (EV\d+)", brief)
    resumed = "本任务之前被切断过" in brief
    mcp.call("checkpoint", note=f"从 {seed} 出发，先拆问法")
    if os.environ.get("FAKE_EVOLVE") == "sleep" and not resumed:
        time.sleep(120)
    if os.environ.get("FAKE_EVOLVE") == "empty":
        result("")
        return
    doc = (f"# {seed} 的三种问法\n\n## 出发点\n\n{seed} 还很模糊。"
           + (f"[接着:{prev.group(1)}]" if prev else "[第一次]") + ("[续写]" if resumed else "")
           + "\n\n## 值得记下的想法\n\n- 接口的问题其实是“谁对接触负责”的问题。\n\n## 还没想通的\n\n- 怎么衡量。")
    result(doc)


def edit(path, old, new, tid="edit1"):
    """模拟内置 Edit：发 tool_use → 改文件 → 发 tool_result（runner 据此提交或回滚）。"""
    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Edit", "input": {"file_path": path}}]}})
    t = open(path, encoding="utf-8").read()
    open(path, "w", encoding="utf-8").write(t.replace(old, new, 1))
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}})


def judge(brief, prompt):
    kind = re.search(r"- 任务：`T\d+`（(\w+)）", brief).group(1)
    target = (re.search(r"本次目标：\*\*([HAI]\d+)\*\*", brief) or [None, None])[1]
    paper = (re.search(r"本次论文：\*\*(P\d+)\*\*", brief) or [None, None])[1]
    state = arg("--add-dir")
    mcp = Mcp()
    if kind == "lit_search":
        mcp.call("search_papers", query="contact rich manipulation feedback")
        ref = os.environ.get("FAKE_REF_" + (target or "X"), "2401.00001")
        mcp.call("register_paper", ref=ref, why="检验目标", for_targets=[target] if target else [])
        mcp.call("checkpoint", note="登记完毕")
        result("登记了 1 篇")
    elif kind == "read_paper":
        opened = mcp.call("open_paper", paper_id=paper)
        pf = os.path.join(state, "papers", f"{paper}.md")
        edit(pf, "（尚未精读。）", "问题：数据规模还是反馈频率。结论：固定低频 chunk 饱和。")
        if os.environ.get("FAKE_BAD_EDIT"):
            t = open(pf, encoding="utf-8").read()
            edit(pf, re.search(r"^title: (.+)$", t, re.M).group(1), "Tampered Title", "edit2")
        if "只有摘要" in opened:
            mcp.call("request_paper", paper_id=paper, why="需要实验细节", blocking=True)
            mcp.call("checkpoint", note="等全文")
            result("已请求全文，挂起")
            return
        lib = os.path.join(os.path.dirname(state), "library", paper, "anchors.json")
        anchors = json.load(open(lib, encoding="utf-8"))
        a = next(k for k in anchors if not k.startswith(("abstract", "fig", "tab")) and
                 ("plateau" in anchors[k] or "88 percent" in anchors[k] or k.endswith("-p1")))
        mcp.call("record_evidence", target_id=target, stance=os.environ.get("FAKE_STANCE", "contradict"),
                 source=paper, locator=[a], quote=anchors[a][:120], note="实验显示饱和",
                 strength="strong")
        mcp.call("checkpoint", note="证据已记")
        result("记了 1 条证据")
    elif kind == "assess":
        ev_dir = os.path.join(state, "evidence")
        ids = sorted(f[:-3] for f in os.listdir(ev_dir)
                     if re.search(rf"^target: {target}$", open(os.path.join(ev_dir, f)).read(), re.M))
        if target.startswith("H"):
            mcp.call("transition_hypothesis", hypothesis_id=target,
                     new_status=os.environ.get("FAKE_VERDICT", "refuted"), evidence_ids=ids,
                     rationale="全文实验直接反驳")
        else:
            mcp.call("examine_assumption", assumption_id=target, verdict="holds", evidence_ids=ids,
                     note="文献支持")
        result("评估完毕")
    elif kind == "grounding":
        mcp.call("annotate_grounding", target_id=target, verdict="novel", note="没找到先例")
        result("接地完毕")
    else:
        result("没有发现冲突")


if __name__ == "__main__":
    main()
