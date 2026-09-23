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
        self.assertEqual({b["origin"] for b in o["bias"]}, {"human"})

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

    def test_errors_are_400(self):
        s, r = self.req("POST", "/api/candidates/C999/reject", {"reason": "x"})
        self.assertEqual(s, 400)
        s, r = self.req("POST", "/api/discussions/DS001/messages", {"text": ""})
        self.assertEqual(s, 400)


if __name__ == "__main__":
    unittest.main()
