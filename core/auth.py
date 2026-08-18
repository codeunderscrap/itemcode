"""Authentication, sessions, password handling and login rate-limiting.
Agent B owns this file — agents/CONTRACTS.md §6, §9.

The import path everyone else uses:

    from core.auth import require_session, current_user, require_admin

    current_user(req)   -> username str, or None
    require_session(req) -> username str, or raises core.api.ApiError("AUTH_REQUIRED", ...) -> 401
    require_admin(req)  -> username str, or raises core.api.ApiError("FORBIDDEN", ...)   -> 403

`req` is the same Req object every route handler receives (core/dispatch.py) —
it carries `.headers`, from which the session cookie is read. There is no
other lookup path: attribution comes from the session, never from a header
the caller supplies.

Security notes (agents/CONTRACTS.md §2, "Security consequences of being on
the public internet" — this server is a public VPS now, not a LAN box):
  - passwords: hashlib.scrypt with a random per-user salt, both stored in
    app_user (pw_hash BLOB, salt BLOB). Never plaintext, never recoverable.
    Comparison is hmac.compare_digest.
  - sessions: secrets.token_urlsafe(32) handed to the browser as a cookie;
    only its SHA-256 hash is ever stored server-side (session.token_hash),
    so a stolen database dump does not hand over live sessions.
  - the cookie is HttpOnly, Secure, SameSite=Strict, Path=/, 12 hours.
  - login is rate-limited per IP and per account (see below); nothing here
    ever logs a password, a raw token or the LLM/ERP API keys.
"""
import datetime
import hashlib
import hmac
import json
import secrets
import string
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie

from core import db as D
from core.api import ApiError
from core.context import ctx

# ---------------------------------------------------------------- constants

SESSION_COOKIE = "ics_session"
SESSION_HOURS = 12

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SALT_BYTES, KEY_LEN = 16, 32

GENERIC_LOGIN_FAIL = "incorrect username or password"

MUST_CHANGE_KEY = "auth.must_change_pw"

# per-IP: back off after this many failures within the window
IP_MAX, IP_WINDOW = 5, 15 * 60
# per-account: lock briefly after this many failures within the window
USER_MAX, USER_WINDOW = 10, 15 * 60


# ---------------------------------------------------------------- passwords

def hash_password(password, salt=None):
    """Returns (pw_hash: bytes, salt: bytes). Pass `salt` back in to verify."""
    salt = salt or secrets.token_bytes(SALT_BYTES)
    pw_hash = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                              n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_LEN)
    return pw_hash, salt


def verify_password(password, pw_hash, salt):
    if not pw_hash or not salt:
        return False
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, bytes(pw_hash))


def generate_password(length=14):
    """A strong, printable, human-typeable password for handover."""
    alphabet = string.ascii_letters + string.digits + "!@#%^&*-_="
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------- must-change

def _must_change_set(con):
    return set(D.get_setting(con, MUST_CHANGE_KEY, []) or [])


def flag_must_change(con, username):
    s = _must_change_set(con)
    s.add(username)
    D.set_setting(con, MUST_CHANGE_KEY, sorted(s))


def clear_must_change(con, username):
    s = _must_change_set(con)
    s.discard(username)
    D.set_setting(con, MUST_CHANGE_KEY, sorted(s))


def must_change(con, username):
    return username in _must_change_set(con)


# ---------------------------------------------------------------- sessions

def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(con, username):
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.now() + datetime.timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
    con.execute("INSERT INTO session(token_hash, username, issued_at, expires_at) VALUES (?,?,?,?)",
                (_token_hash(token), username, D.now(), expires))
    con.commit()
    return token, expires


def delete_session(con, token):
    if not token:
        return
    con.execute("DELETE FROM session WHERE token_hash=?", (_token_hash(token),))
    con.commit()


def delete_sessions_for(con, username):
    con.execute("DELETE FROM session WHERE username=?", (username,))
    con.commit()


def _cookie_token(req):
    raw = req.headers.get("Cookie")
    if not raw:
        return None
    c = SimpleCookie()
    try:
        c.load(raw)
    except Exception:                                          # noqa: BLE001
        return None
    m = c.get(SESSION_COOKIE)
    return m.value if m else None


def resolve_session(con, req):
    """Cookie -> live username, or None. Expired/unknown sessions are quietly
    dropped (and the row cleaned up) rather than raising - callers decide
    what "no session" means for their endpoint."""
    token = _cookie_token(req)
    if not token:
        return None
    th = _token_hash(token)
    row = D.one(con, "SELECT username, expires_at FROM session WHERE token_hash=?", (th,))
    if not row:
        return None
    if row["expires_at"] < D.now():
        con.execute("DELETE FROM session WHERE token_hash=?", (th,))
        con.commit()
        return None
    return row["username"]


def _tls_configured():
    """Whether this deployment is actually served over HTTPS - config.json's
    top-level "tls" flag (default false), which Agent H's deployment step
    flips on once a real certificate is in front of the process. Read from
    ctx.cfg, never inferred from a client-supplied header like
    X-Forwarded-Proto - that's spoofable and this decision is security-
    relevant (agents/CONTRACTS.md §2 mandates HTTPS for the eventual public
    VPS, but agents/AGENT_H_DEPLOY.md's tier-2 local server is explicitly
    meant to "work today with no VPS at all", i.e. over plain HTTP)."""
    return bool((ctx.cfg or {}).get("tls"))


def cookie_header(token, clear=False):
    """A browser will silently refuse to send a `Secure` cookie back over
    plain HTTP, so `Secure` is only set once config.json's "tls" is true -
    otherwise nobody could stay logged in on the tier-2 local server, which
    runs without TLS by design. HttpOnly and SameSite=Strict apply either
    way; only the Secure attribute is conditional."""
    secure = "; Secure" if _tls_configured() else ""
    if clear:
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly{secure}; SameSite=Strict; Max-Age=0"
    return (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly{secure}; SameSite=Strict; "
            f"Max-Age={SESSION_HOURS * 3600}")


# ---------------------------------------------------------------- the helper everyone else uses

def current_user(req):
    """username str, or None. Never raises."""
    con = ctx.con
    username = resolve_session(con, req)
    if not username:
        return None
    row = D.one(con, "SELECT active FROM app_user WHERE username=?", (username,))
    if not row or not row["active"]:
        return None
    return username


def require_session(req):
    u = current_user(req)
    if not u:
        raise ApiError("AUTH_REQUIRED", "sign in required")
    return u


def require_admin(req):
    u = require_session(req)
    row = D.one(ctx.con, "SELECT is_admin FROM app_user WHERE username=?", (u,))
    if not row or not row["is_admin"]:
        raise ApiError("FORBIDDEN", "admin only")
    return u


# ---------------------------------------------------------------- login rate limiting
# In-memory only (every request already runs under ctx.lock - core/dispatch.py -
# so no extra locking is needed here). Resets on process restart; that's fine,
# it only needs to survive one abuse burst, not forever.

_fails_ip = {}
_fails_user = {}


def _prune(lst, window):
    cutoff = time.time() - window
    while lst and lst[0] < cutoff:
        lst.pop(0)


def note_login_failure(ip, username):
    lst = _fails_ip.setdefault(ip, [])
    _prune(lst, IP_WINDOW)
    lst.append(time.time())
    if username:
        lst2 = _fails_user.setdefault(username, [])
        _prune(lst2, USER_WINDOW)
        lst2.append(time.time())


def login_blocked(ip, username):
    """True if either the IP or the account has tripped its limit. Callers
    must not reveal which - agents/AGENT_B_AUTH.md: 'Never reveal which of
    the two tripped.'"""
    lst = _fails_ip.get(ip, [])
    _prune(lst, IP_WINDOW)
    if len(lst) >= IP_MAX:
        return True
    if username:
        lst2 = _fails_user.get(username, [])
        _prune(lst2, USER_WINDOW)
        if len(lst2) >= USER_MAX:
            return True
    return False


def clear_login_failures(ip, username):
    _fails_ip.pop(ip, None)
    if username:
        _fails_user.pop(username, None)


def client_ip(req):
    """Best-effort caller identity for rate limiting. There is no raw socket
    address on Req today (core/dispatch.py only hands handlers method/path/
    query/params/body/fields/files/user/headers) - only a reverse proxy's
    forwarded-for header, if one is in front of us. Documented as a gap for
    Agent H in agents/done/AGENT_B.md: ideally dispatch.py stamps the real
    peer address onto Req so this stops trusting a client-supplied header.
    Until then this still narrows abuse to "no proxy => one shared bucket",
    which is stricter than no limiting at all, never looser."""
    for h in ("X-Forwarded-For", "X-Real-IP"):
        v = req.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return "unknown"


# ---------------------------------------------------------------- first-run bootstrap

def ensure_bootstrap_admin(con):
    """If app_user is empty, create a single admin account, flag it for a
    forced password change, and return (username, password) so the caller
    can print it once. Returns None if accounts already exist."""
    count = D.one(con, "SELECT COUNT(*) c FROM app_user")["c"]
    if count:
        return None
    username = "admin"
    password = generate_password()
    pw_hash, salt = hash_password(password)
    con.execute(
        "INSERT INTO app_user(username, display_name, pw_hash, salt, is_admin, active, created_at, created_by) "
        "VALUES (?,?,?,?,1,1,?,?)",
        (username, "Administrator", pw_hash, salt, D.now(), "system"))
    con.commit()
    flag_must_change(con, username)
    return username, password


# ---------------------------------------------------------------- LLM key test
# A minimal, provider-agnostic "does this key work" ping for the Settings
# screen's Test key button. Deliberately separate from core/matcher.py /
# core/llm.py (Agent C's file, agents/README.md file-ownership table) - this
# is a credential check, not a matching call, and Agent C's LLM client may
# not exist yet when Settings needs to validate a freshly pasted key. If
# core/llm.py later grows a canonical single-call tester, this can delegate
# to it instead; until then this stays self-contained stdlib-only urllib,
# matching the style already used in core/matcher.py.

def test_llm_key(provider, api_key, model):
    """Returns (ok: bool, detail: str|None). Never raises for an ordinary
    upstream failure - that IS the answer this reports back."""
    if provider in (None, "", "none"):
        return False, "choose a provider first"
    if not api_key:
        return False, "no API key set"

    try:
        if provider == "bharatrouter":
            req = urllib.request.Request(
                "https://api.bharatrouter.com/v1/chat/completions",
                data=json.dumps({
                    "model": model or "krutrim/Krutrim-spectre-v2",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                }).encode(),
                headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
                method="POST")
        else:
            return False, "unknown provider"

        # A real User-Agent, not urllib's default "Python-urllib/3.x" -
        # Groq (confirmed live, 7 August 2026) sits behind Cloudflare bot
        # protection that returns a bare 403 "error code: 1010" against the
        # default one, unrelated to the key or payload being wrong.
        req.add_header("User-Agent", "Mozilla/5.0 (compatible; ItemCodeStudio/1.0)")
        with urllib.request.urlopen(req, timeout=12) as resp:
            resp.read(200)
        return True, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(300).decode("utf-8", "ignore")
        except Exception:                                       # noqa: BLE001
            detail = ""
        return False, f"{e.code} {e.reason}: {detail[:200]}".strip()
    except urllib.error.URLError as e:
        return False, f"could not reach {provider}: {e.reason}"
    except Exception as e:                                      # noqa: BLE001
        return False, str(e)
