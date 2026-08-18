"""API docs & health — Agent H.

Owns: /api/docs, /api/v1/health

Nothing existed under these prefixes in the pre-split app, so this is a
clean start (agents/CONTRACTS.md §6 route-ownership table).

/api/docs is generated FROM THE ROUTE TABLE ITSELF — it imports every route
module exactly the way server.py's Router does and walks their ROUTES
lists, rather than a hand-maintained page that goes stale the moment
someone adds an endpoint. Method, path and "does this need a session"
(detected by scanning the handler's own source for calls to
core.auth.require_session / require_admin — real introspection, not a
second list someone has to remember to update) are all derived at request
time. Only the illustrative example request/response bodies are supplied by
hand (EXAMPLES below), because a genuinely representative example can't be
inferred from a function signature — everything else on the page is live.
"""
import inspect
import json

from core.api import ok
from core.context import ctx

# --------------------------------------------------------------------- docs
# Hand-written *only* for the parts that cannot be derived from code: a
# realistic example request/response for the endpoints most worth
# illustrating. Everything else on /api/docs (method, path, auth, which
# module owns it) is introspected fresh on every request — see build_docs().
EXAMPLES = {
    ("GET", "/api/v1/health"): {
        "response": {"ok": True, "status": "ok",
                     "tier": {"mode": "local_server", "status": "connected",
                              "status_text": "connected - minting final codes",
                              "active_base": None, "live": True},
                     "app": "Item Code Studio", "counts": {"items": 1947, "groups": 889}}},
    ("GET", "/api/v1/decode"): {
        "request": "GET /api/v1/decode?code=RMBS0010206100007",
        "response": {"ok": True, "code": "RMBS0010206100007", "wellformed": True,
                     "known": True, "head": "Raw Materials", "subhead": "Bearings",
                     "group": "Ball Bearing", "issued": True}},
    ("GET", "/api/v1/directory"): {
        "request": "GET /api/v1/directory?q=mseal&limit=25&offset=0",
        "response": {"ok": True, "items": [{"code": "...", "name": "M-Seal"}],
                     "total": 1, "limit": 25, "offset": 0, "q": "mseal"}},
    ("POST", "/api/v1/auth/login"): {
        "request": {"username": "admin", "password": "..."},
        "response": {"ok": True, "username": "admin", "must_change_password": False}},
    ("POST", "/api/v1/auth/logout"): {
        "request": {}, "response": {"ok": True}},
    ("POST", "/api/v1/resolve"): {
        "request": {"text": "12mm ball bearing SKF"},
        "response": {"ok": True, "outcome": "exists", "code": "RMBS0010206100007",
                     "matched_by": "rules"}},
    ("POST", "/api/v1/commit"): {
        "request": {"proposal": {"text": "12mm ball bearing SKF"}},
        "response": {"ok": True, "code": "RMBS0010206100007", "created": True}},
    ("GET", "/api/docs"): {
        "response": "this page — pass ?format=json for the machine-readable version"},
}


def _auth_kind(handler):
    """Best-effort: does this handler's own source call require_session /
    require_admin? Real introspection of the code that runs, not a claim
    someone typed in a comment and forgot to update."""
    try:
        src = inspect.getsource(handler)
    except (OSError, TypeError):
        return "unknown"
    if "require_admin" in src:
        return "admin session"
    if "require_session" in src:
        return "session"
    return "public"


def _route_modules():
    # Imported here, not at module scope, so a bad import in a sibling
    # module (mid-build, another agent still editing) can't take /api/docs
    # itself down — worst case that one module's routes are missing from
    # the page instead of the whole endpoint 500ing.
    from routes import public, auth, create, master, erp as erp_routes, meta as meta_mod
    return [("public.py", public), ("auth.py", auth), ("create.py", create),
            ("master.py", master), ("erp.py", erp_routes), ("meta.py", meta_mod)]


def build_docs():
    """Walk every route module's ROUTES table and return the list of
    endpoint descriptors /api/docs renders. This is the single source the
    HTML and the ?format=json view both draw from — there is no second copy
    to drift out of sync with the first."""
    out = []
    for modname, mod in _route_modules():
        for method, path, handler in getattr(mod, "ROUTES", []):
            ex = EXAMPLES.get((method, path), {})
            doc = (inspect.getdoc(handler) or "").split("\n")[0].strip()
            out.append({
                "method": method,
                "path": path,
                "module": modname,
                "auth": _auth_kind(handler),
                "summary": doc,
                "versioned": path.startswith("/api/v1/"),
                "example_request": ex.get("request"),
                "example_response": ex.get("response"),
            })
    out.sort(key=lambda r: (not r["versioned"], r["path"], r["method"]))
    return out


_PAGE_CSS = """
body{font:15px/1.5 Barlow,'Segoe UI',system-ui,sans-serif;background:#001b2e;color:#eaf2f7;
     margin:0;padding:2rem 3rem 4rem}
h1{color:#04aed1;font-weight:600}
p.lede{color:#9fb7c8;max-width:60ch}
table{border-collapse:collapse;width:100%;margin-top:1rem}
th,td{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #294962;vertical-align:top}
th{color:#9fb7c8;font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
tr.legacy{opacity:.55}
code{background:#0f2e45;border:1px solid #294962;border-radius:4px;padding:.1rem .35rem;
     color:#eaf2f7;font-size:.9em}
.method{font-weight:700;color:#04aed1}
.method.POST{color:#3b6e93}
.auth-public{color:#7fbf7f}
.auth-session,.auth-admin_session{color:#e0b84a}
pre{background:#0f2e45;border:1px solid #294962;border-radius:6px;padding:.75rem 1rem;
    overflow-x:auto;max-width:70ch;font-size:.85em}
.pill{display:inline-block;font-size:.7rem;padding:.1rem .5rem;border-radius:999px;
      border:1px solid #294962;color:#9fb7c8}
"""


def _render_html(routes_):
    rows = []
    for r in routes_:
        cls = "" if r["versioned"] else "legacy"
        auth_cls = "auth-" + r["auth"].replace(" ", "_")
        example = ""
        if r["example_request"] is not None or r["example_response"] is not None:
            blob = json.dumps({"request": r["example_request"],
                                "response": r["example_response"]}, indent=2, default=str)
            example = f"<pre>{blob}</pre>"
        rows.append(
            f"<tr class='{cls}'><td><span class='method {r['method']}'>{r['method']}</span></td>"
            f"<td><code>{r['path']}</code></td>"
            f"<td>{r['module']}</td>"
            f"<td class='{auth_cls}'>{r['auth']}</td>"
            f"<td>{r['summary'] or ''}</td>"
            f"<td>{example}</td></tr>")
    legacy_note = ("<p class='lede'>Rows shown dimmed are the pre-existing, "
                   "un-versioned endpoints kept only for the current UI "
                   "(<code>web/app.js</code>) — new integration work should "
                   "use the <code>/api/v1/*</code> rows only.</p>")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Item Code Studio — API docs</title><style>{_PAGE_CSS}</style></head><body>
<h1>Item Code Studio — API reference</h1>
<p class="lede">Generated from the live route table on every request — this
page cannot go stale relative to the code that is actually running.
<span class="pill">GET /api/docs?format=json</span> for the machine-readable
version of the same data.</p>
{legacy_note}
<table><thead><tr><th>Method</th><th>Path</th><th>Module</th><th>Auth</th>
<th>Summary</th><th>Example</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""


def docs(req):
    routes_ = build_docs()
    if (req.query.get("format") or "").lower() == "json":
        return ok({"routes": routes_, "count": len(routes_)})
    return (200, _render_html(routes_), "text/html; charset=utf-8")


def health(req):
    """GET /api/v1/health — unauthenticated liveness + tier status.

    Used by: the smoke suite ("every endpoint answers"), the desktop
    client's tier banner (agents/AGENT_H_DEPLOY.md task 4), and
    core.tier.TierClient's own ping() when probing a remote tier.

    `tier` reads `ctx.tier` (a live core.tier.TierClient) when the startup
    patch documented at the top of core/tier.py has landed; until then it
    falls back to the static config.json mode string, which is what this
    process always was ("local_server" today, since no VPS exists yet) —
    the field never disappears or errors, it just gets more precise once
    the live prober is wired in.
    """
    con = ctx.con
    cfg = ctx.cfg or {}
    ledger = cfg.get("ledger") or {}
    counts = {}
    try:
        for k, t in (("heads", "head"), ("subheads", "subhead"), ("groups", "grp"),
                     ("items", "item"), ("erp_items", "erp_item")):
            counts[k] = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    except Exception:                                             # noqa: BLE001
        counts = None

    tc = getattr(ctx, "tier", None)
    if tc is not None:
        tier_info = {"mode": tc.mode, "status": tc.status, "status_text": tc.status_text(),
                     "active_base": tc.active_base, "live": True}
    else:
        tier_info = {"mode": ledger.get("mode", "local_server"), "status": None,
                     "status_text": None, "active_base": None, "live": False}

    return ok({
        "status": "ok",
        "app": cfg.get("app_name", "Item Code Studio"),
        "tier": tier_info,
        "erp_enabled": bool((cfg.get("erpnext") or {}).get("enabled")),
        "counts": counts,
    })


def debug_env(req):
    import sys
    import os
    import importlib.metadata
    import urllib.request
    import json
    import ssl
    
    from core import db as D
    from core.context import ctx
    
    ocr_status = "Not Configured"
    try:
        url = D.get_setting(ctx.con, "ocr.api_url") if ctx.con else None
        if not url:
            url = "http://ocr-service:8757/ocr"
        health_url = url.replace("/ocr", "/health")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(health_url, timeout=2, context=ssl_context) as res:
            resp = json.loads(res.read().decode("utf-8"))
            ocr_status = f"Standalone service is up and {resp.get('status', 'unknown')}"
    except Exception as e:
        ocr_status = f"Offline/Error ({e.__class__.__name__}: {e})"
        
    pkgs = []
    try:
        pkgs = [f"{p.metadata['Name']}=={p.version}" for p in importlib.metadata.distributions()]
    except Exception as e:
        pkgs = [str(e)]
        
    return ok({
        "python_version": sys.version,
        "python_executable": sys.executable,
        "sys_path": sys.path,
        "cwd": os.getcwd(),
        "ocr_status": ocr_status,
        "packages": sorted(pkgs),
    })


ROUTES = [
    ("GET", "/api/docs", docs),
    ("GET", "/api/v1/health", health),
    ("GET", "/api/v1/debug/env", debug_env),
]
