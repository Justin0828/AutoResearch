#!/usr/bin/env python3
"""测试用的假 claude：按 FAKE_MODE 输出与真实 stream-json 同形的事件。

reply          读 briefing.md，回复里带上它看到的交接与摘要（证明“从 briefing 接上”）
sleep          吐一半回复后挂住，等着被 SIGKILL
exhaust        rate_limit_event status=rejected，随后 is_error 的 result
distill        真的启动 MCP server，提交一个候选并更新摘要
distill_sleep  提交一个候选、记一条 checkpoint 后挂住
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
    prompt = arg("-p") or ""
    emit({"type": "system", "subtype": "init", "tools": (arg("--tools") or "").split(","),
          "session_id": arg("--session-id")})
    brief = open("briefing.md", encoding="utf-8").read() if os.path.exists("briefing.md") else ""

    if mode == "exhaust":
        rate("rejected", int(time.time()) + int(os.environ.get("FAKE_RESET_IN", "2")))
        result("Claude usage limit reached", error=True)
        return
    rate()

    if mode in ("reply", "sleep"):
        handoff = re.search(r"## 2\. 上一班交接（(HO\d+)", brief)
        summ = re.search(r"### 第 1–(\d+) 轮摘要\n\n(.+)", brief)
        reply = ("我接着上次的讨论说。"
                 + (f"[交接:{handoff.group(1)}]" if handoff else "[交接:无]")
                 + (f"[摘要至:{summ.group(1)}:{summ.group(2)[:40]}]" if summ else "[摘要:无]")
                 + ("[补答]" if "被切断" in prompt else ""))
        text(reply[: len(reply) // 2])
        if mode == "sleep":
            time.sleep(120)
        text(reply)
        result(reply)
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


if __name__ == "__main__":
    main()
