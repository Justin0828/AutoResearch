"""Task 契约与任务台账（DESIGN.md §5.3）。

台账在 run/tasks/T#####/，每任务一个目录，同时是 agent 的 cwd。
task.json 用 tmp + rename 原子写，进程被 SIGKILL 也不会留下半个文件。
"""
import fcntl
import json
import os
import re

from . import protocol
from .store import now

# 永远禁用的内置工具（deny-list 兜底；真正的移除靠 --tools，§5.3）
ALWAYS_DENY = ["Bash", "Task", "Agent", "NotebookEdit", "ToolSearch", "Workflow", "Skill",
               "CronCreate", "CronDelete", "RemoteTrigger", "Monitor", "SendMessage",
               "EnterWorktree", "ExitWorktree", "PushNotification", "ScheduleWakeup",
               "Write", "Edit"]

KINDS = {
    "discuss_turn": {
        "lane": "interactive", "profile": "discuss", "timeout": 900,
        "tools": ["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
        "mcp": ["check_dead_ends", "propose_candidate", "checkpoint"],
        "protocol": protocol.DISCUSS_PROTOCOL,
        "expected": "对研究者的一轮回复（原样写入讨论记录）",
    },
    "distill": {
        "lane": "background", "profile": "distill", "timeout": 1200,
        "tools": ["Read", "Grep", "Glob"],
        "mcp": ["check_dead_ends", "propose_candidate", "update_discussion_summary",
                "checkpoint"],
        "protocol": protocol.DISTILL_PROTOCOL,
        "expected": "候选对象（进候选区）+ 推进到最新一轮的滚动摘要",
    },
}

# 评判类任务的路径封读（§5.2 judge 规则 3），Phase 2 的 judge 任务使用
JUDGE_DENY_PATHS = ["provenance.json", "discussions/**", "candidates/**", "handoffs/**"]

STATUSES = {"queued", "running", "done", "failed", "interrupted", "blocked_on_human",
            "cancelled"}


def deny_rules(kind, state_dir):
    spec = KINDS[kind]
    rules = [t for t in ALWAYS_DENY if t not in spec["tools"]]
    if spec["profile"] == "judge":
        for p in JUDGE_DENY_PATHS:
            for tool in ("Read", "Grep", "Glob"):
                rules.append(f"{tool}(/{state_dir}/{p})")   # //abs 表示文件系统绝对路径
    return rules


class Ledger:
    def __init__(self, cfg):
        self.dir = cfg.tasks
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = cfg.run / "ledger.lock"

    def _next_id(self):
        n = [int(m.group(1)) for f in os.listdir(self.dir) if (m := re.match(r"^T(\d+)$", f))]
        return f"T{(max(n) + 1) if n else 1:05d}"

    def create(self, kind, goal, **fields):
        if kind not in KINDS:
            raise ValueError(kind)
        with open(self._lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            tid = self._next_id()
            (self.dir / tid).mkdir()
        spec = KINDS[kind]
        task = {"id": tid, "kind": kind, "lane": spec["lane"], "profile": spec["profile"],
                "goal": goal, "expected": spec["expected"], "status": "queued",
                "attempts": 0, "created": now(), "started": None, "ended": None,
                "shift": None, "session_id": None, "result": None, "error": None,
                "priority": 5, **fields}
        self.save(task)
        return task

    def path(self, tid):
        return self.dir / tid

    def save(self, task):
        p = self.dir / task["id"] / "task.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(task, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    def get(self, tid):
        p = self.dir / tid / "task.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def all(self):
        out = []
        for d in sorted(self.dir.glob("T*")):
            t = self.get(d.name)
            if t:
                out.append(t)
        return out

    def checkpoints(self, tid):
        p = self.dir / tid / "checkpoints.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
