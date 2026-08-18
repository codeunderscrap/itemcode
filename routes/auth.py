"""Auth & settings — Agent B. agents/CONTRACTS.md §6 assigns this module
`/api/v1/auth/*` and `/settings/*`; the settings screen is implemented under
`/api/v1/settings` instead (see the note above ROUTES below) because
CONTRACTS.md §6 also says plainly "Everything under /api/v1. Two envelopes,
no exceptions" and because core/dispatch.py only ever routes a GET request
through the table when its path starts with "/api/" - anything else falls
straight to static-file serving, so a bare "/settings" GET could never have
reached a handler anyway. Documented as a judgement call in
agents/done/AGENT_B.md.

Provides the helper the rest of the system imports:

    from core.auth import require_session, current_user, require_admin

`require_session` raises core.api.ApiError("AUTH_REQUIRED", ...) -> 401 on a
missing/expired session; every mutating endpoint outside this file must call
it. Hiding a button in the UI is not the security boundary - this is.
"""
import os
import sys

from core import auth as A
from core import db as D
from core.api import ApiError, ok
from core.context import ctx

# ---------------------------------------------------------------- auth

def login(req):
    p = req.body or {}
    username = (p.get("username") or "").strip().lower()
    password = p.get("password") or ""
    ip = A.client_ip(req)

    if A.login_blocked(ip, username):
        raise ApiError("RATE_LIMITED", "too many attempts — try again in a few minutes")

    con = ctx.con
    row = D.one(con, "SELECT * FROM app_user WHERE username=?", (username,))
    ok_pw = False
    if row and row["active"] and password:
        ok_pw = A.verify_password(password, row["pw_hash"], row["salt"])
    else:
        # do roughly the same amount of work for an unknown username, so a
        # timing difference does not tell an attacker the account exists
        A.hash_password(password or " ")

    if not row or not row["active"] or not ok_pw:
        A.note_login_failure(ip, username)
        raise ApiError("VALIDATION", A.GENERIC_LOGIN_FAIL)

    A.clear_login_failures(ip, username)
    token, _expires = A.create_session(con, username)
    D.log(con, username, "login", username)
    con.commit()  # D.log() does not commit itself - see the note in AGENT_B.md

    body = ok({
        "user": {"username": row["username"], "display_name": row["display_name"],
                  "is_admin": bool(row["is_admin"])},
        "must_change_password": A.must_change(con, username),
    })
    return (200, body, "application/json; charset=utf-8",
            {"Set-Cookie": A.cookie_header(token)})


def logout(req):
    token = A._cookie_token(req)
    A.delete_session(ctx.con, token)
    return (200, ok(), "application/json; charset=utf-8",
            {"Set-Cookie": A.cookie_header(None, clear=True)})


def me(req):
    username = A.current_user(req)
    if not username:
        return ok({"user": None})
    con = ctx.con
    row = D.one(con, "SELECT username, display_name, is_admin FROM app_user WHERE username=?", (username,))
    if not row:
        return ok({"user": None})
    return ok({
        "user": {"username": row["username"], "display_name": row["display_name"],
                  "is_admin": bool(row["is_admin"])},
        "must_change_password": A.must_change(con, username),
    })


def change_password(req):
    username = A.require_session(req)
    p = req.body or {}
    old = p.get("old") or ""
    new = p.get("new") or ""
    if not old:
        raise ApiError("VALIDATION", "current password is required")
    if len(new) < 8:
        raise ApiError("VALIDATION", "new password must be at least 8 characters")

    con = ctx.con
    row = D.one(con, "SELECT * FROM app_user WHERE username=?", (username,))
    if not row or not A.verify_password(old, row["pw_hash"], row["salt"]):
        raise ApiError("VALIDATION", "current password is incorrect")

    pw_hash, salt = A.hash_password(new)
    con.execute("UPDATE app_user SET pw_hash=?, salt=? WHERE username=?", (pw_hash, salt, username))
    con.commit()
    A.clear_must_change(con, username)
    D.log(con, username, "change-password", username)
    con.commit()  # D.log() does not commit itself - see the note in AGENT_B.md
    return ok()


# ---------------------------------------------------------------- settings
# Reserved keys, agents/CONTRACTS.md §5: llm.provider, llm.api_key, llm.model,
# match.mode, match.threshold, erp.enabled, erp.dry_run, erp.base_url,
# erp.api_key, erp.api_secret, sync.times. This screen edits the subset
# agents/AGENT_B_AUTH.md lists explicitly; erp.api_key/erp.api_secret are
# reserved for Agent G to read/write via core.db.get_setting/set_setting
# directly if ERPNext ever needs its own credential, since they are not in
# that table.

PROVIDER_MODELS = {
    "none": "",
    "anthropic": "claude-haiku-4-5-20251001",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
    "grok": "grok-4",
    "groq": "llama-3.3-70b-versatile",
}
PROVIDERS = set(PROVIDER_MODELS)

DEFAULTS = {
    "match.mode": "fuzzy",
    "llm.provider": "none",
    "llm.api_key": "",
    "llm.model": "",
    "match.threshold": 60,
    "erp.enabled": False,
    "erp.dry_run": True,
    "erp.auto_push": False,
    "erp.base_url": "",
    "erp.username": "",
    "erp.password": "",
    "sync.times": "09:00,17:00",
    "ocr.provider": "api",
    "ocr.api_url": "http://localhost:8757/ocr",
}
SECRET_KEYS = {"llm.api_key", "erp.password"}
MASK = "••••••••"


def _read_settings(con):
    out = {}
    for key, default in DEFAULTS.items():
        val = D.get_setting(con, key, default)
        if key in SECRET_KEYS:
            out[key] = MASK if val else ""
        else:
            out[key] = val
    key_present = bool(D.get_setting(con, "llm.api_key", ""))
    provider_set = D.get_setting(con, "llm.provider", "none") not in (None, "", "none")
    out["llm_key_set"] = key_present
    out["llm_ready"] = key_present and provider_set
    return out


def get_settings(req):
    A.require_admin(req)
    return ok({"settings": _read_settings(ctx.con)})


def post_settings(req):
    user = A.require_admin(req)
    con = ctx.con
    p = req.body or {}
    updates = {}

    if "llm.provider" in p:
        prov = p["llm.provider"]
        if prov not in PROVIDERS:
            raise ApiError("VALIDATION", "unknown provider")
        updates["llm.provider"] = prov

    if "llm.api_key" in p:
        key = (p["llm.api_key"] or "").strip()
        if key and key != MASK:
            updates["llm.api_key"] = key
        elif key == "":
            updates["llm.api_key"] = ""    # explicit clear

    if "llm.model" in p:
        model = (p["llm.model"] or "").strip()
        if not model:
            provider = updates.get("llm.provider", D.get_setting(con, "llm.provider", "none"))
            model = PROVIDER_MODELS.get(provider, "")
        updates["llm.model"] = model

    if "match.threshold" in p:
        try:
            t = int(p["match.threshold"])
        except (TypeError, ValueError):
            raise ApiError("VALIDATION", "match.threshold must be a whole number")
        if not (0 <= t <= 100):
            raise ApiError("VALIDATION", "match.threshold must be between 0 and 100")
        updates["match.threshold"] = t

    for key in ("erp.enabled", "erp.dry_run", "erp.auto_push"):
        if key in p:
            updates[key] = bool(p[key])

    if "erp.base_url" in p:
        updates["erp.base_url"] = (p["erp.base_url"] or "").strip()

    if "erp.username" in p:
        updates["erp.username"] = (p["erp.username"] or "").strip()

    if "erp.password" in p:
        pwd = (p["erp.password"] or "").strip()
        if pwd and pwd != MASK:
            updates["erp.password"] = pwd
        elif pwd == "":
            updates["erp.password"] = ""

    if "sync.times" in p:
        updates["sync.times"] = (p["sync.times"] or "").strip() or DEFAULTS["sync.times"]

    if "ocr.provider" in p:
        prov = p["ocr.provider"]
        if prov not in ("local", "api"):
            raise ApiError("VALIDATION", "ocr.provider must be 'local' or 'api'")
        updates["ocr.provider"] = prov

    if "ocr.api_url" in p:
        updates["ocr.api_url"] = (p["ocr.api_url"] or "").strip()

    # figure out where the key/provider land AFTER this update, before
    # deciding match.mode - this is what "until a key is present, match.mode
    # must be fuzzy" (agents/CONTRACTS.md decision 11) actually means
    new_key = updates.get("llm.api_key", D.get_setting(con, "llm.api_key", ""))
    new_provider = updates.get("llm.provider", D.get_setting(con, "llm.provider", "none"))
    key_ready = bool(new_key) and new_provider not in (None, "", "none")

    if "match.mode" in p:
        mode = p["match.mode"]
        if mode not in ("fuzzy", "llm"):
            raise ApiError("VALIDATION", "match.mode must be 'fuzzy' or 'llm'")
        if mode == "llm" and not key_ready:
            raise ApiError("VALIDATION", "set a provider and an API key before switching to LLM matching")
        updates["match.mode"] = mode
    elif not key_ready:
        # the key/provider just got cleared (or never existed) - force fuzzy
        # so the app "runs fuzzy-only" rather than silently failing every call
        if D.get_setting(con, "match.mode", "fuzzy") != "fuzzy":
            updates["match.mode"] = "fuzzy"

    for k, v in updates.items():
        D.set_setting(con, k, v)

    D.log(con, user, "update-settings", "settings",
          {k: ("***" if k in SECRET_KEYS else v) for k, v in updates.items()})
    con.commit()  # D.log() does not commit itself - see the note in AGENT_B.md

    return ok({"settings": _read_settings(con)})


def test_llm(req):
    A.require_admin(req)
    con = ctx.con
    p = req.body or {}
    provider = p.get("llm.provider") or D.get_setting(con, "llm.provider", "none")
    key = (p.get("llm.api_key") or "").strip()
    if not key or key == MASK:
        key = D.get_setting(con, "llm.api_key", "")
    model = (p.get("llm.model") or "").strip() or D.get_setting(con, "llm.model", "")
    success, detail = A.test_llm_key(provider, key, model)
    return ok({"success": success, "detail": detail})


ROUTES = [
    ("POST", "/api/v1/auth/login", login),
    ("POST", "/api/v1/auth/logout", logout),
    ("GET", "/api/v1/auth/me", me),
    ("POST", "/api/v1/auth/password", change_password),
    ("GET", "/api/v1/settings", get_settings),
    ("POST", "/api/v1/settings", post_settings),
    ("POST", "/api/v1/settings/test-llm", test_llm),
]


# ---------------------------------------------------------------- first-run bootstrap
# Runs once, at import time. server.py calls ctx.init(...) before it imports
# routes/auth (see server.py's own comment on that ordering), so ctx.con is
# guaranteed ready here. This is how agents/AGENT_B_AUTH.md task 5's "On
# first run, if app_user is empty, create an admin account" is satisfied
# without editing server.py, which Agent 0 owns exclusively.

def _bootstrap():
    created = A.ensure_bootstrap_admin(ctx.con)
    if not created:
        return
    username, password = created
    banner = "=" * 66
    print(banner, file=sys.stderr)
    print("  first run: no accounts existed, so one was created", file=sys.stderr)
    print(f"    username: {username}", file=sys.stderr)
    print(f"    password: {password}", file=sys.stderr)
    print("  hand this to the person directly. It will not be shown again.", file=sys.stderr)
    print("  they will be required to change it at first login.", file=sys.stderr)
    print(banner, file=sys.stderr)


if os.environ.get("ICS_SKIP_BOOTSTRAP") != "1":
    _bootstrap()
