import unittest

from autoresearch import bootstrap, briefing, frontmatter, reviews, schema
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from tests.util import temp_cfg


class ReviewTest(unittest.TestCase):
    """推翻的传播（DESIGN.md M5.6b）。种子：A001.relied_on_by=[Q001]，H001/H002 proposed。"""

    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)

    def put(self, ident, meta, body="正文"):
        with self.st.tx(f"test: {ident}", actor="human") as tx:
            tx.write_obj(ident, {"id": ident, **meta, "created": "2026-09-23"}, body)

    def targets(self, trigger=None):
        return {r["target"]: r for r in reviews.list_all(self.st)
                if trigger is None or r["trigger"] == trigger}

    def refute(self, hid):
        self.put("P001", {"type": "paper", "title": "p"})
        self.put("E001", {"type": "evidence", "hypothesis": hid, "stance": "contradict",
                          "strength": "strong", "source": "P001"})
        meta, body = self.st.read_obj(hid)
        meta.update(status="refuted", evidence=["E001"])
        self.put(hid, {k: v for k, v in meta.items() if k not in ("id", "created")}, body)

    def test_invalidated_assumption_flags_dependents(self):
        did, new = reviews.human_invalidate(self.st, "A001", "interface 划分被证明不可行")
        self.assertEqual(set(self.targets()), {"Q001"})
        meta, _ = self.st.read_obj("A001")
        self.assertEqual((meta["status"], meta["invalidated_by"]), ("invalidated", [did]))
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_propagates_down_the_assumption_chain(self):
        # A002 支撑 A001，A001 支撑 Q001：推翻 A002 → A001（直接）→ Q001（间接，距离 2）
        self.put("A002", {"type": "assumption", "status": "unexamined", "relied_on_by": ["A001"]})
        reviews.human_invalidate(self.st, "A002", "x")
        t = self.targets("A002")
        self.assertEqual(t["A001"]["depth"], "1")
        self.assertEqual(t["Q001"]["depth"], "2")
        self.assertIn("A002 → A001 → Q001", t["Q001"]["body"])

    def test_refuted_hypothesis_flags_its_premises_and_source(self):
        self.put("A002", {"type": "assumption", "status": "unexamined", "relied_on_by": ["H001"]})
        self.put("A003", {"type": "assumption", "status": "promoted", "relied_on_by": ["Q001"],
                          "promoted_to": "H003"})
        self.put("H003", {"type": "hypothesis", "status": "proposed", "confidence": "low",
                          "falsifier": "f", "validation": "v", "evidence": [],
                          "promoted_from": "A003", "group": "G1"})
        meta, body = self.st.read_obj("H001")
        meta["group"] = "G1"
        self.put("H001", {k: v for k, v in meta.items() if k not in ("id", "created")}, body)
        self.refute("H003")
        reviews.reconcile(self.st)
        t = self.targets("H003")
        self.assertIn("A003", t)        # 假设被反驳 = 其来源前提被推翻
        self.assertIn("Q001", t)        # ……并继续向下游传
        self.assertIn("H001", t)        # 同组竞争假设
        self.assertNotIn("A002", t)     # A002 支撑的是 H001，与 H003 无关
        self.refute("H001")
        reviews.reconcile(self.st)
        self.assertIn("A002", self.targets("H001"))   # 为被反驳假设提供前提的也要提醒

    def test_reconcile_is_idempotent_and_respects_resolution(self):
        reviews.human_invalidate(self.st, "A001", "x")
        rid = self.targets()["Q001"]["id"]
        self.assertEqual(reviews.reconcile(self.st), [])
        reviews.resolve(self.st, rid, "dismissed", "Q001 可以换一个前提继续成立")
        self.assertEqual(reviews.reconcile(self.st), [])
        self.assertEqual(self.targets()["Q001"]["status"], "dismissed")
        with self.assertRaises(ValueError):
            reviews.resolve(self.st, rid, "resolved", "再改一次")

    def test_requires_basis(self):
        with self.assertRaises(ValueError):
            reviews.invalidate_assumption(self.st, "A001", [], "x")
        with self.assertRaises(ValueError):
            reviews.human_invalidate(self.st, "A001", "")
        with self.assertRaises(ValueError):
            reviews.resolve(self.st, "R001", "resolved", "")

    def test_human_edit_in_editor_also_propagates(self):
        """人在编辑器里直接把前提改成 invalidated，daemon 的 sweep 也要对账。"""
        with self.st.tx("test: decision", actor="human") as tx:
            tx.write_obj("DEC001", {"id": "DEC001", "type": "decision", "kind": "research",
                                    "refs": ["A001"], "created": "2026-09-23"}, "理由")
        p = self.cfg.state / "assumptions" / "A001.md"
        meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
        meta.update(status="invalidated", invalidated_by=["DEC001"])
        p.write_text(frontmatter.dump(meta, body), encoding="utf-8")
        d = Daemon(self.cfg, self.st, Bus(self.cfg))
        d.sweep_human_edits()
        self.assertIn("Q001", self.targets("A001"))

    def test_briefing_and_handoff_show_open_reviews(self):
        reviews.human_invalidate(self.st, "A001", "x")
        asm = briefing.Assembler(self.st)
        b = asm.briefing("discuss", {"id": "T1", "kind": "discuss_turn", "goal": "g"})
        self.assertIn("## 11. 待重新审视", b)
        self.assertIn("**Q001**", b)
        self.assertIn("## 11.", asm.briefing("judge", {"id": "T1", "kind": "judge", "goal": "g"}))
        h = asm.handoff({"id": "SH1", "started": "a", "ended": "b", "reason": "manual"}, [], [])
        self.assertIn("待重新审视 1 项", h)

    def test_invalidated_needs_basis_in_schema(self):
        p = self.cfg.state / "assumptions" / "A001.md"
        meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
        meta["status"] = "invalidated"
        p.write_text(frontmatter.dump(meta, body), encoding="utf-8")
        self.assertTrue(any("invalidated_by" in e for e in schema.validate_repo(self.cfg.state)[0]))


if __name__ == "__main__":
    unittest.main()
