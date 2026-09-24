import json
import tempfile
import unittest
from pathlib import Path

from autoresearch import bootstrap, config, frontmatter, schema
from tests.util import temp_cfg


class FrontmatterTest(unittest.TestCase):
    def test_roundtrip(self):
        meta = {"id": "H009", "evidence": ["E001", "E002"], "falsifier": "若 a: b 则错",
                "empty": [], "note": "[不是列表]", "flag": True}
        m2, body = frontmatter.parse(frontmatter.dump(meta, "正文\n"))
        self.assertEqual(m2["evidence"], ["E001", "E002"])
        self.assertEqual(m2["falsifier"], "若 a: b 则错")
        self.assertEqual(m2["empty"], [])
        self.assertEqual(m2["note"], "[不是列表]")
        self.assertEqual(m2["flag"], "true")
        self.assertEqual(body.strip(), "正文")

    def test_nested_rejected(self):
        with self.assertRaises(frontmatter.FrontmatterError):
            frontmatter.parse("---\na:\n  b: 1\n---\n")


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)

    def put(self, rel, meta, body="正文"):
        p = self.cfg.state / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(frontmatter.dump(meta, body), encoding="utf-8")

    def errors(self):
        return schema.validate_repo(self.cfg.state)[0]

    def test_seed_is_valid(self):
        errs, warns = schema.validate_repo(self.cfg.state)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_seed_moves_origin_to_provenance(self):
        text = (self.cfg.state / "hypotheses/H001.md").read_text(encoding="utf-8")
        self.assertNotIn("origin", text)
        prov = json.loads((self.cfg.state / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov["H001"]["origin"], "human")
        self.assertTrue(prov["H002"]["disputed"])

    def test_origin_in_frontmatter_is_leak(self):
        self.put("hypotheses/H003.md", {
            "id": "H003", "type": "hypothesis", "origin": "ai", "status": "proposed",
            "confidence": "low", "falsifier": "x", "validation": "y", "evidence": [],
            "created": "2026-09-23"})
        self.assertTrue(any("origin" in e and "H003" in e for e in self.errors()))

    def test_hypothesis_needs_validation(self):
        self.put("hypotheses/H003.md", {
            "id": "H003", "type": "hypothesis", "status": "proposed", "confidence": "low",
            "falsifier": "x", "evidence": [], "created": "2026-09-23"})
        self.assertTrue(any("validation" in e for e in self.errors()))

    def test_assumption_needs_relied_on_by(self):
        self.put("assumptions/A002.md", {
            "id": "A002", "type": "assumption", "status": "unexamined", "relied_on_by": [],
            "created": "2026-09-23"})
        self.assertTrue(any("relied_on_by 不能为空" in e for e in self.errors()))

    def test_dead_end_must_reference(self):
        self.put("dead-ends/D001.md", {
            "id": "D001", "type": "dead-end", "status": "closed", "closed_by": [],
            "created": "2026-09-23"})
        self.assertTrue(any("closed_by 不能为空" in e for e in self.errors()))

    def test_evidence_source_cannot_be_dead_end(self):
        self.put("dead-ends/D001.md", {
            "id": "D001", "type": "dead-end", "status": "closed", "closed_by": ["E001"],
            "created": "2026-09-23"})
        self.put("evidence/E001.md", {
            "id": "E001", "type": "evidence", "hypothesis": "H001", "stance": "support",
            "strength": "weak", "source": "D001", "created": "2026-09-23"})
        self.assertTrue(any("source 中的 'D001'" in e for e in self.errors()))

    def test_candidate_unclear_needs_note(self):
        self.put("candidates/C001.md", {
            "id": "C001", "type": "candidate", "kind": "assumption", "status": "pending",
            "origin": "unclear", "source": "DS001", "turns": [1], "relied_on_by": ["Q001"],
            "created": "2026-09-23"})
        (self.cfg.state / "discussions/DS001").mkdir(parents=True)
        (self.cfg.state / "discussions/DS001/transcript.md").write_text(
            frontmatter.dump({"id": "DS001", "type": "discussion", "title": "t",
                              "status": "open", "created": "2026-09-23"}, ""), encoding="utf-8")
        self.assertTrue(any("origin_note" in e for e in self.errors()))

    def test_protected_paths(self):
        self.assertTrue(schema.is_protected("evidence/E001.md"))
        self.assertTrue(schema.is_protected("provenance.json"))
        self.assertTrue(schema.is_protected("./discussions/DS001/transcript.md"))
        self.assertFalse(schema.is_protected("hypotheses/H001.md"))


if __name__ == "__main__":
    unittest.main()
