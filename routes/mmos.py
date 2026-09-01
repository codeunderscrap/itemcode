"""MM OS single sign-on acceptance path — additive, opt-in (MMOS-RETROFIT.md).

Item Code Studio keeps its own username/password login (core/auth.py,
routes/auth.py) completely unchanged; this module only ADDS a second way to
obtain the same kind of session, gated end-to-end by `core.context.ctx.mmos`
being configured (server.py wires it from config.json's "mmos" block plus the
MMOS_SERVICE_KEY environment variable — see MMOS-RETROFIT.md).

Session machinery is reused, never reinvented (retrofit brief: "reuse
core/auth.py's session machinery — do not invent a parallel session
system"):

    A.create_session(con, username)  -> the exact same `session` table row,
                                         same 12-hour TTL, as a password login
    A.cookie_header(token)           -> the exact same Set-Cookie shape,
                                         same HttpOnly/SameSite/Secure rules

Once minted, the cookie is indistinguishable from one issued by
`POST /api/v1/auth/login` — every downstream route (`require_session`,
`require_admin`, `current_user`) sees no difference and needed no changes.

Fail-closed posture (retrofit brief item 3): if `ctx.mmos` is None or
`ctx.mmos.configured` is False, every handler below refuses outright and
never falls through to "skip verification, sign in anyway". With MM OS
unconfigured this whole module is inert and the tool behaves exactly as it
did before this file existed.
"""
import secrets

from core import auth as A
from core import db as D
from core.api import ApiError, ok
from core.context import ctx
from core.mmos_client import TokenError

# ------------------------------------------------------------- local mapping
# A dedicated table, not a new column on app_user — Agent 0 owns
# core/db.py's schema exclusively (agents/CONTRACTS.md §5) and this retrofit
# does not touch that file at all. Created lazily, IF NOT EXISTS, the same
# non-destructive pattern core/db.py's own migrations use.

_LINK_TABLE_READY = False


def _ensure_table(con):
    global _LINK_TABLE_READY
    if _LINK_TABLE_READY:
        return
    con.execute("""CREATE TABLE IF NOT EXISTS mmos_link(
        username TEXT PRIMARY KEY,
        mmos_sub TEXT UNIQUE,
        employee_code TEXT,
        email TEXT,
        linked_at TEXT)""")
    con.commit()
    _LINK_TABLE_READY = True


def _slug_username(claims):
    base = (claims.get("emp") or "").strip().lower()
    if not base:
        email = (claims.get("email") or "").strip().lower()
        base = email.split("@")[0] if email else ""
    if not base:
        base = (claims.get("sub") or "user").strip().lower()
    base = "".join(ch if (ch.isalnum() or ch in "._-") else "-" for ch in base)
    return f"mmos_{base}" if base else "mmos_user"


def _provision_user(con, claims):
    """Returns the local username to mint a session for. Creates or reuses a
    local app_user row keyed by the MM OS `sub` claim; never touches an
    account that was not created through this path, and never reactivates
    one an admin disabled locally."""
    _ensure_table(con)
    sub = claims.get("sub") or ""
    if not sub:
        raise ApiError("AUTH_REQUIRED", "MM OS token has no subject")

    is_admin = "admin" in (claims.get("roles") or [])
    display_name = claims.get("name") or claims.get("email") or sub

    link = D.one(con, "SELECT username FROM mmos_link WHERE mmos_sub=?", (sub,))
    if link:
        username = link["username"]
        row = D.one(con, "SELECT active FROM app_user WHERE username=?", (username,))
        if row and not row["active"]:
            raise ApiError("FORBIDDEN", "this account has been disabled locally")
        if row:
            con.execute("UPDATE app_user SET display_name=?, is_admin=? WHERE username=?",
                        (display_name, 1 if is_admin else 0, username))
            con.commit()
            return username
        # the link row survived but the account row is gone somehow - fall
        # through and provision fresh rather than 500ing

    # first time this MM OS identity has signed in here - mint a local
    # account for it, exactly like `manage.py adduser` would, except the
    # password is a random value nobody is ever told: password login stays
    # disabled for this account, MM OS is the only way in
    base = _slug_username(claims)
    username, n = base, 0
    while D.one(con, "SELECT 1 FROM app_user WHERE username=?", (username,)):
        n += 1
        username = f"{base}{n}"

    pw_hash, salt = A.hash_password(secrets.token_urlsafe(32))
    con.execute(
        "INSERT INTO app_user(username, display_name, pw_hash, salt, is_admin, active, created_at, created_by) "
        "VALUES (?,?,?,?,?,1,?,?)",
        (username, display_name, pw_hash, salt, 1 if is_admin else 0, D.now(), "mmos-sso"))
    con.execute(
        "INSERT INTO mmos_link(username, mmos_sub, employee_code, email, linked_at) VALUES (?,?,?,?,?)",
        (username, sub, claims.get("emp"), claims.get("email"), D.now()))
    con.commit()
    D.log(con, "system", "mmos-provision", username, {"sub": sub, "employee_code": claims.get("emp")})
    con.commit()
    return username


# ------------------------------------------------------------------- routes

_ACCEPT_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Signing in - Item Code Studio</title>
<body style="font-family:Barlow,'Segoe UI',sans-serif;background:#001b2e;color:#eaf2f7;
             display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<p id="mmos-msg">Signing in with MM OS...</p>
<script>
(function () {
  var frag = window.location.hash || "";
  var m = frag.match(/token=([^&]+)/);
  var msg = document.getElementById("mmos-msg");
  if (!m) { msg.textContent = "No token in the URL."; return; }
  var token = decodeURIComponent(m[1]);
  fetch("/api/v1/mmos/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ token: token })
  }).then(function (r) {
    if (!r.ok) throw new Error("rejected");
    history.replaceState(null, "", window.location.pathname);
    window.location.replace("/index.html");
  }).catch(function () {
    history.replaceState(null, "", window.location.pathname);
    msg.textContent = "MM OS sign-in failed. Ask MM OS to send a new link, or sign in with a password below.";
    var a = document.createElement("a");
    a.href = "/login.html"; a.style.color = "#04aed1"; a.textContent = "Go to sign in";
    document.body.appendChild(document.createElement("br"));
    document.body.appendChild(a);
  });
})();
</script>
</body>
"""


def accept(req):
    """GET /_mmos/accept — MM OS's launch_url is hardcoded to this exact
    path (MM OS repo backend/app/routers/tokens.py: `f"{base_url}/_mmos/
    accept#token={token}"`), so it cannot be moved under /api/v1. Served
    regardless of whether MM OS is configured here, so a mis-launched or
    stale link always shows a plain page instead of a raw 404 — the token
    itself is only ever verified by POST /api/v1/mmos/session below, never
    read or trusted here."""
    return (200, _ACCEPT_HTML, "text/html; charset=utf-8", {})


def session(req):
    """POST /api/v1/mmos/session — verifies the MM OS token offline, maps it
    to a local account, mints THIS tool's own session (core.auth), and sets
    the same cookie a normal username/password login sets. Fails closed:
    unconfigured MM OS or a bad token never falls through to a session."""
    mmos = ctx.mmos
    if mmos is None or not mmos.configured:
        raise ApiError("FORBIDDEN", "MM OS sign-in is not configured on this deployment")

    token = (req.body or {}).get("token") or ""
    try:
        claims = mmos.verify(token)
    except TokenError as exc:
        raise ApiError("AUTH_REQUIRED", f"MM OS token rejected: {exc.reason}")

    con = ctx.con
    username = _provision_user(con, claims)
    sess_token, _expires = A.create_session(con, username)
    D.log(con, username, "login-mmos", username, {"sub": claims.get("sub")})
    con.commit()

    return (200, ok({"user": {"username": username}}), "application/json; charset=utf-8",
            {"Set-Cookie": A.cookie_header(sess_token)})


def health(req):
    """GET /api/v1/mmos/health — informational only. Drives the login
    page's "Sign in with MM OS" button (hidden when not configured) and
    doubles as the MM OS integration checklist's health probe."""
    mmos = ctx.mmos
    return ok({"configured": bool(mmos and mmos.configured),
               "slug": (mmos.slug if mmos else None),
               "os_url": (mmos.os_url if mmos and mmos.configured else None)})


ROUTES = [
    ("GET", "/_mmos/accept", accept),
    ("POST", "/api/v1/mmos/session", session),
    ("GET", "/api/v1/mmos/health", health),
]
