"""Phase 2.7：问题的生命周期、活跃集、理解的合并、问题树、Tidy up（DESIGN.md §5.16）。"""
import json
import os
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from autoresearch import (bootstrap, briefing, candidates, discussion, incubation, insights, objects,
                          planner, questions, reviews, schema)
from tests.test_phase26 import mk_ds, propose_q
from tests.util import temp_cfg

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


def brief(st, profile="discuss", ds=None):
    return briefing.Assembler(st).briefing(profile, {"id": "T1", "kind": "discuss_turn", "goal": "回复",
                                                     "discussion": ds})


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.ds = mk_ds(self.st)

    def ok(self):
        errs, _ = schema.validate_repo(self.cfg.state)
        self.assertEqual(errs, [])

    def q(self, text="子问题", rel=("Q001",), **kw):
        return candidates.accept(self.st, propose_q(self.st, self.ds, text, rel=rel), **kw)

    def ins(self, text="理解", **kw):
        return insights.create(self.st, text, kw.pop("firmness", "hunch"), basis=kw.pop("basis", [self.ds]), **kw)


class LifecycleTest(Base):
    def test_answered_and_reopen(self):
        q, i = self.q(), self.ins()
        did, research = questions.resolve(self.st, q, "answered", answered_by=[i], reason="聊透了")
        self.assertIsNone(research)
        m, body = self.st.read_obj(q)
        self.assertEqual((m["status"], m["answered_by"]), ("answered", [i]))
        self.assertIn("## 结（", body)
        self.assertIn("子问题", objects.statement_of(body))          # 陈述不动
        dm, _ = self.st.read_obj(did)
        self.assertEqual((dm["kind"], dm["refs"]), ("curation", [q, i]))
        self.ok()
        did2 = questions.reopen(self.st, q, "又有了新疑问")
        m, body = self.st.read_obj(q)
        self.assertNotIn("status", m)
        self.assertNotIn("answered_by", m)
        self.assertIn("## 重开（", body)
        self.assertIn(i, self.st.read_obj(did2)[1])                  # 旧值留档
        self.assertIn(i, body)
        self.ok()

    def test_decided_writes_research_decision(self):
        q = self.q("定位：独立模块还是协同设计？")
        with self.assertRaisesRegex(ValueError, "决定本身"):
            questions.resolve(self.st, q, "decided", reason="拍板")
        did, research = questions.resolve(self.st, q, "decided", decision="选独立模块：可被任意上层驱动。",
                                          reason="讨论后拍板")
        rm, rb = self.st.read_obj(research)
        self.assertEqual((rm["kind"], rm["refs"]), ("research", [q]))
        self.assertIn("选独立模块", rb)
        self.assertEqual(self.st.read_obj(q)[0]["decided_by"], research)
        questions.reopen(self.st, q, "研究者认为还要再议")
        warns = schema.validate_repo(self.cfg.state)[1]
        self.assertFalse([w for w in warns if q in w], warns)       # 状态史不带署名线索（§5.6）
        self.ok()

    def test_merged_and_target_page_shows_source(self):
        a, b = self.q("甲"), self.q("乙")
        focus = discussion.create(self.st, "", focus=a)
        child = self.q("甲的子问题")
        questions.set_parent(self.st, child, a)
        questions.resolve(self.st, a, "merged", merged_into=b, reason="其实是同一个问题")
        info = objects.links(self.st, b)
        self.assertIn(focus, [d["id"] for d in info["discussions"] if d.get("via") == a])
        self.assertIn(child, [r["id"] for r in info["related"] if r.get("via") == a])
        self.assertEqual(info["question"]["merged_from"], [a])
        self.assertEqual(self.st.read_obj(child)[0]["parent"], a)     # 子问题的 parent 不自动改写
        with self.assertRaisesRegex(ValueError, "只能并入 open"):
            questions.resolve(self.st, self.q("丙"), "merged", merged_into=a, reason="x")
        self.ok()

    def test_guards(self):
        q = self.q()
        with self.assertRaisesRegex(ValueError, "主问题"):
            questions.resolve(self.st, "Q001", "answered", answered_by=["H001"], reason="x")
        with self.assertRaisesRegex(ValueError, "自己"):
            questions.resolve(self.st, q, "merged", merged_into=q, reason="x")
        with self.assertRaisesRegex(ValueError, "不存在"):
            questions.resolve(self.st, q, "answered", answered_by=["IN099"], reason="x")
        with self.assertRaisesRegex(ValueError, "理由"):
            questions.resolve(self.st, q, "answered", answered_by=["H001"], reason=" ")
        questions.resolve(self.st, q, "answered", answered_by=["H001"], reason="x")
        with self.assertRaisesRegex(ValueError, "只有 open"):
            questions.resolve(self.st, q, "answered", answered_by=["H001"], reason="x")
        with self.assertRaisesRegex(ValueError, "重开"):
            objects.withdraw(self.st, q, "不要了")
        with self.assertRaisesRegex(ValueError, "没有结"):
            questions.reopen(self.st, "Q001", "x")

    def test_validator(self):
        q = self.q()
        m, b = self.st.read_obj(q)
        with self.st.tx("手改", actor="human") as tx:
            tx.write_obj(q, dict(m, status="answered"), b)
        errs, _ = schema.validate_repo(self.cfg.state)
        self.assertTrue(any("answered_by" in e for e in errs))
        with self.st.tx("手改", actor="human") as tx:
            tx.write_obj(q, dict(m, parent=q), b)
        self.assertTrue(any("成环" in e or "自己" in e for e in schema.validate_repo(self.cfg.state)[0]))
        m1, b1 = self.st.read_obj("Q001")
        with self.st.tx("手改", actor="human") as tx:
            tx.write_obj(q, dict(m, parent="Q001"), b)
            tx.write_obj("Q001", dict(m1, status="decided", decided_by="DEC999"), b1)
        errs = schema.validate_repo(self.cfg.state)[0]
        self.assertTrue(any("主问题不能结" in e for e in errs))


class ResolveCandidateTest(Base):
    def test_resolve_with_pending_insight_candidate(self):
        q = self.q()
        ic = candidates.propose(self.st, kind="insight", statement="答案", rationale="r", origin="human",
                                source=self.ds, turns=[3], firmness="working", basis=[self.ds])
        rc = candidates.propose(self.st, kind="resolve", target=q, resolution="answered", answered_by=[ic],
                                statement=f"{q} 被回答了", rationale="第 3 轮", origin="human",
                                source=self.ds, turns=[3])
        self.ok()
        c = next(x for x in candidates.list_all(self.st) if x["id"] == rc)
        self.assertEqual(c["refs"][0]["status"], "pending")
        with self.assertRaises(questions.PendingRefs) as e:
            candidates.accept(self.st, rc)
        self.assertEqual(e.exception.refs, [ic])
        new = candidates.accept(self.st, ic)                        # 一并确认：先确认被引用的
        self.assertEqual(candidates.accept(self.st, rc), q)
        m, _ = self.st.read_obj(q)
        self.assertEqual((m["status"], m["answered_by"]), ("answered", [new]))
        cm, _ = self.st.read_obj(rc)
        self.assertEqual((cm["status"], cm["promoted_to"], cm["answered_by"]), ("accepted", q, [new]))
        self.ok()

    def test_rejected_ref_blocks(self):
        q = self.q()
        ic = candidates.propose(self.st, kind="insight", statement="答案", rationale="r", origin="human",
                                source=self.ds, turns=[3], firmness="working", basis=[self.ds])
        rc = candidates.propose(self.st, kind="resolve", target=q, resolution="answered", answered_by=[ic],
                                statement="x", rationale="r", origin="human", source=self.ds, turns=[3])
        candidates.reject(self.st, ic, "不对")
        with self.assertRaisesRegex(ValueError, "rejected"):
            candidates.accept(self.st, rc)
        self.assertEqual(self.st.read_obj(q)[0].get("status"), None)

    def test_decided_and_merged_candidates(self):
        a, b = self.q("甲"), self.q("乙")
        dc = candidates.propose(self.st, kind="resolve", target=a, resolution="decided",
                                statement="建议：选独立模块。", rationale="r", origin="ai", source=self.ds, turns=[2])
        candidates.accept(self.st, dc, decision="选独立模块（研究者改过措辞）。")
        dm, db = self.st.read_obj(self.st.read_obj(a)[0]["decided_by"])
        self.assertIn("研究者改过措辞", db)
        c = self.q("丙")
        mc = candidates.propose(self.st, kind="resolve", target=c, resolution="merged", merged_into=b,
                                statement="与乙重复", rationale="r", origin="ai", source=self.ds, turns=[2])
        candidates.accept(self.st, mc)
        self.assertEqual(self.st.read_obj(c)[0]["merged_into"], b)
        self.ok()
        with self.assertRaisesRegex(ValueError, "主问题"):
            candidates.propose(self.st, kind="resolve", target="Q001", resolution="answered",
                               answered_by=["H001"], statement="x", rationale="r", origin="ai",
                               source=self.ds, turns=[2])
        pc = candidates.propose(self.st, kind="resolve", target=self.q("丁"), resolution="merged", merged_into=b,
                                statement="与乙重复", rationale="r", origin="ai", source=self.ds, turns=[2])
        with self.assertRaisesRegex(ValueError, "不能互相改类别"):
            candidates.update(self.st, pc, {"kind": "question"})

    def test_stale_when_target_closed_elsewhere(self):
        q = self.q()
        rc = candidates.propose(self.st, kind="resolve", target=q, resolution="answered", answered_by=["H001"],
                                statement="x", rationale="r", origin="ai", source=self.ds, turns=[2])
        questions.resolve(self.st, q, "answered", answered_by=["H001"], reason="手动结了")
        self.assertTrue(next(x for x in candidates.list_all(self.st) if x["id"] == rc)["stale"])
        with self.assertRaisesRegex(ValueError, "只有 open"):
            candidates.accept(self.st, rc)


class MergeInsightTest(Base):
    def test_merge_candidate(self):
        i1 = self.ins("接口要稀疏", basis=["H001"], informs=["Q001"])
        i2 = self.ins("接口要稠密", basis=["H002"], informs=["A001"], basis_note="手感")
        a = candidates.accept(self.st, candidates.propose(
            self.st, kind="assumption", statement="凭 i1 排除高频接口", rationale="r", origin="human",
            source=self.ds, turns=[1], relied_on_by=["Q001"], derived_from=i1))
        c = candidates.propose(self.st, kind="insight", supersedes=[i1, i2], statement="时间稀疏、信息稠密",
                               rationale="同一件事的两面", origin="ai", source=self.ds, turns=[2],
                               firmness="hunch", actor="agent")
        self.ok()
        new = candidates.accept(self.st, c, changes={"firmness": "working"})
        nm, _ = self.st.read_obj(new)
        self.assertEqual(nm["consolidates"], [i1, i2])
        self.assertEqual(nm["firmness"], "working")
        for b in ("H001", "H002", i1, i2, self.ds):
            self.assertIn(b, nm["basis"])
        self.assertEqual(set(nm["informs"]), {"Q001", "A001"})
        for i in (i1, i2):
            m, _ = self.st.read_obj(i)
            self.assertEqual((m["status"], m["superseded_by"]), ("superseded", new))
        self.assertEqual(reviews.reconcile(self.st), [])                # 派生的前提不进 Review
        info = objects.links(self.st, a)
        self.assertEqual(info["derived_merged"], {"from": i1, "into": new})
        self.assertEqual(objects.links(self.st, i1)["merged_into"], new)
        self.ok()
        insights.abandon(self.st, new, "整条都不对")
        self.assertIn(a, [r["target"] for r in reviews.list_all(self.st, "open")])   # 合成的被放弃才传播

    def test_merge_validation(self):
        i1 = self.ins()
        with self.assertRaisesRegex(ValueError, "至少"):
            candidates.propose(self.st, kind="insight", supersedes=[i1], statement="x", rationale="r",
                               origin="ai", source=self.ds, turns=[2], firmness="hunch")
        i2 = self.ins("另一条")
        insights.abandon(self.st, i2, "不要了")
        with self.assertRaisesRegex(ValueError, "active"):
            candidates.propose(self.st, kind="insight", supersedes=[i1, i2], statement="x", rationale="r",
                               origin="ai", source=self.ds, turns=[2], firmness="hunch")


class ActiveAndBriefingTest(Base):
    def test_layered_chapter_3(self):
        act, idle, done, dec = self.q("活跃的问题"), self.q("闲着的问题"), self.q("结了的问题"), self.q("拍板的问题")
        wd = self.q("撤下的问题")
        questions.set_active(self.st, act, True)
        questions.set_parent(self.st, idle, act)
        i = self.ins("答案")
        questions.resolve(self.st, done, "answered", answered_by=[i], reason="r")
        questions.resolve(self.st, dec, "decided", decision="就选 A。", reason="r")
        objects.withdraw(self.st, wd, "不再相关")
        self.assertEqual(self.st.read_obj(act)[0]["active"], "true")
        text = brief(self.st, ds=self.ds)
        ch3 = text.split("## 3. 研究问题")[1].split("## 3b.")[0]
        self.assertIn("### Q001（主问题", ch3)
        self.assertIn(f"#### {act}（成熟度 vague", ch3)
        self.assertIn("活跃的问题", ch3)
        self.assertIn(f"- {idle} · vague · 母问题 {act}：闲着的问题", ch3)
        self.assertIn(f"- {done}（已回答）：结了的问题 → 答案见 {i}", ch3)
        self.assertIn("就选 A。", ch3)
        self.assertNotIn(wd, ch3)
        self.assertIn(f"- {wd}：", text.split("## 已撤下")[1])
        fb = incubation.foundation_body(self.st)
        self.assertIn("当前关注", fb)
        self.assertIn(act, fb)
        self.assertIn("已有结论", fb)
        self.assertNotIn(idle, fb)
        self.ok()
        with self.assertRaisesRegex(ValueError, "只有 open"):
            questions.set_active(self.st, done, True)
        self.assertTrue(questions.set_active(self.st, act, False))
        self.assertNotIn("active", self.st.read_obj(act)[0])

    def test_prep_picks_active_question(self):
        for m, _ in self.st.list("assumption"):
            objects.withdraw(self.st, m["id"], "x")
        for m, _ in self.st.list("hypothesis"):
            objects.withdraw(self.st, m["id"], "x")
        self.assertEqual(planner.pick_prep_target(self.st), (None, None))
        q = self.q()
        questions.set_active(self.st, q, True)
        self.assertEqual(planner.pick_prep_target(self.st)[0], q)


class TreeTest(Base):
    def test_tree_unplaced_and_suggestion(self):
        a, b = self.q("甲"), self.q("乙", rel=("Q001", "H001"))
        c = self.q("丙", rel=())
        t = questions.tree(self.st)
        self.assertEqual(t["main"], "Q001")
        self.assertEqual(t["unplaced"], [a, b, c])
        self.assertEqual((t["nodes"][a]["suggest_parent"], t["nodes"][c]["suggest_parent"]), ("Q001", None))
        questions.set_parent(self.st, a, "Q001")
        questions.set_parent(self.st, b, a)
        with self.assertRaisesRegex(ValueError, "成环"):
            questions.set_parent(self.st, a, b)
        with self.assertRaisesRegex(ValueError, "根"):
            questions.set_parent(self.st, "Q001", a)
        t = questions.tree(self.st)
        self.assertEqual((t["nodes"]["Q001"]["children"], t["nodes"][a]["children"]), ([a], [b]))
        self.assertEqual(t["unplaced"], [c])
        i = self.ins("答案", informs=[a])
        self.assertEqual(questions.tree(self.st)["nodes"][a]["insights"], [i])
        self.ok()

    def test_candidate_parent_and_focus_default(self):
        q = self.q()
        fds = discussion.create(self.st, "", focus=q)
        discussion.append_turn(self.st, fds, "human", "拆出一个子问题")
        c = candidates.propose(self.st, kind="question", statement="子子问题", rationale="r", origin="human",
                               source=fds, turns=[1], maturity="vague")
        self.assertEqual(self.st.read_obj(c)[0]["parent"], q)
        new = candidates.accept(self.st, c)
        self.assertEqual(self.st.read_obj(new)[0]["parent"], q)
        c2 = candidates.propose(self.st, kind="question", statement="另一个", rationale="r", origin="human",
                                source=self.ds, turns=[1], maturity="vague", parent="Q001")
        self.assertEqual(self.st.read_obj(candidates.accept(self.st, c2))[0]["parent"], "Q001")
        with self.assertRaises(ValueError):
            candidates.propose(self.st, kind="question", statement="x", rationale="r", origin="human",
                               source=self.ds, turns=[1], maturity="vague", parent="Q099")
        self.ok()


class TidyRulesTest(Base):
    def test_tidy_only_merges_and_resolves(self):
        i1, i2 = self.ins("甲"), self.ins("乙")
        with self.assertRaisesRegex(ValueError, "整理任务只提"):
            candidates.propose(self.st, kind="question", statement="x", rationale="r", origin="ai",
                               source="T00001", turns=[], maturity="vague", tidy=True)
        with self.assertRaisesRegex(ValueError, "拍板"):
            candidates.propose(self.st, kind="resolve", target=self.q(), resolution="decided", statement="x",
                               rationale="r", origin="ai", source="T00001", turns=[], tidy=True)
        with self.assertRaisesRegex(ValueError, "只出自讨论或整理"):
            candidates.propose(self.st, kind="insight", supersedes=[i1, i2], statement="x", rationale="r",
                               origin="ai", source="T00001", turns=[], firmness="hunch", basis=["E001"])
        c = candidates.propose(self.st, kind="insight", supersedes=[i1, i2], statement="合", rationale="r",
                               origin="ai", source="T00001", turns=[], firmness="hunch", tidy=True, actor="agent")
        self.ok()
        new = candidates.accept(self.st, c)
        self.assertEqual(self.st.read_obj(new)[0]["consolidates"], [i1, i2])
        self.ok()


class LegacyCompatTest(unittest.TestCase):
    """用户真实 State 的形态：Q001–Q005 无 status / active / parent，Q002–Q005 relates_to [Q001]。"""

    def test_legacy(self):
        cfg = temp_cfg(self)
        st, _ = bootstrap.init(cfg)
        bootstrap.setup(st, "项目", "描述", "主问题？", "vague")
        ds = mk_ds(st)
        qs = [candidates.accept(st, propose_q(st, ds, f"子问题 {i}")) for i in range(4)]
        self.assertEqual(schema.validate_repo(cfg.state)[0], [])
        t = questions.tree(st)
        self.assertEqual(t["unplaced"], qs)
        self.assertTrue(all(t["nodes"][q]["suggest_parent"] == "Q001" for q in qs))
        ch3 = briefing.Assembler(st).briefing("discuss", {"id": "T1", "kind": "discuss_turn", "goal": "g",
                                                          "discussion": ds}).split("## 3. ")[1].split("## 3b.")[0]
        for q in qs:
            self.assertIn(f"- {q} · vague：", ch3)


class ApiTest(unittest.TestCase):
    def setUp(self):
        from autoresearch.daemon import Daemon
        from autoresearch.events import Bus
        from autoresearch.server import App
        os.environ["FAKE_MODE"] = "reply"
        self.addCleanup(os.environ.pop, "FAKE_MODE", None)
        self.cfg = temp_cfg(self, AR_CLAUDE_BIN=FAKE, AR_PORT=0, AR_DISTILL_EVERY=100)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.daemon = Daemon(self.cfg, self.st, Bus(self.cfg))
        self.daemon.start()
        self.addCleanup(self.daemon.stop)
        self.httpd = App(self.cfg, self.st, self.daemon.bus, self.daemon).serve()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def req(self, method, path, body=None):
        r = urllib.request.Request(self.base + path, method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait(self, cond, timeout=20):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if cond():
                return
            time.sleep(0.2)
        self.fail("timeout")

    def pending(self):
        return self.req("GET", "/api/candidates?status=pending")[1]

    def test_focus_answer_resolve_reopen(self):
        ds = mk_ds(self.st)
        q = candidates.accept(self.st, propose_q(self.st, ds))
        fds = self.req("POST", "/api/discussions", {"focus": q})[1]["id"]
        self.req("POST", f"/api/discussions/{fds}/messages", {"text": "这个问题就这样了：只要几何子目标"})
        self.wait(lambda: len(self.req("GET", f"/api/discussions/{fds}")[1]["turns"]) == 2)
        os.environ["FAKE_MODE"] = "distill_resolve"
        self.req("POST", f"/api/discussions/{fds}/distill")
        self.wait(lambda: any(c["kind"] == "resolve" for c in self.pending()))
        rc = next(c for c in self.pending() if c["kind"] == "resolve")
        ic = rc["refs"][0]["id"]
        s, r = self.req("POST", f"/api/candidates/{rc['id']}/accept", {})
        self.assertEqual((s, r["pending_refs"]), (409, [ic]))
        new = self.req("POST", f"/api/candidates/{ic}/accept", {})[1]["id"]
        self.assertEqual(self.req("POST", f"/api/candidates/{rc['id']}/accept", {})[0], 200)
        info = self.req("GET", f"/api/objects/{q}/links")[1]
        self.assertEqual((info["question"]["status"], info["question"]["answered_by"][0]["id"]), ("answered", new))
        s, _ = self.req("POST", f"/api/questions/{q}/reopen", {"reason": "还没完"})
        self.assertEqual(s, 200)
        self.assertEqual(self.req("GET", f"/api/objects/{q}/links")[1]["question"]["status"], "open")
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])

    def test_endpoints(self):
        ds = mk_ds(self.st)
        a = candidates.accept(self.st, propose_q(self.st, ds, "甲"))
        b = candidates.accept(self.st, propose_q(self.st, ds, "乙"))
        self.assertEqual(self.req("POST", f"/api/questions/{a}/active", {"active": True})[0], 200)
        self.assertEqual(self.req("POST", f"/api/questions/{b}/parent", {"parent": "Q001"})[0], 200)
        s, r = self.req("POST", f"/api/questions/{a}/parent", {"parent": a})
        self.assertEqual(s, 400)
        o = self.req("GET", "/api/overview")[1]
        self.assertEqual((o["tree"]["active"], o["tree"]["unplaced"]), ([a], [a]))
        s, r = self.req("POST", f"/api/questions/{a}/resolve", {"resolution": "decided", "decision": "选甲",
                                                              "reason": "拍板"})
        self.assertEqual(s, 200)
        self.assertTrue(r["research"])
        self.assertEqual(self.req("POST", "/api/questions/Q001/resolve",
                                  {"resolution": "merged", "merged_into": b, "reason": "x"})[0], 400)

    def test_tidy_task(self):
        i1 = insights.create(self.st, "接口要稀疏", "hunch", basis=["H001"])
        i2 = insights.create(self.st, "接口要稠密", "hunch", basis=["H002"])
        s, r = self.req("POST", "/api/tidy", {})
        self.assertEqual(s, 200)
        self.wait(lambda: any(c.get("supersedes") for c in self.pending()))
        c = next(c for c in self.pending() if c.get("supersedes"))
        self.assertEqual((c["supersedes"], c["origin"], c["source"]), ([i1, i2], "ai", r["task"]["id"]))
        self.wait(lambda: (self.req("GET", "/api/overview")[1]["tidy"] or {}).get("status") == "done")
        self.assertIn(c["id"], self.req("GET", "/api/overview")[1]["tidy"]["result_brief"])
        new = self.req("POST", f"/api/candidates/{c['id']}/accept", {"changes": {"firmness": "working"}})[1]["id"]
        self.assertEqual(self.st.read_obj(new)[0]["consolidates"], [i1, i2])
        os.environ["FAKE_TIDY"] = "blank"
        self.addCleanup(os.environ.pop, "FAKE_TIDY", None)
        tid = self.req("POST", "/api/tidy", {})[1]["task"]["id"]
        self.wait(lambda: (self.req("GET", "/api/overview")[1]["tidy"] or {}).get("id") == tid and
                  self.req("GET", "/api/overview")[1]["tidy"]["status"] == "done")
        self.assertIn("白卷", self.req("GET", "/api/overview")[1]["tidy"]["result_brief"])
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
