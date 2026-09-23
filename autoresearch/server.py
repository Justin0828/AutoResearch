"""M9 后端：HTTP API + SSE 事件流 + 静态前端。零依赖（stdlib http.server）。

只绑 127.0.0.1，经 SSH 端口转发访问，不做鉴权（DESIGN.md M9）。
"""
import json
import queue
import re
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import candidates, discussion, frontmatter, insights, metrics, reviews, schema

STATIC = Path(__file__).resolve().parent / "static"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def make_handler(app):
    routes = []

    def route(method, pattern):
        def deco(fn):
            routes.append((method, re.compile(f"^{pattern}$"), fn))
            return fn
        return deco

    st, d = app.store, app.daemon

    # ------------------------------------------------------------ 研究状态

    @route("GET", "/api/overview")
    def overview(req, q):
        pmeta, pbody = st.project()
        prov = st.provenance()

        def objs(t):
            return [dict(m, body=b.strip(), provenance=prov.get(m.get("id")))
                    for m, b in st.list(t)]
        errs, warns = schema.validate_repo(st.state)
        return {"project": dict(pmeta, body=pbody.strip()),
                "questions": objs("question"), "insights": objs("insight"),
                "assumptions": objs("assumption"),
                "hypotheses": objs("hypothesis"), "uncertainties": objs("uncertainty"),
                "dead_ends": objs("dead-end"), "evidence": objs("evidence"),
                "papers": objs("paper"),
                "bias": metrics.bias_by_origin(st),
                "reviews": reviews.list_all(st, "open"),
                "validation": {"errors": errs, "warnings": warns},
                "head": st.head()}

    @route("GET", "/api/objects/(?P<id>[A-Z]+\\d+)")
    def get_object(req, q, id):
        meta, body = st.read_obj(id)
        if meta is None:
            raise ApiError(404, f"{id} 不存在")
        return {"meta": meta, "body": body, "provenance": st.provenance().get(id)}

    # ------------------------------------------------------------ 讨论

    @route("GET", "/api/discussions")
    def list_discussions(req, q):
        return discussion.list_all(st)

    @route("POST", "/api/discussions")
    def new_discussion(req, q):
        return {"id": discussion.create(st, (req.json().get("title") or "").strip())}

    @route("GET", "/api/discussions/(?P<ds>DS\\d+)")
    def get_discussion(req, q, ds):
        try:
            meta, turns = discussion.read(st, ds)
        except KeyError:
            raise ApiError(404, f"{ds} 不存在")
        smeta, sbody = discussion.summary(st, ds)
        busy = [t for t in d.ledger.all() if t.get("discussion") == ds
                and t["status"] in ("queued", "running")]
        return {"meta": meta, "turns": turns,
                "summary": {"covers_through": smeta.get("covers_through", 0), "body": sbody.strip()},
                "streaming": d.replies.get(ds), "tasks": busy,
                "undistilled_human": discussion.undistilled_human(st, ds, turns)}

    @route("POST", "/api/discussions/(?P<ds>DS\\d+)/messages")
    def post_message(req, q, ds):
        text = (req.json().get("text") or "").strip()
        if not text:
            raise ApiError(400, "发言不能为空")
        return {"n": d.human_message(ds, text)}

    @route("POST", "/api/discussions/(?P<ds>DS\\d+)/distill")
    def distill(req, q, ds):
        t = d.request_distill(ds)
        return {"task": t, "note": None if t else "没有需要蒸馏的新内容，或已有蒸馏任务在排队"}

    @route("POST", "/api/discussions/(?P<ds>DS\\d+)/status")
    def set_status(req, q, ds):
        s = req.json().get("status")
        if s not in ("open", "closed"):
            raise ApiError(400, "status 必须是 open / closed")
        discussion.set_status(st, ds, s)
        if s == "closed":
            d.request_distill(ds, manual=False)
        return {"ok": True}

    # ------------------------------------------------------------ 候选区

    @route("GET", "/api/candidates")
    def list_candidates(req, q):
        return candidates.list_all(st, (q.get("status") or [None])[0])

    @route("POST", "/api/candidates/(?P<cid>C\\d+)/accept")
    def accept(req, q, cid):
        body = req.json()
        new = candidates.accept(st, cid, origin=body.get("origin"), changes=body.get("changes"))
        app.bus.publish("candidates", {"accepted": cid, "as": new})
        return {"id": new}

    @route("POST", "/api/candidates/(?P<cid>C\\d+)/reject")
    def reject(req, q, cid):
        candidates.reject(st, cid, req.json().get("reason", ""))
        app.bus.publish("candidates", {"rejected": cid})
        return {"ok": True}

    @route("POST", "/api/candidates/(?P<cid>C\\d+)/update")
    def update(req, q, cid):
        candidates.update(st, cid, dict(req.json().get("changes") or {}))
        app.bus.publish("candidates", {"updated": cid})
        return {"ok": True}

    # ------------------------------------------------------------ 理解（Insight）

    @route("POST", "/api/insights")
    def new_insight(req, q):
        b = req.json()
        iid = insights.create(st, b.get("statement", ""), b.get("firmness", ""), b.get("basis"),
                              b.get("basis_note", ""), b.get("informs"), b.get("change_mind", ""))
        app.bus.publish("state", {"insight": iid})
        return {"id": iid}

    @route("POST", "/api/insights/(?P<iid>IN\\d+)/revise")
    def revise_insight(req, q, iid):
        b = req.json()
        new = insights.revise(st, iid, b.get("statement", ""), b.get("firmness", ""),
                              b.get("note", ""), change_mind=b.get("change_mind"))
        app.bus.publish("state", {"insight": new})
        return {"id": new}

    @route("POST", "/api/insights/(?P<iid>IN\\d+)/abandon")
    def abandon_insight(req, q, iid):
        new = insights.abandon(st, iid, req.json().get("reason", ""))
        app.bus.publish("state", {"insight": iid, "reviews": new})
        return {"reviews": new}

    # ------------------------------------------------------------ 推翻与待重新审视（M5.6b）

    @route("POST", "/api/assumptions/(?P<aid>A\\d+)/invalidate")
    def invalidate(req, q, aid):
        did, new = reviews.human_invalidate(st, aid, req.json().get("reason", ""))
        app.bus.publish("state", {"invalidated": aid, "reviews": new})
        return {"decision": did, "reviews": new}

    @route("GET", "/api/reviews")
    def list_reviews(req, q):
        return reviews.list_all(st, (q.get("status") or [None])[0])

    @route("POST", "/api/reviews/(?P<rid>R\\d+)/resolve")
    def resolve_review(req, q, rid):
        b = req.json()
        reviews.resolve(st, rid, b.get("status"), b.get("note", ""))
        app.bus.publish("state", {"review": rid})
        return {"ok": True}

    # ------------------------------------------------------------ 班次 / 任务 / 通知

    @route("GET", "/api/shift")
    def shift(req, q):
        return d.shift_view()

    @route("POST", "/api/shift/pause")
    def pause(req, q):
        d.pause_manual()
        return d.shift_view()

    @route("POST", "/api/shift/resume")
    def resume(req, q):
        d.resume()
        return d.shift_view()

    @route("POST", "/api/shift/cutoff")
    def cutoff(req, q):
        d.cutoff(int(req.json().get("resume_in", 60)))
        return d.shift_view()

    @route("GET", "/api/tasks")
    def tasks(req, q):
        n = int((q.get("limit") or ["30"])[0])
        return list(reversed(d.ledger.all()))[:n]

    @route("GET", "/api/handoffs")
    def handoffs(req, q):
        out = []
        hd = st.state / "handoffs"
        for p in sorted(hd.glob("HO*.md"), key=lambda p: schema.split_id(p.stem)[1] or 0,
                        reverse=True)[:20] if hd.is_dir() else []:
            meta, body = frontmatter.parse(p.read_text(encoding="utf-8"))
            out.append(dict(meta, body=body))
        return out

    @route("GET", "/api/notifications")
    def notifications(req, q):
        return app.bus.notifications()

    return routes


class Handler(BaseHTTPRequestHandler):
    app = None
    routes = []
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # 不往终端刷访问日志
        pass

    def json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            raise ApiError(400, "请求体不是合法 JSON")

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else \
            json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self, method):
        u = urlparse(self.path)
        if method == "GET" and u.path == "/api/events":
            return self._sse()
        if method == "GET" and not u.path.startswith("/api/"):
            return self._static(u.path)
        for m, pat, fn in self.routes:
            mt = pat.match(u.path)
            if m == method and mt:
                try:
                    return self._send(200, fn(self, parse_qs(u.query), **mt.groupdict()))
                except ApiError as e:
                    return self._send(e.status, {"error": str(e)})
                except (ValueError, KeyError) as e:
                    return self._send(400, {"error": str(e).strip("'\"")})
                except Exception:
                    return self._send(500, {"error": traceback.format_exc()[-1500:]})
        self._send(404, {"error": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        p = (STATIC / rel).resolve()
        if STATIC not in p.parents or not p.is_file():
            return self._send(404, b"not found", "text/plain")
        self._send(200, p.read_bytes(), MIME.get(p.suffix, "application/octet-stream"))

    def _sse(self):
        bus = self.app.bus
        q = bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    data = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(q)
            self.close_connection = True


class App:
    def __init__(self, cfg, store, bus, daemon):
        self.cfg, self.store, self.bus, self.daemon = cfg, store, bus, daemon

    def serve(self):
        handler = type("BoundHandler", (Handler,), {"app": self, "routes": make_handler(self)})
        httpd = ThreadingHTTPServer((self.cfg.host, self.cfg.port), handler)
        httpd.daemon_threads = True
        return httpd
