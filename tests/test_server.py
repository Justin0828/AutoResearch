import json
import os
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from autoresearch import bootstrap
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from autoresearch.server import App
from tests.util import temp_cfg

FAKE = str(Path(__file__).resolve().parent / "fake_claude.py")


class ServerTest(unittest.TestCase):
    def setUp(self):
        os.environ["FAKE_MODE"] = "reply"
        self.addCleanup(os.environ.pop, "FAKE_MODE", None)
        self.cfg = temp_cfg(self, AR_CLAUDE_BIN=FAKE, AR_PORT=0, AR_DISTILL_EVERY=1)
        st, _ = bootstrap.init(self.cfg)
        self.daemon = Daemon(self.cfg, st, Bus(self.cfg))
        self.daemon.start()
        self.addCleanup(self.daemon.stop)
        self.httpd = App(self.cfg, st, self.daemon.bus, self.daemon).serve()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
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

    def test_bound_to_localhost(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_static_and_overview(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("AutoResearch", r.read().decode())
        s, o = self.req("GET", "/api/overview")
        self.assertEqual(s, 200)
        self.assertEqual(o["project"]["mode"], "discussion")
        self.assertEqual(o["validation"]["errors"], [])
        self.assertEqual({b["origin"] for b in o["bias"]}, {"human", "disputed"})   # H002 单列

    def test_discussion_to_candidate_to_state(self):
        s, r = self.req("POST", "/api/discussions", {"title": "瓶颈"})
        ds = r["id"]
        s, r = self.req("POST", f"/api/discussions/{ds}/messages", {"text": "感知不是瓶颈。"})
        self.assertEqual(r["n"], 1)
        self.wait(lambda: len(self.req("GET", f"/api/discussions/{ds}")[1]["turns"]) == 2)
        os.environ["FAKE_MODE"] = "distill"
        self.wait(lambda: self.req("GET", "/api/candidates?status=pending")[1])
        cid = self.req("GET", "/api/candidates?status=pending")[1][0]["id"]
        s, r = self.req("POST", f"/api/candidates/{cid}/accept", {})
        self.assertEqual((s, r["id"]), (200, "A002"))
        o = self.req("GET", "/api/overview")[1]
        self.assertIn("A002", [a["id"] for a in o["assumptions"]])
        self.assertEqual(o["validation"]["errors"], [])

    def test_inline_edit_payload_then_accept(self):
        """前端内联编辑发的形态：id 列表是逗号分隔字符串、可同时改类别，然后接受。"""
        from autoresearch import candidates, discussion
        st = self.daemon.store
        ds = discussion.create(st, "t")
        discussion.append_turn(st, ds, "human", "我们下周做粒度消融。")
        cid = candidates.propose(st, kind="assumption", statement="粒度是瓶颈", rationale="r",
                                 origin="unclear", origin_note="拿不准", source=ds, turns=[1],
                                 relied_on_by=["Q001"], task="T1", actor="agent")
        changes = {"kind": "hypothesis", "statement": "动作粒度是瓶颈", "rationale": "已安排消融",
                   "origin": "human", "origin_note": "", "falsifier": "消融无差异",
                   "validation": "下周粒度消融", "confidence": ""}
        s, r = self.req("POST", f"/api/candidates/{cid}/update", {"changes": changes})
        self.assertEqual(s, 200, r)
        s, r = self.req("POST", f"/api/candidates/{cid}/accept", {})
        self.assertEqual((s, r.get("id")), (200, "H003"), r)
        meta, _ = st.read_obj("H003")
        self.assertEqual(meta["validation"], "下周粒度消融")
        self.assertEqual(st.provenance()["H003"]["origin"], "human")
        # 列表字段以字符串提交
        cid2 = candidates.propose(st, kind="assumption", statement="x", rationale="r", origin="ai",
                                  source=ds, turns=[1], relied_on_by=["Q001"], task="T1", actor="agent")
        s, r = self.req("POST", f"/api/candidates/{cid2}/update",
                        {"changes": {"relied_on_by": "Q001, H001", "derived_from": ""}})
        self.assertEqual(s, 200, r)
        self.assertEqual(candidates.load(st, cid2)[0]["relied_on_by"], ["Q001", "H001"])

    def test_errors_are_400(self):
        s, r = self.req("POST", "/api/candidates/C999/reject", {"reason": "x"})
        self.assertEqual(s, 400)
        s, r = self.req("POST", "/api/discussions/DS001/messages", {"text": ""})
        self.assertEqual(s, 400)


if __name__ == "__main__":
    unittest.main()


class IncubationApiTest(unittest.TestCase):
    """前端走的同一套 API：闸门 → 成熟度 → 进入自演进 → 按轮看 → 分诊 → 回到讨论（Phase 2.5 验收 1/4/6）。"""

    def setUp(self):
        for k, v in (("FAKE_MODE", "judge"), ("FAKE_INC_PLAN", "idea1,idea2,blank,blank,blank,blank")):
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        net = str(Path(__file__).resolve().parent / "fixtures" / "net")
        self.cfg = temp_cfg(self, AR_CLAUDE_BIN=FAKE, AR_PORT=0, AR_NET_FIXTURES=net)
        self.st, _ = bootstrap.init(self.cfg)
        self.daemon = Daemon(self.cfg, self.st, Bus(self.cfg))
        self.daemon.start()
        self.addCleanup(self.daemon.stop)
        self.httpd = App(self.cfg, self.st, self.daemon.bus, self.daemon).serve()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    req = ServerTest.req

    def test_incubation_flow(self):
        s, m = self.req("GET", "/api/mode")
        self.assertTrue(any("scoped" in p for p in m["entry_problems"]))
        s, e = self.req("POST", "/api/mode/incubate", {"note": "x"})
        self.assertEqual(s, 400)
        self.assertIn("formalized", e["error"])
        s, _ = self.req("POST", "/api/questions/Q001/maturity", {"maturity": "formalized"})
        self.assertEqual(s, 200)
        s, x = self.req("POST", "/api/mode/incubate", {"note": "睡前"})
        self.assertEqual((s, x["foundation"]), (200, "F001"))
        t0 = time.time()
        while not (self.daemon.st.get("hold") or {}).get("kind") == "incubation_done":
            self.assertLess(time.time() - t0, 60, "自演进没有收工")
            time.sleep(0.2)
        s, v = self.req("GET", "/api/incubation")
        sess = v["sessions"][0]
        self.assertEqual(sess["rounds"][0]["foundation"]["id"], "F001")
        self.assertEqual(sess["rounds"][1]["foundation"]["parent"], "F001")
        self.assertTrue(all(c["angle"] for r in sess["rounds"] for c in r["chains"]))
        self.assertTrue(sess["summary"])
        s, ideas = self.req("GET", "/api/ideas")
        ids = [i["id"] for i in ideas["ideas"]]
        self.assertEqual([i["signals"]["n"] for i in ideas["ideas"]], [1, 2])     # N 小的在前
        s, a = self.req("POST", f"/api/ideas/{ids[0]}/accept",
                        {"hypothesis": {"validation": "文献核查"}, "note": "值得"})
        self.assertEqual((s, a["made"][0][0]), (200, "H"))
        s, e = self.req("POST", f"/api/ideas/{ids[1]}/reject", {"reason": ""})
        self.assertEqual(s, 400)
        s, _ = self.req("POST", f"/api/ideas/{ids[1]}/reject", {"reason": "方向不对"})
        self.assertEqual(s, 200)
        s, f = self.req("GET", "/api/text/F002")
        self.assertIn("本 session 外部核查带回的结果", f["body"])
        s, _ = self.req("POST", "/api/mode/recall", {"reason": "去讨论"})
        self.assertEqual(self.req("GET", "/api/mode")[1]["mode"], "discussion")
