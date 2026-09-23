"""M3 Agent Execution Layer：把一个任务变成一次真实的 `claude -p` 执行。

旗标组合由 spike/phase0/run_trial.py 长成（15 个 trial 零越界工具），并加了三层禁用
（§5.3）：--tools 真移除内置工具、--disallowedTools 兜底、MCP server 按 toolset 注册。
**不使用 --bare**（它不读 OAuth，会破坏复用订阅额度的前提）。
"""
import json
import os
import signal
import subprocess
import sys
import threading
import uuid

from . import frontmatter, schema
from .briefing import Assembler
from .config import CODE_ROOT
from .tasks import KINDS, allow_rules, deny_rules

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


class Outcome:
    def __init__(self):
        self.status = "failed"      # done / failed / interrupted
        self.result = ""
        self.error = None
        self.exhausted = None       # quota_5h / quota_7d / None
        self.resets = None
        self.cost = None
        self.usage = None
        self.session_id = None
        self.killed = False


class Runner:
    """执行一个任务。一个 Runner 对象对应一次 attempt，可被 kill()。"""

    def __init__(self, cfg, store, ledger, quota, bus):
        self.cfg, self.store, self.ledger, self.quota, self.bus = cfg, store, ledger, quota, bus
        self.proc = None
        self._killed = False

    # ------------------------------------------------------------ 准备

    def prepare(self, task, prompt):
        spec = KINDS[task["kind"]]
        tdir = self.ledger.path(task["id"])
        brief_task = dict(task)
        if task.get("attempts", 0) > 1:
            brief_task["prior_checkpoints"] = self.ledger.checkpoints(task["id"])
        (tdir / "briefing.md").write_text(
            Assembler(self.store).briefing(spec["profile"], brief_task), encoding="utf-8")
        (tdir / "prompt.txt").write_text(prompt, encoding="utf-8")
        mcp = tdir / "mcp.json"
        mcp.write_text(json.dumps({"mcpServers": {"state": {
            "command": sys.executable,
            "args": ["-m", "autoresearch.mcp_server"],
            "env": {"AR_ROOT": str(self.cfg.root), "AR_TASK": task["id"],
                    "AR_TASK_DIR": str(tdir), "AR_TOOLSET": ",".join(spec["mcp"]),
                    "AR_PROFILE": spec["profile"],
                    "PYTHONPATH": str(CODE_ROOT)},
        }}}, ensure_ascii=False, indent=1), encoding="utf-8")

        sid = str(uuid.uuid4())
        allowed = allow_rules(task["kind"], self.store.state)
        cmd = [
            self.cfg.claude_bin, "-p", prompt,
            "--output-format", "stream-json", "--include-partial-messages", "--verbose",
            "--session-id", sid,
            "--mcp-config", str(mcp), "--strict-mcp-config",
            "--tools", ",".join(spec["tools"]),
            "--allowedTools", *allowed,
            "--disallowedTools", *deny_rules(task["kind"], self.store.state),
            "--permission-mode", "dontAsk",
            "--setting-sources", "",
            "--no-session-persistence",
            "--add-dir", str(self.store.state),
        ]
        if spec["profile"] == "judge":
            cmd += ["--add-dir", str(self.cfg.library)]     # 论文全文（§5.5）
        cmd += ["--append-system-prompt", spec["protocol"]]
        if self.cfg.model:
            cmd += ["--model", self.cfg.model]
        return cmd, sid

    # ------------------------------------------------------------ 执行

    def kill(self):
        """SIGKILL 整个进程组——模拟 / 执行窗口切断。"""
        self._killed = True
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run(self, task, prompt, on_delta=None):
        out = Outcome()
        tdir = self.ledger.path(task["id"])
        cmd, sid = self.prepare(task, prompt)
        out.session_id = sid
        (tdir / "cmd.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
        timeout = KINDS[task["kind"]]["timeout"]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)   # 强制走订阅 OAuth，而不是意外走 API 计费

        with open(tdir / "stream.jsonl", "a", encoding="utf-8") as sf, \
                open(tdir / "stderr.txt", "a", encoding="utf-8") as ef:
            self.proc = subprocess.Popen(cmd, cwd=tdir, stdout=subprocess.PIPE, stderr=ef,
                                         stdin=subprocess.DEVNULL, text=True, env=env,
                                         start_new_session=True)
            timer = threading.Timer(timeout, self._timeout, args=(out,))
            timer.daemon = True
            timer.start()
            pending_writes = {}
            try:
                for line in self.proc.stdout:
                    sf.write(line)
                    sf.flush()
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._on_event(task, ev, out, on_delta, pending_writes)
                self.proc.wait()
            finally:
                timer.cancel()
                self.proc.stdout.close()

        if self._killed or out.killed:
            out.status = "interrupted"
            out.error = out.error or "进程被切断"
        elif out.exhausted:
            out.status = "interrupted"
            out.error = f"额度耗尽（{out.exhausted}）"
        elif out.status != "done" and not out.error:
            out.status = "interrupted" if self.proc.returncode in (-9, 137, None) else "failed"
            out.error = f"claude 退出码 {self.proc.returncode}，没有 result 事件"
        return out

    def _timeout(self, out):
        out.error = "超时"
        out.killed = True
        self.kill()

    def _on_event(self, task, ev, out, on_delta, pending_writes):
        t = ev.get("type")
        if t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            ex = self.quota.update(info)
            self.bus.publish("quota", self.quota.snapshot(), persist=False)
            if ex and info.get("status") not in (None, "allowed", "allowed_warning"):
                out.exhausted = ex
                out.resets = info.get("resetsAt")
        elif t == "stream_event" and on_delta:
            e = ev.get("event") or {}
            if e.get("type") == "message_start":
                on_delta(None)
            elif e.get("type") == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta":
                    on_delta(d.get("text", ""))
        elif t == "assistant":
            for b in _blocks(ev):
                if b.get("type") == "tool_use":
                    self.bus.publish("activity", {"task": task["id"], "tool": b.get("name"),
                                                  "input": _brief(b.get("input"))})
                    if b.get("name") in WRITE_TOOLS:
                        pending_writes[b.get("id")] = (b.get("input") or {}).get("file_path")
        elif t == "user":
            for b in _blocks(ev):
                if b.get("type") == "tool_result" and b.get("tool_use_id") in pending_writes:
                    self._commit_agent_write(task, pending_writes.pop(b["tool_use_id"]))
        elif t == "result":
            out.result = ev.get("result") or ""
            out.cost = ev.get("total_cost_usd")
            out.usage = ev.get("usage")
            if ev.get("is_error") or ev.get("subtype") != "success":
                out.status = "failed"
                out.error = (out.result or ev.get("subtype") or "error")[:2000]
                if ev.get("api_error_status") == 429 or "limit" in (out.result or "").lower():
                    out.exhausted = out.exhausted or "quota_5h"
            else:
                out.status = "done"

    def _commit_agent_write(self, task, path):
        """§5.4：agent 直接编辑文件时，看到 tool_result 即提交；越界则回滚并记违规。

        越界有三种：受保护路径（§5.1）、本任务 writable 之外的 State 路径、改了论文的
        身份 / 阅读状态字段（字段级受保护，§5.5）。deny 规则在执行层已经挡了一遍，这里是第二道。
        """
        if not path:
            return
        try:
            rel = os.path.relpath(os.path.realpath(path), os.path.realpath(self.store.state))
        except ValueError:
            return
        if rel.startswith(".."):
            return
        writable = tuple(KINDS[task["kind"]].get("writable", ()))
        with self.store.locked():
            dirty = self.store.git("status", "--porcelain", "--", rel).strip()
        if not dirty:
            # 执行层已拦下（deny 规则 / dontAsk），或写入与 HEAD 相同：没有要提交或回滚的
            if schema.is_protected(rel) or not rel.startswith(writable):
                self._violation(task, rel, "尝试越界写入，执行层已拦下", notify=False)
            return
        why = None
        if schema.is_protected(rel):
            why = "受保护路径"
        elif not rel.startswith(writable):
            why = "本任务不允许写这个路径"
        elif rel.startswith("papers/"):
            bad = self._paper_fields_changed(rel)
            if bad:
                # 字段级回滚：frontmatter 恢复成 HEAD 版本，笔记正文保留（agent 很快，runner 处理
                # 这条 tool_result 时，工作区里可能已经叠了它之后的合法笔记，整文件回滚会误伤）
                with self.store.locked():
                    hm, _ = frontmatter.parse(self.store.git("show", f"HEAD:{rel}"))
                    try:
                        _, body = frontmatter.parse((self.store.state / rel).read_text(encoding="utf-8"))
                    except frontmatter.FrontmatterError:
                        body = ""
                    (self.store.state / rel).write_text(frontmatter.dump(hm, body), encoding="utf-8")
                    self.store._commit([rel], f"agent({task['kind']}): 编辑 {rel}（越界字段已恢复）",
                                       "agent", task["id"])
                self._violation(task, rel, bad + "，已恢复这些字段，笔记保留")
                return
        if why:
            with self.store.locked():
                tracked = self.store.git("ls-files", "--", rel).strip()
                if tracked:
                    self.store.git("checkout", "--", rel)
                else:
                    (self.store.state / rel).unlink(missing_ok=True)
            self._violation(task, rel, why + "，整文件已回滚")
            return
        with self.store.locked():
            self.store._commit([rel], f"agent({task['kind']}): 编辑 {rel}", "agent", task["id"])

    def _violation(self, task, rel, why, notify=True):
        if notify:
            self.bus.notify("warn", f"{task['id']} edited {rel} out of bounds ({why}).", task=task["id"])
        with open(self.ledger.path(task["id"]) / "violations.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": rel, "why": why}, ensure_ascii=False) + "\n")

    def _paper_fields_changed(self, rel):
        old = self.store.git("show", f"HEAD:{rel}", check=False)
        if not old:
            return "论文只能经 register_paper 登记"
        try:
            om, _ = frontmatter.parse(old)
            nm, _ = frontmatter.parse((self.store.state / rel).read_text(encoding="utf-8"))
        except (frontmatter.FrontmatterError, OSError):
            return "frontmatter 无法解析"
        if nm is None:
            return "frontmatter 被删掉了"
        changed = [f for f in schema.PAPER_TOOL_FIELDS if (om or {}).get(f) != nm.get(f)]
        return f"改了由工具维护的字段 {changed}" if changed else None


def _blocks(ev):
    c = (ev.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _brief(inp):
    if not isinstance(inp, dict):
        return str(inp)[:200]
    for k in ("file_path", "pattern", "query", "url", "kind", "discussion_id", "note"):
        if k in inp:
            return f"{k}={str(inp[k])[:160]}"
    return json.dumps(inp, ensure_ascii=False)[:200]

