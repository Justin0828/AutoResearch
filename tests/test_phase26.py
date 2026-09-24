"""Phase 2.6：聚焦讨论、原地修订、对象详情（反向索引）、讨论组织、撤下与撤回确认（DESIGN.md §5.15）。"""
import json
import os
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from autoresearch import (bootstrap, briefing, candidates, discussion, frontmatter, incubation, insights,
                          objects, papers, reviews, schema)
from autoresearch.library import Library
from tests.util import temp_cfg

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


def mk_ds(st, focus=None, title="接口"):
    ds = discussion.create(st, title, focus=focus)
    discussion.append_turn(st, ds, "human", "上层给什么信息？")
    discussion.append_turn(st, ds, "ai", "先区分语义与几何。")
    discussion.append_turn(st, ds, "human", "只研究几何子目标，不研究语言。")
    return ds


def propose_q(st, ds, text="action model 的 CoT 应该是什么形式？", rel=("Q001",)):
    return candidates.propose(st, kind="question", statement=text, rationale="研究者第 1 轮提出",
                              origin="human", source=ds, turns=[1], maturity="vague",
                              relates_to=list(rel))


def revise(st, ds, target, text, base=None, **fields):
    return candidates.propose(st, kind="revision", target=target, statement=text,
                              rationale="第 3 轮把边界说清楚了", origin="human", source=ds, turns=[3],
                              base_revision=base, **fields)


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.ds = mk_ds(self.st)

    def ok(self):
        errs, _ = schema.validate_repo(self.cfg.state)
        self.assertEqual(errs, [])

    def q2(self):
        return candidates.accept(self.st, propose_q(self.st, self.ds))


class RevisionTest(Base):
    def test_accept_keeps_relates_to(self):
        q = self.q2()
        m, _ = self.st.read_obj(q)
        self.assertEqual(m["relates_to"], ["Q001"])
        self.ok()

    def test_in_place_revision(self):
        q = self.q2()
        focus = discussion.create(self.st, "", focus=q)
        c = revise(self.st, self.ds, q, "CoT 的形式：只研究几何子目标，不研究语言。", base=1)
        self.assertEqual(candidates.accept(self.st, c), q)          # id 不变
        m, body = self.st.read_obj(q)
        self.assertEqual((m["revision"], m["maturity"], m["relates_to"]), ("2", "vague", ["Q001"]))
        self.assertIn("只研究几何子目标", objects.statement_of(body))
        self.assertIn("revised", m)
        dec = [d for d, _ in self.st.list("decision") if d.get("kind") == "revision"]
        self.assertEqual(len(dec), 1)
        _, db = self.st.read_obj(dec[0]["id"])
        self.assertIn("action model 的 CoT 应该是什么形式", db)      # 修订前的表述完整留档
        self.assertEqual(dec[0]["refs"], [q, c])
        prov = self.st.provenance()[q]
        self.assertEqual(prov["origin"], "human")
        self.assertEqual(prov["revisions"][0]["rev"], 2)
        self.assertEqual(prov["revisions"][0]["source"], c)
        cm, _ = self.st.read_obj(c)
        self.assertEqual((cm["status"], cm["promoted_to"]), ("accepted", q))
        info = objects.links(self.st, q)
        self.assertIn(focus, [d["id"] for d in info["discussions"]])  # 聚焦讨论还在
        self.assertEqual(info["revisions"][0]["to"], "2")
        self.ok()

    def test_maturity_only_changes_when_human_picks(self):
        q = self.q2()
        c = revise(self.st, self.ds, q, "更准的表述", base=1, maturity="formalized")
        candidates.accept(self.st, c)
        self.assertEqual(self.st.read_obj(q)[0]["maturity"], "vague")   # 建议不自动生效
        c2 = revise(self.st, self.ds, q, "再准一点", base=2)
        candidates.accept(self.st, c2, maturity="scoped")
        self.assertEqual(self.st.read_obj(q)[0]["maturity"], "scoped")

    def test_stale_needs_force(self):
        q = self.q2()
        a = revise(self.st, self.ds, q, "版本甲", base=1)
        b = revise(self.st, self.ds, q, "版本乙", base=1)
        candidates.accept(self.st, a)
        self.assertTrue(next(c for c in candidates.list_all(self.st) if c["id"] == b)["stale"])
        with self.assertRaises(objects.StaleRevision) as e:
            candidates.accept(self.st, b)
        self.assertEqual(e.exception.current["revision"], 2)
        self.assertEqual(self.st.read_obj(b)[0]["status"], "pending")
        candidates.accept(self.st, b, force=True)
        self.assertEqual(self.st.read_obj(q)[0]["revision"], "3")
        self.ok()

    def test_propose_validation(self):
        q = self.q2()
        with self.assertRaisesRegex(ValueError, "不存在"):
            revise(self.st, self.ds, "Q099", "x", base=1)
        with self.assertRaisesRegex(ValueError, "当前版本 1"):
            revise(self.st, self.ds, q, "x", base=3)
        with self.assertRaisesRegex(ValueError, "完全相同"):
            revise(self.st, self.ds, q, "action model 的 CoT 应该是什么形式？", base=1)
        with self.assertRaisesRegex(ValueError, "不能改"):
            revise(self.st, self.ds, "H001", "x", base=1, importance="high")
        with self.assertRaisesRegex(ValueError, "base_revision"):
            candidates.propose(self.st, kind="revision", target=q, statement="x", rationale="r",
                               origin="ai", source=self.ds, turns=[1], actor="agent")
        with self.assertRaisesRegex(ValueError, "只出自讨论"):
            candidates.propose(self.st, kind="revision", target=q, statement="x", rationale="r",
                               origin="ai", source="T00001", turns=[], base_revision=1, basis=["H001"])

    def test_falsifier_change_with_evidence_is_recorded(self):
        (self.cfg.state / "experiments").mkdir(exist_ok=True)
        (self.cfg.state / "experiments" / "X001.md").write_text("---\nid: X001\n---\n\n实验\n")
        self.st.sweep("x", "human")
        eid, _, _ = papers.record_evidence(self.st, Library(self.cfg.library), "H001", "support", "X001",
                                           "消融显示粒度有影响", strength="moderate")
        self.assertEqual(self.st.read_obj(eid)[0]["revision"], "1")   # 自动记版本
        c = revise(self.st, self.ds, "H001", objects.statement_of(self.st.read_obj("H001")[1]), base=1,
                   falsifier="粒度加细 4 倍后成功率不升")
        pv = objects.preview(self.st, c)
        self.assertEqual(pv["falsifier_evidence"], 1)
        candidates.accept(self.st, c)
        dec = [d for d, _ in self.st.list("decision") if d.get("kind") == "revision"][0]
        self.assertIn("证伪条件", self.st.read_obj(dec["id"])[1])
        m, _ = self.st.read_obj("H001")
        self.assertEqual((m["revision"], m["evidence"]), ("2", [eid]))  # 证据保留
        info = objects.links(self.st, "H001")
        self.assertEqual(info["evidence"][0]["revision"], 1)
        self.ok()

    def test_insight_revision_supersedes(self):
        iid = insights.create(self.st, "接口越稀疏越好", "hunch", basis_note="直觉")
        c = revise(self.st, self.ds, iid, "接口在时间上稀疏、信息上稠密", base=1, firmness="working")
        new = candidates.accept(self.st, c)
        self.assertNotEqual(new, iid)
        self.assertEqual(self.st.read_obj(iid)[0]["status"], "superseded")
        prov = self.st.provenance()[new]
        self.assertEqual((prov["source"], prov["discussion"]), (c, self.ds))
        self.ok()

    def test_kind_cannot_switch_to_or_from_revision(self):
        q = self.q2()
        c = revise(self.st, self.ds, q, "新表述", base=1)
        with self.assertRaisesRegex(ValueError, "不能互相改类别"):
            candidates.update(self.st, c, {"kind": "question"})


class FocusBriefingTest(Base):
    def test_focus_discussion_briefing(self):
        q = self.q2()
        old = discussion.create(self.st, "", focus=q)
        self.assertTrue(discussion.read(self.st, old)[0]["title"].startswith(f"{q} · "))
        discussion.append_turn(self.st, old, "human", "先谈边界")
        discussion.write_summary(self.st, old, "上一场聚焦讨论的摘要：边界是几何。", 1)
        ds = discussion.create(self.st, "第二场", focus=q)
        discussion.append_turn(self.st, ds, "human", "继续")
        text = briefing.Assembler(self.st).briefing("discuss", {"id": "T1", "kind": "discuss_turn",
                                                              "goal": "回复", "discussion": ds})
        self.assertIn(f"## 2b. 聚焦对象：{q}（question，第 1 版）", text)
        self.assertIn("不以提升成熟度为目标", text)
        self.assertIn("上一场聚焦讨论的摘要", text)
        self.assertIn("Q001", text.split("## 2b.")[1].split("## 3.")[0])   # 关联对象
        # 其他章节不重复全文
        rest = text.split("## 3. 研究问题")[1]
        self.assertNotIn("action model 的 CoT 应该是什么形式", rest)
        self.assertIn(f"- {q} · vague：", rest)          # 不活跃的问题只给一行（§5.16.2）
        self.assertIn("全文见第 2b 章", rest)
        self.assertLess(text.index("## 2b."), text.index("## 3."))
        plain = briefing.Assembler(self.st).briefing("discuss", {"id": "T2", "kind": "discuss_turn",
                                                               "goal": "回复", "discussion": self.ds})
        self.assertNotIn("## 2b.", plain)

    def test_focus_must_be_focusable(self):
        with self.assertRaises(ValueError):
            discussion.create(self.st, "", focus="Q099")


class WithdrawTest(Base):
    def test_withdraw_each_type_and_restore(self):
        q = self.q2()
        u = candidates.accept(self.st, candidates.propose(
            self.st, kind="uncertainty", statement="数据够不够", rationale="r", origin="ai",
            source=self.ds, turns=[2], importance="high"))
        for ident, status in ((q, "withdrawn"), ("A001", "retired"), ("H001", "abandoned"), (u, "withdrawn")):
            before = self.st.read_obj(ident)[0].get("status")
            did = objects.withdraw(self.st, ident, "不再是关心的方向")
            m, _ = self.st.read_obj(ident)
            self.assertEqual((m["status"], m["withdrawn_by"]), (status, did))
            self.assertEqual(self.st.read_obj(did)[0]["kind"], "curation")
            self.ok()
            self.assertEqual(reviews.reconcile(self.st), [])            # 不触发 Review
            if ident == "H001":
                objects.restore(self.st, ident, "又相关了")
                m, _ = self.st.read_obj(ident)
                self.assertEqual(m.get("status"), before)
                self.assertNotIn("withdrawn_by", m)
                objects.withdraw(self.st, ident, "还是不要了")
        self.assertEqual(self.st.read_obj("A001")[0]["relied_on_by"], ["Q001"])   # 链接保留
        text = briefing.Assembler(self.st).briefing("discuss", {"id": "T1", "kind": "discuss_turn",
                                                              "goal": "回复", "discussion": self.ds})
        tail = text.split("## 已撤下")[1]
        body = text.split("## 已撤下")[0]
        for ident in (q, "A001", "H001", u):
            self.assertIn(f"- {ident}：", tail)
            self.assertNotIn(f"### {ident}", body)
        self.assertIn("不再是关心的方向", tail)
        self.assertNotIn(u, incubation.foundation_body(self.st))
        self.ok()

    def test_main_question_cannot_be_withdrawn(self):
        with self.assertRaisesRegex(ValueError, "主问题"):
            objects.withdraw(self.st, "Q001", "不要了")

    def test_reason_required(self):
        with self.assertRaises(ValueError):
            objects.withdraw(self.st, "A001", " ")

    def test_candidate_referencing_withdrawn_warns(self):
        q = self.q2()
        objects.withdraw(self.st, q, "不相关")
        propose_q(self.st, self.ds, "另一个问题", rel=(q,))
        errs, warns = schema.validate_repo(self.cfg.state)
        self.assertEqual(errs, [])
        self.assertTrue(any("已撤下" in w for w in warns))
        self.assertEqual(candidates.withdrawn_refs(self.st, [q, "Q001"]), [q])


class UndoAcceptTest(Base):
    def test_undo_fresh_object(self):
        c = propose_q(self.st, self.ds)
        q = candidates.accept(self.st, c)
        info = objects.links(self.st, q)
        self.assertTrue(info["can_undo"], info["undo_blocked"])
        did, back = objects.undo_accept(self.st, q, "手滑")
        self.assertEqual(back, c)
        self.assertFalse(self.st.exists(q))
        self.assertNotIn(q, self.st.provenance())
        cm, _ = self.st.read_obj(c)
        self.assertEqual(cm["status"], "pending")
        self.assertNotIn("promoted_to", cm)
        self.assertNotIn("decided", cm)
        self.assertEqual(self.st.read_obj(did)[0]["undid"], q)
        self.ok()
        again = candidates.accept(self.st, c)
        self.assertNotEqual(again, q)                                   # 编号不复用
        self.ok()

    def test_undo_blocked_when_used(self):
        q = self.q2()
        propose_q(self.st, self.ds, "子问题", rel=(q,))
        info = objects.links(self.st, q)
        self.assertFalse(info["can_undo"])
        self.assertIn("候选", info["undo_blocked"])
        with self.assertRaisesRegex(ValueError, "不能撤回"):
            objects.undo_accept(self.st, q)

    def test_undo_blocked_by_focus_revision_or_seed(self):
        q = self.q2()
        discussion.create(self.st, "", focus=q)
        self.assertIn("聚焦", objects.links(self.st, q)["undo_blocked"])
        q3 = candidates.accept(self.st, propose_q(self.st, self.ds, "第三个问题", rel=()))
        candidates.accept(self.st, revise(self.st, self.ds, q3, "第三个问题（改）", base=1))
        self.assertIn("修订", objects.links(self.st, q3)["undo_blocked"])
        self.assertIn("候选", objects.links(self.st, "A001")["undo_blocked"])   # 不是经候选确认的


class DiscussionOrgTest(Base):
    def test_rename_group_pin(self):
        discussion.set_meta(self.st, self.ds, title="新名字", group="接口", pinned=True)
        d = next(x for x in discussion.list_all(self.st) if x["id"] == self.ds)
        self.assertEqual((d["title"], d["group"], d["pinned"]), ("新名字", "接口", True))
        _, turns = discussion.read(self.st, self.ds)
        self.assertEqual(len(turns), 3)                                  # 转录不受影响
        self.assertIn("# 接口", self.st.read(discussion.rel_transcript(self.ds)))   # 旧标题照实保留
        self.assertEqual(discussion.rename_group(self.st, "接口", "架构"), [self.ds])
        self.assertEqual(discussion.read(self.st, self.ds)[0]["group"], "架构")
        discussion.rename_group(self.st, "架构", "")
        discussion.set_meta(self.st, self.ds, pinned=False)
        d = next(x for x in discussion.list_all(self.st) if x["id"] == self.ds)
        self.assertEqual((d.get("group"), d["pinned"]), (None, False))
        self.assertEqual(self.st.dirty_paths(), [])
        self.ok()


class LegacyCompatTest(unittest.TestCase):
    """用户当前真实 State 的形态：Q001 无 status/revision，候选带 relates_to、均 pending。"""

    def test_legacy_state_validates_and_accepts(self):
        cfg = temp_cfg(self)
        st, _ = bootstrap.init(cfg)
        bootstrap.setup(st, "项目", "描述", "主问题？", "vague")
        ds = mk_ds(st)
        cids = [propose_q(st, ds, f"子问题 {i}") for i in range(2)]
        cids.append(candidates.propose(st, kind="insight", statement="理解", rationale="r", origin="unclear",
                                       origin_note="不确定", source=ds, turns=[2], firmness="hunch",
                                       basis=[ds], relates_to=["Q001"]))
        self.assertEqual(schema.validate_repo(cfg.state)[0], [])
        for c in cids:
            new = candidates.accept(st, c, origin="human")
            self.assertEqual(st.read_obj(new)[0]["relates_to"], ["Q001"])
        self.assertEqual(schema.validate_repo(cfg.state)[0], [])


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

    def test_focus_flow_end_to_end(self):
        ds = mk_ds(self.st)
        q = candidates.accept(self.st, propose_q(self.st, ds))
        s, r = self.req("POST", "/api/discussions", {"focus": q})
        self.assertEqual(s, 200)
        fds = r["id"]
        self.req("POST", f"/api/discussions/{fds}/messages", {"text": "只研究几何，不研究语言"})
        self.wait(lambda: len(self.req("GET", f"/api/discussions/{fds}")[1]["turns"]) == 2)
        self.assertIn(f"[聚焦:{q}]", self.req("GET", f"/api/discussions/{fds}")[1]["turns"][1]["text"])
        os.environ["FAKE_MODE"] = "distill_revise"
        self.req("POST", f"/api/discussions/{fds}/distill")
        self.wait(lambda: any(c["kind"] == "revision" for c in self.req("GET", "/api/candidates?status=pending")[1]))
        c = next(c for c in self.req("GET", "/api/candidates?status=pending")[1] if c["kind"] == "revision")
        self.assertEqual((c["target"], c["base_revision"], c["stale"]), (q, "1", False))
        s, pv = self.req("GET", f"/api/candidates/{c['id']}/revision")
        self.assertEqual(pv["changes"][0]["field"], "statement")
        s, r = self.req("POST", f"/api/candidates/{c['id']}/accept", {"maturity": "vague"})
        self.assertEqual((s, r["id"]), (200, q))
        s, info = self.req("GET", f"/api/objects/{q}/links")
        self.assertEqual(info["revision"], 2)
        self.assertIn(fds, [d["id"] for d in info["discussions"]])
        self.assertIn("Q001", [x["id"] for x in info["related"]])
        o = self.req("GET", "/api/overview")[1]
        self.assertEqual(o["validation"]["errors"], [])
        row = next(x for x in o["questions"] if x["id"] == q)
        self.assertEqual((row["counts"]["discussions"], row["counts"]["revision"]), (1, 2))

    def test_stale_409_and_force(self):
        ds = mk_ds(self.st)
        q = candidates.accept(self.st, propose_q(self.st, ds))
        a = revise(self.st, ds, q, "甲", base=1)
        b = revise(self.st, ds, q, "乙", base=1)
        self.assertEqual(self.req("POST", f"/api/candidates/{a}/accept", {})[0], 200)
        s, r = self.req("POST", f"/api/candidates/{b}/accept", {})
        self.assertEqual((s, r["stale"], r["current"]["revision"]), (409, True, 2))
        s, r = self.req("POST", f"/api/candidates/{b}/accept", {"force": True})
        self.assertEqual(s, 200)

    def test_withdraw_restore_undo_and_meta(self):
        ds = mk_ds(self.st)
        q = candidates.accept(self.st, propose_q(self.st, ds))
        s, r = self.req("POST", "/api/objects/Q001/withdraw", {"reason": "x"})
        self.assertEqual(s, 400)
        self.assertEqual(self.req("POST", "/api/objects/H001/withdraw", {"reason": "不相关"})[0], 200)
        self.assertTrue(next(h for h in self.req("GET", "/api/overview")[1]["hypotheses"] if h["id"] == "H001")["withdrawn"])
        self.assertEqual(self.req("POST", "/api/objects/H001/restore", {"reason": "回来"})[0], 200)
        s, r = self.req("POST", "/api/objects/A001/undo-accept", {})
        self.assertEqual(s, 400)
        s, r = self.req("POST", f"/api/objects/{q}/undo-accept", {"reason": "手滑"})
        self.assertEqual(s, 200)
        self.assertEqual(self.req("GET", f"/api/objects/{q}/links")[0], 404)
        s, _ = self.req("POST", f"/api/discussions/{ds}/meta", {"title": "改名", "group": "G", "pinned": True})
        self.assertEqual(s, 200)
        d = next(x for x in self.req("GET", "/api/discussions")[1] if x["id"] == ds)
        self.assertEqual((d["title"], d["group"], d["pinned"]), ("改名", "G", True))
        self.assertEqual(self.req("POST", "/api/discussion-groups/rename", {"from": "G", "to": "H"})[1]["discussions"], [ds])
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
