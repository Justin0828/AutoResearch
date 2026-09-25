"""自演进（M11 v1.8，DESIGN.md §5.18）：从问题 / 理解出发演进一段思考，写成演进文档。"""
import os
import time
import unittest

from autoresearch import (bootstrap, candidates, discussion, evolution, insights, objects, protocol, questions,
                          schema, tasks)
from autoresearch.briefing import Assembler
from tests.test_phase26 import mk_ds, propose_q
from tests.test_phase27 import ApiTest as _Api
from tests.util import temp_cfg


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, _ = bootstrap.init(self.cfg, seed=True)
        self.ds = mk_ds(self.st)

    def ok(self):
        self.assertEqual(schema.validate_repo(self.cfg.state)[0], [])

    def q(self, text, maturity="vague", **kw):
        c = candidates.propose(self.st, kind="question", statement=text, rationale="r", origin="human",
                               source=self.ds, turns=[1], maturity=maturity, **kw)
        return candidates.accept(self.st, c)

    def doc(self, seed, text="# 标题\n\n正文"):
        return evolution.record(self.st, {"id": "T00099", "seed": seed, "trigger": "manual"}, text)


class PickSeedTest(Base):
    def test_vaguer_first_then_active_then_fewer_docs(self):
        # 夹具的 Q001 是 scoped
        a, b = self.q("甲（vague）"), self.q("乙（vague）")
        self.q("丙（formalized）", maturity="formalized")
        self.assertEqual(evolution.pick_seed(self.st)[0], a)
        questions.set_active(self.st, b, True)
        seed, why = evolution.pick_seed(self.st)
        self.assertEqual(seed, b)
        self.assertIn("vague", why)
        self.doc(b)
        self.assertEqual(evolution.pick_seed(self.st)[0], b)          # 活跃仍优先于演进次数
        questions.set_active(self.st, b, False)
        self.assertEqual(evolution.pick_seed(self.st)[0], a)          # 演进过的靠后
        for x in (a, b):
            questions.resolve(self.st, x, "answered", answered_by=["H001"], reason="r")
        self.assertEqual(evolution.pick_seed(self.st)[0], "Q001")      # 只剩 scoped 的主问题

    def test_seed_checks(self):
        with self.assertRaisesRegex(ValueError, "Q### 或 IN###"):
            evolution.check_seed(self.st, "H001")
        i = insights.create(self.st, "理解", "hunch", basis_note="x")
        insights.abandon(self.st, i, "不要了")
        with self.assertRaisesRegex(ValueError, "active"):
            evolution.check_seed(self.st, i)


class BriefingAndRecordTest(Base):
    def test_briefing_is_self_contained_and_redacted(self):
        a = self.q("接口到底该给什么？")
        kid = self.q("子问题", parent=a)
        rel = insights.create(self.st, "接口宜稀疏", "hunch", basis_note="x", informs=[a])
        star = insights.create(self.st, "星标的理解", "working", basis_note="x")
        insights.set_starred(self.st, star, True)
        other = insights.create(self.st, "无关的理解", "hunch", basis_note="x")
        t = {"id": "T00001", "seed": a, "why": "测试", "prior_checkpoints": [{"ts": "t", "note": "拆到第二种问法"}]}
        text = Assembler(self.st).briefing("evolve", t)
        self.assertIn(f"- 出发点：**{a}**", text)
        self.assertIn("成熟度 vague", text)
        self.assertIn(f"{kid}（open）", text)
        self.assertIn(f"#### {rel} · hunch · 与 {a} 相关", text)
        self.assertIn(f"#### {star} · working · 研究者星标", text)
        self.assertIn(f"**{other}** · hunch：无关的理解", text.split("## 4. 研究全景")[1])
        self.assertIn("拆到第二种问法", text)
        self.assertIn("（没有，这是第一次。）", text)
        self.assertNotIn("提出者", text)                              # 剥离 origin
        objects.withdraw(self.st, kid, "不再相关")
        self.assertIn(f"- {kid}：子问题", evolution.briefing(self.st, t).split("研究者撤下的问题")[1])
        self.assertNotIn("DS00", text.split("## 4.")[0])             # 不给讨论

    def test_record_and_continuity(self):
        a = self.q("接口到底该给什么？")
        e1 = self.doc(a, "# 第一次\n\n## 还没想通的\n\n怎么衡量")
        m, b = self.st.read_obj(e1)
        self.assertEqual((m["seeds"], m["question"], m["title"], m["trigger"]), ([a], a, "第一次", "manual"))
        self.ok()
        text = evolution.briefing(self.st, {"id": "T2", "seed": a})
        self.assertIn(f"### 最近一篇 {e1}", text)
        self.assertIn("怎么衡量", text)
        self.assertIn("##### 还没想通的", text)                       # 上一篇的标题降级，不打乱章节
        new = insights.create(self.st, "上次之后新形成的", "hunch", basis_note="x")
        self.assertIn(new, evolution.related_insights(self.st, a))
        with self.assertRaises(ValueError):
            evolution.record(self.st, {"id": "T3", "seed": a}, "  ")
        info = objects.links(self.st, a)
        self.assertIn(e1, [r["id"] for r in info["related"]])        # 问题页能看到以它为出发点的文档

    def test_insight_seed(self):
        i = insights.create(self.st, "接口宜稀疏", "hunch", basis_note="x", informs=["Q001"])
        text = evolution.briefing(self.st, {"id": "T1", "seed": i})
        self.assertIn("它影响的问题 Q001", text)
        e = self.doc(i)
        self.assertEqual(self.st.read_obj(e)[0]["question"], "Q001")
        self.ok()

    def test_evolution_can_be_discussed_and_cited(self):
        e = self.doc("Q001", "# 三种问法\n\n## 想法\n\n谁对接触负责")
        ds = discussion.create(self.st, "", focus=e)
        self.assertTrue(discussion.read(self.st, ds)[0]["title"].startswith(f"{e} · 三种问法"))
        discussion.append_turn(self.st, ds, "human", "第二条站得住")
        text = Assembler(self.st).briefing("discuss", {"id": "T1", "kind": "discuss_turn", "goal": "g",
                                                       "discussion": ds})
        self.assertIn(f"## 2b. 聚焦对象：{e}（evolution", text)
        self.assertIn("谁对接触负责", text)
        c = candidates.propose(self.st, kind="insight", statement="接口问题是接触责任问题", rationale="r",
                               origin="human", source=ds, turns=[1], firmness="hunch", basis=[e])
        new = candidates.accept(self.st, c)
        self.assertIn(e, self.st.read_obj(new)[0]["basis"])
        self.ok()


class RemovalTest(unittest.TestCase):
    def test_old_incubation_is_gone(self):
        self.assertNotIn("incubate", tasks.KINDS)
        self.assertNotIn("ground_idea", tasks.KINDS)
        self.assertNotIn("incubation", schema.PROJECT_MODES)
        for k in ("idea", "foundation", "chain"):
            self.assertNotIn(k, schema.KINDS)
        spec = tasks.KINDS["evolve"]
        self.assertTrue(spec["sealed"])
        self.assertEqual((spec["tools"], spec["mcp"]), (["Read", "Grep", "Glob"], ["checkpoint"]))
        self.assertIn("做一次**自演进**", protocol.EVOLVE_PROTOCOL)
        self.assertIn("evolutions/**", tasks.JUDGE_DENY_PATHS)        # 评判看不到演进文档


class ApiTest(_Api):
    def setUp(self):
        super().setUp()
        os.environ["FAKE_EVOLVE"] = "doc"
        self.addCleanup(os.environ.pop, "FAKE_EVOLVE", None)

    def docs(self):
        return self.req("GET", "/api/evolutions")[1]["docs"]

    def idle(self):
        """文档写入与任务状态落定之间有一瞬间（_finish 先写文档，finally 再存 done）：等任务真正结束再发下一次。"""
        self.wait(lambda: not self.req("GET", "/api/evolutions")[1]["live"])

    def test_manual_evolve(self):
        s, r = self.req("POST", "/api/evolve", {"seed": "Q001"})
        self.assertEqual(s, 200)
        self.assertEqual(self.req("POST", "/api/evolve", {"seed": "Q001"})[1]["task"], None)   # 不重复建
        self.wait(lambda: len(self.docs()) == 1)
        self.idle()
        d = self.docs()[0]
        self.assertEqual((d["seeds"], d["trigger"], d["task"]), (["Q001"], "manual", r["task"]["id"]))
        self.assertIn("[第一次]", d["body"])
        self.req("POST", "/api/evolve", {"seed": "Q001"})
        self.wait(lambda: len(self.docs()) == 2)
        self.idle()
        self.assertIn(f"[接着:{d['id']}]", self.docs()[0]["body"])     # 接着上一篇往前走
        self.assertEqual(self.req("POST", "/api/evolve", {"seed": "H001"})[0], 400)
        s, r = self.req("POST", "/api/evolve", {})                  # 让系统挑
        self.assertIn("系统挑的", r["task"]["why"])
        self.assertEqual(self.req("GET", "/api/overview")[1]["validation"]["errors"], [])

    def test_empty_reply_fails(self):
        os.environ["FAKE_EVOLVE"] = "empty"
        tid = self.req("POST", "/api/evolve", {"seed": "Q001"})[1]["task"]["id"]
        self.wait(lambda: self.daemon.ledger.get(tid)["status"] == "failed")
        self.assertEqual(self.docs(), [])

    def test_auto_when_idle_once_per_idle_period(self):
        for m, _ in self.st.list("assumption"):      # 让夜间预习没有题目可做（它排在自演进之前）
            objects.withdraw(self.st, m["id"], "x")
        for m, _ in self.st.list("hypothesis"):
            objects.withdraw(self.st, m["id"], "x")
        self.daemon.cfg.prep_idle_minutes = 0
        ds = mk_ds(self.st)
        self.wait(lambda: any(t["kind"] == "evolve" for t in self.daemon.ledger.all()), timeout=30)
        self.wait(lambda: len(self.docs()) == 1)
        self.assertEqual(self.docs()[0]["trigger"], "auto")
        time.sleep(2)
        self.assertEqual(len([t for t in self.daemon.ledger.all() if t["kind"] == "evolve"]), 1)
        discussion.append_turn(self.st, ds, "human", "我回来了")       # 新的空闲期
        self.wait(lambda: len([t for t in self.daemon.ledger.all() if t["kind"] == "evolve"]) == 2, timeout=30)


    def test_prep_off_still_evolves(self):
        """AR_PREP_AUTO=0：离开后不再自动读论文（即使有未检验的前提），空闲自动演进照常。"""
        self.daemon.cfg.prep_auto = False
        self.daemon.cfg.prep_idle_minutes = 0
        mk_ds(self.st)
        self.wait(lambda: len(self.docs()) == 1, timeout=30)
        kinds = {t["kind"] for t in self.daemon.ledger.all()}
        self.assertEqual(kinds, {"evolve"})
        self.assertFalse(self.req("GET", "/api/mode")[1]["prep_auto"])


del _Api

if __name__ == "__main__":
    unittest.main()
