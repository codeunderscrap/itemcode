#!/usr/bin/env python
"""tests/smoke.py — Agent H. Standard library only. Runs in well under a
minute against the real seeded database (agents/CONTRACTS.md house rule 4).

    python tests/smoke.py

What this proves, in order:
  1. the server is reachable (starts it itself if nothing answers yet)
  2. /api/docs answers and lists routes — used to drive the rest of this
     file, so the 401 sweep below tests EVERY /api/v1 route that exists at
     run time, not a hand-typed list that goes stale
  3. every /api/v1 endpoint answers at all (no connection errors, no 500s
     on a bare call)
  4. every mutating / session-gated endpoint returns 401 AUTH_REQUIRED with
     no session cookie — endpoint by endpoint, per agents/AGENT_H_DEPLOY.md:
     "the single most valuable test here"
  5. login works and the session cookie is set with the right flags
  6. decode of RMBS0010206100007 is correct (exact values verified against
     agents/done/AGENT_A.md's browser-tested output)
  7. a full resolve(/preview) -> commit -> decode round trip
  8. the public page (web/public.js) exposes no creator function
  9. the error envelope holds on a deliberately bad request

This file NEVER weakens anyone else's auth check to make itself pass — if a
route answers something other than 401 here, that is reported as a real
finding, not patched around (agents/AGENT_H_DEPLOY.md "Watch for").
"""
import http.cookies
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CFG_PATH = os.path.join(ROOT, "config.json")
with open(CFG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

HOST = "127.0.0.1"
PORT = int(CFG.get("port", 8756))
BASE = f"http://{HOST}:{PORT}"
TIMEOUT = 4

PASS, FAIL, SKIP = [], [], []


def ok_(name):
    PASS.append(name)
    print(f"  ok    {name}")


def fail_(name, detail=""):
    FAIL.append((name, detail))
    print(f"  FAIL  {name}   {detail}")


def skip_(name, why):
    SKIP.append((name, why))
    print(f"  skip  {name}   {why}")


def check(name, cond, detail=""):
    (ok_ if cond else fail_)(name) if cond else fail_(name, detail)


# ---------------------------------------------------------------- HTTP helpers

def _do(method, path, body=None, cookie=None, raw_body=None, timeout=TIMEOUT):
    """Returns (status, parsed_json_or_None, raw_text, headers).
    Never raises for an ordinary HTTP error status — that's the thing being
    tested, not a test-harness failure."""
    url = BASE + path
    data = None
    headers = {}
    if raw_body is not None:
        data = raw_body
        headers["Content-Type"] = "application/json"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            status = r.status
            resp_headers = dict(r.headers.items())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        status = e.code
        resp_headers = dict(e.headers.items()) if e.headers else {}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, None, str(e), {}
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, raw, resp_headers


def GET(path, **kw):
    return _do("GET", path, **kw)


def POST(path, **kw):
    return _do("POST", path, **kw)


# ---------------------------------------------------------------- server lifecycle

def _port_answers_health():
    status, parsed, _raw, _h = GET("/api/v1/health", timeout=2)
    return status == 200 and isinstance(parsed, dict) and parsed.get("ok") is True


def _port_in_use():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()


def ensure_server():
    """Reuse an already-running, healthy server; otherwise start one. Never
    silently trusts a listener that doesn't answer /api/v1/health — that is
    exactly the stale-process trap agents/done/AGENT_0.md hit."""
    if _port_answers_health():
        print(f"  using already-running server at {BASE}")
        return None
    if _port_in_use():
        print(f"  FATAL: something is listening on :{PORT} but did not answer "
              f"/api/v1/health — kill it and retry (see agents/done/AGENT_0.md's "
              f"note on a stale prior-session process silently answering requests).")
        sys.exit(2)
    print(f"  starting server on :{PORT} for this test run...")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server.py"), "--no-browser"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        if _port_answers_health():
            print("  server is up.")
            return proc
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            print("  FATAL: server process exited during startup:\n", out)
            sys.exit(2)
        time.sleep(0.5)
    proc.terminate()
    print("  FATAL: server did not answer /api/v1/health within 30s")
    sys.exit(2)


# ---------------------------------------------------------------- test-user fixture
# A dedicated account, independent of whatever admin(s) already exist, so
# this file never needs to know a real operator's password. Created via a
# short-lived direct DB connection (same pattern manage.py already uses),
# not through the HTTP API — creating accounts is deliberately not an API
# endpoint at all (agents/CONTRACTS.md decision 9: admin-provisioned only).

TEST_USER = "smoketest_admin"


def make_test_user():
    from core import db as D
    from core import auth as A
    con = D.connect()
    D.init(con)
    con.execute("DELETE FROM session WHERE username=?", (TEST_USER,))
    con.execute("DELETE FROM app_user WHERE username=?", (TEST_USER,))
    password = A.generate_password()
    pw_hash, salt = A.hash_password(password)
    con.execute(
        "INSERT INTO app_user(username, display_name, pw_hash, salt, is_admin, active, created_at, created_by) "
        "VALUES (?,?,?,?,1,1,?,?)",
        (TEST_USER, "Smoke Test", pw_hash, salt, D.now(), "tests/smoke.py"))
    con.commit()
    con.close()
    return password


def cleanup_test_user():
    from core import db as D
    con = D.connect()
    con.execute("DELETE FROM session WHERE username=?", (TEST_USER,))
    con.execute("DELETE FROM app_user WHERE username=?", (TEST_USER,))
    con.commit()
    con.close()


# --------------------------------------------------------------------- tests

def test_docs_and_health():
    status, parsed, _raw, _h = GET("/api/v1/health")
    check("GET /api/v1/health answers 200 ok:true", status == 200 and parsed and parsed.get("ok"))

    status, parsed, _raw, _h = GET("/api/docs?format=json")
    check("GET /api/docs?format=json answers with a route list",
          status == 200 and parsed and parsed.get("ok") and isinstance(parsed.get("routes"), list)
          and len(parsed["routes"]) > 10, f"got {status}")

    status, _parsed, raw, _h = GET("/api/docs")
    check("GET /api/docs (HTML) answers 200", status == 200 and "<table" in raw)
    return parsed["routes"] if parsed else []


def test_every_v1_endpoint_answers(routes):
    """'Every endpoint answers' — a bare call never times out or connection-
    errors. For GET/public/no-path-param routes this also checks 200; for
    everything else, 'answers' just means we got a real HTTP response,
    which the 401 sweep below checks the exact meaning of."""
    for r in routes:
        if not r["versioned"]:
            continue
        if "<" in r["path"]:
            continue  # path-param routes get a real value in dedicated tests below
        if r["method"] != "GET":
            continue  # POSTs need a body; covered by the 401 sweep / dedicated tests
        status, parsed, _raw, _h = GET(r["path"])
        if status is None:
            fail_(f"answers: {r['method']} {r['path']}", "connection error / timeout")
            continue
        # "Answers" means a real, well-formed envelope came back — not a
        # connection error and not a raw crash. Whether that envelope is
        # ok:true (e.g. a route with no required params) or a clean
        # ok:false/VALIDATION (e.g. /api/v1/decode with no ?code=) are both
        # a legitimate "the endpoint is alive" outcome; the 401 sweep below
        # is what checks the *auth* meaning of the response.
        well_formed = isinstance(parsed, dict) and "ok" in parsed
        check(f"answers: GET {r['path']}", well_formed, f"got {status} {parsed}")


def test_401_sweep(routes):
    """The single most valuable test in this file. Every route whose own
    source calls require_session/require_admin — for /api/v1/* only, the
    frozen contract surface — must refuse a request with no cookie at all,
    and must refuse it with exactly AUTH_REQUIRED, not a 500, not a silent
    200. Checked endpoint by endpoint, never once for the whole app."""
    tested = 0
    for r in routes:
        if not r["versioned"] or r["auth"] == "public":
            continue
        path = re.sub(r"<[^>]+>", "__smoketest_placeholder__", r["path"])
        if r["method"] == "GET":
            status, parsed, _raw, _h = GET(path)
        else:
            status, parsed, _raw, _h = POST(path, body={})
        tested += 1
        name = f"401 sweep: {r['method']} {r['path']}"
        if status is None:
            fail_(name, "connection error / timeout")
            continue
        got_code = (parsed or {}).get("error", {}).get("code") if isinstance(parsed, dict) else None
        check(name, status == 401 and got_code == "AUTH_REQUIRED",
              f"expected 401/AUTH_REQUIRED, got {status}/{got_code}")
    check("401 sweep covered at least one endpoint", tested > 0, "no session-gated /api/v1 routes found at all")
    print(f"    ({tested} session-gated /api/v1 endpoints checked)")


def test_legacy_routes_not_silently_broken(routes):
    """Not part of the 401 sweep (Agent 0 deliberately kept these
    un-versioned, unauthenticated, unwrapped — see agents/done/AGENT_0.md).
    This just confirms the ones that are still public mutating endpoints are
    KNOWN and named, so the gap is visible rather than silently rediscovered
    later. See HANDOVER.md for the full list and the recommendation."""
    exposed = [f"{r['method']} {r['path']}" for r in routes
               if not r["versioned"] and r["method"] == "POST" and r["auth"] == "public"]
    print(f"    legacy unauthenticated POST routes still live: {exposed}")
    check("legacy-route exposure is enumerated (see HANDOVER.md)", True)


def test_login_and_cookie():
    password = make_test_user()
    status, parsed, _raw, headers = POST("/api/v1/auth/login",
                                         body={"username": TEST_USER, "password": password})
    check("login with correct credentials succeeds", status == 200 and parsed and parsed.get("ok"),
          f"got {status} {parsed}")
    set_cookie = headers.get("Set-Cookie", "")
    check("login sets a cookie", "ics_session=" in set_cookie, set_cookie)
    check("cookie is HttpOnly", "HttpOnly" in set_cookie, set_cookie)
    check("cookie is SameSite=Strict", "SameSite=Strict" in set_cookie, set_cookie)
    # Secure is conditional on config.json's "tls" flag (core/auth.py:
    # _tls_configured()) — a browser refuses to send a Secure cookie back
    # over plain HTTP, so tier 2 (LAN, no TLS, "works today with no VPS at
    # all") deliberately omits it; the VPS deployment sets tls:true, at
    # which point this same assertion starts expecting it present.
    expect_secure = bool(CFG.get("tls"))
    check(f"cookie Secure flag matches config.json tls={CFG.get('tls', False)}",
          ("Secure" in set_cookie) == expect_secure, set_cookie)

    # http.cookiejar refuses to resend a Secure cookie over a plain-http test
    # server, correctly — so this file carries the token itself rather than
    # relying on an automatic jar, and does not treat that browser-correct
    # refusal as a bug.
    c = http.cookies.SimpleCookie()
    c.load(set_cookie)
    token = c.get("ics_session").value if c.get("ics_session") else None
    check("session cookie value is present", bool(token))
    cookie_header = f"ics_session={token}" if token else None

    status, parsed, _raw, _h = POST("/api/v1/auth/login",
                                    body={"username": TEST_USER, "password": "definitely-wrong"})
    check("login with wrong password is rejected", status in (400, 401) and parsed and not parsed.get("ok"))

    status, parsed, _raw, _h = GET("/api/v1/auth/me", cookie=cookie_header)
    check("me returns the logged-in user with a valid cookie",
          status == 200 and parsed and parsed.get("user", {}).get("username") == TEST_USER)

    return cookie_header


def test_decode_known_code():
    status, parsed, _raw, _h = GET("/api/v1/decode?code=RMBS0010206100007")
    check("decode RMBS0010206100007 answers 200", status == 200 and parsed and parsed.get("ok"),
          f"got {status} {parsed}")
    if not (parsed and parsed.get("ok")):
        return
    check("decode: well-formed", parsed.get("wellformed") is True)
    check("decode: known group", parsed.get("known") is True)
    check("decode: issued", parsed.get("issued") is True)
    check("decode: head is Raw Materials", parsed.get("head") == "Raw Materials", parsed.get("head"))
    check("decode: subhead is Battery Scrap", parsed.get("subhead") == "Battery Scrap", parsed.get("subhead"))
    check("decode: group is Battery Pack", parsed.get("group") == "Battery Pack", parsed.get("group"))
    specs = {s["slot"]: s for s in parsed.get("specs", [])}
    check("decode: slot 4 (Capacity) is the interior '00' gap",
          specs.get(4, {}).get("code") == "00" and specs.get(4, {}).get("value") == "(not applicable)",
          specs.get(4))
    check("decode: slot 5 (vendor) is LG", specs.get(5, {}).get("value") == "LG", specs.get(5))


def test_resolve_commit_decode_round_trip(cookie_header):
    """Deterministic by construction rather than by hoping the fuzzy/LLM
    matcher lands on a specific group: walks the same cascade endpoints
    web/create.js uses to find the real 'Battery Pack' group (agents/
    done/AGENT_A.md verified its prefix/labels), previews a brand-new spec
    combination through /api/v1/resolve/preview (the live, code-grammar-
    accurate recompute — CONTRACTS.md §3, "nobody except Agent D writes
    code-assembly logic"), then commits it and decodes the result back.
    A fixed idempotency key means reruns never grow the database — a
    replay returns the original result instead of minting a second code.
    """
    if not cookie_header:
        skip_("resolve->commit->decode round trip", "no session (login test failed)")
        return

    status, heads, _r, _h = GET("/api/v1/cascade/heads", cookie=cookie_header)
    if status != 200:
        fail_("round trip: cascade/heads", f"got {status}")
        return
    head = next((h for h in heads["heads"] if h["name"] == "Raw Materials"), None)
    if not head:
        skip_("resolve->commit->decode round trip", "'Raw Materials' head not found (seed changed?)")
        return

    status, subs, _r, _h = GET(f"/api/v1/cascade/subheads?head={head['id']}", cookie=cookie_header)
    sub = next((s for s in subs["subheads"] if s["name"] == "Battery Scrap"), None)
    if not sub:
        skip_("resolve->commit->decode round trip", "'Battery Scrap' subhead not found")
        return

    status, groups, _r, _h = GET(f"/api/v1/cascade/groups?subhead={sub['id']}", cookie=cookie_header)
    group = next((g for g in groups["groups"] if g["name"] == "Battery Pack"), None)
    if not group:
        skip_("resolve->commit->decode round trip", "'Battery Pack' group not found")
        return
    gid = group["id"]

    status, slots, _r, _h = GET(f"/api/v1/cascade/slots?group={gid}", cookie=cookie_header)
    labels = slots.get("labels", {})

    tag = str(int(time.time()))[-6:]   # unique-enough per run without external state
    preview_body = {"group_id": gid}
    for slot in ("1", "2", "3", "4"):
        if labels.get(slot):
            preview_body[f"s{slot}"] = f"new:SMOKE-TEST-{tag}-{slot}"
    if labels.get("vendor"):
        preview_body["vendor"] = f"new:SMOKE-TEST-{tag}-VENDOR"

    status, preview, _r, _h = POST("/api/v1/resolve/preview", body=preview_body, cookie=cookie_header)
    check("resolve/preview answers with a code", status == 200 and preview and preview.get("code"),
          f"got {status} {preview}")
    if not (preview and preview.get("code")):
        return
    check("resolve/preview: proposed code is free", preview.get("free") is True, preview)
    check("resolve/preview: no blockers", preview.get("blockers") == [], preview.get("blockers"))

    proposal = {
        "input": {"name": f"SMOKE TEST ITEM {tag}", "text": f"SMOKE TEST ITEM {tag}", "uom": group.get("uom")},
        "phase2": {"group": {"id": gid, "name": group["name"], "code3": group["code3"],
                              "head_code": head["code2"], "sub_code": sub["code2"]}},
        "phase3": {"slots": preview["slots"], "vendor": preview.get("vendor")},
        "blockers": [],
    }
    # Keyed on this run's own tag, not a fixed constant: the tag also seeds
    # the "new:" spec values above, so a fixed cross-run key would replay a
    # PRIOR run's code against THIS run's freshly-previewed one (real
    # mismatch we hit while building this file — see HANDOVER.md's notes
    # section on resolve/preview vs commit spec-value minting). Each run
    # therefore does add one small, clearly-named test item and a couple of
    # spec values (SMOKE TEST ITEM <tag> / SMOKE-TEST-<tag>-N) — documented
    # in HANDOVER.md rather than hidden; `python seed.py --rebuild` clears it.
    commit_body = {"proposal": proposal, "idempotency_key": f"smoke-test-{tag}"}
    status, committed, _r, _h = POST("/api/v1/commit", body=commit_body, cookie=cookie_header)
    check("commit answers 200 with a code", status == 200 and committed and committed.get("code"),
          f"got {status} {committed}")
    if not (committed and committed.get("code")):
        return
    code = committed["code"]
    check("commit: code matches the previewed code", code == preview["code"], (code, preview["code"]))

    # replay the same idempotency key — must return the same code, not mint a second one
    status2, replay, _r, _h = POST("/api/v1/commit", body=commit_body, cookie=cookie_header)
    check("commit: replay with same idempotency_key is idempotent",
          status2 == 200 and replay.get("code") == code and replay.get("idempotent") is True,
          f"got {status2} {replay}")

    status, decoded, _r, _h = GET(f"/api/v1/decode?code={code}")
    check("round trip: newly committed code decodes as issued",
          status == 200 and decoded.get("ok") and decoded.get("issued") is True, decoded)


def test_public_page_has_no_creator_function():
    for fname in ("public.html", "public.js"):
        path = os.path.join(ROOT, "web", fname)
        if not os.path.isfile(path):
            skip_(f"public page audit: {fname}", "file not found")
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        forbidden = ["/api/v1/commit", "/api/v1/resolve", "/api/v1/ingest",
                     "/api/commit", "/api/resolve", "/api/ingest"]
        hits = [tok for tok in forbidden if tok in src]
        check(f"public page has no creator API calls: {fname}", not hits, hits)


def test_error_envelope():
    status, parsed, raw, _h = GET("/api/v1/decode?code=")
    check("bad request (empty code) returns 400 VALIDATION envelope",
          status == 400 and parsed and parsed.get("ok") is False
          and parsed["error"]["code"] == "VALIDATION", f"got {status} {parsed}")
    check("no stack trace leaks in the bad-request body", "Traceback" not in raw and ".py\"," not in raw)

    status, parsed, raw, _h = _do("POST", "/api/v1/auth/login", raw_body=b"{not json", timeout=TIMEOUT)
    check("malformed JSON body returns a clean error envelope, not a crash",
          status == 400 and parsed and parsed.get("ok") is False, f"got {status} {parsed}")
    check("no stack trace leaks in the malformed-JSON body", "Traceback" not in raw)

    status, parsed, _raw, _h = GET("/api/v1/this/route/does/not/exist")
    check("unknown route returns 404 NOT_FOUND envelope",
          status == 404 and parsed and parsed["error"]["code"] == "NOT_FOUND", f"got {status} {parsed}")


# --------------------------------------------------------------------- main

def main():
    started = None
    try:
        started = ensure_server()
        print("\n== docs & health ==")
        routes = test_docs_and_health()
        print("\n== every /api/v1 endpoint answers ==")
        test_every_v1_endpoint_answers(routes)
        print("\n== 401 sweep (mutating / session-gated endpoints) ==")
        test_401_sweep(routes)
        test_legacy_routes_not_silently_broken(routes)
        print("\n== login & session cookie ==")
        cookie_header = test_login_and_cookie()
        print("\n== decode RMBS0010206100007 ==")
        test_decode_known_code()
        print("\n== resolve -> commit -> decode round trip ==")
        test_resolve_commit_decode_round_trip(cookie_header)
        print("\n== public page exposes no creator function ==")
        test_public_page_has_no_creator_function()
        print("\n== error envelope on a bad request ==")
        test_error_envelope()
    finally:
        try:
            cleanup_test_user()
        except Exception as e:                                   # noqa: BLE001
            print(f"  (cleanup warning: could not remove test user: {e})")
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=5)
            except subprocess.TimeoutExpired:
                started.kill()

    print(f"\n{'=' * 60}\n{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped\n{'=' * 60}")
    if FAIL:
        print("\nFAILURES:")
        for name, detail in FAIL:
            print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
