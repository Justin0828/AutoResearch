import unittest

from autoresearch import bootstrap, briefing, candidates, discussion, insights, reviews, schema
from tests.util import temp_cfg


class InsightTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg)
        self.ds = discussion.create(self.st, "t")
        discussion.append_turn(self.st, self.ds, "human", "我总觉得难点在接触瞬间的时序。")
        discussion.append_turn(self.st, self.ds, "ai", "这更像闭环问题。")

    def propose(self, **kw):
        base = dict(kind="insight", statement="接触密集任务的难点更像闭环时序问题，而非表示问题",
                    rationale="两轮讨论形成的共同感觉", origin="human", source=self.ds, turns=[1, 2],
                    firmness="hunch", basis=["H001"], task="T1", actor="agent")
        base.update(kw)
        return candidates.propose(self.st, **base)

    def test_ai_insight_needs_real_basis(self):
        with self.assertRaisesRegex(ValueError, "说不出根基"):
            self.propose(basis=None, basis_note="感觉")
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.propose(basis=["H999"])
        with self.assertRaisesRegex(ValueError, "firmness"):
            self.propose(firmness="strong")

    def test_no_falsifier_required_and_accept(self):
        cid = self.propose()
        iid = candidates.accept(self.st, cid)
        self.assertEqual(iid, "IN001")
        meta, _ = self.st.read_obj(iid)
        self.assertEqual(meta["status"], "active")
        self.assertEqual(meta["basis"], ["H001", self.ds])     # 来源讨论自动并入根基
        self.assertNotIn("falsifier", meta)
        self.assertEqual(self.st.provenance()[iid]["origin"], "human")
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_human_can_use_basis_note(self):
        iid = insights.create(self.st, "插孔时手感比视觉先知道快到了", "hunch",
                              basis_note="做装配多年的手感")
        self.assertEqual(self.st.read_obj(iid)[0]["basis_note"], "做装配多年的手感")
        with self.assertRaises(ValueError):
            insights.create(self.st, "没有根基的感觉", "hunch")
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_revise_keeps_history(self):
        iid = insights.create(self.st, "v1", "hunch", basis=["H001"])
        new = insights.revise(self.st, iid, "v2", "working", note="读了更多")
        old = self.st.read_obj(iid)[0]
        self.assertEqual((old["status"], old["superseded_by"]), ("superseded", new))
        self.assertEqual(self.st.read_obj(new)[0]["basis"], ["H001", iid])
        # 修订不应让新理解因“根基里有被取代的旧理解”而被标记
        self.assertNotIn(new, {r["target"] for r in reviews.list_all(self.st)})
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_basis_overturned_flags_insight(self):
        iid = insights.create(self.st, "理解", "working", basis=["A001"])
        reviews.human_invalidate(self.st, "A001", "不成立")
        self.assertIn(iid, {r["target"] for r in reviews.list_all(self.st, "open")})

    def test_withdrawn_insight_flags_derived_assumption(self):
        iid = insights.create(self.st, "感知不是瓶颈（直觉）", "hunch", basis=[self.ds])
        cid = candidates.propose(self.st, kind="assumption", statement="排除感知方向",
                                 rationale="凭 IN001 这条直觉排除", origin="human", source=self.ds,
                                 turns=[1], relied_on_by=["Q001"], derived_from=iid)
        aid = candidates.accept(self.st, cid)
        self.assertEqual(self.st.read_obj(aid)[0]["derived_from"], iid)
        insights.abandon(self.st, iid, "对照实验表明感知差距很大")
        r = [r for r in reviews.list_all(self.st) if r["target"] == aid]
        self.assertEqual(r[0]["event"], "insight_withdrawn")
        self.assertIn("放弃", r[0]["body"])

    def test_judge_does_not_see_insights(self):
        insights.create(self.st, "独特的理解文本XYZ", "settled", basis=["H001"])
        asm = briefing.Assembler(self.st)
        d = asm.briefing("discuss", {"id": "T1", "kind": "discuss_turn", "goal": "g", "discussion": self.ds})
        j = asm.briefing("judge", {"id": "T1", "kind": "judge", "goal": "g"})
        self.assertIn("## 3b. 当前理解（是理解，不是证据）", d)
        self.assertLess(d.index("## 3b."), d.index("## 4."))
        self.assertIn("独特的理解文本XYZ", d)
        self.assertNotIn("独特的理解文本XYZ", j)
        self.assertNotIn("3b", j)


if __name__ == "__main__":
    unittest.main()
