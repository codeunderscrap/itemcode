#!/usr/bin/env python
"""tests/test_mmos_sso.py — proves the MM OS SSO retrofit (MMOS-RETROFIT.md).

No live network anywhere in this file. The MM OS JWKS/token boundary is
faked entirely in-process: a real RSA-2048 keypair is generated locally
(pycryptodome, a test-only fixture dependency — not added to requirements.txt,
not used anywhere in the shipped app, which stays stdlib+requests only per
agents/CONTRACTS.md house rule 1), tokens are signed by hand to the exact
shape MM OS docs/03-api-contract.md specifies, and a FakeJWKS / FakeDenyList
stand in for the real HTTP calls `core/mmos_client` would otherwise make.

This is the LIVE copy (cu-itemcode). Its config.json ships with MM OS SSO
ENABLED (mmos.enabled=true) and pointed at the real MM OS URL, per the port
brief — so, unlike the source copy, "the shipped default" here is *on*.
Because of that this file does NOT import server.py (which would both start
the real deny-list poller against the live URL — a live network call — and
build a *configured* client, contradicting a fail-closed assertion). Instead:

  * the fail-closed posture is proven directly, with an explicitly
    unconfigured MMOSClient (enabled=false) and with ctx.mmos=None — the exact
    state any deployment that leaves mmos.enabled false still has;
  * the shipped config's wiring is proven by reproducing server.py's
    construction offline (no start_background(), so no poller/heartbeat
    network) and asserting it yields a CONFIGURED client;
  * a fully faked, configured client exercises the real route handlers
    end to end.

What this proves, in order:
  1. core.mmos_client._verify.verify_token accepts a well-formed token and
     rejects wrong-aud / expired / tampered / bad-sig / wrong-iss / alg=none /
     revoked tokens
  2. the shipped config.json builds a *configured* client (SSO is on for this
     deployment) and never reads the service key from config.json
  3. an unconfigured client (and ctx.mmos=None) fails closed: every MM OS
     request is refused outright, never falls through to a session
  4. end to end through the real route handlers (routes/mmos.py, unit-called
     directly, not over HTTP — no server process needed): a valid token mints
     a real Item Code Studio session via core.auth's own session table,
     indistinguishable from a password login; wrong-aud / expired / revoked
     tokens do not

Run directly:

    python tests/test_mmos_sso.py
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Crypto.PublicKey import RSA                                     # noqa: E402  test fixture only
from Crypto.Signature import pkcs1_15                                 # noqa: E402  test fixture only
from Crypto.Hash import SHA256                                        # noqa: E402  test fixture only

PASS, FAIL = [], []


def ok_(name):
    PASS.append(name)
    print(f"  ok    {name}")


def fail_(name, detail=""):
    FAIL.append((name, detail))
    print(f"  FAIL  {name}   {detail}")


def check(name, cond, detail=""):
    (ok_(name) if cond else fail_(name, detail))


# ------------------------------------------------------------- fake key pair

_KEY = RSA.generate(2048)
_KID = "mmos-test-2026-08"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwk():
    def enc(i):
        n_bytes = i.to_bytes((i.bit_length() + 7) // 8, "big")
        return _b64url(n_bytes)
    return {"kid": _KID, "kty": "RSA", "alg": "RS256", "use": "sig",
            "n": enc(_KEY.n), "e": enc(_KEY.e)}


def make_token(*, sub="mmos-user-1", aud="itemcode", iss="https://os.m-mines.test",
                roles=None, emp="MM1234", email="person@m-mines.com", name="Test Person",
                exp_delta=900, iat_delta=0, jti=None, alg="RS256", tamper_payload=False,
                bad_signature=False):
    header = {"alg": alg, "typ": "JWT", "kid": _KID}
    now = int(time.time())
    payload = {
        "sub": sub, "aud": aud, "iss": iss,
        "iat": now + iat_delta, "exp": now + exp_delta,
        "jti": jti or f"jti-{sub}-{now}",
        "emp": emp, "email": email, "name": name,
        "roles": roles if roles is not None else ["viewer"],
    }
    h_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h_b64}.{p_b64}".encode()
    sig = pkcs1_15.new(_KEY).sign(SHA256.new(signing_input))
    if tamper_payload:
        # flip the token AFTER signing - a valid signature over the wrong bytes
        p_b64 = _b64url(json.dumps({**payload, "roles": ["admin"]}, separators=(",", ":")).encode())
    if bad_signature:
        sig = bytes([sig[0] ^ 0xFF]) + sig[1:]
    s_b64 = _b64url(sig)
    return f"{h_b64}.{p_b64}.{s_b64}"


class FakeJWKS:
    """Stands in for core.mmos_client._jwks.JWKSCache — no HTTP call."""
    def __init__(self, jwk):
        self._jwk = jwk

    def get_key(self, kid):
        return self._jwk if kid == self._jwk["kid"] else None


class FakeDenyList:
    def __init__(self):
        self._subs = set()

    def is_revoked(self, *, sub=None, jti=None):
        return sub in self._subs

    def revoke(self, sub):
        self._subs.add(sub)


# ------------------------------------------------------- offline ctx fixture

def _init_ctx():
    """Bring up the shared context WITHOUT importing server.py — so no live
    deny-list poller ever fires at the real MM OS URL. Only ctx.con and
    ctx.cfg are needed by routes/mmos.py + core.auth; ctx.mmos is left None
    (the "not configured" state) and each test installs the client it wants."""
    from core.context import ctx
    from core import db as D
    con = D.connect()
    D.init(con)
    ctx.con = con
    ctx.cfg = {"tls": False}
    ctx.mmos = None
    return ctx


# ================================================================= 1: verify_token unit tests

def test_verify_unit():
    from core.mmos_client._verify import verify_token, TokenError

    jwks = FakeJWKS(_jwk())
    denylist = FakeDenyList()

    good = make_token()
    claims = verify_token(good, jwks_cache=jwks, issuer="https://os.m-mines.test",
                           audience="itemcode", skew_seconds=60, denylist=denylist)
    check("valid token verifies and returns claims", claims.get("sub") == "mmos-user-1")
    check("valid token: employee code present", claims.get("emp") == "MM1234")

    wrong_aud = make_token(aud="some-other-service")
    try:
        verify_token(wrong_aud, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("wrong-audience token is rejected", "no exception raised")
    except TokenError as e:
        check("wrong-audience token is rejected", e.reason == "bad_audience", e.reason)

    expired = make_token(exp_delta=-3600)
    try:
        verify_token(expired, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("expired token is rejected", "no exception raised")
    except TokenError as e:
        check("expired token is rejected", e.reason == "expired", e.reason)

    tampered = make_token(tamper_payload=True)
    try:
        verify_token(tampered, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("tampered payload (bad signature) is rejected", "no exception raised")
    except TokenError as e:
        check("tampered payload (bad signature) is rejected", e.reason == "bad_signature", e.reason)

    badsig = make_token(bad_signature=True)
    try:
        verify_token(badsig, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("corrupted signature bytes are rejected", "no exception raised")
    except TokenError as e:
        check("corrupted signature bytes are rejected", e.reason == "bad_signature", e.reason)

    wrong_iss = make_token(iss="https://not-mm-os.example")
    try:
        verify_token(wrong_iss, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("wrong issuer is rejected", "no exception raised")
    except TokenError as e:
        check("wrong issuer is rejected", e.reason == "bad_issuer", e.reason)

    alg_confusion = make_token(alg="none")
    try:
        verify_token(alg_confusion, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("alg=none token is rejected", "no exception raised")
    except TokenError as e:
        check("alg=none token is rejected", e.reason == "alg_not_allowed", e.reason)

    # revocation: a perfectly valid token, but the subject is on the deny-list
    revocable = make_token(sub="mmos-user-revoke-me")
    denylist.revoke("mmos-user-revoke-me")
    try:
        verify_token(revocable, jwks_cache=jwks, issuer="https://os.m-mines.test",
                     audience="itemcode", skew_seconds=60, denylist=denylist)
        fail_("revoked subject is rejected even with a valid signature", "no exception raised")
    except TokenError as e:
        check("revoked subject is rejected even with a valid signature", e.reason == "revoked", e.reason)


# ================================================================= 2: shipped config wires an ENABLED client

def test_shipped_config_constructs_configured_client():
    """server.py builds ctx.mmos from config.json's "mmos" block plus the
    MMOS_SERVICE_KEY env var. Reproduce exactly that construction here, OFFLINE
    (no start_background(), so no poller/heartbeat network at all), and prove
    this LIVE deployment's shipped config yields a CONFIGURED client — SSO is
    genuinely on, as the port brief requires — while the service key is never
    read from config.json."""
    from core.mmos_client import MMOSClient
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    m = cfg.get("mmos", {})

    check("shipped config.json has a mmos block", bool(m))
    check("shipped config.json: mmos.enabled is true", m.get("enabled") is True, m.get("enabled"))
    check("shipped config.json: slug is 'itemcode'", m.get("slug") == "itemcode", m.get("slug"))
    check("service key is never stored in config.json (env-only)",
          "service_key" not in m and "MMOS_SERVICE_KEY" not in m, list(m.keys()))

    client = MMOSClient(
        enabled=m.get("enabled", False),
        slug=m.get("slug", "itemcode"),
        os_url=m.get("os_url", ""),
        service_key=os.environ.get("MMOS_SERVICE_KEY", ""),
        issuer=m.get("issuer") or None,
        poll_after_seconds=m.get("poll_after_seconds", 60),
        clock_skew_seconds=m.get("clock_skew_seconds", 60),
        heartbeat_seconds=m.get("heartbeat_seconds", 300),
    )
    # NOTE: deliberately no client.start_background() — this stays fully offline.
    check("shipped config builds a CONFIGURED client (SSO is on for this deployment)",
          client.configured is True)
    check("configured client's audience is the service slug", client.slug == "itemcode", client.slug)


# ================================================================= 3: fail-closed posture

def test_fail_closed(ctx, R):
    """With MM OS unconfigured — enabled=false, OR a half-filled config, OR
    ctx.mmos=None — every MM OS request is refused outright. Proves "MM OS not
    configured" never silently grants access, independently of what the shipped
    config happens to be set to."""
    from core.mmos_client import MMOSClient
    from core.api import ApiError

    off = MMOSClient(enabled=False, slug="itemcode", os_url="", service_key="")
    check("enabled=false => client is not configured", off.configured is False)

    # "enabled alone is not enough": enabled=true but a missing os_url is still
    # not configured, so a half-filled config can never silently grant access.
    half = MMOSClient(enabled=True, slug="itemcode", os_url="", service_key="")
    check("enabled=true but no os_url => still not configured", half.configured is False)

    real = ctx.mmos

    class FakeReq:
        body = {"token": "irrelevant-because-unconfigured"}

    ctx.mmos = off
    try:
        try:
            R.session(FakeReq())
            fail_("POST session with MM OS unconfigured is refused", "no exception raised")
        except ApiError as e:
            check("POST session with MM OS unconfigured is refused", e.code == "FORBIDDEN", e.code)
        health = R.health(FakeReq())
        check("health reports configured:false when unconfigured", health["configured"] is False)

        # ctx.mmos=None is treated exactly the same as "not configured"
        ctx.mmos = None
        try:
            R.session(FakeReq())
            fail_("POST session with ctx.mmos None is refused", "no exception raised")
        except ApiError as e:
            check("POST session with ctx.mmos None is refused", e.code == "FORBIDDEN", e.code)
        health2 = R.health(FakeReq())
        check("health reports configured:false when ctx.mmos is None", health2["configured"] is False)
    finally:
        ctx.mmos = real


# ================================================================= 4: route-level, configured (fake boundary)

def test_configured_end_to_end(ctx, R):
    """Swap in an MMOSClient wired to the fake JWKS/deny-list from part 1 -
    exercises the exact production code path (MMOSClient.verify ->
    routes.mmos.session -> core.auth.create_session) with no live network."""
    from core.mmos_client import MMOSClient
    from core import auth as A
    from core import db as D
    from core.api import ApiError

    real_mmos = ctx.mmos   # restore after this test - other tests must still see "unconfigured"

    fake = MMOSClient(enabled=True, slug="itemcode", os_url="https://os.m-mines.test",
                       service_key="", issuer="https://os.m-mines.test")
    fake.configured = True
    fake._jwks = FakeJWKS(_jwk())
    fake._denylist = FakeDenyList()
    ctx.mmos = fake

    try:
        class FakeReq:
            def __init__(self, body):
                self.body = body

        # -- a fresh MM OS identity provisions a new local account and mints a session
        sub = f"mmos-e2e-{int(time.time())}"
        token = make_token(sub=sub, emp="MM9001", email="e2e.person@m-mines.com",
                            name="E2E Person", roles=["viewer"])
        status, body, ctype, headers = R.session(FakeReq({"token": token}))
        check("valid MM OS token -> 200 with a session cookie", status == 200 and "Set-Cookie" in headers,
              f"status={status} headers={headers}")
        check("valid MM OS token -> ok envelope with the new username", body.get("ok") is True)
        username = body.get("user", {}).get("username")
        check("provisioned username is present", bool(username), body)

        cookie_val = headers["Set-Cookie"].split(";")[0]
        check("cookie name matches core.auth.SESSION_COOKIE", cookie_val.startswith(A.SESSION_COOKIE + "="))

        row = D.one(ctx.con, "SELECT username, active, created_by FROM app_user WHERE username=?", (username,))
        check("a local app_user row was created for the MM OS identity", row is not None)
        check("provisioned account is active", bool(row and row["active"]))
        check("provisioned account is attributed to mmos-sso", row and row["created_by"] == "mmos-sso")

        link = D.one(ctx.con, "SELECT mmos_sub FROM mmos_link WHERE username=?", (username,))
        check("mmos_link row maps the local account back to the MM OS subject",
              link is not None and link["mmos_sub"] == sub)

        # the minted cookie is a REAL core.auth session - require_session must accept it
        token_val = cookie_val.split("=", 1)[1]

        class FakeCookieReq:
            headers = {"Cookie": f"{A.SESSION_COOKIE}={token_val}"}

        who = A.current_user(FakeCookieReq())
        check("the minted session is accepted by core.auth.current_user (same machinery as password login)",
              who == username, who)

        # -- same subject logging in again reuses the SAME local account (no duplicate)
        token2 = make_token(sub=sub, emp="MM9001", email="e2e.person@m-mines.com", roles=["viewer"])
        status2, body2, _ct2, _h2 = R.session(FakeReq({"token": token2}))
        check("repeat MM OS login for the same subject reuses the same local account",
              status2 == 200 and body2.get("user", {}).get("username") == username)
        n_rows = D.one(ctx.con, "SELECT COUNT(*) c FROM app_user WHERE username=?", (username,))["c"]
        check("no duplicate account was created on the repeat login", n_rows == 1, n_rows)

        # -- wrong audience: a token perfectly valid for a DIFFERENT service is refused
        other_aud_token = make_token(sub="mmos-other-service-user", aud="some-other-service")
        try:
            R.session(FakeReq({"token": other_aud_token}))
            fail_("a token minted for a different service (wrong aud) is refused", "no exception raised")
        except ApiError as e:
            check("a token minted for a different service (wrong aud) is refused",
                  e.code == "AUTH_REQUIRED", e.code)

        # -- expired token is refused
        expired_token = make_token(sub="mmos-expired-user", exp_delta=-120)
        try:
            R.session(FakeReq({"token": expired_token}))
            fail_("an expired MM OS token is refused", "no exception raised")
        except ApiError as e:
            check("an expired MM OS token is refused", e.code == "AUTH_REQUIRED", e.code)

        # -- revocation: access ends within the poll window (immediately here, since
        # the fake deny-list has no polling delay - the poller itself is proven
        # separately not to lose entries; this proves the CHECK is wired correctly)
        revoke_sub = f"mmos-revoke-{int(time.time())}"
        rev_token = make_token(sub=revoke_sub)
        status3, body3, _ct3, headers3 = R.session(FakeReq({"token": rev_token}))
        check("token for a not-yet-revoked subject is accepted", status3 == 200)

        ctx.mmos._denylist.revoke(revoke_sub)
        rev_token2 = make_token(sub=revoke_sub, jti="a-different-jti")
        try:
            R.session(FakeReq({"token": rev_token2}))
            fail_("a revoked subject's new token is refused - access ends", "no exception raised")
        except ApiError as e:
            check("a revoked subject's new token is refused - access ends",
                  e.code == "AUTH_REQUIRED" and "revoked" in e.message, e.message)

        # -- health now reports configured:true with the fake client installed
        health = R.health(FakeReq({}))
        check("health reports configured:true once ctx.mmos is configured", health["configured"] is True)

    finally:
        ctx.mmos = real_mmos   # leave the shared ctx exactly as other tests expect it


def main():
    print("== core.mmos_client._verify: offline token verification ==")
    test_verify_unit()

    ctx = _init_ctx()
    import routes.mmos as R

    print("\n== server.py wiring: shipped config.json builds an ENABLED client (offline) ==")
    test_shipped_config_constructs_configured_client()

    print("\n== routes/mmos.py: MM OS unconfigured / ctx.mmos None -> fails closed ==")
    test_fail_closed(ctx, R)

    print("\n== routes/mmos.py: MM OS configured against a faked JWKS/deny-list ==")
    test_configured_end_to_end(ctx, R)

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    print("=" * 60)
    if FAIL:
        for name, detail in FAIL:
            print(f"FAILED: {name} — {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
