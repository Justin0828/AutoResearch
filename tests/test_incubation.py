"""Phase 2.5：想法自演进（DESIGN.md §5.9–5.14）。假 claude + 离线网络夹具，不耗额度、不碰网络。"""
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from autoresearch import (bootstrap, briefing, config, incubation, insights, modes, papers, planner,
                          schema, tasks)
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from autoresearch.library import Library
from autoresearch.runner import Runner, SealError, check_sealed
from tests.util import temp_cfg

HERE = Path(__file__).resolve().parent
FAKE = str(HERE / "fake_claude.py")
NET = str(HERE / "fixtures" / "net")


def formalize(st, maturity="formalized"):
    m, b = st.read_obj("Q001")
    m["maturity"] = maturity
    with st.tx("t", actor="human") as tx:
        tx.write_obj("Q001", m, b)


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self, AR_NET_FIXTURES=NET, AR_CLAUDE_BIN=FAKE)
        self.st, _ = bootstrap.init(self.cfg)
        self.lib = Library(self.cfg.library)

    def errors(self):
        return schema.validate_repo(self.st.state)[0]

    def chain(self, tid="T00001"):
        did = incubation.session(self.st)
        return {"id": tid, "incubation": did, "round": 1,
                "foundation": incubation.current_foundation(self.st, did)}

    def idea(self, tid="T00001", **kw):
        args = dict(task=tid, chain=self.chain(tid), statement="反馈频率比数据规模更关键",
                    falsifier="固定低频 chunk 在更多数据下继续提升", new_premises=["接触瞬间决定成败"],
                    reasoning="从 A001 出发", called_dead_ends=True)
        args.update(kw)
        return incubation.record_idea(self.st, **args)


class GateTest(Base):
    def test_entry_gate_explains_what_is_missing(self):
        with self.assertRaises(ValueError) as e:
            incubation.enter(self.st, "试试")
        msg = str(e.exception)
        self.assertIn("scoped", msg)
        self.assertIn("formalized", msg)
        self.assertIn("可测量", msg)                         # Q001 正文里记下的缺口被带出来
        self.assertEqual(modes.mode(self.st), "discussion")
        formalize(self.st)
        did, fid = incubation.enter(self.st, "睡前推演一下")
        pm, _ = self.st.project()
        self.assertEqual((pm["mode"], pm["incubation"]), ("incubation", did))
        self.assertEqual(self.st.read_obj(did)[0]["kind"], "mode")
        f, fb = self.st.read_obj(fid)
        self.assertEqual((f["session"], f["round"], f["question"]), (did, "1", "Q001"))
        self.assertIn(f["base"][:7], "".join(c["sha"] for c in self.st.log()))
        self.assertNotIn("提出者", fb)
        self.assertEqual(self.errors(), [])
        with self.assertRaisesRegex(ValueError, "已经在自演进"):
            incubation.enter(self.st)
        with self.assertRaisesRegex(ValueError, "互斥"):
            modes.handoff(self.st, ["H001"])

    def test_validation_mode_blocks_entry(self):
        formalize(self.st)
        modes.handoff(self.st, ["H001"])
        self.assertTrue(any("验证模式" in p for p in incubation.entry_problems(self.st)))


class FoundationTest(Base):
    def setUp(self):
        super().setUp()
        formalize(self.st)
        self.iid_in = insights.create(self.st, "精细操作的瓶颈在接触瞬间", "hunch", basis=["H001"])
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001", for_targets=["H001"])
        self.pid = pid
        self.did, self.fid = incubation.enter(self.st)

    def test_foundation_contents(self):
        _, fb = self.st.read_obj(self.fid)
        self.assertIn(self.iid_in, fb)                  # 看得到理解（用户决定）
        self.assertIn("是理解，不是证据", fb)
        self.assertIn("A001", fb)
        self.assertIn("H001", fb)
        self.assertNotIn("Contact-Rich", fb)            # 不给论文标题、笔记、全文
        self.assertNotIn("提出者", fb)

    def test_update_only_by_external_evidence(self):
        iid, _ = self.idea()
        self.assertIsNone(incubation.next_foundation(self.st, self.did, 1, []))   # 没有外部证据不出新版
        eid, _, _ = papers.record_evidence(
            self.st, self.lib, iid, "contradict", self.pid, "反驳这条想法的外部证据",
            locator=["s4.2-p1"], quote="success on peg insertion  plateaus at 41% after 500 demonstrations")
        title = self.st.read_obj(self.pid)[0]["title"]
        gid = papers.annotate_grounding(self.st, iid, "contradicted", [self.pid, eid],
                                        f"外部核查：被直接反驳，见 {title}")
        f2 = incubation.next_foundation(self.st, self.did, 1, [eid, gid])
        m, b = self.st.read_obj(f2)
        self.assertEqual((m["parent"], m["round"], m["delta"]), (self.fid, "2", [eid, gid]))
        self.assertIn("外部核查：被直接反驳", b)
        self.assertNotIn(title, b)                       # 接地的话里带的论文名换成编号（§5.11 不给标题）
        self.assertIn(f"见 {self.pid}", b)
        self.assertIn(eid, b)
        self.assertNotIn("反馈频率比数据规模更关键", b)   # 不取 Idea 原文，只取接地自己的话
        self.assertIn(f"{f2} ← {self.fid}：+{eid}（{gid}）", self.st.log(limit=1)[0]["subject"])
        _, b1 = self.st.read_obj(self.fid)
        self.assertNotIn(eid, b1)                        # 旧版不动：链内冻结
        self.assertEqual(incubation.current_foundation(self.st, self.did), f2)
        self.assertEqual(self.errors(), [])


class IdeaTest(Base):
    def setUp(self):
        super().setUp()
        formalize(self.st)
        self.did, self.fid = incubation.enter(self.st)

    def test_structural_gates(self):
        with self.assertRaisesRegex(ValueError, "check_dead_ends"):
            self.idea(called_dead_ends=False)
        with self.assertRaisesRegex(ValueError, "falsifier"):
            self.idea(falsifier=" ")
        with self.assertRaisesRegex(ValueError, "不能与陈述相同"):
            self.idea(falsifier="反馈频率比数据规模更关键")
        with self.assertRaisesRegex(ValueError, "new_premises"):
            self.idea(new_premises=None)
        with self.assertRaisesRegex(ValueError, "challenge_notes"):
            self.idea(challenges=["A001"])
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.idea(builds_on=["IN999"])
        self.assertEqual(self.st.list("idea"), [])
        iid, aids = self.idea(challenges=["A001"], challenge_notes="A001 可能不成立")
        self.idea(new_premises=[])
        with self.assertRaisesRegex(ValueError, "最多"):
            self.idea()
        m, b = self.st.read_obj(iid)
        self.assertEqual((m["status"], m["premises"], m["foundation"], m["chain"]),
                         ("grounding", aids, self.fid, "T00001"))
        self.assertEqual(self.st.provenance()[iid]["origin"], "ai")
        a, _ = self.st.read_obj(aids[0])
        self.assertEqual((a["idea"], a["relied_on_by"], a["status"]), (iid, [iid], "unexamined"))
        self.assertEqual(self.errors(), [])

    def test_challenge_must_be_in_foundation(self):
        # 进入之后才建的前提不在本轮基本盘里
        with self.st.tx("t", actor="human") as tx:
            tx.write_obj("A009", {"id": "A009", "type": "assumption", "status": "unexamined",
                                  "relied_on_by": ["Q001"], "created": "2026-09-23"}, "后来的前提\n")
        with self.assertRaisesRegex(ValueError, "不在本轮基本盘"):
            self.idea(challenges=["A009"], challenge_notes="x")

    def test_speculative_premises_stay_out_of_normal_flows(self):
        iid, aids = self.idea()
        brief = briefing.Assembler(self.st).briefing("discuss", {"id": "T9", "kind": "discuss_turn",
                                                                 "goal": "g"})
        self.assertNotIn(aids[0], brief)
        with self.assertRaisesRegex(ValueError, "想法被接受前"):
            modes.check_item(self.st, aids[0])
        with self.st.tx("t", actor="human") as tx:     # 让 A001 不再是 unexamined，只剩推测性前提
            a, ab = self.st.read_obj("A001")
            a["status"] = "retired"
            tx.write_obj("A001", a, ab)
        self.assertNotEqual(planner.pick_prep_target(self.st)[0], aids[0])

    def test_grounding_only_screens_out_contradicted(self):
        i1, _ = self.idea()
        i2, _ = self.idea()
        pid, _, _ = papers.register(self.st, self.lib, "2401.00001")
        before = self.st.read_obj(i1)[1]
        papers.annotate_grounding(self.st, i1, "prior_work", [pid], "有人做过相近的思想")
        self.assertEqual(self.st.read_obj(i1)[0]["status"], "shortlisted")   # 做过不淘汰
        self.assertEqual(self.st.read_obj(i1)[1], before)                       # 正文一字不改
        papers.annotate_grounding(self.st, i2, "contradicted", [pid], "被直接反驳")
        self.assertEqual(self.st.read_obj(i2)[0]["status"], "screened_out")
        self.assertEqual(self.errors(), [])

    def test_triage_accept_and_reject(self):
        i1, a1 = self.idea()
        i2, a2 = self.idea()
        with self.assertRaisesRegex(ValueError, "validation"):
            incubation.accept(self.st, i1, hypothesis={"statement": "s"})
        made = incubation.accept(self.st, i1, hypothesis={"validation": "文献核查"},
                                 insight={"firmness": "hunch"}, note="值得验证")
        h, _ = self.st.read_obj(made[0])
        self.assertEqual((h["idea"], h["status"], h["falsifier"]),
                         (i1, "proposed", "固定低频 chunk 在更多数据下继续提升"))
        n, _ = self.st.read_obj(made[1])
        self.assertEqual(n["basis"], [i1])
        self.assertEqual(self.st.provenance()[made[0]]["origin"], "ai")
        self.assertFalse(incubation.speculative(self.st, self.st.read_obj(a1[0])[0]))  # 转为普通前提
        with self.assertRaisesRegex(ValueError, "理由"):
            incubation.reject(self.st, i2, " ")
        incubation.reject(self.st, i2, "和上周讨论过的方向重复")
        self.assertEqual(self.st.read_obj(a2[0])[0]["status"], "retired")
        # 与 dead-end 同等对待：briefing 的已关闭方向、下一版基本盘都全文列出
        brief = briefing.Assembler(self.st).briefing("judge", {"id": "T9", "kind": "assess", "goal": "g"})
        self.assertIn("和上周讨论过的方向重复", brief)
        self.assertIn("和上周讨论过的方向重复", incubation.foundation_body(self.st))
        with self.assertRaisesRegex(ValueError, "已是"):
            incubation.reject(self.st, i1, "x")
        self.assertEqual(self.errors(), [])

    def test_stop_rules_and_blank_paper(self):
        t = [{"ended": "x"}] * 5
        self.assertIsNone(incubation.stop_reason(self.st, self.did, t, 0, 1, 8))
        self.assertIn("window", incubation.stop_reason(self.st, self.did, [{"ended": "x"}] * 30, 0, 1, 8))
        real = [{"q5_start": 0.2, "q5_end": 0.36, "ended": "x"}, {"q5_start": 0.36, "q5_end": 0.52, "ended": "x"}]
        self.assertIn("window", incubation.stop_reason(self.st, self.did, real, 0, 1, 8))
        self.assertIn("nothing", incubation.stop_reason(self.st, self.did, t, 2, 3, 8))
        self.assertIn("limit", incubation.stop_reason(self.st, self.did, t, 0, 8, 8))
        dec, n = incubation.conclude(self.st, self.did, "nothing", 2, 4)
        self.assertEqual(n, 0)
        self.assertIn("本轮没有值得你看的东西", self.st.read_obj(dec)[1])


class SealTest(Base):
    def test_incubate_cmd_is_sealed(self):
        formalize(self.st)
        did, fid = incubation.enter(self.st)
        from autoresearch.tasks import Ledger
        from autoresearch.quota import Quota
        ledger = Ledger(self.cfg)
        t = ledger.create("incubate", "推演", incubation=did, round=1, foundation=fid)
        r = Runner(self.cfg, self.st, ledger, Quota(self.cfg), Bus(self.cfg))
        cmd, _ = r.prepare(t, "p")
        self.assertIn("--restricted", cmd)
        self.assertNotIn("--add-dir", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Grep,Glob")
        for x in ("WebSearch", "WebFetch", "Bash", "mcp__state__search_papers", "mcp__state__open_paper",
                  f"Read(/{self.st.state}/**)", f"Read(/{self.cfg.library}/**)"):
            self.assertIn(x, cmd)
        env = json.loads((ledger.path(t["id"]) / "mcp.json").read_text())["mcpServers"]["state"]["env"]
        self.assertEqual(env["AR_TOOLSET"], "check_dead_ends,record_idea,checkpoint")
        brief = (ledger.path(t["id"]) / "briefing.md").read_text()
        self.assertIn(f"基本盘 {fid}（冻结）", brief)
        self.assertNotIn("提出者", brief)
        # 配置回归：开跑前自检拒绝启动
        bad = cmd + ["--add-dir", "/tmp"]
        with self.assertRaises(SealError):
            check_sealed(bad)
        with self.assertRaises(SealError):
            check_sealed([x for x in cmd if x != "--restricted"])

    def test_mcp_fail_closed(self):
        tdir = self.cfg.tasks / "T00001"
        tdir.mkdir(parents=True)
        env = dict(os.environ, AR_TASK="T00001", AR_TASK_DIR=str(tdir), PYTHONPATH=str(config.CODE_ROOT))
        env.pop("AR_TOOLSET", None)
        msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]
        p = subprocess.run([sys.executable, "-m", "autoresearch.mcp_server"], env=env,
                           input="\n".join(json.dumps(m) for m in msgs) + "\n",
                           capture_output=True, text=True, timeout=30)
        names = [t["name"] for t in json.loads(p.stdout.splitlines()[1])["result"]["tools"]]
        self.assertEqual(names, ["checkpoint"])


class DaemonIncubationTest(Base):
    def setUp(self):
        super().setUp()
        formalize(self.st)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("FAKE_MODE", "FAKE_INC_PLAN", "FAKE_GVERDICT")])
        os.environ["FAKE_MODE"] = "judge"
        self.d = Daemon(self.cfg, self.st, Bus(self.cfg))
        self.d.start(background=False)
        self.addCleanup(lambda: [r.kill() for r in list(self.d.runners.values())])

    def until(self, cond, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.d.tick()
            if cond():
                return
            time.sleep(0.1)
        self.fail("等待超时：" + json.dumps([(t["id"], t["kind"], t["status"], t.get("error"))
                                          for t in self.d.ledger.all()], ensure_ascii=False))

    def test_rounds_blank_paper_and_foundation_history(self):
        os.environ["FAKE_INC_PLAN"] = "idea1,idea3,blank,blank,blank,blank"
        did, f1 = self.d.enter_incubation("睡前")
        self.until(lambda: (self.d.st.get("hold") or {}).get("kind") == "incubation_done")
        ks = [(t["kind"], t.get("round")) for t in self.d.ledger.all()]
        self.assertEqual([k for k, _ in ks].count("incubate"), 6)      # 3 轮 × 2 条
        self.assertEqual([k for k, _ in ks].count("ground_idea"), 2)
        ideas = {m["id"]: m for m, _ in self.st.list("idea")}
        self.assertEqual(sorted(m["status"] for m in ideas.values()), ["shortlisted", "shortlisted"])
        self.assertEqual(sorted(len(m["premises"]) for m in ideas.values()), [1, 3])
        # 基本盘：第 1 轮带回了接地结论 → F002；之后两轮没有外部证据，不出新版
        fs = [m for m, _ in self.st.list("foundation")]
        self.assertEqual([m["id"] for m in fs], [f1, "F002"])
        self.assertEqual(fs[1]["parent"], f1)
        self.assertTrue(all(x.startswith("GR") for x in fs[1]["delta"]))
        log = subprocess.run(["git", "log", "--format=%s", "--", "foundations/"], cwd=self.st.state,
                             capture_output=True, text=True).stdout
        self.assertIn(f"F002 ← {f1}", log)
        # 第 2 轮的推演从 F002 出发
        r2 = [t for t in self.d.ledger.all() if t["kind"] == "incubate" and t["round"] == 2]
        self.assertEqual(len(r2), 2)
        self.assertTrue(all(t["foundation"] == "F002" for t in r2))
        # 推演记录全部留存，含交白卷的；角度被记下来，下一条链的 briefing 里能看到
        chains = [m for m, _ in self.st.list("chain")]
        self.assertEqual(sorted(m["status"] for m in chains), ["done", "done", "empty", "empty", "empty", "empty"])
        last = self.d.ledger.all()[-1] if self.d.ledger.all()[-1]["kind"] == "incubate" else \
            [t for t in self.d.ledger.all() if t["kind"] == "incubate"][-1]
        brief = (self.d.ledger.path(last["id"]) / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("第 1 次推演的角度", brief)
        # 交卷：连续两轮没东西 → 停；只有 2 条过关，不凑数
        hold = self.d.st["hold"]
        self.assertIn("nothing", hold["reason"])
        summ = self.st.read_obj(hold["summary"])[1]
        self.assertIn("过关 2 条", summ)
        # 推演任务是封死的
        inc = next(t for t in self.d.ledger.all() if t["kind"] == "incubate")
        cmd = json.loads((self.d.ledger.path(inc["id"]) / "cmd.json").read_text(encoding="utf-8"))
        self.assertIn("--restricted", cmd)
        self.assertNotIn("--add-dir", cmd)
        # 接地任务看不到理解与 origin，但看得到被核查的想法与它的基本盘
        g = next(t for t in self.d.ledger.all() if t["kind"] == "ground_idea")
        gb = (self.d.ledger.path(g["id"]) / "briefing.md").read_text(encoding="utf-8")
        self.assertIn("被核查的想法", gb)
        self.assertNotIn("提出者", gb)
        # hold 期间不再开新链；回到讨论模式
        n = len(self.d.ledger.all())
        for _ in range(5):
            self.d.tick()
        self.assertEqual(len(self.d.ledger.all()), n)
        self.d.recall("去分诊")
        self.assertEqual(modes.mode(self.st), "discussion")
        self.assertEqual(self.st.dirty_paths(), [])
        self.assertEqual(self.errors(), [])

    def test_three_passing_ideas_stop_and_contradiction_feeds_foundation(self):
        os.environ["FAKE_INC_PLAN"] = "idea1"
        os.environ["FAKE_GVERDICT"] = "contradicted"
        did, f1 = self.d.enter_incubation()
        self.until(lambda: self.d.st.get("inc", {}).get("finalized") == 1)
        # 被直接反驳 → screened_out；反驳证据进下一版基本盘
        self.assertEqual({m["status"] for m, _ in self.st.list("idea")}, {"screened_out"})
        f2, f2b = self.st.read_obj("F002")
        self.assertTrue(any(x.startswith("E") for x in f2["delta"]))
        self.assertIn("原文直接反驳", f2b)
        os.environ["FAKE_GVERDICT"] = "novel"
        self.until(lambda: (self.d.st.get("hold") or {}).get("kind") == "incubation_done")
        ok = [m for m, _ in self.st.list("idea") if m["status"] == "shortlisted"]
        self.assertGreaterEqual(len(ok), 3)
        self.assertIn("passed", self.d.st["hold"]["reason"])
        self.assertEqual(self.errors(), [])


if __name__ == "__main__":
    unittest.main()
