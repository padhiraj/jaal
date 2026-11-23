"""Zero-dependency HTTP API + static UI server for JAAL."""
from __future__ import annotations

import json
import mimetypes
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .audit import ROLE_DESC, ROLES, USERS, AuditLog, mask
from .engine import Engine

WEB = Path(__file__).resolve().parent.parent / "web"


class App:
    def __init__(self, data_dir, state_dir):
        self.engine = Engine(data_dir, state_dir)
        self.audit = AuditLog(Path(state_dir) / "audit.jsonl")
        self.lock = threading.RLock()
        self.audit.append({"user": "system", "role": "system"}, "engine.build", "",
                           detail={"records": len(self.engine.ds.persons), "ms": self.engine.build_ms,
                                   "sources": {d["id"]: d["sha256"][:16] for d in self.engine.ds.docs.values()}})


def make_handler(app: App):
    E = lambda: app.engine

    class H(BaseHTTPRequestHandler):
        server_version = "JAAL/1.0"

        def log_message(self, fmt, *args):  # quiet console
            pass

        # ---------------------------------------------------------------- utils
        def _user(self):
            tok = self.headers.get("X-JAAL-Token") or parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return USERS.get(tok)

        def _send(self, code, obj, ctype="application/json"):
            body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _deny(self, user, perm, target):
            app.audit.append(user or {}, perm, target, outcome="denied")
            self._send(403 if user else 401, {"error": "forbidden" if user else "login required",
                                              "needed": perm, "role": (user or {}).get("role")})

        def _ok(self, user, perm, target, obj, detail=None):
            app.audit.append(user, perm, target, detail=detail)
            if user["role"] == "viewer":
                obj = mask(obj)
            self._send(200, obj)

        # ---------------------------------------------------------------- GET
        def do_GET(self):
            u = urlparse(self.path)
            path, q = u.path, {k: v[0] for k, v in parse_qs(u.query).items()}
            if not path.startswith("/api/"):
                return self._static(path)
            try:
                return self._api_get(path, q)
            except Exception as ex:  # noqa
                traceback.print_exc()
                self._send(500, {"error": str(ex)})

        def _static(self, path):
            if path in ("", "/"):
                path = "/index.html"
            f = (WEB / path.lstrip("/")).resolve()
            if not str(f).startswith(str(WEB)) or not f.is_file():
                return self._send(404, {"error": "not found"})
            self._send(200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "application/octet-stream")

        def _api_get(self, path, q):
            if path == "/api/users":
                return self._send(200, [{"token": t, **u, "desc": ROLE_DESC[u["role"]], "perms": sorted(ROLES[u["role"]])}
                                        for t, u in USERS.items()])
            user = self._user()
            route = {
                "/api/summary": "summary", "/api/graph": "graph", "/api/kg": "graph", "/api/targets": "targets",
                "/api/alerts": "alerts", "/api/cases": "cases", "/api/path": "path", "/api/links": "links",
                "/api/er/review": "er_read", "/api/audit": "audit", "/api/audit/verify": "audit",
                "/api/evaluate": "evaluate", "/api/search": "search",
            }
            perm = route.get(path)
            if path.startswith("/api/entity/"):
                perm = "entity"
            elif path.startswith("/api/timeline/"):
                perm = "timeline"
            elif path.startswith("/api/doc/"):
                perm = "doc"
            if not perm:
                return self._send(404, {"error": "unknown endpoint"})
            if not user or perm not in ROLES[user["role"]]:
                return self._deny(user, perm, path)
            e = E()
            with app.lock:
                if path == "/api/summary":
                    s = e.summary()
                    if user["role"] == "auditor":
                        s = {k: s[k] for k in ("built_at", "manifest", "records", "kg_nodes", "kg_edges")}
                    return self._ok(user, "summary", "", s)
                if path == "/api/graph":
                    return self._ok(user, "graph.person", "", e.person_graph())
                if path == "/api/kg":
                    return self._ok(user, "graph.kg", q.get("focus", ""), e.kg(q.get("focus") or None, int(q.get("depth", 2))))
                if path == "/api/targets":
                    return self._ok(user, "targets", "", e.targets(int(q.get("n", 25))))
                if path == "/api/alerts":
                    return self._ok(user, "alerts", "", e.alerts)
                if path == "/api/cases":
                    cases = [{k: c[k] for k in ("case_no", "station", "state", "date", "sections", "mo", "doc")} |
                             {"people": [{"id": p, "label": e.nodes[p]["label"], "why": w} for p, w in e.case_sets[c["case_no"]].items()]}
                             for c in e.ds.cases.values()]
                    return self._ok(user, "cases", "", {"cases": cases, "links": e.case_links})
                if path == "/api/links":
                    return self._ok(user, "links", "", {"predicted": e.predicted, "inferences": e.inferences,
                                                        "key_players": e.key_players})
                if path == "/api/er/review":
                    return self._ok(user, "er.review", "", {"queue": e.review, "merges": e.merges[:400],
                                                            "decisions": e.decisions})
                if path == "/api/path":
                    r = e.path(q.get("a"), q.get("b"))
                    return self._ok(user, "path", f"{q.get('a')} -> {q.get('b')}", r or {"path": [], "hops": []})
                if path == "/api/search":
                    return self._ok(user, "search", q.get("q", ""), e.search(q.get("q", "")))
                if path == "/api/evaluate":
                    return self._ok(user, "evaluate", "", e.evaluate())
                if path == "/api/audit":
                    return self._ok(user, "audit.read", "", app.audit.entries(int(q.get("n", 200))))
                if path == "/api/audit/verify":
                    return self._ok(user, "audit.verify", "", app.audit.verify())
                if path.startswith("/api/entity/"):
                    pid = unquote(path[len("/api/entity/"):])
                    d = e.dossier(pid)
                    return self._ok(user, "entity.read", pid, d) if d else self._send(404, {"error": "no such entity"})
                if path.startswith("/api/timeline/"):
                    pid = unquote(path[len("/api/timeline/"):])
                    return self._ok(user, "timeline", pid, e.timeline(pid))
                if path.startswith("/api/doc/"):
                    did = unquote(path[len("/api/doc/"):])
                    d = e.document(did)
                    return self._ok(user, "doc.read", did + (f" {q['loc']}" if q.get("loc") else ""), d) if d \
                        else self._send(404, {"error": "no such document"})

        # ---------------------------------------------------------------- POST
        def do_POST(self):
            u = urlparse(self.path)
            user = self._user()
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON"})
            try:
                if u.path == "/api/ingest/fir":
                    if not user or "ingest" not in ROLES[user["role"]]:
                        return self._deny(user, "ingest", u.path)
                    with app.lock:
                        r = E().ingest_fir(body.get("text", ""))
                    app.audit.append(user, "ingest.fir", r["case"], detail={"file": r["file"], "links": len(r["links"])})
                    return self._send(200, r)
                if u.path == "/api/er/decide":
                    if not user or "er_decide" not in ROLES[user["role"]]:
                        return self._deny(user, "er_decide", u.path)
                    with app.lock:
                        E().decide(body["key"], body["decision"])
                    app.audit.append(user, "er.decide", body["key"], detail={"decision": body["decision"]})
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "unknown endpoint"})
            except ValueError as ex:
                return self._send(400, {"error": str(ex)})
            except Exception as ex:  # noqa
                traceback.print_exc()
                return self._send(500, {"error": str(ex)})

    return H


def serve(data_dir, state_dir, host="127.0.0.1", port=8765):
    app = App(data_dir, state_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"JAAL running on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
