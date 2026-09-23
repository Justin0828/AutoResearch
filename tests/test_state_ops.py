import json
import os
import subprocess
import sys
import unittest

from autoresearch import bootstrap, briefing, candidates, config, discussion, schema
from tests.util import temp_cfg


def mk_discussion(st):
    ds = discussion.create(st, "精细操作的瓶颈")
    discussion.append_turn(st, ds, "human", "我觉得感知不是主要问题，视觉编码器已经很强了。")
    discussion.append_turn(st, ds, "ai", "那可以先区分表示和数据两种解释。\n\n### 看起来像标题\n也不会切错")
    discussion.append_turn(st, ds, "human", "我怀疑是动作表示太粗。我们下周用粒度消融验证。")
    return ds


class DiscussionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg)

    def test_turns_roundtrip(self):
        ds = mk_discussion(self.st)
        meta, turns = discussion.read(self.st, ds)
        self.assertEqual([t["role"] for t in turns], ["human", "ai", "human"])
        self.assertIn("### 看起来像标题", turns[1]["text"])
        self.assertEqual(discussion.undistilled_human(self.st, ds), 2)
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_each_turn_is_a_commit(self):
        ds = mk_discussion(self.st)
        subjects = [c["subject"] for c in self.st.log()]
        self.assertEqual(sum(1 for s in subjects if s.startswith(f"discussion {ds}:")), 3)
        self.assertEqual(self.st.dirty_paths(), [])

    def test_summary_cannot_go_backwards(self):
        ds = mk_discussion(self.st)
        discussion.write_summary(self.st, ds, "摘要", 2)
        with self.assertRaises(ValueError):
            discussion.write_summary(self.st, ds, "摘要", 1)
        with self.assertRaises(ValueError):
            discussion.write_summary(self.st, ds, "摘要", 9)
        self.assertEqual(discussion.undistilled_human(self.st, ds), 1)


class CandidateTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg)
        self.ds = mk_discussion(self.st)

    def propose(self, **kw):
        base = dict(kind="assumption", statement="感知不是精细操作的主要瓶颈",
                    rationale="被用来排除感知方向，但无人安排验证", origin="human",
                    source=self.ds, turns=[1], relied_on_by=["Q001"], task="T00001",
                    actor="agent")
        base.update(kw)
        return candidates.propose(self.st, **base)

    def test_assumption_requires_relied_on_by(self):
        with self.assertRaisesRegex(ValueError, "relied_on_by"):
            self.propose(relied_on_by=None)

    def test_hypothesis_requires_validation_with_role_hint(self):
        with self.assertRaisesRegex(ValueError, "按当前角色分类"):
            self.propose(kind="hypothesis", falsifier="粒度消融无差异", relied_on_by=None)

    def test_bad_turn_rejected(self):
        with self.assertRaisesRegex(ValueError, "turns"):
            self.propose(turns=[7])

    def test_accept_creates_object_and_provenance(self):
        cid = self.propose()
        new = candidates.accept(self.st, cid)
        self.assertEqual(new, "A002")
        meta, body = self.st.read_obj(new)
        self.assertEqual(meta["relied_on_by"], ["Q001"])
        self.assertNotIn("origin", meta)
        self.assertEqual(self.st.provenance()[new]["origin"], "human")
        self.assertEqual(candidates.load(self.st, cid)[0]["promoted_to"], new)
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def test_unclear_origin_needs_human_choice(self):
        cid = self.propose(origin="unclear", origin_note="记录显示 AI 先提，旧记录写 human")
        with self.assertRaisesRegex(ValueError, "unclear"):
            candidates.accept(self.st, cid)
        new = candidates.accept(self.st, cid, origin="ai")
        self.assertEqual(self.st.provenance()[new]["origin"], "ai")
        self.assertIn("由人裁定", self.st.provenance()[new]["note"])

    def test_reject_needs_reason(self):
        cid = self.propose()
        with self.assertRaises(ValueError):
            candidates.reject(self.st, cid, "")
        candidates.reject(self.st, cid, "和 A001 重复")
        c = candidates.as_dict(*candidates.load(self.st, cid))
        self.assertEqual(c["status"], "rejected")
        self.assertIn("和 A001 重复", c["decision_note"])


class BriefingTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg)
        self.ds = mk_discussion(self.st)
        self.asm = briefing.Assembler(self.st)

    def task(self, kind="discuss_turn"):
        return {"id": "T00001", "kind": kind, "goal": "回复研究者", "discussion": self.ds}

    def test_discuss_briefing_contains_all_sections(self):
        b = self.asm.briefing("discuss", self.task())
        for h in ["## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.",
                  "## 8.", "## 9.", "## 10."]:
            self.assertIn(h, b)
        self.assertIn("讨论模式", b)
        self.assertIn("提出者=human", b)
        self.assertIn("动作表示太粗", b)

    def test_judge_briefing_strips_origin(self):
        b = self.asm.briefing("judge", self.task("judge"))
        self.assertNotIn("提出者", b)
        self.assertNotIn("## 9.", b)
        self.assertNotIn("## 10.", b)
        self.assertNotIn("动作表示太粗", b)      # 讨论原文不进评判类 briefing

    def test_neutralize(self):
        self.assertEqual(briefing.neutralize("研究者认为 X 成立"), "有观点认为 X 成立")
        self.assertNotIn("AI", briefing.neutralize("AI 提出了 H003"))

    def test_summary_replaces_covered_turns(self):
        discussion.write_summary(self.st, self.ds, "前两轮：感知被排除。", 2)
        b = self.asm.briefing("discuss", self.task())
        self.assertIn("第 1–2 轮摘要", b)
        self.assertNotIn("视觉编码器已经很强", b)
        self.assertIn("动作表示太粗", b)


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg)
        self.ds = mk_discussion(self.st)
        self.tdir = self.cfg.tasks / "T00001"
        self.tdir.mkdir(parents=True)

    def rpc(self, calls, toolset="*"):
        env = dict(os.environ, AR_TASK="T00001", AR_TASK_DIR=str(self.tdir),
                   AR_TOOLSET=toolset, PYTHONPATH=str(config.CODE_ROOT))
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"}]
        msgs += [{"jsonrpc": "2.0", "id": i + 1, **c} for i, c in enumerate(calls)]
        p = subprocess.run([sys.executable, "-m", "autoresearch.mcp_server"], env=env,
                           input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return [json.loads(l) for l in p.stdout.splitlines()][1:]

    def call(self, name, **args):
        return {"method": "tools/call", "params": {"name": name, "arguments": args}}

    def test_toolset_filters(self):
        r = self.rpc([{"method": "tools/list"}, self.call("record_evidence")],
                     toolset="check_dead_ends,propose_candidate,checkpoint")
        names = {t["name"] for t in r[0]["result"]["tools"]}
        self.assertEqual(names, {"check_dead_ends", "propose_candidate", "checkpoint"})
        self.assertTrue(r[1]["result"]["isError"])

    def test_propose_and_checkpoint_commit(self):
        r = self.rpc([
            self.call("propose_candidate", kind="hypothesis", statement="动作表示粒度是瓶颈",
                      rationale="讨论中约定粒度消融", origin="human", source=self.ds,
                      turns=[3], falsifier="消融无差异", validation="下周粒度消融"),
            self.call("checkpoint", note="已提交 C001"),
            self.call("update_discussion_summary", discussion_id=self.ds, summary="摘要",
                      covers_through=3),
        ])
        self.assertFalse(any(x["result"]["isError"] for x in r), r)
        log = self.st.log()
        self.assertEqual(log[0]["author"], "ar-agent")
        self.assertIn("C001", log[1]["subject"])
        self.assertEqual(self.st.dirty_paths(), [])
        cps = (self.tdir / "checkpoints.jsonl").read_text(encoding="utf-8")
        self.assertIn("已提交 C001", cps)
        body = self.st.git("log", "-1", "--format=%B", "HEAD~1")
        self.assertIn("AR-Task: T00001", body)

    def test_evidence_source_cannot_be_dead_end(self):
        r = self.rpc([self.call("record_evidence", hypothesis_id="H001", stance="support",
                                source="D001", note="x")])
        self.assertTrue(r[0]["result"]["isError"])
        self.assertIn("dead-end 不是出处", r[0]["result"]["content"][0]["text"])

    def test_transition_needs_evidence(self):
        r = self.rpc([self.call("transition_hypothesis", hypothesis_id="H001",
                                new_status="supported", evidence_ids=[], rationale="x")])
        self.assertTrue(r[0]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
