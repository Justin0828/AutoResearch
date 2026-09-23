"""M2 班次 daemon（Phase 1 子集）：派发任务、额度闸、暂停 / 交接 / 恢复。

系统不是连续运行的 daemon，而是一串离散的班次（§0.5）。一个班次从开班持续到
暂停（额度耗尽、窗口切断、人工暂停、daemon 停止或崩溃）；每次结束都机械生成
一份交接记录写进 State，下一班的所有 briefing 都带着它。

Phase 1 只有讨论类任务，且只由人触发——M2.0 的模式门在这里的体现就是：
daemon 从不自己找事做，只执行人发起的讨论回合、蒸馏，以及闭合上一班被截断的任务。
"""
import json
import threading
import time
import traceback

from . import discussion, frontmatter, protocol, schema
from .briefing import Assembler
from .quota import Quota
from .runner import Runner
from .store import now, today
from .tasks import Ledger

MAX_ATTEMPTS = 3


class Daemon:
    def __init__(self, cfg, store, bus):
        self.cfg, self.store, self.bus = cfg, store, bus
        self.ledger = Ledger(cfg)
        self.quota = Quota(cfg)
        self.lock = threading.RLock()
        self.runners = {}              # task id -> Runner
        self.lanes = {"interactive": None, "background": None}
        self.state_path = cfg.run / "daemon.json"
        self.shifts_path = cfg.run / "shifts.jsonl"
        self.st = self._load_state()   # {"shift": {...}|None, "pause": {...}|None, "n": int}
        self.replies = {}              # ds -> 正在流式生成的回复文本
        self._stop = threading.Event()
        self._threads = []

    # ------------------------------------------------------------ 持久状态

    def _load_state(self):
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"shift": None, "pause": None, "n": 0}

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------ 生命周期

    def start(self, background=True):
        self.recover()
        if background:
            for fn in (self._dispatch_loop, self._sweep_loop):
                t = threading.Thread(target=fn, daemon=True)
                t.start()
                self._threads.append(t)

    def recover(self):
        """启动时的恢复：上一次进程可能是被 SIGKILL 的。"""
        with self.lock:
            sha, paths = self.store.sweep("recovered: 上次进程遗留的未提交改动", "system")
            if paths:
                self.bus.notify("warn", f"启动时发现 {len(paths)} 个未提交的改动，已以 recovered 提交", sha=sha)
            for t in self.ledger.all():
                if t["status"] == "running":
                    t.update(status="interrupted", error="进程随 daemon 一起终止", ended=now())
                    self.ledger.save(t)
            sh = self.st.get("shift")
            if sh and not sh.get("ended"):
                self._end_shift("crash", "上一次 daemon 未正常退出")
            pause = self.st.get("pause")
            if pause and not pause.get("manual") and \
                    (not pause.get("resume_at") or pause["resume_at"] > time.time()):
                return          # 仍在额度暂停中，等 dispatch loop 到点开班
            if pause and pause.get("manual"):
                return
            self._start_shift()

    def stop(self):
        """daemon 正常停止：切断在飞任务（下一班会接上），写交接。"""
        self._stop.set()
        with self.lock:
            for r in list(self.runners.values()):
                r.kill()
        self._wait_drain(10)
        with self.lock:
            if self.st.get("shift") and not self.st["shift"].get("ended"):
                self._end_shift("manual", "daemon 停止")

    def _wait_drain(self, timeout):
        t0 = time.time()
        while self.runners and time.time() - t0 < timeout:
            time.sleep(0.1)

    # ------------------------------------------------------------ 班次

    def _start_shift(self):
        self.st["n"] = self.st.get("n", 0) + 1
        self.st["shift"] = {"id": f"SH{self.st['n']:04d}", "started": now(),
                            "head_start": self.store.head(), "ended": None}
        self.st["pause"] = None
        self._save_state()
        # M2.0b 第 1 级：闭合上一班被截断的任务
        requeued = []
        for t in self.ledger.all():
            if t["status"] == "interrupted":
                if t.get("attempts", 0) >= MAX_ATTEMPTS:
                    t.update(status="failed", error=f"连续 {MAX_ATTEMPTS} 次被切断，放弃")
                else:
                    t.update(status="queued", priority=1)
                    requeued.append(t["id"])
                self.ledger.save(t)
        # 人的话还没人回：补一个回复任务（覆盖“追加发言后、入队前”被切断的情形）
        for d in discussion.list_all(self.store):
            if d.get("status") == "open" and d.get("awaiting_reply"):
                self._enqueue_discuss(d["id"], priority=1)
        self.bus.publish("shift", self.shift_view())
        self.bus.notify("info", f"开班 {self.st['shift']['id']}" +
                        (f"，重新接上被截断的任务 {', '.join(requeued)}" if requeued else ""))

    def _end_shift(self, reason, detail=""):
        """结束当前班次并写交接记录。调用方持锁，且此时不应有在飞任务。"""
        sh = self.st.get("shift")
        if not sh or sh.get("ended"):
            return None
        sh.update(ended=now(), reason=reason, reason_detail=detail,
                  quota_end={"five_hour": self.quota.data.get("five_hour"),
                             "seven_day": self.quota.data.get("seven_day"),
                             "five_hour_resets": self.quota.snapshot().get("five_hour_resets_iso")})
        for t in self.ledger.all():   # 在飞的在此刻一律视为被截断
            if t["status"] == "running":
                t.update(status="interrupted", error=f"班次结束（{reason}）", ended=now())
                self.ledger.save(t)
        tasks = []
        for t in self.ledger.all():
            if t.get("shift") == sh["id"]:
                t = dict(t, checkpoints=self.ledger.checkpoints(t["id"]))
                tasks.append(t)
        queue = [t for t in self.ledger.all() if t["status"] == "queued"]
        body = Assembler(self.store).handoff(sh, tasks, queue)
        with self.store.tx(f"handoff: {sh['id']} 交接（{reason}）", actor="system") as tx:
            hid = self.store.next_id("HO", "handoffs")
            tx.write(f"handoffs/{hid}.md", frontmatter.dump(
                {"id": hid, "type": "handoff", "shift": sh["id"], "reason": reason,
                 "started": sh["started"], "ended": sh["ended"], "created": today()},
                f"# 班次交接 {sh['id']}\n\n{body}"))
        sh["handoff"] = hid
        with open(self.shifts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(sh, ensure_ascii=False) + "\n")
        self._save_state()
        self.bus.publish("shift", self.shift_view())
        self.bus.notify("warn" if reason != "normal" else "info",
                        f"班次 {sh['id']} 结束（{reason}{'：' + detail if detail else ''}），交接记录 {hid}",
                        handoff=hid)
        return hid

    def _pause(self, reason, resume_at=None, manual=False, detail=""):
        """停止派发。在飞任务排空后结束班次（7d 硬闸允许在飞任务完成提交，M2.1）。"""
        if self.st.get("pause"):
            return
        self.st["pause"] = {"reason": reason, "resume_at": resume_at, "manual": manual,
                            "since": now(), "detail": detail}
        self._save_state()
        when = time.strftime("%m-%d %H:%M", time.localtime(resume_at)) if resume_at else None
        msg = {"quota_5h": "5h 额度窗口用尽，已暂停",
               "quota_7d": f"周额度 ≥{round(self.cfg.weekly_stop * 100)}%，全面暂停（保护项目外的 Claude Code 使用）",
               "cutoff": "窗口被切断",
               "manual": "已人工暂停"}.get(reason, reason)
        if when:
            msg += f"，预计 {when} 恢复" + ("（自动开下一班）" if self.cfg.auto_resume and not manual else "")
        self.bus.notify("warn", msg)
        self.bus.publish("shift", self.shift_view())
        if not self.runners:
            self._end_shift(reason, detail)

    # ------------------------------------------------------------ 人的操作

    def pause_manual(self):
        with self.lock:
            self._pause("manual", manual=True)

    def resume(self):
        with self.lock:
            p = self.st.get("pause")
            if p and p["reason"] in ("quota_5h", "quota_7d"):
                r, at = self.quota.gate()
                if r:
                    raise ValueError(f"额度仍未恢复（{r}），预计 {time.strftime('%m-%d %H:%M', time.localtime(at)) if at else '未知'}")
            if self.runners:
                raise ValueError("仍有任务在收尾，稍后再试")
            if self.st.get("shift") and not self.st["shift"].get("ended"):
                self._end_shift("manual", "人工开新班")
            self._start_shift()

    def cutoff(self, resume_in=60):
        """模拟 5h 窗口切断：SIGKILL 在飞任务，写交接，resume_in 秒后自动开下一班。"""
        with self.lock:
            self._pause("cutoff", resume_at=time.time() + resume_in,
                        detail="模拟窗口切断（SIGKILL 在飞任务）")
            runners = list(self.runners.values())
        for r in runners:
            r.kill()

    def human_message(self, ds, text):
        with self.lock:
            n = discussion.append_turn(self.store, ds, "human", text)
            self._enqueue_discuss(ds)
        return n

    def request_distill(self, ds, manual=True):
        with self.lock:
            return self._enqueue_distill(ds, manual)

    # ------------------------------------------------------------ 入队

    def _active(self, kind, ds):
        return next((t for t in self.ledger.all() if t["kind"] == kind and t.get("discussion") == ds
                     and t["status"] in ("queued", "running")), None)

    def _enqueue_discuss(self, ds, priority=3):
        if self._active("discuss_turn", ds):
            return None
        t = self.ledger.create("discuss_turn", f"回复讨论 {ds} 中研究者的最新发言",
                               discussion=ds, priority=priority)
        self.bus.publish("task", t)
        return t

    def _enqueue_distill(self, ds, manual):
        if self._active("distill", ds):
            return None
        if discussion.undistilled_human(self.store, ds) == 0 and not manual:
            return None
        _, turns = discussion.read(self.store, ds)
        if not turns or turns[-1]["n"] <= discussion.covers_through(self.store, ds):
            return None
        t = self.ledger.create("distill", f"蒸馏讨论 {ds} 中摘要尚未覆盖的部分",
                               discussion=ds, priority=5)
        self.bus.publish("task", t)
        return t

    # ------------------------------------------------------------ 派发

    def _dispatch_loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.bus.notify("error", "dispatch 异常：" + traceback.format_exc()[-800:])
            time.sleep(0.5)

    def tick(self):
        with self.lock:
            p = self.st.get("pause")
            if p:
                if not p.get("manual") and self.cfg.auto_resume and not self.runners and \
                        p.get("resume_at") and time.time() >= p["resume_at"] and \
                        not self.quota.gate()[0]:
                    if self.st.get("shift") and not self.st["shift"].get("ended"):
                        self._end_shift(p["reason"], p.get("detail", ""))
                    self._start_shift()
                return
            if not self.st.get("shift") or self.st["shift"].get("ended"):
                return
            for lane, busy in self.lanes.items():
                if busy:
                    continue
                task = self._next(lane)
                if not task:
                    continue
                reason, at = self.quota.gate()
                if reason:
                    self._pause(reason, resume_at=at)
                    return
                self._launch(task)

    def _next(self, lane):
        q = [t for t in self.ledger.all() if t["status"] == "queued" and t["lane"] == lane]
        q.sort(key=lambda t: (t.get("priority", 5), t["created"], t["id"]))
        return q[0] if q else None

    def _launch(self, task):
        task.update(status="running", started=now(), attempts=task.get("attempts", 0) + 1,
                    shift=self.st["shift"]["id"], error=None)
        self.ledger.save(task)
        runner = Runner(self.cfg, self.store, self.ledger, self.quota, self.bus)
        self.runners[task["id"]] = runner
        self.lanes[task["lane"]] = task["id"]
        self.bus.publish("task", task)
        th = threading.Thread(target=self._work, args=(task, runner), daemon=True)
        th.start()

    def _work(self, task, runner):
        out = None
        try:
            prompt = self._prompt(task)
            if prompt is None:
                task.update(status="done", result="无事可做（已被其他任务处理）", ended=now())
                return
            ds = task.get("discussion")
            on_delta = None
            if task["kind"] == "discuss_turn":
                self.replies[ds] = ""

                def on_delta(text, ds=ds):
                    if text is None:          # 新的一条 assistant 消息：只流式显示最后一条
                        self.replies[ds] = ""
                        self.bus.publish("reply_reset", {"discussion": ds}, persist=False)
                    else:
                        self.replies[ds] += text
                        self.bus.publish("reply_delta", {"discussion": ds, "text": text},
                                         persist=False)
            out = runner.run(task, prompt, on_delta)
            task.update(status=out.status, ended=now(), session_id=out.session_id,
                        error=out.error, cost_usd=out.cost)
            if out.status == "done":
                self._finish(task, out)
        except Exception:
            task.update(status="failed", ended=now(), error=traceback.format_exc()[-2000:])
        finally:
            with self.lock:
                self.ledger.save(task)
                self.runners.pop(task["id"], None)
                self.lanes[task["lane"]] = None
                self.replies.pop(task.get("discussion"), None)
                self.bus.publish("task", task)
                if out and out.exhausted:
                    self.quota.mark_exhausted(out.exhausted, out.resets)
                    self._pause(out.exhausted, resume_at=out.resets or self.quota.gate()[1])
                if self.st.get("pause") and not self.runners:
                    self._end_shift(self.st["pause"]["reason"], self.st["pause"].get("detail", ""))
                if task["status"] == "failed":
                    self.bus.notify("error", f"{task['id']} {task['kind']} 执行失败：{(task.get('error') or '')[:200]}",
                                    task=task["id"])

    def _prompt(self, task):
        ds = task.get("discussion")
        _, turns = discussion.read(self.store, ds)
        if task["kind"] == "discuss_turn":
            pending = []
            for t in reversed(turns):
                if t["role"] != "human":
                    break
                pending.insert(0, t)
            if not pending:
                return None
            text = "\n\n".join(t["text"] for t in pending)
            n = pending[-1]["n"]
            task["reply_to"] = n
            if task.get("attempts", 1) > 1:
                return protocol.reply_prompt_for_pending(ds, n, text)
            return protocol.discuss_prompt(ds, n, text)
        if task["kind"] == "distill":
            start = discussion.covers_through(self.store, ds) + 1
            end = turns[-1]["n"] if turns else 0
            if start > end:
                return None
            task["range"] = [start, end]
            return protocol.distill_prompt(ds, start, end)
        raise ValueError(task["kind"])

    def _finish(self, task, out):
        ds = task.get("discussion")
        if task["kind"] == "discuss_turn":
            reply = (out.result or "").strip()
            if not reply:
                task.update(status="failed", error="agent 没有给出回复")
                return
            n = discussion.append_turn(self.store, ds, "ai", reply, task=task["id"])
            task["result_brief"] = f"已回复（第 {n} 轮）"
            self.bus.publish("turn", {"discussion": ds, "n": n})
            if discussion.undistilled_human(self.store, ds) >= self.cfg.distill_every:
                with self.lock:
                    self._enqueue_distill(ds, manual=False)
        elif task["kind"] == "distill":
            c = discussion.covers_through(self.store, ds)
            lo, hi = task.get("range", [0, 0])
            task["result_brief"] = (f"摘要覆盖至第 {c} 轮" if c >= hi else
                                    f"摘要未推进到第 {hi} 轮（当前 {c}）")
            task["result"] = (out.result or "")[:4000]
            self.bus.publish("candidates", {"discussion": ds})

    # ------------------------------------------------------------ 人在编辑器里的直接修改

    def _sweep_loop(self):
        while not self._stop.wait(self.cfg.sweep_seconds):
            try:
                self.sweep_human_edits()
            except Exception:
                self.bus.notify("error", "sweep 异常：" + traceback.format_exc()[-800:])

    def sweep_human_edits(self):
        sha, paths = self.store.sweep("human: 编辑器中的直接修改", "human")
        if paths:
            errs, _ = schema.validate_repo(self.store.state)
            self.bus.publish("state", {"paths": paths, "sha": sha})
            self.bus.notify("warn" if errs else "info",
                            f"已提交你在编辑器里的 {len(paths)} 处修改（{sha}）" +
                            (f"；校验发现 {len(errs)} 个问题" if errs else ""))
        return sha, paths

    # ------------------------------------------------------------ 视图

    def shift_view(self):
        with self.lock:
            sh = self.st.get("shift") or {}
            p = self.st.get("pause")
            return {
                "shift": sh,
                "state": "paused" if p else ("active" if sh and not sh.get("ended") else "idle"),
                "pause": dict(p, resume_at_iso=time.strftime("%m-%d %H:%M:%S", time.localtime(p["resume_at"])))
                if p and p.get("resume_at") else p,
                "running": [dict(self.ledger.get(t) or {}, lane=self.ledger.get(t)["lane"])
                            for t in self.runners],
                "queued": [t for t in self.ledger.all() if t["status"] == "queued"],
                "quota": self.quota.snapshot(),
                "auto_resume": self.cfg.auto_resume,
            }
