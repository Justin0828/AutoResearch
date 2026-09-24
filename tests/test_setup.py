"""新项目的建立（DESIGN.md §5）：空 State → 研究者在前端填写 → project.md + Q001。"""
import json
import threading
import unittest
import urllib.request

from autoresearch import bootstrap, frontmatter, schema
from autoresearch.daemon import Daemon
from autoresearch.events import Bus
from autoresearch.server import App
from tests.util import temp_cfg


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self)
        self.st, created = bootstrap.init(self.cfg)
        self.assertTrue(created)

    def test_default_init_is_empty(self):
        self.assertTrue(bootstrap.needs_setup(self.st))
        files = sorted(p.name for p in self.cfg.state.iterdir() if p.name != ".git")
        self.assertEqual(files, [".gitignore", "README.md"])
        self.assertEqual(self.st.list("question"), [])

    def test_setup_writes_valid_project(self):
        bootstrap.setup(self.st, "标题", "方向描述", "interface 应该划分在哪里？")
        self.assertFalse(bootstrap.needs_setup(self.st))
        pm, pb = self.st.project()
        self.assertEqual((pm["title"], pm["mode"], pm["main_question"]), ("标题", "discussion", "Q001"))
        self.assertIn("方向描述", pb)
        meta, body = self.st.read_obj("Q001")
        self.assertEqual(meta["maturity"], "vague")
        self.assertIn("interface 应该划分在哪里？", body)
        self.assertEqual(self.st.provenance()["Q001"]["origin"], "human")
        self.assertEqual(schema.validate_repo(self.cfg.state), ([], []))
        self.assertEqual(self.st.dirty_paths(), [])

    def test_setup_refuses_to_overwrite(self):
        bootstrap.setup(self.st, "A", "", "问题一")
        with self.assertRaises(ValueError):
            bootstrap.setup(self.st, "B", "", "问题二")
        self.assertEqual(self.st.project()[0]["title"], "A")

    def test_setup_requires_title_and_question(self):
        for t, q in (("", "问题"), ("标题", "  ")):
            with self.assertRaises(ValueError):
                bootstrap.setup(self.st, t, "", q)
        self.assertTrue(bootstrap.needs_setup(self.st))

    def test_daemon_plans_nothing_before_setup(self):
        d = Daemon(self.cfg, self.st, Bus(self.cfg))
        d._plan()
        self.assertEqual(d.ledger.all(), [])


class SetupServerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = temp_cfg(self, AR_PORT=0)
        st, _ = bootstrap.init(self.cfg)
        daemon = Daemon(self.cfg, st, Bus(self.cfg))
        daemon.start()
        self.addCleanup(daemon.stop)
        httpd = App(self.cfg, st, daemon.bus, daemon).serve()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.shutdown)
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def req(self, method, path, body=None):
        r = urllib.request.Request(self.base + path, method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_setup_flow(self):
        s, o = self.req("GET", "/api/overview")
        self.assertEqual(s, 200)
        self.assertTrue(o["setup_needed"])
        self.assertEqual(self.req("POST", "/api/discussions", {"title": "x"})[0], 409)
        s, _ = self.req("POST", "/api/project/setup", {"title": "T", "description": "D", "question": "Q?"})
        self.assertEqual(s, 200)
        s, o = self.req("GET", "/api/overview")
        self.assertFalse(o["setup_needed"])
        self.assertEqual(o["validation"]["errors"], [])
        self.assertEqual(o["questions"][0]["id"], "Q001")
        self.assertEqual(self.req("POST", "/api/project/setup", {"title": "T2", "question": "Q2"})[0], 400)
        self.assertEqual(self.req("POST", "/api/discussions", {"title": "x"})[0], 200)


if __name__ == "__main__":
    unittest.main()
