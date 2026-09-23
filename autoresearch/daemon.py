"""M2 班次 daemon（Phase 1 子集）：派发任务、额度闸、暂停 / 交接 / 恢复。

系统不是连续运行的 daemon，而是一串离散的班次（§0.5）。一个班次从开班持续到
暂停（额度耗尽、窗口切断、人工暂停、daemon 停止或崩溃）；每次结束都机械生成
一份交接记录写进 State，下一班的所有 briefing 都带着它。

M2.0 的模式门在这里：讨论模式下 daemon 只执行人发起的讨论回合、蒸馏、闭合被截断的任务，
以及窗口空闲时的夜间预习（只读文献，不迁移状态）；验证模式下由 planner 机械规划批次的
检索 → 精读 → 评估。出现重大结果或批次饱和就停下建议收回（hold），不自己继续闭环（§5.7）。
"""
import datetime
import json
import threading
import time
import traceback

from . import discussion, frontmatter, incubation, modes, papers, planner, protocol, reviews, schema
from .briefing import Assembler
from .quota import Quota
from .runner import Runner
from .store import now, today
from .tasks import KINDS, Ledger

MAX_ATTEMPTS = 3
JUDGE_KINDS = {k for k, v in KINDS.items() if v["profile"] == "judge"}
UNATTENDED_KINDS = JUDGE_KINDS | {"incubate"}
NOTE_REASON = {"quota_5h": "5-hour limit", "quota_7d": "weekly limit", "cutoff": "cut off",
               "manual": "stopped", "crash": "the previous run crashed", "normal": "normal"}


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
                self.bus.notify("warn", f"Found {len(paths)} uncommitted change(s) left by the previous run; committed them as recovered.", sha=sha)
            self._reconcile()
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
        self.bus.notify("info", f"Shift {self.st['shift']['id']} started" +
                        (f"; picking up interrupted task(s) {', '.join(requeued)}" if requeued else "") + ".")

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
                        f"Shift {sh['id']} ended ({NOTE_REASON.get(reason, reason)}). Handoff saved as {hid}.",
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
        msg = {"quota_5h": "The 5-hour usage window is exhausted. Work is paused",
               "quota_7d": f"Weekly usage reached {round(self.cfg.weekly_stop * 100)}%. Everything is paused to protect your other Claude Code usage",
               "cutoff": "Work was cut off",
               "manual": "Paused by you"}.get(reason, reason)
        if when:
            msg += f"; expected to resume around {when}" + (" (automatically)" if self.cfg.auto_resume and not manual else "")
        msg += "."
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
            if (self.st.get("prep") or {}).get("active"):
                self._end_prep("研究者回来了，不再开新的预习任务")
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
                self.bus.notify("error", "Dispatcher error: " + traceback.format_exc()[-800:])
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
            if not self.lanes["background"]:
                self._plan()
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
                if task["kind"] in UNATTENDED_KINDS and not self._unattended_ok():
                    continue
                self._launch(task)

    def _next(self, lane):
        q = [t for t in self.ledger.all() if t["status"] == "queued" and t["lane"] == lane
             and not (self.st.get("hold") and t.get("batch"))]
        q.sort(key=lambda t: (t.get("priority", 5), t["created"], t["id"]))
        return q[0] if q else None

    def _launch(self, task):
        task.update(status="running", started=now(), attempts=task.get("attempts", 0) + 1,
                    shift=self.st["shift"]["id"], error=None,
                    q5_start=task.get("q5_start", self.quota.data.get("five_hour")),
                    tools_from=planner.tool_lines(self.ledger, task["id"]))
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
            task["q5_end"] = self.quota.data.get("five_hour")
            if task["kind"] == "incubate":
                self._chain_record(task, out)
            with self.lock:
                self.ledger.save(task)
                self.runners.pop(task["id"], None)
                self.lanes[task["lane"]] = None
                self.replies.pop(task.get("discussion"), None)
                self.bus.publish("task", task)
                # 回复刚写进讨论、任务还没收尾的那一瞬间，人又说了一句：当时入队被“已有回复任务”挡掉了，这里补上
                if task["kind"] == "discuss_turn" and task["status"] == "done":
                    _, turns = discussion.read(self.store, task["discussion"])
                    if turns and turns[-1]["role"] == "human":
                        self._enqueue_discuss(task["discussion"])
                if out and out.exhausted:
                    self.quota.mark_exhausted(out.exhausted, out.resets)
                    self._pause(out.exhausted, resume_at=out.resets or self.quota.gate()[1])
                if self.st.get("pause") and not self.runners:
                    self._end_shift(self.st["pause"]["reason"], self.st["pause"].get("detail", ""))
                if task["status"] == "failed":
                    self.bus.notify("error", f"Task {task['id']} ({task['kind']}) failed: {(task.get('error') or '')[:200]}",
                                    task=task["id"])

    def _prompt(self, task):
        if task["kind"] == "incubate":
            return protocol.incubate_prompt(task)
        if task["kind"] in JUDGE_KINDS:
            return protocol.judge_prompt(task)
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
        if task["kind"] == "incubate":
            task["result"] = (out.result or "")[:4000]
            task["result_brief"] = " ".join((out.result or "").split())[:160]
            self.bus.publish("state", {"task": task["id"]})
            return
        if task["kind"] in JUDGE_KINDS:
            return self._finish_judge(task, out)
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

    # ------------------------------------------------------------ Phase 2：评判类任务收尾

    def _finish_judge(self, task, out):
        task["result"] = (out.result or "")[:4000]
        task["result_brief"] = " ".join((out.result or "").split())[:160]
        blocking = [c for c in planner.tool_calls(self.ledger, task["id"], "request_paper",
                                                  since=task.get("tools_from", task.get("started")))
                    if c.get("blocking")]
        if blocking:
            rid = blocking[-1]["id"]
            rq, _ = self.store.read_obj(rid)
            status = (rq or {}).get("status")
            if status == "open":
                task.update(status="blocked_on_human", blocked_on=rid)
                self.bus.notify("warn", f"{task['id']} needs a paper you can fetch ({rid}); "
                                "it is suspended and other work continues.", task=task["id"])
            elif status == "fulfilled":
                # 任务还没结束，全文就已经到了：直接重新入队，不挂起
                task.update(status="queued", priority=2,
                            resume_note=f"你请求的全文（{rid}）已经上传，现在可以读全文了。")
            # dismissed：研究者已经说拿不到，任务按摘要级做完即可，不再挂起
        self.bus.publish("state", {"task": task["id"]})
        if task.get("batch"):
            why = self._major_result(task)
            if why:
                self._hold(why)

    def _major_result(self, task):
        """§5.7：出现这些就停下建议收回，不自己继续闭环。"""
        tid, since = task["id"], task.get("tools_from", task.get("started"))
        for c in planner.tool_calls(self.ledger, tid, "transition_hypothesis", since):
            if c.get("to") in ("supported", "refuted"):
                return f"{c['id']} was {c['to']} ({tid})"
        for c in planner.tool_calls(self.ledger, tid, "invalidate_assumption", since):
            return f"assumption {c['id']} was invalidated ({tid})"
        for c in planner.tool_calls(self.ledger, tid, "examine_assumption", since):
            if c.get("verdict") == "fragile":
                return f"assumption {c['id']} turned out fragile ({tid})"
        if task["kind"] == "contradiction_scan" and planner.tool_calls(self.ledger, tid, "propose_candidate", since):
            return f"the contradiction scan found conflicting evidence ({tid})"
        return None

    def _hold(self, reason, kind="major", **extra):
        if self.st.get("hold"):
            return
        self.st["hold"] = {"reason": reason, "since": now(), "kind": kind, **extra}
        self._save_state()
        if kind == "incubation_done":
            self.bus.notify("info", f"Incubation finished: {reason}. Ideas are waiting in the Inbox — "
                            "suggest going back to discussion mode to triage them.", hold=True)
        else:
            self.bus.notify("warn", f"Validation paused: {reason}. Suggest going back to discussion mode — "
                            "or choose to continue validation.", hold=True)
        self.bus.publish("mode", self.mode_view())

    def _unattended_ok(self):
        """§5.7 窗口份额：无人值守只用 5h 窗口的前 AR_UNATTENDED_CAP，其余留给你的交互使用。"""
        used = self.quota.data.get("five_hour")
        if used is None or used < self.cfg.unattended_cap:
            self.st.pop("cap_note", None)
            return True
        if not self.st.get("cap_note"):
            self.st["cap_note"] = now()
            self._save_state()
            self.bus.notify("info", f"5-hour window is {round(used * 100)}% used; unattended work stops at "
                            f"{round(self.cfg.unattended_cap * 100)}% to leave the rest for you.")
        return False

    # ------------------------------------------------------------ Phase 2：规划

    def _plan(self):
        if any(t["lane"] == "background" and t["status"] == "queued" for t in self.ledger.all()):
            return
        m = modes.mode(self.store)
        if m == "validation":
            if self.st.get("hold"):
                return
            did = modes.batch_decision(self.store)
            tasks = self.ledger.all()
            step = planner.next_step(self.store, self.ledger, self.cfg, modes.batch(self.store),
                                     tasks, ("batch", did))
            if step:
                self._create(step, batch=did)
            elif not any(t.get("batch") == did and t["status"] in planner.LIVE for t in tasks):
                if any(t.get("batch") == did and t["status"] == "blocked_on_human" for t in tasks):
                    return      # 还有任务在等你取论文：不算饱和，上传后会接着做
                done = planner.batch_saturated(self.store, self.ledger, self.cfg, tasks)
                self._hold("every item in the batch has reached a conclusion" if done else
                           "nothing worthwhile left to do on this batch within its budget — "
                           "stopping to leave quota", kind="saturated")
        elif m == "discussion":
            self._plan_prep()
        elif m == "incubation":
            self._plan_incubation()

    # ------------------------------------------------------------ Phase 2.5：自演进（§5.13）

    def _plan_incubation(self):
        """一轮：若干条推演链 → 每条新 Idea 一个接地（优先）→ 全部结束后由外部证据更新基本盘 → 下一轮。
        停止条件只在轮与轮之间、开新链之前检查（3 条过关 / 30% 窗口 / 连续两轮没东西 / 轮数上限）。"""
        did = incubation.session(self.store)
        if not did or self.st.get("hold"):
            return
        inc = self.st.get("inc") or {}
        if inc.get("session") != did:
            inc = {"session": did, "round": 0, "finalized": 0, "empty": 0}
            self.st["inc"] = inc
            self._save_state()
        tasks = [t for t in self.ledger.all() if t.get("incubation") == did]
        # 被切断的推演 / 接地会在下一班重新入队（半条链是合法的可恢复状态，M11.12），这一轮还没完
        live = [t for t in tasks if t["status"] in planner.LIVE | {"interrupted"}]
        # 接地先于下一条推演：Idea 悬在 grounding 状态就没法进基本盘，也没法分诊
        for m, _ in incubation.session_ideas(self.store, did):
            if m.get("status") != "grounding":
                continue
            gts = [t for t in tasks if t["kind"] == "ground_idea" and t.get("target") == m["id"]]
            if any(t["status"] in planner.LIVE | {"blocked_on_human"} for t in gts):
                continue
            if len(gts) < 2:
                self._create({"kind": "ground_idea", "target": m["id"], "priority": 3,
                              "round": int(m.get("round") or 0),
                              "goal": f"对想法 {m['id']} 做外部核查：有没有直接反驳、有哪些相近的工作（只标注，不改写）",
                              "why": f"{m['id']} 由推演 {m.get('chain')} 登记，每条想法都要经过接地"
                                     + ("（上一次核查没有给出结论，重试）" if gts else "")},
                             incubation=did)
                return
        if live or any(t["status"] == "blocked_on_human" for t in tasks):
            return
        r = inc["round"]
        if r and r > inc["finalized"]:
            delta = []
            for t in tasks:
                if t["kind"] == "ground_idea" and t.get("round") == r:
                    for c in planner.tool_calls(self.ledger, t["id"]):
                        if c.get("tool") in ("record_evidence", "annotate_grounding") and c.get("id"):
                            delta.append(c["id"])
            fid = incubation.next_foundation(self.store, did, r, delta)
            got = [m for m, _ in incubation.session_ideas(self.store, did)
                   if int(m.get("round") or 0) == r]
            inc.update(finalized=r, empty=0 if got else inc.get("empty", 0) + 1)
            self._save_state()
            if fid:
                self.bus.notify("info", f"Foundation updated to {fid} from external evidence ({', '.join(delta)}).")
        reason = incubation.stop_reason(self.store, did, tasks, inc.get("empty", 0), r,
                                        self.cfg.incubate_max_rounds) if r else None
        if reason:
            chains = len([t for t in tasks if t["kind"] == "incubate"])
            dec, n_ok = incubation.conclude(self.store, did, reason, r, chains)
            self._hold(reason, kind="incubation_done", summary=dec, shortlisted=n_ok)
            self.bus.publish("state", {"decision": dec})
            return
        fid = incubation.current_foundation(self.store, did)
        r += 1
        for k in range(self.cfg.incubate_chains):
            self._create({"kind": "incubate", "round": r, "foundation": fid, "priority": 4,
                          "goal": f"从基本盘 {fid} 出发做一条短推演（第 {r} 轮第 {k + 1} 条）：自己选切入角度，"
                                  "推出站得住的想法就登记，推不出就交白卷",
                          "why": f"自演进 {did} 第 {r} 轮" + (f"（基本盘已由外部证据更新为 {fid}）"
                                                             if r > 1 and fid else "")},
                         incubation=did)
        inc["round"] = r
        self._save_state()

    def _chain_record(self, task, out):
        """推演记录进 State（§5.10）：每次结束都写，被切断的下一次续推后会覆盖。"""
        try:
            ideas = [m["id"] for m, _ in self.store.list("idea") if m.get("chain") == task["id"]]
            status = "done" if ideas else ("empty" if task["status"] == "done" else "interrupted")
            task["angle"] = incubation.write_chain(self.store, task, status,
                                                   (out.result if out else "") or task.get("error") or "",
                                                   self.ledger.checkpoints(task["id"]), ideas)
        except Exception:
            self.bus.notify("error", "Chain record error: " + traceback.format_exc()[-600:])

    def enter_incubation(self, note=""):
        with self.lock:
            did, fid = incubation.enter(self.store, note)
            self._cancel(lambda t: t.get("prep"), "进入自演进，夜间预习只在讨论模式进行")
            if (self.st.get("prep") or {}).get("active"):
                self._end_prep("进入自演进")
            self.st.pop("hold", None)
            self.st["inc"] = {"session": did, "round": 0, "finalized": 0, "empty": 0}
            self._save_state()
            self.bus.publish("mode", self.mode_view())
            self.bus.notify("info", f"Incubation started ({did}); foundation {fid} frozen.", decision=did)
            return did, fid

    def _create(self, step, **extra):
        step = dict(step)
        kind, goal = step.pop("kind"), step.pop("goal")
        t = self.ledger.create(kind, goal, **step, **extra)
        self.bus.publish("task", t)
        return t

    # ------------------------------------------------------------ 夜间预习（M2.0）

    def _last_human_ts(self):
        last = None
        for d in discussion.list_all(self.store):
            _, turns = discussion.read(self.store, d["id"])
            for t in turns:
                if t["role"] == "human" and (last is None or t["ts"] > last):
                    last = t["ts"]
        return last

    def _plan_prep(self):
        prep = self.st.get("prep") or {}
        if prep.get("active"):
            return self._continue_prep(prep)
        last = self._last_human_ts()
        if not last or prep.get("after") == last or self.runners:
            return
        try:
            idle = (datetime.datetime.now() - datetime.datetime.fromisoformat(last)).total_seconds()
        except ValueError:
            return
        if idle < self.cfg.prep_idle_minutes * 60 or not self._unattended_ok():
            return
        self._absorb_prep_request()
        req = self.st.get("prep_request")
        if req:
            target, topic = None, req["text"]
            why = f"你要求的（{req.get('source') or 'Chat 页'}，{req['ts'][:16]}）：「{topic}」"
            self.st.pop("prep_request", None)
        else:
            target, why = planner.pick_prep_target(self.store)
            topic = f"核查 {target}" if target else None
        if not topic:
            self.st["prep"] = {"after": last, "skipped": "没有与你相关、值得预习的题目"}
            self._save_state()
            return
        did = modes.record_prep(self.store, topic, why, [target] if target else [])
        self.st["prep"] = {"active": True, "decision": did, "target": target, "topic": topic,
                           "why": why, "after": last, "started": now()}
        self._save_state()
        self.bus.notify("info", f"Overnight reading started: {topic}. Why: {why}", decision=did)
        self._continue_prep(self.st["prep"])

    def _continue_prep(self, prep):
        did = prep["decision"]
        tasks = self.ledger.all()
        mine = [t for t in tasks if t.get("prep") == did]
        live = [t for t in mine if t["status"] in planner.LIVE]
        if len(mine) >= self.cfg.prep_max_tasks:
            if not live:
                self._end_prep(f"用满了 {self.cfg.prep_max_tasks} 个任务的预算")
            return
        if prep.get("target"):
            step = planner.next_step(self.store, self.ledger, self.cfg, [prep["target"]], tasks,
                                     ("prep", did))
        else:
            step = self._freeform_prep_step(prep, mine)
        if step:
            step["why"] = f"夜间预习（{did}）：" + step.get("why", "")
            self._create(step, prep=did)
        elif not live:
            self._end_prep("没有更多值得读的")

    def _freeform_prep_step(self, prep, mine):
        searches = [t for t in mine if t["kind"] == "lit_search"]
        if not searches:
            return {"kind": "lit_search", "priority": 6,
                    "goal": f"夜间预习：为研究者的问题「{prep['topic']}」检索最相关的论文", "why": prep["why"]}
        if any(t["status"] in planner.LIVE for t in searches):
            return None
        read = {t.get("paper") for t in mine if t["kind"] == "read_paper"}
        for s in searches:
            for c in planner.tool_calls(self.ledger, s["id"], "register_paper"):
                if c["id"] not in read:
                    m, _ = self.store.read_obj(c["id"])
                    return {"kind": "read_paper", "paper": c["id"], "priority": 6,
                            "goal": f"夜间预习：精读 {c['id']}「{(m or {}).get('title')}」，"
                                    f"回答研究者的问题「{prep['topic']}」，并抽取对现有假设 / 前提的证据",
                            "why": f"由 {s['id']} 的检索找到"}
        return None

    def _absorb_prep_request(self):
        """讨论 agent 经 note_prep_request 记下的“今晚查什么”（研究者在讨论里的回答）。"""
        p = self.cfg.run / "prep_request.json"
        if p.exists():
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
                if r.get("ts", "") > (self.st.get("prep_request") or {}).get("ts", ""):
                    self.st["prep_request"] = r
                    self._save_state()
            except (OSError, json.JSONDecodeError):
                pass
            p.unlink(missing_ok=True)

    def _end_prep(self, why):
        prep = self.st.get("prep") or {}
        if not prep.get("active"):
            return
        prep.update(active=False, ended=now(), end_reason=why)
        self._save_state()
        self.bus.notify("info", f"Overnight reading finished ({why}). See {prep.get('decision')}.",
                        decision=prep.get("decision"))

    # ------------------------------------------------------------ Phase 2：人的操作

    def handoff(self, items, note=""):
        with self.lock:
            did, batch = modes.handoff(self.store, items, note)
            self._cancel(lambda t: t.get("prep"), "交棒进验证模式，文献工作由验证模式接管")
            if (self.st.get("prep") or {}).get("active"):
                self._end_prep("交棒进验证模式")
            self.st.pop("hold", None)
            self._save_state()
            self.bus.publish("mode", self.mode_view())
            self.bus.notify("info", f"Handed off to validation mode ({did}): {', '.join(batch)}.",
                            decision=did)
            return did, batch

    def recall(self, reason=""):
        with self.lock:
            if modes.mode(self.store) == "incubation":
                prev = incubation.session(self.store)
                did = incubation.leave(self.store, reason)
                n = self._cancel(lambda t: t.get("incubation") == prev, "研究者回到讨论模式")
                self.st.pop("hold", None)
                self.st.pop("inc", None)
                self._save_state()
                self.bus.publish("mode", self.mode_view())
                self.bus.notify("info", f"Back to discussion mode ({did})." +
                                (f" Cancelled {n} queued incubation task(s)." if n else ""), decision=did)
                return did
            did = modes.recall(self.store, reason)
            n = self._cancel(lambda t: t.get("batch"), "研究者收回讨论模式")
            self.st.pop("hold", None)
            self._save_state()
            self.bus.publish("mode", self.mode_view())
            self.bus.notify("info", f"Back to discussion mode ({did})." +
                            (f" Cancelled {n} queued validation task(s); running ones finish and commit." if n else ""),
                            decision=did)
            return did

    def continue_validation(self, reason):
        with self.lock:
            hold = self.st.get("hold")
            if not hold:
                raise ValueError("当前没有“建议收回”的提示")
            did = modes.continue_validation(self.store, reason, hold["reason"])
            self.st.pop("hold", None)
            self._save_state()
            self.bus.publish("mode", self.mode_view())
            return did

    def request_grounding(self, target):
        with self.lock:
            if not self.store.exists(target) or target[0] not in "HAC":
                raise ValueError(f"{target} 不存在或不能做接地核查")
            if any(t["kind"] == "grounding" and t.get("target") == target and t["status"] in planner.LIVE
                   for t in self.ledger.all()):
                raise ValueError(f"{target} 的接地核查已在队列里")
            return self._create({"kind": "grounding", "target": target, "priority": 3,
                                 "goal": f"对 {target} 做对抗性接地：有没有先例、有没有直接反驳",
                                 "why": "研究者在前端要求"})

    def set_prep_request(self, text, source=""):
        with self.lock:
            text = (text or "").strip()
            if not text:
                self.st.pop("prep_request", None)
            else:
                self.st["prep_request"] = {"text": text, "ts": now(), "source": source}
            self._save_state()
            self.bus.publish("mode", self.mode_view())

    def unblock(self, rid, note=None):
        """全文到了（或确定拿不到）：挂起在这条请求上的任务带着 checkpoint 重新入队。"""
        with self.lock:
            n = 0
            for t in self.ledger.all():
                if t["status"] == "blocked_on_human" and t.get("blocked_on") == rid:
                    t.update(status="queued", priority=2, resume_note=note, blocked_on=None)
                    self.ledger.save(t)
                    self.bus.publish("task", t)
                    n += 1
            hold = self.st.get("hold") or {}
            if n and hold.get("kind") == "saturated":
                # 饱和是“没事可做”，新到的全文就是新的事；重大结果的 hold 仍要人决定
                self.st.pop("hold", None)
                self._save_state()
                self.bus.publish("mode", self.mode_view())
            return n

    def _cancel(self, pred, why):
        n = 0
        for t in self.ledger.all():
            if t["status"] in ("queued", "blocked_on_human") and pred(t):
                t.update(status="cancelled", error=why, ended=now())
                self.ledger.save(t)
                self.bus.publish("task", t)
                n += 1
        return n

    def mode_view(self):
        pm, _ = self.store.project()
        return {"mode": pm.get("mode", "discussion"), "batch_decision": pm.get("batch"),
                "batch": modes.batch(self.store), "hold": self.st.get("hold"),
                "prep": self.st.get("prep"), "prep_request": self.st.get("prep_request"),
                "incubation": pm.get("incubation"), "inc": self.st.get("inc"),
                "entry_problems": incubation.entry_problems(self.store)
                if pm.get("mode", "discussion") == "discussion" else [],
                "cap": self.cfg.unattended_cap, "cap_note": self.st.get("cap_note")}

    # ------------------------------------------------------------ 人在编辑器里的直接修改

    def _sweep_loop(self):
        while not self._stop.wait(self.cfg.sweep_seconds):
            try:
                self.sweep_human_edits()
            except Exception:
                self.bus.notify("error", "Sweep error: " + traceback.format_exc()[-800:])

    def sweep_human_edits(self):
        sha, paths = self.store.sweep("human: 编辑器中的直接修改", "human")
        self._reconcile()      # 人在编辑器里改了状态也要传播（M5.6b）
        if paths:
            errs, _ = schema.validate_repo(self.store.state)
            self.bus.publish("state", {"paths": paths, "sha": sha})
            self.bus.notify("warn" if errs else "info",
                            f"Committed {len(paths)} change(s) you made in an editor ({sha})" +
                            (f"; validation found {len(errs)} problem(s)." if errs else "."))
        return sha, paths

    def _reconcile(self):
        new = reviews.reconcile(self.store)
        if new:
            self.bus.publish("state", {"reviews": new})
            self.bus.notify("warn", f"Something was overturned: {len(new)} linked object(s) need re-examination ({', '.join(new)}).")
        return new

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
