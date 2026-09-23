import os
import time
import unittest
from pathlib import Path

from autoresearch import bootstrap, candidates, discussion, frontmatter
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from tests.util import temp_cfg

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


class DaemonTest(unittest.TestCase):
    def setUp(self):
        os.environ["FAKE_MODE"] = "reply"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("FAKE_MODE", "FAKE_7D", "FAKE_RESET_IN")])
        self.cfg = temp_cfg(self, AR_CLAUDE_BIN=FAKE, AR_DISTILL_EVERY=2)
        self.st, _ = bootstrap.init(self.cfg)
        self.d = self.new_daemon()
        self.ds = discussion.create(self.st, "测试讨论")

    def new_daemon(self):
        d = Daemon(self.cfg, self.st, Bus(self.cfg))
        d.start(background=False)
        self.addCleanup(lambda: [r.kill() for r in list(d.runners.values())])
        return d

    def until(self, cond, timeout=20, d=None):
        d = d or self.d
        t0 = time.time()
        while time.time() - t0 < timeout:
            d.tick()
            if cond():
                return
            time.sleep(0.1)
        self.fail("等待超时：" + repr(d.shift_view()))

    def turns(self):
        return discussion.read(self.st, self.ds)[1]

    def handoffs(self):
        out = []
        for p in sorted((self.cfg.state / "handoffs").glob("HO*.md")):
            out.append(frontmatter.parse(p.read_text(encoding="utf-8")))
        return out

    # ------------------------------------------------------------------

    def test_discuss_then_auto_distill(self):
        self.d.human_message(self.ds, "感知不是瓶颈吧？")
        self.until(lambda: len(self.turns()) == 2)
        self.assertEqual(self.turns()[1]["role"], "ai")
        self.assertIn("[交接:无]", self.turns()[1]["text"])
        self.d.human_message(self.ds, "我更怀疑动作表示。")
        os.environ["FAKE_MODE"] = "reply"
        self.until(lambda: len(self.turns()) == 4)
        os.environ["FAKE_MODE"] = "distill"
        self.until(lambda: discussion.covers_through(self.st, self.ds) == 4)
        self.assertEqual([c["id"] for c in candidates.list_all(self.st, "pending")], ["C001"])
        with self.st.locked():      # 写入与提交在同一把锁内完成，持锁观察才不会撞上中间态
            self.assertEqual(self.st.dirty_paths(), [])
        # 摘要生效后，新 session 的 briefing 用摘要替代原文
        os.environ["FAKE_MODE"] = "reply"
        self.d.human_message(self.ds, "继续。")
        self.until(lambda: len(self.turns()) == 6)
        self.assertIn("[摘要至:4:", self.turns()[5]["text"])

    def test_cutoff_handoff_and_resume(self):
        os.environ["FAKE_MODE"] = "sleep"
        self.d.human_message(self.ds, "这句话在回复前被切断。")
        self.until(lambda: self.d.runners)
        time.sleep(0.5)
        self.d.cutoff(resume_in=1)
        self.until(lambda: self.handoffs())
        meta, body = self.handoffs()[0]
        self.assertEqual(meta["reason"], "cutoff")
        self.assertIn("被截断的任务", body)
        self.assertIn("尚未回复", body)
        self.assertEqual(len(self.turns()), 1)          # 人的话在，AI 的半条回复不进记录
        os.environ["FAKE_MODE"] = "reply"
        self.until(lambda: len(self.turns()) == 2)
        reply = self.turns()[1]["text"]
        self.assertIn("[交接:HO001]", reply)
        self.assertIn("[补答]", reply)
        self.assertEqual(self.d.shift_view()["shift"]["id"], "SH0002")

    def test_distill_interrupted_resumes_from_checkpoint(self):
        discussion.append_turn(self.st, self.ds, "human", "感知不是瓶颈。")
        discussion.append_turn(self.st, self.ds, "ai", "为什么？")
        os.environ["FAKE_MODE"] = "distill_sleep"
        self.d.request_distill(self.ds)
        self.until(lambda: (self.cfg.tasks / "T00001" / "checkpoints.jsonl").exists())
        self.d.cutoff(resume_in=1)
        self.until(lambda: self.handoffs())
        self.assertIn("已提交 C001", self.handoffs()[0][1])
        os.environ["FAKE_MODE"] = "distill"
        self.until(lambda: discussion.covers_through(self.st, self.ds) == 2)
        self.assertEqual(len(candidates.list_all(self.st)), 1)   # 没有重复提交
        brief = (self.cfg.tasks / "T00001" / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("本任务之前被中断过", brief)

    def test_quota_exhaustion_pauses_and_auto_resumes(self):
        os.environ["FAKE_MODE"] = "exhaust"
        os.environ["FAKE_RESET_IN"] = "2"
        self.d.human_message(self.ds, "额度快没了。")
        self.until(lambda: self.handoffs())
        self.assertEqual(self.handoffs()[0][0]["reason"], "quota_5h")
        self.assertEqual(self.d.shift_view()["state"], "paused")
        os.environ["FAKE_MODE"] = "reply"
        self.until(lambda: len(self.turns()) == 2, timeout=15)
        self.assertEqual(self.d.shift_view()["state"], "active")

    def test_weekly_gate_lets_inflight_finish_then_pauses(self):
        os.environ["FAKE_7D"] = "0.96"
        self.d.human_message(self.ds, "第一句。")
        self.until(lambda: len(self.turns()) == 2)       # 在飞任务正常完成提交
        self.d.human_message(self.ds, "第二句。")
        self.until(lambda: self.d.shift_view()["state"] == "paused")
        self.assertEqual(self.d.shift_view()["pause"]["reason"], "quota_7d")
        time.sleep(0.5)
        self.d.tick()
        self.assertEqual(len(self.turns()), 3)           # 第二句不会被派发
        self.assertEqual(self.handoffs()[0][0]["reason"], "quota_7d")

    def test_crash_recovery(self):
        t = self.d.ledger.create("discuss_turn", "x", discussion=self.ds)
        t.update(status="running", shift="SH0001", attempts=1)
        self.d.ledger.save(t)
        discussion.append_turn(self.st, self.ds, "human", "崩溃前的发言")
        (self.cfg.state / "hypotheses" / "H001.md").write_text(
            (self.cfg.state / "hypotheses" / "H001.md").read_text() + "\n人手改的一行\n")
        d2 = Daemon(self.cfg, self.st, Bus(self.cfg))   # 模拟进程被 SIGKILL 后重启
        d2.start(background=False)
        self.addCleanup(lambda: [r.kill() for r in list(d2.runners.values())])
        self.assertEqual(self.handoffs()[0][0]["reason"], "crash")
        recovered = [c for c in self.st.log() if c["subject"].startswith("recovered")]
        self.assertEqual(recovered[0]["files"], ["hypotheses/H001.md"])
        self.until(lambda: len(self.turns()) == 2, d=d2)

    def test_human_edit_sweep(self):
        p = self.cfg.state / "questions" / "Q001.md"
        p.write_text(p.read_text() + "\n补一句。\n")
        sha, paths = self.d.sweep_human_edits()
        self.assertEqual(paths, ["questions/Q001.md"])
        self.assertEqual(self.st.log()[0]["author"], "ar-human")


if __name__ == "__main__":
    unittest.main()
