# AGENT B — AUTH & SETTINGS

**Start after Agent 0 reports done. Runs in parallel with A, C, D, G, H.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You are building the security boundary of this tool, and the Settings screen
where Anuraag will paste the LLM API key himself. Both matter more than their
size suggests: everyone else's "is this allowed" check calls your helper, and
until your Settings screen exists there is no way to turn the LLM on at all.

Keep it simple — five to ten creators. But note what changed on 6 August: the
server is now a **cloud VPS on the public internet**, not a machine on your LAN.
The old reasoning that "plaintext on our own network is acceptable" is dead.

* **Assume HTTPS** (Agent H provisions it). Set `Secure` on the session cookie
  alongside `HttpOnly` and `SameSite=Strict`.
* **Rate-limit login.** Your endpoint is reachable by anyone in the world now.
  Back off after ~5 failures from one IP, and lock an account briefly after
  ~10. Never reveal which of the two tripped.
* **Never log a password, token or key** — the logs are on a public host.

## What you own

`core/auth.py` · `routes/auth.py` · `web/login.html` · `web/settings.js` ·
`manage.py` (user commands only — Agent H owns the rest of it)

## Tasks

**1. Password storage and login (5 pts).** `hashlib.scrypt` with a per-user
random salt, both stored in `app_user`. Never plaintext, never recoverable.
Compare with `hmac.compare_digest`.

`POST /api/v1/auth/login {username, password}`. A wrong username and a wrong
password return **the same** message — otherwise the endpoint tells an attacker
who has an account.

**2. Sessions (3 pts).** `secrets.token_urlsafe(32)`, store only its hash in
`session`, hand the raw token out as a cookie: `HttpOnly`, `SameSite=Strict`,
`Path=/`, 12-hour expiry. `POST /auth/logout` deletes the row.
`GET /auth/me` returns the user or `null`.

Expiry must land the user back on the public page with a quiet "your session
ended" — never a blank screen and never a raw 401 page.

**3. The helper everyone else uses (3 pts).**

```python
def require_session(handler) -> str        # username, or raise AuthRequired
def current_user(handler) -> str | None
def require_admin(handler) -> str          # or raise Forbidden
```

`AuthRequired` becomes `401 AUTH_REQUIRED`, `Forbidden` becomes `403 FORBIDDEN`.
Document these in your handover note — six other agents import them.

**4. Attribution from the session (2 pts).** The codebase currently trusts an
`X-User` **request header** for attribution. Anyone could forge it. **Delete it
everywhere** — `grep -rn "X-User"` and remove every occurrence. Attribution comes
from the session or it does not happen.

This is a real hole, not a theoretical one. It is the highest-value thing in your
packet.

**5. Provisioning (3 pts).** Accounts are issued by an admin, never
self-registered. No sign-up page, no email reset, no OTP.

```
python manage.py adduser <username> --name "Full Name" [--admin]
python manage.py disable <username>
python manage.py resetpw <username>
python manage.py listusers
```

Generate a strong password, print it once, and say plainly that it must be handed
over directly. At least one active admin must always remain — refuse the command
that would remove the last one.

On first run, if `app_user` is empty, create an `admin` account, print the
password once and require it to be changed at first login.

**6. Change own password (2 pts).** `POST /auth/password {old, new}`. Old
required. Minimum 8 characters — do not build a policy engine.

**7. Settings screen — the API key (8 pts).** Admin only, behind `require_admin`.
`GET /api/v1/settings` and `POST /api/v1/settings`.

Editable there:

| Setting | Notes |
|---|---|
| `match.mode` | **`fuzzy`** or `llm` — a visible toggle |
| `llm.provider` | none · anthropic · gemini · openai · ollama |
| `llm.api_key` | **password field.** Never returned to the client — send `"••••••••"` when set, `""` when not |
| `llm.model` | free text with a sensible default per provider |
| `match.threshold` | default 60 |
| `erp.enabled`, `erp.dry_run`, `erp.base_url` | Agent G reads these |
| `sync.times` | default `09:00,17:00` |

**Anuraag pastes the key himself, into this screen.** Nothing is hardcoded and
nothing is committed. **Until a key is present, `match.mode` must be `fuzzy` and
the app must work perfectly well without any LLM.** Show this state plainly:
*"Fuzzy matching only — no API key set."* A "Test key" button that makes one
cheap call and reports success or the real error would save a lot of confusion.

Secrets go in the `settings` table. Never `config.json`, never git.

## Watch for

* Test the boundary by **calling a mutating endpoint directly with curl and no
  cookie**. It must be 401. Hiding a button is not security.
* Never log a password, a token or a key — not even at debug level.
* Do not put the key in a `GET` response "just for the settings form".

## Done when

Login works; a bad password is indistinguishable from a bad username; the cookie
is HttpOnly and expires; `require_session` is importable and documented;
`grep -rn "X-User"` returns nothing; `manage.py adduser` works; Settings saves an
API key that never comes back to the client; with no key set the app runs
fuzzy-only and says so.

## Then

Write `agents/done/AGENT_B.md` — especially the exact import path and signature
of the auth helpers, since six agents depend on them.
