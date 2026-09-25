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

from . import (bootstrap, candidates, discussion, evolution, frontmatter, insights, metrics, objects, observe,
               papers, questions, reviews, schema)
from .library import Library

STATIC = Path(__file__).resolve().parent / "static"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


class ApiError(Exception):
    def __init__(self, status, msg, **extra):
        super().__init__(msg)
        self.status = status
        self.extra = extra


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
        idx = objects.Index(st)

        def one(m, b):
            # counts：紧凑行上的各类关联计数；withdrawn：折进 Withdrawn 区（§5.15.4 / §5.15.6）
            return dict(m, body=b.strip(), provenance=prov.get(m.get("id")),
                        counts=idx.counts(m.get("id")), withdrawn=schema.is_withdrawn(m))

        def objs(t):
            return [one(m, b) for m, b in st.list(t)]
        errs, warns = schema.validate_repo(st.state)
        tidy = [t for t in d.ledger.all() if t["kind"] == "tidy"]
        return {"project": dict(pmeta, body=pbody.strip()),
                "tree": questions.tree(st, idx),
                "tidy": ({k: tidy[-1].get(k) for k in ("id", "status", "result_brief", "ended", "created")}
                         if tidy else None),
                "questions": objs("question"), "insights": objs("insight"),
                "assumptions": objs("assumption"),
                "hypotheses": objs("hypothesis"), "uncertainties": objs("uncertainty"),
                "dead_ends": objs("dead-end"), "evidence": objs("evidence"),
                "papers": objs("paper"), "groundings": objs("grounding"),
                "mode": d.mode_view(),
                "bias": metrics.bias_by_origin(st),
                "reviews": reviews.list_all(st, "open"),
                "validation": {"errors": errs, "warnings": warns},
                "setup_needed": bootstrap.needs_setup(st),
                "head": st.head()}

    @route("POST", "/api/project/setup")
    def setup_project(req, q):
        """研究者建立项目（DESIGN.md §5「新项目的建立」）。已有项目时拒绝。"""
        b = req.json()
        bootstrap.setup(st, b.get("title"), b.get("description"), b.get("question"),
                        b.get("maturity") or "vague")
        app.bus.publish("state", {"project": "setup"})
        return {"ok": True}

    @route("GET", "/api/objects/(?P<id>[A-Z]+\\d+)")
    def get_object(req, q, id):
        meta, body = st.read_obj(id)
        if meta is None:
            raise ApiError(404, f"{id} 不存在")
        return {"meta": meta, "body": body, "provenance": st.provenance().get(id)}

    # ------------------------------------------------------------ 对象详情与打磨（§5.15）

    @route("GET", "/api/objects/(?P<id>[A-Z]+\\d+)/links")
    def object_links(req, q, id):
        """对象详情页：当前表述、修订史、讨论、证据、关联、Review、相关候选、能否撤回 / 撤下。"""
        try:
            return objects.links(st, id)
        except KeyError:
            raise ApiError(404, f"{id} 不存在")

    @route("POST", "/api/objects/(?P<id>[A-Z]+\\d+)/withdraw")
    def withdraw(req, q, id):
        did = objects.withdraw(st, id, req.json().get("reason", ""))
        app.bus.publish("state", {"withdrawn": id})
        return {"decision": did}

    @route("POST", "/api/objects/(?P<id>[A-Z]+\\d+)/restore")
    def restore(req, q, id):
        did = objects.restore(st, id, req.json().get("reason", ""))
        app.bus.publish("state", {"restored": id})
        return {"decision": did}

    @route("POST", "/api/objects/(?P<id>[A-Z]+\\d+)/undo-accept")
    def undo_accept(req, q, id):
        did, cid = objects.undo_accept(st, id, req.json().get("reason", ""))
        app.bus.publish("candidates", {"undone": id, "candidate": cid})
        return {"decision": did, "candidate": cid}
    # ------------------------------------------------------------ 让研究收敛（§5.16）

    @route("POST", "/api/questions/(?P<qid>Q\\d+)/resolve")
    def resolve_question(req, q, qid):
        """人直接结一个问题（不经候选）：answered / decided / merged。"""
        b = req.json()
        did, research = questions.resolve(st, qid, b.get("resolution"), reason=b.get("reason", ""),
                                          answered_by=b.get("answered_by"), merged_into=b.get("merged_into"),
                                          decision=b.get("decision"))
        app.bus.publish("state", {"question": qid})
        return {"decision": did, "research": research}

    @route("POST", "/api/questions/(?P<qid>Q\\d+)/reopen")
    def reopen_question(req, q, qid):
        did = questions.reopen(st, qid, req.json().get("reason", ""))
        app.bus.publish("state", {"question": qid})
        return {"decision": did}

    @route("POST", "/api/questions/(?P<qid>Q\\d+)/active")
    def set_active(req, q, qid):
        changed = questions.set_active(st, qid, req.json().get("active"))
        app.bus.publish("state", {"question": qid})
        return {"changed": changed}

    @route("POST", "/api/questions/(?P<qid>Q\\d+)/parent")
    def set_parent(req, q, qid):
        changed = questions.set_parent(st, qid, req.json().get("parent"))
        app.bus.publish("state", {"question": qid})
        return {"changed": changed}

    @route("GET", "/api/questions/tree")
    def question_tree(req, q):
        return questions.tree(st)

    @route("POST", "/api/tidy")
    def tidy(req, q):
        t = d.request_tidy()
        return {"task": t, "note": None if t else "已有一个整理任务在排队或在跑"}

    # ------------------------------------------------------------ 讨论

    @route("GET", "/api/discussions")
    def list_discussions(req, q):
        return discussion.list_all(st)

    @route("POST", "/api/discussions")
    def new_discussion(req, q):
        if bootstrap.needs_setup(st):
            raise ApiError(409, "还没有项目：先建立项目与主问题")
        b = req.json()
        ds = discussion.create(st, (b.get("title") or "").strip(), focus=b.get("focus"))
        app.bus.publish("state", {"discussion": ds})
        return {"id": ds}

    @route("POST", "/api/discussions/(?P<ds>DS\\d+)/meta")
    def discussion_meta(req, q, ds):
        """改名 / 分组 / 置顶（§5.15.5）。discussions/ 是受保护路径，只经这里写。"""
        b = req.json()
        try:
            discussion.set_meta(st, ds, title=b.get("title"), group=b.get("group"), pinned=b.get("pinned"))
        except KeyError:
            raise ApiError(404, f"{ds} 不存在")
        app.bus.publish("state", {"discussion": ds})
        return {"ok": True}

    @route("POST", "/api/discussion-groups/rename")
    def rename_group(req, q):
        b = req.json()
        hit = discussion.rename_group(st, b.get("from", ""), b.get("to", ""))
        app.bus.publish("state", {"discussions": hit})
        return {"discussions": hit}

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
        try:
            new = candidates.accept(st, cid, origin=body.get("origin"), changes=body.get("changes"),
                                    force=bool(body.get("force")), maturity=body.get("maturity"),
                                    decision=body.get("decision"))
        except questions.PendingRefs as e:
            # resolve 候选引用的同批候选还没确认：前端给“一并确认”（§5.16.1）
            raise ApiError(409, str(e), pending_refs=e.refs)
        except objects.StaleRevision as e:
            # 基于旧版本起草：带回当前版本，前端对照后可显式 force（§5.15.3）
            raise ApiError(409, str(e), current=e.current, stale=True)
        app.bus.publish("candidates", {"accepted": cid, "as": new})
        return {"id": new}

    @route("GET", "/api/candidates/(?P<cid>C\\d+)/revision")
    def revision_preview(req, q, cid):
        return objects.preview(st, cid)

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

    @route("POST", "/api/insights/(?P<iid>IN\\d+)/star")
    def star_insight(req, q, iid):
        """星标（§5.17.2）：星标的理解以全文进 briefing，其余一行。"""
        changed = insights.set_starred(st, iid, req.json().get("starred"))
        app.bus.publish("state", {"insight": iid})
        return {"changed": changed}

    @route("POST", "/api/insights/merge")
    def merge_insights(req, q):
        """研究者手动合并（§5.17.3）：自己写合并后的表述，不经候选。"""
        b = req.json()
        new = insights.consolidate(st, b.get("supersedes"), b.get("statement", ""), b.get("firmness", ""),
                                   change_mind=b.get("change_mind", ""), note=b.get("note", ""),
                                   origin="human", source="direct")
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

    # ------------------------------------------------------------ 模式与交棒（§5.7）

    @route("GET", "/api/mode")
    def get_mode(req, q):
        return d.mode_view()

    @route("POST", "/api/mode/handoff")
    def handoff(req, q):
        b = req.json()
        did, batch = d.handoff(b.get("items") or [], b.get("note", ""))
        app.bus.publish("state", {"mode": "validation"})
        return {"decision": did, "batch": batch}

    @route("POST", "/api/mode/recall")
    def recall(req, q):
        did = d.recall(req.json().get("reason", ""))
        app.bus.publish("state", {"mode": "discussion"})
        return {"decision": did}

    # ------------------------------------------------------------ 自演进（M11 v1.8，§5.18）

    @route("GET", "/api/evolutions")
    def evolutions(req, q):
        """全部演进文档（最新在前）+ 在排队或在跑的演进任务 + 系统现在会挑哪个出发点。"""
        docs = [evolution.as_dict(m, b) for m, b in evolution.docs(st)]
        docs.reverse()
        live = [{k: t.get(k) for k in ("id", "status", "seed", "trigger", "why", "created", "started")}
                for t in d.ledger.all() if t["kind"] == "evolve" and t["status"] in ("queued", "running")]
        seed, why = evolution.pick_seed(st)
        return {"docs": docs, "live": live, "next": {"seed": seed, "why": why}}

    @route("POST", "/api/evolve")
    def evolve(req, q):
        """手动触发（§5.18.2）：seed 为空时由系统按“越 vague 越优先”挑。"""
        t = d.request_evolve((req.json().get("seed") or "").strip() or None)
        return {"task": t, "note": None if t else "同一出发点已有一篇在排队或在跑"}

    @route("POST", "/api/questions/(?P<qid>Q\\d+)/maturity")
    def set_maturity(req, q, qid):
        """人把问题推到 formalized（或退回）。成熟度只由人改——自演进的入场闸门看它（§5.13）。"""
        mat = req.json().get("maturity")
        if mat not in schema.KINDS["question"].enums["maturity"]:
            raise ValueError("maturity 必须是 vague / scoped / formalized")
        meta, body = st.read_obj(qid)
        if meta is None:
            raise ApiError(404, f"{qid} 不存在")
        old = meta.get("maturity")
        meta["maturity"] = mat
        with st.tx(f"question {qid}: 成熟度 {old} → {mat}", actor="human") as tx:
            tx.write_obj(qid, meta, body)
        app.bus.publish("state", {"question": qid})
        return {"ok": True, "from": old, "to": mat}

    @route("POST", "/api/mode/continue")
    def cont(req, q):
        return {"decision": d.continue_validation(req.json().get("reason", ""))}

    @route("POST", "/api/prep-request")
    def prep_request(req, q):
        d.set_prep_request(req.json().get("text", ""), "Chat 页")
        return d.mode_view()

    @route("GET", "/api/prep")
    def prep(req, q):
        v = d.mode_view().get("prep") or {}
        did = v.get("decision")
        if not did:
            return {"prep": v}
        meta, body = st.read_obj(did)
        tasks = [t for t in d.ledger.all() if t.get("prep") == did]
        return {"prep": v, "decision": dict(meta or {}, body=body),
                "tasks": [{k: t.get(k) for k in ("id", "kind", "status", "goal", "result_brief", "paper")}
                          for t in tasks]}

    # ------------------------------------------------------------ 文献与观察（M4 / M8）

    @route("GET", "/api/reading")
    def reading(req, q):
        return observe.reading(st, d.ledger)

    @route("GET", "/api/chain/(?P<tid>[HA]\\d+)")
    def chain(req, q, tid):
        return observe.chain(st, d.ledger, app.cfg, tid)

    @route("GET", "/api/papers/(?P<pid>P\\d+)")
    def get_paper(req, q, pid):
        return observe.paper(st, d.ledger, app.cfg, pid)

    @route("GET", "/api/papers/(?P<pid>P\\d+)/fulltext")
    def fulltext(req, q, pid):
        p = Library(app.cfg.library).fulltext_path(pid)
        if not p.exists():
            raise ApiError(404, f"{pid} 没有全文")
        return {"text": p.read_text(encoding="utf-8")}

    @route("GET", "/api/requests")
    def list_requests(req, q):
        return observe.requests(st, d.ledger)

    @route("POST", "/api/requests/(?P<rid>RQ\\d+)/upload")
    def upload(req, q, rid):
        raw = req.raw(limit=100 * 1024 * 1024)
        task, info = papers.fulfill_request(st, Library(app.cfg.library), rid, raw)
        n = d.unblock(rid)
        app.bus.publish("state", {"request": rid})
        app.bus.notify("info", f"{rid}: PDF received ({info['anchors']} paragraphs indexed)"
                       + (f"; resumed {n} suspended task(s)." if n else "."))
        return {"ok": True, "anchors": info["anchors"], "resumed": n}

    @route("POST", "/api/requests/(?P<rid>RQ\\d+)/dismiss")
    def dismiss(req, q, rid):
        reason = req.json().get("reason", "")
        papers.dismiss_request(st, rid, reason)
        n = d.unblock(rid, note=f"全文拿不到（研究者：{reason.strip()}）。只能按摘要级处理，"
                             "或说明没有全文无法判断后结束。")
        app.bus.publish("state", {"request": rid})
        return {"ok": True, "resumed": n}

    @route("POST", "/api/grounding")
    def grounding(req, q):
        return {"task": d.request_grounding((req.json().get("target") or "").strip())}

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

    def raw(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            raise ApiError(400, "请求体为空")
        if n > limit:
            raise ApiError(413, f"文件太大（>{limit // 1024 // 1024}MB）")
        return self.rfile.read(n)

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
                    return self._send(e.status, {"error": str(e), **e.extra})
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
