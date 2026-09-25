"""Phase 2.8：理解的独立表述、星标、手动合并（DESIGN.md §5.17）。自演进的重做见 test_evolution.py。"""
import os
import unittest

from autoresearch import bootstrap, briefing, candidates, insights, protocol, reviews, schema
from autoresearch.store import today
from tests.test_phase26 import mk_ds
from tests.test_phase27 import ApiTest as _Api
from tests.util import temp_cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.ds = mk_ds(self.st)

    def ok(self):
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def ins(self, text, **kw):
        return insights.create(self.st, text, kw.pop("firmness", "hunch"), basis=[self.ds], **kw)


class StarTest(Base):
    def test_star_layers_chapter_3b(self):
        a, b = self.ins("星标的理解全文"), self.ins("没星标的理解开头" + "长" * 200 + "结尾不该出现")
        self.assertTrue(insights.set_starred(self.st, a, True))
        self.assertFalse(insights.set_starred(self.st, a, True))
        text = briefing.Assembler(self.st).briefing("discuss", {"id": "T1", "kind": "discuss_turn", "goal": "g",
                                                               "discussion": self.ds})
        ch = text.split("## 3b.")[1].split("## 4.")[0]
        self.assertIn(f"#### {a} · 直觉", ch)
        self.assertIn("星标的理解全文", ch)
        self.assertIn(f"- {b} · 直觉：没星标的理解开头", ch)
        self.assertNotIn("结尾不该出现", ch)
        tidy = briefing.Assembler(self.st).briefing("tidy", {"id": "T2", "kind": "tidy", "goal": "g"})
        self.assertIn("结尾不该出现", tidy)                         # 整理任务仍看全文
        self.ok()
        new = insights.revise(self.st, a, "改过的星标理解", "working")
        self.assertTrue(schema.is_starred(self.st.read_obj(new)[0]))   # 修订继承星标
        insights.abandon(self.st, b, "不要了")
        with self.assertRaisesRegex(ValueError, "只有 active"):
            insights.set_starred(self.st, b, True)
        self.assertTrue(insights.set_starred(self.st, new, False))
        self.assertNotIn("starred", self.st.read_obj(new)[0])

    def test_manual_merge_inherits_star(self):
        a, b = self.ins("甲", informs=["Q001"]), self.ins("乙")
        insights.set_starred(self.st, b, True)
        new = insights.consolidate(self.st, [a, b], "甲乙合一", "working", origin="human", source="direct")
        m, _ = self.st.read_obj(new)
        self.assertEqual((m["consolidates"], m["starred"], m["informs"]), ([a, b], "true", ["Q001"]))
        self.assertEqual(self.st.provenance()[new]["origin"], "human")
        self.assertEqual(reviews.reconcile(self.st), [])
        self.ok()


class StandaloneTest(Base):
    def test_protocols_ask_for_standalone_insights(self):
        for p in (protocol.DISTILL_PROTOCOL, protocol.DISCUSS_PROTOCOL):
            self.assertIn("脱离讨论也能读懂", p)
        self.assertIn("读不懂的理解改写成独立表述", protocol.TIDY_PROTOCOL)

    def test_tidy_rewrite_candidate(self):
        i = self.ins("上面那个方案更好", firmness="working")
        kw = dict(statement="接口在时间上稀疏、信息上稠密更好。", rationale="原文有指代", origin="ai",
                  source="T00001", turns=[], tidy=True, actor="agent", base_revision=1)
        with self.assertRaisesRegex(ValueError, "只改措辞"):
            candidates.propose(self.st, kind="revision", target=i, firmness="settled", **kw)
        with self.assertRaisesRegex(ValueError, "只针对理解"):
            candidates.propose(self.st, kind="revision", target="Q001", **kw)
        with self.assertRaisesRegex(ValueError, "只出自讨论"):
            candidates.propose(self.st, kind="revision", target=i, **dict(kw, tidy=False))
        c = candidates.propose(self.st, kind="revision", target=i, **kw)
        self.ok()
        new = candidates.accept(self.st, c)
        self.assertNotEqual(new, i)
        m, _ = self.st.read_obj(new)
        self.assertEqual(m["firmness"], "working")
        self.assertEqual(self.st.read_obj(i)[0]["superseded_by"], new)
        self.ok()


class ApiTest(_Api):
    def test_star_and_merge_endpoints(self):
        a = insights.create(self.st, "甲", "hunch", basis_note="直觉")
        b = insights.create(self.st, "乙", "hunch", basis_note="直觉")
        self.assertEqual(self.req("POST", f"/api/insights/{a}/star", {"starred": True})[0], 200)
        o = self.req("GET", "/api/overview")[1]
        self.assertEqual(next(i for i in o["insights"] if i["id"] == a)["starred"], "true")
        s, r = self.req("POST", "/api/insights/merge", {"supersedes": [a], "statement": "x", "firmness": "working"})
        self.assertEqual(s, 400)
        s, r = self.req("POST", "/api/insights/merge", {"supersedes": [a, b], "statement": "甲乙", "firmness": "working"})
        self.assertEqual(s, 200)
        self.assertEqual(self.st.read_obj(r["id"])[0]["consolidates"], [a, b])
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])

    def test_tidy_rewrite_via_task(self):
        i = insights.create(self.st, "上面那个方案更好", "working", basis_note="讨论")
        os.environ["FAKE_TIDY"] = "rewrite"
        self.addCleanup(os.environ.pop, "FAKE_TIDY", None)
        tid = self.req("POST", "/api/tidy", {})[1]["task"]["id"]
        self.wait(lambda: any(c["kind"] == "revision" for c in self.pending()))
        c = next(c for c in self.pending() if c["kind"] == "revision")
        self.assertEqual((c["target"], c["source"], c["origin"]), (i, tid, "ai"))
        new = self.req("POST", f"/api/candidates/{c['id']}/accept", {})[1]["id"]
        self.assertEqual(self.st.read_obj(i)[0]["superseded_by"], new)
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])


del _Api

if __name__ == "__main__":
    unittest.main()
