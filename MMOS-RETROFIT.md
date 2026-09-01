# MM OS retrofit — MM OS single sign-on for Item Code Studio (LIVE copy)

Additive only. Item Code Studio's own username/password auth
(`core/auth.py`: scrypt passwords, sessions, per-IP/per-account lockout,
forced password change, admin-provisioned accounts) is **untouched** and works
exactly as before. This retrofit only ADDS a second way to obtain the same
kind of session — one minted by MM OS on tile launch. This file is what
changed on THIS (live / `cu-itemcode`) copy, how to turn it on, and what still
needs the live VPS.

This copy was ported from a separately-built, tested retrofit on an older
copy of the same app. The two copies had diverged; the port re-applied the
retrofit onto this copy's actual files rather than overwriting them (see
"Divergences handled" below).

## Service slug

**`itemcode`** — the `mmos.slug` in `config.json` and the `aud` claim every
token must carry.

## What changed

| File | New / edited | What |
|---|---|---|
| `core/mmos_client/` | **new** (copied verbatim) | From-scratch, stdlib+`requests` MM OS SSO client: pure-Python RS256/JWKS verification (`_verify.py`, `_jwks.py`), deny-list polling (`_denylist.py`), optional heartbeat (`_heartbeat.py`), and `MMOSClient` (`client.py`). **Not** the FastAPI/httpx/jose reference client — this app is a stdlib `http.server` app whose only deps are `rapidfuzz`/`openpyxl`/`requests`/`pymupdf`. Verify order: `alg` pinned to RS256 first (blocks alg-confusion) → kid → JWKS → RS256 signature → `iss` → `aud` → `exp`/`iat` skew → deny-list. |
| `routes/mmos.py` | **new** (copied verbatim) | `GET /_mmos/accept` (the literal path MM OS's `launch_url` hardcodes), `POST /api/v1/mmos/session` (verify token → provision/find local account → mint a real `core.auth` session + cookie), `GET /api/v1/mmos/health` (drives the login button + registry probe). Fails closed: `ctx.mmos is None or not ctx.mmos.configured` refuses outright. |
| `tests/test_mmos_sso.py` | **new** (ported, adapted) | Offline SSO suite. See "Tests". |
| `config.json` | edited | New `"mmos"` block. `enabled: true`, `slug: "itemcode"`, `os_url`/`issuer` → `http://hrxd6lgu3h7qpnkbpy2mqgdc.200.234.36.153.sslip.io`. **No secret here** — the service key is read only from `MMOS_SERVICE_KEY`. |
| `core/context.py` | edited | `ctx.mmos = None` added to the constructor + a docstring note. `ctx.init(...)`'s signature is unchanged. |
| `server.py` | edited | Constructs `ctx.mmos = MMOSClient(...)` from the `mmos` block + `MMOS_SERVICE_KEY`, right after `ctx.init(...)`; calls `ctx.mmos.start_background()`; imports and mounts `routes/mmos.py` alongside the existing six route modules. |
| `core/dispatch.py` | edited | For a `GET`, the route table is now checked **first**, falling back to static-file serving only if nothing matched (previously any non-`/api/` GET skipped the router). Required so `/_mmos/accept` can exist. This copy's per-request `finally: rollback` guard is preserved. |
| `web/login.html` | edited | A "Sign in with MM OS" affordance, hidden until `GET /api/v1/mmos/health` reports `configured:true`. Links to `os_url`; the token is minted by MM OS on tile launch, not here. |
| `.gitignore` | edited | Added `.venv/` (test venv, not for commit). |

No other file was touched.

## Divergences handled (this copy vs the source copy)

The port re-applied edits onto this copy's real files, which differed:

- **`core/auth.py`** differs from the source only in `test_llm_key` (this copy
  ships a single `bharatrouter` LLM provider; the source had anthropic/openai/
  gemini/ollama/grok/groq). The **session/cookie machinery is byte-identical**:
  `create_session`, `cookie_header`, `hash_password`, and the `app_user`
  schema are the same, so `routes/mmos.py` maps onto this copy's own auth with
  no changes — MM OS identity → `core.auth.create_session(...)` → the same
  `Set-Cookie` a password login sets. `require_session`/`require_admin`/
  `current_user` needed zero changes to accept the minted session.
- **`server.py`** here builds ERP as `ERP(...).refresh(CON)` (the source did
  not call `.refresh`); the mmos wiring was inserted around that, not over it.
- **`core/dispatch.py`** here wraps each handler in a
  `finally: if ctx.con.in_transaction: ctx.con.rollback()` guard that the
  source lacked; the router-first change was applied while keeping that guard.
- **`web/login.html`** here was otherwise identical; the mmos affordance +
  health probe were spliced in.

## How the local account mapping works

A new table `mmos_link(username, mmos_sub, employee_code, email, linked_at)`
is created lazily (`CREATE TABLE IF NOT EXISTS`) by `routes/mmos.py` — the
`app_user` schema in `core/db.py` is not touched. On a valid MM OS token:

1. Look up `mmos_link` by the token's `sub`.
2. Found + linked account active → sync `display_name`/`is_admin` (from the
   `"admin"` role claim) and reuse it.
3. Found but the account was disabled locally by an admin → **refused**
   (`FORBIDDEN`). MM OS sign-in never reactivates a locally-disabled account.
4. Not found → provision a new `app_user` row (`created_by: "mmos-sso"`) with a
   random, never-disclosed password (password login stays impossible for that
   account — MM OS is the only way in), and link it.

The minted session is an ordinary `core.auth.create_session(...)` row with the
ordinary `core.auth.cookie_header(...)` cookie.

## Enabling / disabling

Shipped **enabled** on this copy (`config.json` `mmos.enabled: true`). To turn
SSO off for a deployment, set `enabled: false` — the tool then behaves exactly
as before (only username/password login works, no MM OS route grants a
session). `MMOSClient.configured` is `False` unless `enabled` is true **and**
`slug`/`os_url` are both non-empty, so a half-filled config never silently
grants access.

### Environment variables

- **`MMOS_SERVICE_KEY`** — the only env var needed. The MM OS service key
  (`mmk_...`), read only from the environment, never from `config.json`.
  Without it the client still verifies tokens (JWKS is public) but cannot poll
  the deny-list or heartbeat — so set it in the deployment environment
  (Coolify env / systemd `EnvironmentFile`). `slug`, `os_url`, and `issuer`
  live in `config.json` and need no env var.

## Live-phase steps (need the real VPS — not run in this pass)

No live network was used to *configure* anything in this pass. To finish
registration against the live MM OS at the `os_url` above, as a platform admin:

1. `POST {os_url}/api/admin/services` with `{"slug":"itemcode","name":"Item Code Studio","base_url":"<this deployment's public URL>","launch_mode":"handoff", ...}`.
2. `POST {os_url}/api/admin/services/itemcode/roles` for `viewer` (default) and `admin` (`routes/mmos.py` maps the `admin` role claim to `is_admin=1`; anything else is a normal account).
3. `POST {os_url}/api/admin/services/itemcode/rotate-key` → copy the `mmk_...` (shown once) into `MMOS_SERVICE_KEY` in this deployment's environment, then restart.
4. `POST {os_url}/api/admin/grants` to grant people the `itemcode` service.
5. Serve over HTTPS in production (`config.json` `tls: true` so the session cookie is `Secure`); a real MM OS token should not cross plain HTTP on the public internet.

## Tests

`pycryptodome` is a **test-only** fixture dep (throwaway RSA-2048 keypair to
sign test tokens) — not in `requirements.txt`, not used by the shipped app,
whose verification code (`core/mmos_client/_verify.py`) is pure
`hashlib`/`hmac`/big-int, no crypto library.

Environment: `python -m venv .venv`; `.venv/…/pip install -r requirements.txt`
plus `pycryptodome`.

- **`python tests/smoke.py`** — the existing suite, run BEFORE and AFTER the
  retrofit. Baseline (before): **93 passed / 3 failed**; after: comparable
  (runs observed 91–94 passed). **Every** failure in every run was pre-existing
  and unrelated to the retrofit: `GET /api/v1/erp/*` timing out against the
  live ERPNext cloud (a slow network call made while holding the global
  request lock — the count fluctuates run to run with network latency), plus
  one seed-data decode mismatch (`vendor 07` decodes to `(unknown value)`
  rather than `LG` in this copy's shipped DB). No auth / dispatch / static /
  404 / 401-sweep / login assertion ever failed — the retrofit's dispatch and
  route changes are clean.
- **`python tests/test_mmos_sso.py`** → **37 passed / 0 failed**, no live
  network (a fake JWKS + fake deny-list stand in for MM OS's HTTP endpoints):
  valid token → claims; wrong-aud / expired / tampered / bad-sig / wrong-iss /
  `alg:none` / revoked → rejected; the shipped `config.json` builds a
  *configured* client offline (proving SSO is genuinely on) and never reads
  the service key from config; an unconfigured client **and** `ctx.mmos=None`
  fail closed; and end-to-end through the real `routes/mmos.py`: a valid token
  provisions a local `app_user`, links it, mints a cookie `core.auth.
  current_user` accepts, and a second login reuses the same account.

### Adaptation note (why the fail-closed test differs from the source)

The source copy's `test_unconfigured_fails_closed` imported `server` and
asserted `ctx.mmos.configured is False` — valid only while the shipped config
had SSO *disabled*. This live copy ships SSO **enabled** (per the brief), so
that assertion no longer holds, and importing `server` would also fire the
real deny-list poller at the live URL (a live network call the tests must
avoid). The ported test therefore proves fail-closed **directly** — with an
explicitly unconfigured `MMOSClient(enabled=False)`, a half-filled config, and
`ctx.mmos=None` — and separately proves the shipped enabled config wires a
configured client by reproducing `server.py`'s construction **offline** (no
`start_background()`). Same security guarantees, no live network, compatible
with SSO being on.

## Note for whoever commits this

`data/itemcode.db` (a committed file) was modified only by the **required**
`tests/smoke.py` runs, which add clearly-named `SMOKE TEST …` ledger rows
(documented smoke behaviour, cleared by `seed.py --rebuild`). The SSO test's
own `app_user`/`mmos_link` rows were cleaned up. The retrofit itself needs
**no** DB change — `mmos_link` is created at runtime. Recommend restoring the
DB to HEAD (`git checkout -- data/itemcode.db`) or reseeding before deploying,
so no test rows ship. (Per the port brief, no git was run by the port itself.)

## What was NOT done

- No live network call to configure/register anything; the live-phase steps
  above are unrun.
- No `embed.js` OS bar (cosmetic, independent of auth).
- The reference `mmos-client-py` was not vendored — a from-scratch
  reimplementation of the same wire contract, to avoid adding FastAPI/httpx/
  jose to a stdlib+requests app.
- Existing auth, sessions, lockout, must-change-password, ERPNext sync, and
  every pre-existing route are unmodified.
