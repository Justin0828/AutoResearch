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
        "mcp": ["check_dead_ends", "propose_candidate", "note_prep_request", "checkpoint"],
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

_J = {"lane": "background", "profile": "judge"}
_JUDGE = {
    "lit_search": {**_J, "timeout": 1200, "tools": ["Read", "Grep", "Glob", "WebSearch"],
                   "mcp": ["search_papers", "register_paper", "check_dead_ends", "checkpoint"],
                   "expected": "为目标登记 0–6 篇最能检验它的真实论文"},
    "read_paper": {**_J, "timeout": 2400, "tools": ["Read", "Grep", "Glob", "Edit"],
                   "writable": ["papers/"],
                   "mcp": ["open_paper", "record_evidence", "request_paper", "register_paper",
                           "check_dead_ends", "checkpoint"],
                   "expected": "阅读笔记（papers/P###.md）+ 追到段落的证据"},
    "assess": {**_J, "timeout": 1200, "tools": ["Read", "Grep", "Glob"],
               "mcp": ["transition_hypothesis", "examine_assumption", "invalidate_assumption",
                       "propose_candidate", "check_dead_ends", "checkpoint"],
               "expected": "据全部证据决定目标的状态（证据不够就不动）"},
    "contradiction_scan": {**_J, "timeout": 1200, "tools": ["Read", "Grep", "Glob"],
                           "mcp": ["propose_candidate", "checkpoint"],
                           "expected": "证据之间的冲突 → 不确定性候选（没有就说没有）"},
    "grounding": {**_J, "timeout": 1800, "tools": ["Read", "Grep", "Glob", "WebSearch"],
                  "mcp": ["search_papers", "register_paper", "open_paper", "annotate_grounding",
                          "check_dead_ends", "checkpoint"],
                  "expected": "接地结论（只标注，不改写被核查对象）"},
}
# 对 Idea 的接地（§5.12）：复用 GR 与 annotate_grounding；另可记证据（下一轮基本盘的唯一来源）
_JUDGE["ground_idea"] = {**_J, "timeout": 1800, "tools": ["Read", "Grep", "Glob", "WebSearch"],
                         "mcp": ["search_papers", "register_paper", "open_paper", "record_evidence",
                                 "annotate_grounding", "check_dead_ends", "checkpoint"],
                         "expected": "接地结论（只标注，不改写 Idea）+ 追到段落的反驳证据（若有）"}
for _k, _v in _JUDGE.items():
    _v["protocol"] = protocol.JUDGE_PROTOCOL + protocol.JUDGE_ROLES[_k]
KINDS.update(_JUDGE)

# 自演进的推演链（§5.9 / §5.12）：检索物理关闭。sealed = 不挂任何目录 + --restricted，
# 文件读取被关进只有 briefing 的任务目录（Phase 2.5 spike：裸 Read 可读宿主机任意文件）。
KINDS["incubate"] = {
    "lane": "background", "profile": "incubate", "timeout": 1800, "sealed": True,
    "tools": ["Read", "Grep", "Glob"],
    "mcp": ["check_dead_ends", "record_idea", "checkpoint"],
    "protocol": protocol.INCUBATE_PROTOCOL,
    "expected": "0–2 条 Idea（交白卷合法）",
}
ALL_MCP = ["check_dead_ends", "search_papers", "register_paper", "open_paper", "record_evidence",
           "transition_hypothesis", "examine_assumption", "invalidate_assumption", "request_paper",
           "annotate_grounding", "log_decision", "propose_candidate", "update_discussion_summary",
           "note_prep_request", "record_idea", "checkpoint"]

# 评判类任务的路径封读（§5.2 judge 规则 3、§5.6）。
# .git/** 由 Phase 2 起手 spike 发现：提交信息（COMMIT_EDITMSG、logs/HEAD）可直接 Read，
# 会带出候选确认、归属裁定一类记录（spike/phase2/README.md）。
# insights/**：评判看不到理解（避免锚定）；decisions/**：人推翻前提、选批次的决定都是权威线索。
JUDGE_DENY_PATHS = ["provenance.json", "discussions/**", "candidates/**", "handoffs/**", ".git/**",
                    "insights/**", "decisions/**"]

# 可写任务（Edit）在 State 里只能写 spec["writable"]；其余路径一律 deny（runner 另有回滚兜底）
STATE_ENTRIES = ["project.md", "provenance.json", "README.md", ".gitignore", "questions/**",
                 "assumptions/**", "hypotheses/**", "evidence/**", "papers/**", "dead-ends/**",
                 "uncertainties/**", "insights/**", "decisions/**", "candidates/**",
                 "discussions/**", "handoffs/**", "reviews/**", "groundings/**", "requests/**",
                 "experiments/**", "ideas/**", "foundations/**", "chains/**", ".git/**"]

STATUSES = {"queued", "running", "done", "failed", "interrupted", "blocked_on_human",
            "cancelled"}


def deny_rules(kind, state_dir):
    spec = KINDS[kind]
    rules = [t for t in ALWAYS_DENY if t not in spec["tools"]]
    if spec.get("sealed"):
        # 纵深：--tools 已移除、MCP 未注册、--restricted 已关进 cwd，这里再显式禁一遍
        rules += [t for t in ("WebSearch", "WebFetch") if t not in spec["tools"]]
        rules += [f"mcp__state__{t}" for t in ALL_MCP if t not in spec["mcp"]]
        library = f"{state_dir}".rsplit("/", 1)[0] + "/library"
        for base in (state_dir, library):
            for tool in ("Read", "Grep", "Glob"):
                rules.append(f"{tool}(/{base}/**)")
    if spec["profile"] == "judge":
        for p in JUDGE_DENY_PATHS:
            for tool in ("Read", "Grep", "Glob"):
                rules.append(f"{tool}(/{state_dir}/{p})")   # //abs 表示文件系统绝对路径
    if "Edit" in spec["tools"]:
        ok = tuple(spec.get("writable", ()))
        for p in STATE_ENTRIES:
            if not p.startswith(ok):
                rules.append(f"Edit(/{state_dir}/{p})")
    return rules


def allow_rules(kind, state_dir):
    """--allowedTools：免批准白名单。Edit 只对 writable 路径免批准——dontAsk 模式下其余一律拒绝。"""
    spec = KINDS[kind]
    out = []
    for t in spec["tools"]:
        if t == "Edit":
            out += [f"Edit(/{state_dir}/{w}**)" for w in spec.get("writable", ())]
        else:
            out.append(t)
    return out + [f"mcp__state__{t}" for t in spec["mcp"]]


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
