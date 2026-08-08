"""HTTP protocol plumbing for server.py — kept out of server.py itself so
that file stays a short, readable startup script rather than a router/HTTP
implementation (agents/AGENT_0_FOUNDATION.md: "server.py under ~120 lines").

Nothing here is route-specific; it has no opinion about what any endpoint
does, only how a (method, path) is matched against a ROUTES table, how a raw
request becomes the `Req` a handler expects, and how its result becomes an
HTTP response.
"""
import json
import os
import re
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

from core.api import err, ApiError
from core.context import ctx

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".json": "application/json"}


class Req:
    """What a route handler receives. `body` is the parsed JSON object for a
    plain POST; `fields`/`files` are populated instead for multipart form
    uploads. `params` holds any <name> segments captured from the path."""

    __slots__ = ("method", "path", "query", "params", "body", "fields", "files", "user", "headers")

    def __init__(self, method, path, query, params, body, fields, files, user, headers):
        self.method = method
        self.path = path
        self.query = query
        self.params = params
        self.body = body
        self.fields = fields
        self.files = files
        self.user = user
        self.headers = headers


def parse_multipart(body, ctype):
    """Minimal multipart/form-data reader -> (fields, files)."""
    m = re.search(r'boundary="?([^";]+)"?', ctype)
    if not m:
        return {}, {}
    b = ("--" + m.group(1)).encode()
    fields, files = {}, {}
    for part in body.split(b):
        if not part.strip(b"-\r\n"):
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        data = data.rstrip(b"\r\n")
        h = head.decode("utf-8", "ignore")
        nm = re.search(r'name="([^"]*)"', h)
        fn = re.search(r'filename="([^"]*)"', h)
        if not nm:
            continue
        if fn and fn.group(1):
            files[nm.group(1)] = (fn.group(1), data)
        else:
            fields[nm.group(1)] = data.decode("utf-8", "ignore")
    return fields, files


def _compile(pattern):
    """"/api/group/<id>" -> a regex with a named group per <segment>."""
    if "<" not in pattern:
        return None
    parts = []
    for seg in pattern.split("/"):
        if seg.startswith("<") and seg.endswith(">"):
            parts.append(f"(?P<{seg[1:-1]}>[^/]+)")
        else:
            parts.append(re.escape(seg))
    return re.compile("^" + "/".join(parts) + "$")


class Router:
    """Concatenates each route module's ROUTES list and matches
    method + path against it, first match wins - so a module that lists a
    static path ("/api/group/move") ahead of a parametrized one
    ("/api/group/<id>") never has it swallowed by the wildcard."""

    def __init__(self, modules):
        table = []
        for mod in modules:
            table.extend(getattr(mod, "ROUTES", []))
        self._compiled = [(method, path, _compile(path), handler) for method, path, handler in table]

    def match(self, method, path):
        for m, pattern, rx, handler in self._compiled:
            if m != method:
                continue
            if rx is None:
                if pattern == path:
                    return handler, {}
            else:
                mo = rx.match(path)
                if mo:
                    return handler, mo.groupdict()
        return None, None


def parse_json_body(raw):
    """Raises json.JSONDecodeError on malformed input; caller decides how to
    report it."""
    return json.loads(raw) if raw else {}


def make_handler(web_dir, router):
    """Build the BaseHTTPRequestHandler server.py hands to
    ThreadingHTTPServer. `web_dir` is where static files are served from;
    `router` is a Router already built from every route module's ROUTES."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ItemCodeStudio"

        def log_message(self, fmt, *args):
            if "/api/" in (args[0] if args else ""):
                import sys
                sys.stderr.write("  %s\n" % (fmt % args))

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra_headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, default=str).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path):
            rel = path.lstrip("/") or "index.html"
            full = os.path.normpath(os.path.join(web_dir, rel))
            if not full.startswith(web_dir) or not os.path.isfile(full):
                return self._send(404, err("NOT_FOUND", "not found"))
            with open(full, "rb") as f:
                self._send(200, f.read(), MIME.get(os.path.splitext(full)[1], "application/octet-stream"))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _dispatch(self, method):
            u = urlparse(self.path)
            path = unquote(u.path)
            query = {k: v[0] for k, v in parse_qs(u.query).items()}

            if method == "GET" and not path.startswith("/api/"):
                return self._static(path)

            handler, params = router.match(method, path)
            if handler is None:
                return self._send(404, err("NOT_FOUND", "unknown endpoint"))

            fields, files, body = {}, {}, {}
            if method == "POST":
                ctype = self.headers.get("Content-Type", "")
                raw = self._body()
                if ctype.startswith("multipart/form-data"):
                    fields, files = parse_multipart(raw, ctype)
                elif raw:
                    try:
                        body = parse_json_body(raw)
                    except json.JSONDecodeError:
                        return self._send(400, err("VALIDATION", "malformed JSON body"))

            # req.user is resolved from the session cookie (core.auth), never
            # from a client-supplied header - see agents/done/AGENT_B.md for
            # why this line changed from the old forgeable "X-User" header.
            req = Req(method, path, query, params, body, fields, files, None, self.headers)

            try:
                with ctx.lock:
                    from core.auth import current_user               # lazy: avoids any import-order coupling at module load
                    try:
                        req.user = current_user(req) or "unknown"
                    except Exception:                                 # noqa: BLE001
                        req.user = "unknown"
                    try:
                        result = handler(req)
                    finally:
                        if ctx.con.in_transaction:
                            ctx.con.rollback()
            except ApiError as e:
                return self._send(e.status, err(e.code, e.message, e.detail))
            except Exception as e:                                # noqa: BLE001
                traceback.print_exc()
                return self._send(500, err("INTERNAL", f"{e.__class__.__name__}: {e}"))

            if isinstance(result, tuple):
                return self._send(*result)
            return self._send(200, result)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return Handler
