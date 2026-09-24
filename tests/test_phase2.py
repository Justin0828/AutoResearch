"""Phase 2：文献闭环与交棒（DESIGN.md §5.5–5.8）。用假 claude 与离线网络夹具，不耗额度、不碰网络。"""
import json
import os
import time
import unittest
from pathlib import Path

from autoresearch import (attribution, bootstrap, candidates, discussion, library, modes, papers,
                          schema, tasks)
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from autoresearch.library import Library
from tests.util import temp_cfg

HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "fake_claude.py")
NET = str(HERE / "fixtures" / "net")
PDF = HERE / "fixtures" / "restricted.pdf"


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self, AR_NET_FIXTURES=NET, AR_CLAUDE_BIN=FAKE)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.lib = Library(self.cfg.library)

    def errors(self):
        return schema.validate_repo(self.st.state)[0]


class LibraryTest(Base):
    def test_register_verifies_and_fetches_fulltext(self):
        pid, new, msg = papers.register(self.st, self.lib, "arXiv:2401.00001v2", why="w",
                                        for_targets=["H001"])
        self.assertTrue(new)
        m, _ = self.st.read_obj(pid)
        self.assertEqual((m["arxiv"], m["fulltext"], m["read"]), ("2401.00001", "open", "none"))
        anchors = self.lib.anchors(pid)
        self.assertIn("s4.2-p1", anchors)
        self.assertIn("tab1", anchors)
        self.assertNotIn("References", " ".join(anchors.values()))
        self.assertIn("x_t", anchors["s1-p1"])            # 公式取 alttext
        # 重复登记返回同一篇，并合并 for
        pid2, new2, _ = papers.register(self.st, self.lib, "https://arxiv.org/abs/2401.00001",
                                        for_targets=["H002"])
        self.assertEqual((pid2, new2), (pid, False))
        self.assertEqual(self.st.read_obj(pid)[0]["for"], ["H001", "H002"])
        self.assertEqual(self.errors(), [])

    def test_unknown_paper_rejected(self):
        with self.assertRaisesRegex(ValueError, "查无此文"):
            papers.register(self.st, self.lib, "2401.99999")
        with self.assertRaisesRegex(ValueError, "不是 arXiv id"):
            papers.register(self.st, self.lib, "Smith et al. 2023")
        self.assertEqual(self.st.list("paper"), [])

    def test_quote_must_match_paragraph(self):
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001", for_targets=["H001"])
        bad = dict(target="H001", stance="contradict", source=pid, note="n")
        with self.assertRaisesRegex(ValueError, "对不上"):
            papers.record_evidence(self.st, self.lib, **bad, locator=["s4.2-p1"],
                                   quote="success keeps improving with more demonstrations")
        with self.assertRaisesRegex(ValueError, "不存在"):
            papers.record_evidence(self.st, self.lib, **bad, locator=["s9-p9"], quote="x" * 30)
        with self.assertRaisesRegex(ValueError, "不能是 strong"):
            papers.record_evidence(self.st, self.lib, **bad, locator=["abstract"], strength="strong",
                                   quote="We study fine-grained contact-rich manipulation")
        eid, basis, moved = papers.record_evidence(
            self.st, self.lib, **bad, locator=["s4.2"], strength="strong", batch=["H001"],
            quote="success on peg insertion  plateaus at 41% after 500 demonstrations")
        self.assertEqual(basis, "fulltext")
        h, _ = self.st.read_obj("H001")
        self.assertEqual((h["status"], h["evidence"]), ("investigating", [eid]))   # 簿记归工具
        self.assertEqual(self.st.read_obj(pid)[0]["read"], "fulltext")
        self.assertEqual(self.errors(), [])

    def test_transition_rules_and_propagation(self):
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001", for_targets=["H001"])
        e_abs, _, _ = papers.record_evidence(
            self.st, self.lib, "H001", "contradict", pid, "摘要", locator=["abstract-p1"],
            quote="We study fine-grained contact-rich manipulation")
        with self.assertRaisesRegex(ValueError, "读过全文"):
            papers.transition_hypothesis(self.st, "H001", "refuted", [e_abs], "只有摘要")
        with self.assertRaisesRegex(ValueError, "只有研究者"):
            papers.transition_hypothesis(self.st, "H001", "abandoned", [e_abs], "x")
        e_full, _, _ = papers.record_evidence(
            self.st, self.lib, "H001", "contradict", pid, "全文", locator=["s4.2-p2"],
            quote="Raising the control frequency to 100 Hz with force input lifts success")
        # A001 支撑 H001 → H001 被反驳后 A001 进待重新审视（验收 4）
        a, ab = self.st.read_obj("A001")
        a["relied_on_by"] = ["Q001", "H001"]
        with self.st.tx("t", actor="human") as tx:
            tx.write_obj("A001", a, ab)
        old, new_reviews = papers.transition_hypothesis(self.st, "H001", "refuted", [e_full], "全文反驳")
        self.assertEqual(old, "proposed")
        targets = {self.st.read_obj(r)[0]["target"] for r in new_reviews}
        self.assertIn("A001", targets)
        self.assertEqual(self.errors(), [])

    def test_request_upload_and_dismiss(self):
        pid, _, msg = papers.register(self.st, self.lib, "10.1234/restricted.5678", for_targets=["H002"])
        self.assertIn("request_paper", msg)
        rid, new = papers.request_paper(self.st, pid, "要看实验细节", task="T00009")
        self.assertEqual(self.st.read_obj(pid)[0]["fulltext"], "requested")
        self.assertEqual(papers.request_paper(self.st, pid, "again")[0], rid)   # 不重复登记
        task, info = papers.fulfill_request(self.st, self.lib, rid, PDF.read_bytes())
        self.assertEqual(task, "T00009")
        self.assertEqual(self.st.read_obj(pid)[0]["fulltext"], "uploaded")
        self.assertTrue(self.lib.has_fulltext(pid))
        ok, _, basis = self.lib.verify_quote(pid, ["pg1"], "the tactile policy reached 88 percent success")
        self.assertEqual((ok, basis), (True, "fulltext"))
        self.assertFalse((self.st.state / "papers" / "source.pdf").exists())   # 原件不进 State
        self.assertEqual(self.errors(), [])


class GroundingAndCandidatesTest(Base):
    def test_grounding_only_annotates(self):
        before = self.st.read("hypotheses/H002.md")
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001")
        gid = papers.annotate_grounding(self.st, "H002", "prior_work", [pid], "已有人做过")
        self.assertEqual(self.st.read("hypotheses/H002.md"), before)
        self.assertEqual(self.st.read_obj(gid)[0]["target"], "H002")
        with self.assertRaisesRegex(ValueError, "refs"):
            papers.annotate_grounding(self.st, "H002", "contradicted", [], "x")

    def test_task_candidate_and_promotion(self):
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001", for_targets=["A001"])
        eid, _, _ = papers.record_evidence(
            self.st, self.lib, "A001", "support", pid, "n", locator=["s1-p2"],
            quote="We ask whether data scale or feedback frequency is the bottleneck")
        cid = candidates.propose(self.st, kind="hypothesis", statement="接口可以任务无关",
                                 rationale="审视发现可证伪", origin="ai", source="T00003", turns=[],
                                 basis=[eid], falsifier="f", validation="v", promoted_from="A001",
                                 actor="agent")
        with self.assertRaisesRegex(ValueError, "origin 只能是 ai"):
            candidates.propose(self.st, kind="uncertainty", statement="s", rationale="r",
                               origin="human", source="T00003", turns=[], basis=[eid],
                               importance="low")
        hid = candidates.accept(self.st, cid)
        a, _ = self.st.read_obj("A001")
        self.assertEqual((a["status"], a["promoted_to"]), ("promoted", hid))
        self.assertEqual(self.st.provenance()[hid], {"origin": "ai", "source": cid, "task": "T00003"})
        self.assertEqual(self.errors(), [])

    def test_accept_neutralizes_object_text(self):
        ds = discussion.create(self.st, "d")
        discussion.append_turn(self.st, ds, "human", "我认为粒度是瓶颈")
        cid = candidates.propose(self.st, kind="hypothesis", statement="研究者认为粒度是瓶颈",
                                 rationale="研究者在第 1 轮提出", origin="human", source=ds,
                                 turns=[1], falsifier="f", validation="AI 在第 1 轮提议做消融")
        hid = candidates.accept(self.st, cid)
        text = self.st.read(f"hypotheses/{hid}.md")
        self.assertEqual(attribution.cues(text), [], text)
        self.assertIn(f"来由见候选 {cid}", text)
        self.assertIn("研究者在第 1 轮提出", self.st.read(f"candidates/{cid}.md"))   # 原话留在候选里

    def test_lint_warns_on_attribution(self):
        h, b = self.st.read_obj("H001")
        with self.st.tx("t", actor="human") as tx:
            tx.write_obj("H001", h, b + "\n研究者倾向于认为这是对的。\n")
        warns = schema.validate_repo(self.st.state)[1]
        self.assertTrue(any("H001" in w and "署名线索" in w for w in warns), warns)


class JudgeFlagsTest(Base):
    def test_judge_isolation_and_edit_scope(self):
        state = self.st.state
        rules = tasks.deny_rules("read_paper", state)
        for p in ("provenance.json", "insights/**", "decisions/**", ".git/**", "candidates/**"):
            self.assertIn(f"Read(/{state}/{p})", rules)
        self.assertIn(f"Edit(/{state}/hypotheses/**)", rules)
        self.assertNotIn(f"Edit(/{state}/papers/**)", rules)
        self.assertIn("Write", rules)
        self.assertEqual([r for r in tasks.allow_rules("read_paper", state) if r.startswith("Edit")],
                         [f"Edit(/{state}/papers/**)"])
        self.assertNotIn("Edit", tasks.allow_rules("read_paper", state))   # 不给不限路径的 Edit

    def test_judge_briefing_redacts(self):
        from autoresearch.briefing import Assembler
        b = Assembler(self.st).briefing("judge", {"id": "T1", "kind": "assess", "goal": "g",
                                                  "target": "H002"})
        self.assertNotIn("提出者", b)
        self.assertNotIn("## 3b", b)
        self.assertNotIn("## 9. 候选区", b)
        self.assertIn("## 12. 文献", b)
        self.assertIn("本次目标：**H002**", b)


class ModesTest(Base):
    def test_handoff_and_recall(self):
        ds = discussion.create(self.st, "d")
        discussion.append_turn(self.st, ds, "human", "x")
        cid = candidates.propose(self.st, kind="assumption", statement="s", rationale="r",
                                 origin="human", source=ds, turns=[1], relied_on_by=["Q001"])
        with self.assertRaisesRegex(ValueError, "不能为空"):
            modes.handoff(self.st, [])
        did, batch = modes.handoff(self.st, ["H001", cid], "先看粒度")
        self.assertEqual(batch[0], "H001")
        self.assertTrue(batch[1].startswith("A"))                  # 候选在交棒时确认入库
        self.assertEqual(modes.mode(self.st), "validation")
        self.assertEqual(modes.batch(self.st), batch)
        dm, db = self.st.read_obj(did)
        self.assertEqual((dm["kind"], dm["refs"]), ("handoff", batch))
        self.assertIn("先看粒度", db)
        rid = modes.recall(self.st, "有结果了")
        self.assertEqual(modes.mode(self.st), "discussion")
        self.assertEqual(modes.batch(self.st), [])
        self.assertEqual(self.st.read_obj(rid)[0]["kind"], "mode")
        self.assertEqual(self.errors(), [])


class DaemonPhase2Test(Base):
    def setUp(self):
        super().setUp()
        os.environ["FAKE_MODE"] = "judge"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("FAKE_MODE", "FAKE_BAD_EDIT", "FAKE_REF_H001", "FAKE_REF_H002",
                                  "FAKE_VERDICT")])
        self.d = Daemon(self.cfg, self.st, Bus(self.cfg))
        self.d.start(background=False)
        self.addCleanup(lambda: [r.kill() for r in list(self.d.runners.values())])

    def until(self, cond, timeout=40):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.d.tick()
            if cond():
                return
            time.sleep(0.1)
        self.fail("等待超时：" + json.dumps([(t["id"], t["kind"], t["status"], t.get("error"))
                                          for t in self.d.ledger.all()], ensure_ascii=False))

    def kinds(self):
        return [(t["kind"], t["status"]) for t in self.d.ledger.all()]

    def test_validation_loop_refutes_then_holds(self):
        os.environ["FAKE_BAD_EDIT"] = "1"
        a, ab = self.st.read_obj("A001")
        a["relied_on_by"] = ["Q001", "H001"]
        with self.st.tx("t", actor="human") as tx:
            tx.write_obj("A001", a, ab)
        self.d.handoff(["H001"], "验证粒度假设")
        self.until(lambda: self.d.st.get("hold"))
        ks = [k for k, _ in self.kinds()]
        self.assertEqual(ks[:3], ["lit_search", "read_paper", "assess"])
        self.assertEqual(self.st.read_obj("H001")[0]["status"], "refuted")
        self.assertIn("refuted", self.d.st["hold"]["reason"])
        # 精读任务的笔记被提交；越界改 title 被回滚
        p, body = self.st.read_obj("P001")
        self.assertIn("Contact-Rich", p["title"])
        self.assertIn("固定低频 chunk 饱和", body)
        read_task = next(t for t in self.d.ledger.all() if t["kind"] == "read_paper")
        v = (self.d.ledger.path(read_task["id"]) / "violations.jsonl").read_text(encoding="utf-8")
        self.assertIn("title", v)
        self.assertEqual(self.st.dirty_paths(), [])
        # 做迁移的 session 看不到 origin
        assess = next(t for t in self.d.ledger.all() if t["kind"] == "assess")
        brief = (self.d.ledger.path(assess["id"]) / "briefing.md").read_text(encoding="utf-8")
        self.assertNotIn("提出者", brief)
        cmd = json.loads((self.d.ledger.path(assess["id"]) / "cmd.json").read_text(encoding="utf-8"))
        self.assertIn(f"Read(/{self.st.state}/provenance.json)", cmd)
        # 推翻的传播（验收 4）
        rv = [m for m, _ in self.st.list("review")]
        self.assertTrue(any(m["trigger"] == "H001" and m["target"] == "A001" for m in rv))
        # hold 期间不派发新的验证任务；收回后排队的取消
        n = len(self.d.ledger.all())
        for _ in range(10):
            self.d.tick()
        self.assertEqual(len(self.d.ledger.all()), n)
        self.d.recall("回去讨论")
        self.assertEqual(modes.mode(self.st), "discussion")
        self.assertIsNone(self.d.st.get("hold"))
        self.assertEqual(self.errors(), [])

    def test_blocked_task_does_not_stall_loop(self):
        os.environ["FAKE_REF_H001"] = "10.1234/restricted.5678"
        os.environ["FAKE_VERDICT"] = "inconclusive"      # 不触发“重大结果”，看流水线本身
        self.d.handoff(["H002", "H001"], "")
        # H001 的论文只有 DOI → 精读请求全文 → 挂起；H002 的流水线照常推进到评估
        self.until(lambda: any(t["status"] == "blocked_on_human" for t in self.d.ledger.all())
                   and self.st.read_obj("H002")[0]["status"] == "inconclusive", timeout=60)
        blocked = next(t for t in self.d.ledger.all() if t["status"] == "blocked_on_human")
        rid = blocked["blocked_on"]
        self.assertEqual(self.st.read_obj(rid)[0]["status"], "open")
        self.d.st.pop("hold", None)          # 模拟研究者选择继续验证
        papers.fulfill_request(self.st, self.lib, rid, PDF.read_bytes())
        self.assertEqual(self.d.unblock(rid), 1)
        self.until(lambda: self.d.ledger.get(blocked["id"])["status"] == "done")
        t = self.d.ledger.get(blocked["id"])
        self.assertEqual(t["attempts"], 2)
        ev = [m for m, _ in self.st.list("evidence") if m["target"] == "H001"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["basis"], "fulltext")

    def test_unattended_cap(self):
        self.d.quota.data["five_hour"] = 0.7
        self.d.handoff(["H001"], "")
        for _ in range(20):
            self.d.tick()
            time.sleep(0.02)
        self.assertEqual([s for _, s in self.kinds()], ["queued"])     # 排了但没启动
        self.assertTrue(self.d.st.get("cap_note"))

    def test_saturation_holds_and_leaves_quota(self):
        os.environ["FAKE_REF_H001"] = "2401.99999"     # 检索到的编号查无此文 → 登记失败
        self.d.handoff(["H001"], "")
        self.until(lambda: self.d.st.get("hold"), timeout=60)
        self.assertIn("leave quota", self.d.st["hold"]["reason"])
        self.assertEqual([k for k, _ in self.kinds()], ["lit_search", "lit_search"])

    def test_prep_in_discussion_mode(self):
        ds = discussion.create(self.st, "d")
        discussion.append_turn(self.st, ds, "human", "今天先到这")
        # 把最后一次发言挪到两小时前
        p = self.st.state / discussion.rel_transcript(ds)
        t = p.read_text(encoding="utf-8")
        import re
        t = re.sub(r"(<!-- turn 1 human )(\S+)( -->)", r"\g<1>2000-01-01T00:00:00\3", t)
        p.write_text(t, encoding="utf-8")
        self.d.st.setdefault("prep", {})
        self.d._absorb_prep_request()
        self.d.set_prep_request("接触感知的高频闭环有没有人做过", "测试")
        # 讨论回合的 reply 模式不会被触发（没有待回复），夜间预习启动
        self.until(lambda: (self.d.st.get("prep") or {}).get("ended"), timeout=60)
        prep = self.d.st["prep"]
        dm, db = self.st.read_obj(prep["decision"])
        self.assertIn("你要求的", db)
        ks = [(t["kind"], t.get("prep")) for t in self.d.ledger.all()]
        self.assertEqual([k for k, _ in ks], ["lit_search", "read_paper"])
        self.assertNotIn("assess", [k for k, _ in ks])            # 预习不评估、不迁移
        self.assertEqual(self.st.read_obj("H001")[0]["status"], "proposed")


if __name__ == "__main__":
    unittest.main()


class ArxivDoiTest(Base):
    def test_arxiv_doi_is_treated_as_arxiv(self):
        pid, new, msg = papers.register(self.st, self.lib, "10.48550/arXiv.2401.00001", for_targets=["H001"])
        m, _ = self.st.read_obj(pid)
        self.assertEqual((m["arxiv"], m["fulltext"]), ("2401.00001", "open"))
        self.assertTrue(self.lib.has_fulltext(pid))
        self.assertEqual(papers.register(self.st, self.lib, "2401.00001")[0], pid)   # 两种写法去重

    def test_repair_old_doi_registrations(self):
        with self.st.tx("旧登记", actor="agent") as tx:
            tx.write_obj("P001", {"id": "P001", "type": "paper", "title": "T", "doi": "10.48550/arxiv.2401.00001",
                                  "read": "abstract", "fulltext": "requested", "created": "2026-09-23"}, "x\n")
        rid, _ = papers.request_paper(self.st, "P001", "要全文")
        self.assertEqual(papers.repair_arxiv_dois(self.st, self.lib)[0][0], "P001")
        m, _ = self.st.read_obj("P001")
        self.assertEqual((m["arxiv"], m["fulltext"]), ("2401.00001", "open"))
        self.assertEqual(self.st.read_obj(rid)[0]["status"], "fulfilled")
        self.assertEqual(self.errors(), [])
