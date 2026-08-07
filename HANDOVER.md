# Handover — Item Code Studio

Written by Agent H at the end of the 6 August 2026 parallel build (agents
0, A–G, H — see `agents/done/*.md` for each one's own notes). This document
is the single place to look for: what exists, what's thin or untested, what
Anuraag still owes, and how to restart / restore / add a user / rotate a key.

Read this before relying on the system for anything real. It is written to
be straight rather than reassuring — a handover that oversells is worse than
none, because it removes your chance to check.

---

## 1. What's actually here, in one paragraph

A working, tested, single-machine deployment (`ledger.mode: "local_server"`)
that does everything the product needs today: OCR/fuzzy/LLM-assisted item
coding, a public read-only decoder/directory, a login-gated create screen,
an editable master with version history and revert, ERPNext sync behind a
closed-list guardrail (currently off), and an admin CLI. On top of that,
the plumbing for the eventual three-tier VPS architecture is built and
independently tested but **not yet wired into a continuously running
process** — that gap, and exactly what closes it, is in §4 below.

## 2. How to restart it

```bash
cd C:\Users\Anura\ItemCodeStudio
python server.py
```

or double-click `run.bat`. Prints two URLs (localhost + LAN IP), the loaded
item/group/ERP counts, matching mode, ERPNext state, and ledger tier. `Ctrl+C`
to stop. If a previous process is still holding port 8756, `netstat -ano |
findstr 8756` (PowerShell) and stop it first — a stale listener silently
answering requests instead of a freshly started one is a real trap Agent 0
hit once during this build.

**First run**: if no accounts exist yet, one `admin` account is created
automatically and its password printed **once**, to the console only.

**A credential was originally printed here in plaintext** when this document
was first written — that was a mistake (a live password belongs on a
console, handed to a person directly, never in a persistent document). It
has been rotated and removed. If you don't already have the current admin
password, generate a fresh one yourself and hand it to whoever needs it:

```
python manage.py resetpw admin
```

The new password is printed once, to the console only, and must be changed
at first login (`POST /api/v1/auth/password`). Never paste a live credential
back into this file or any other document.

## 3. How to verify it's healthy

```bash
python tests/smoke.py
```

Standard library only, runs in a few seconds against an already-running
server (or starts and stops its own if none is up). 86 checks: every
`/api/v1` endpoint answers, every mutating/session-gated one refuses with
exactly 401 `AUTH_REQUIRED` with no cookie (checked endpoint by endpoint —
the single most valuable test in the suite), login and cookie flags, a known
decode, a full resolve→commit→decode round trip, the public page has no
creator function reachable, and the error envelope holds on a bad request.

It also leaves one clearly-named `"SMOKE TEST ITEM <tag>"` behind per run —
there's no delete-item API yet to clean it up. Harmless, documented, visible
by name. `python seed.py --rebuild` clears it along with everything else if
you want a pristine baseline again.

## 4. The three-tier architecture: what's real, what's wired, what isn't

```
TIER 1  cloud VPS           not provisioned — host/domain not yet confirmed
TIER 2  local LAN server    what's actually running today (mode: local_server)
TIER 3  desktop clients     installer built, "client" mode partially wired
```

**What's fully built and independently tested:**

* `core/tier.py` — `TierClient` (resolves tier1→tier2→offline with a ~1.5s
  timeout, re-checks in the background, calls an `on_change(old, new)`
  hook) and `DictCache` (read-only groups/specs/aliases/item-index snapshot
  for offline fuzzy matching, `data/dict_cache.json`, `python manage.py
  refresh-cache` to build it today with no VPS).
* `core.sync.tier_reconnect_hook(con)` (Agent D/G) — returns exactly the
  `on_change` callback `TierClient` expects, firing `reconcile_failsafe()`
  only on the `local_failsafe → connected` transition.
* **Proven together, live, this session**: two real `server.py` processes on
  separate databases (ports 8756/8760) simulating tier 1 and tier 2; a
  standalone `TierClient` watching tier 1 correctly flipped
  `connected → local_failsafe` within ~1.5s of tier 1 being killed, and
  `local_failsafe → connected` within ~1.5s of it coming back, firing the
  reconcile hook on reconnect (a real row landed in tier 2's `sync_log`).
  Both tiers down: `offline` in ~3s, not a hang.

**What is NOT done: the continuous wiring.** `server.py` always calls
`core.db.connect()` directly and never constructs a `TierClient`. That file
is Agent 0's exclusively (per `agents/README.md`'s file-ownership table), so
rather than reach into it, the exact patch is documented at the top of
`core/tier.py` and repeated here — three lines, additive only, right after
`ctx.init(...)`:

```python
from core.tier import TierClient
from core.sync import tier_reconnect_hook
ctx.tier = TierClient(CFG.get("ledger", {}), on_change=tier_reconnect_hook(CON))
ctx.tier.start()
```

Once that lands: `GET /api/v1/health`'s `tier` field automatically upgrades
from the static config-file mode to the live prober's status (the field
already reads `ctx.tier` when present — no second change needed), and the
interface can poll it to show the three states from the brief:

```
● connected            minting final codes
● local failsafe        VPS unreachable — codes are provisional
● offline               read-only. decode and search only.
```

**Also not wired: tier-to-tier authentication.** Agent G's
`v1_reconcile_upload`/`v1_reconcile_finalize` (in `routes/erp.py`) are
deliberately sessionless — a tier-2 server calls them, not an operator — and
their own docstrings name the gap: "the two sides authenticate at the
transport layer... whatever Agent H's tier wiring adds." That's
`core.tier.require_tier_secret`/`set_tier_secret` (new, built, not wired —
Agent G had already reported done by the time this landed, and editing
their file after the fact without coordinating felt worse than documenting
the two-line patch here):

```python
# top of v1_reconcile_upload and v1_reconcile_finalize in routes/erp.py:
from core.tier import require_tier_secret
require_tier_secret(req)
```

and `core/sync.py`'s `_vps_call` needs to send the same value in an
`X-Tier-Secret` header. Generate the shared value once, on the VPS, after
provisioning:

```bash
python -c "from core import db as D, tier as T; c=D.connect(); print(T.set_tier_secret(c))"
```

and put the same string in tier 2's `settings` table the same way.

**Client mode (tier 3) is also partial, honestly.** `install/install.ps1`
writes `config.json` with `ledger.mode: "client"` and a server URL, but
until the `server.py` patch above lands, a freshly installed copy still
opens its own local `core.db.connect()` rather than truly proxying — see
`install/install.ps1`'s header for why it deliberately does **not** ship
`data/itemcode.db`, so that failure mode is an obviously-empty database
rather than a look-alike duplicate one.

## 5. The API — frozen, documented, tested

Everything real lives under `/api/v1` — `{"ok": true, ...}` /
`{"ok": false, "error": {"code", "message", "detail"}}`, error codes exactly
as `agents/CONTRACTS.md` §6 lists. Reference, generated live from the actual
route table (cannot go stale relative to what's running):

```
GET /api/docs                (HTML)
GET /api/docs?format=json    (machine-readable — method, path, auth, an
                               example where one's been hand-supplied)
GET /api/v1/health           (liveness, counts, tier status)
```

**Older, un-versioned `/api/...` paths still work, unchanged, on purpose**
(Agent 0's call, so the current UI didn't need a simultaneous rewrite) — see
§7 below for why several of them are a real, not just theoretical, concern
now that a VPS is in the plan.

## 6. Installer, VPS deployment, backup

**Local install (Windows, no admin rights, no secret):**

```powershell
powershell -ExecutionPolicy Bypass -File install\install.ps1 -ServerUrl "https://items.example.com"
```

Tested this session: refuses cleanly with a plain message if Python is
missing; warns (doesn't fail) if Tesseract is missing; copies the app to
`%LOCALAPPDATA%\ItemCodeStudio`; writes a `config.json` with **zero
secrets** (verified by grepping the written file for `api_key`/`password`/
`secret` — all blank); creates Desktop + Start Menu shortcuts without admin
rights. See §4 for what "client mode" does and doesn't yet do.

**VPS deployment — `install/vps/`** (systemd unit, Caddyfile, `setup.sh`):
TLS is automatic via Caddy (cert + HTTP→HTTPS redirect + explicit HSTS
header), the app binds to `127.0.0.1` only, firewall allows just 22/443, SSH
hardening (key-only, no root login) is a manual last step by design — an
unattended lockout with nobody else able to log in is worse than one manual
step. Login rate limiting is already in the app (Agent B, per-IP and
per-account, `core/auth.py`) — the Caddyfile forwards the real client IP so
that stops degrading to one shared bucket the moment it's live.
**Not run against a real box.** `agents/README.md` lists "a VPS and a
domain" as not urgent and explicitly not blocking; `agents/CONTRACTS.md`
task 5 says to confirm host and domain with Anuraag before provisioning
anything, and neither exists yet. Everything in `install/vps/` is
vendor-neutral and ready the moment they do.

**Backup — `install/backup.py` / `python manage.py backup [daily|weekly|run]`:**

```bash
python install/backup.py run                            # daily snapshot + weekly export, verified
python install/backup.py restore-verify <path-to-a-daily-backup.db>
```

Uses SQLite's own online-backup API (`sqlite3.Connection.backup()`) — safe
against a live WAL-mode database being written to, unlike a raw file copy.
Writes into `config.json → backup.drive_folder`, a plain folder Google
Drive for Desktop already syncs — no OAuth, no credential anywhere in this
codebase. Keeps 14 dailies / 8 weeklies, prunes older.

**Restore was actually verified this session**, not just claimed: ran a
real backup of the live seeded database, restored it into a scratch folder,
opened it as an independent connection, confirmed non-zero group/item
counts came back. Output from that run:

```
daily backup written: ...\itemcode-20260807-094337.db (1572 KB)
weekly export written: ...\itemcode-2026-W32.xlsx
restored ...itemcode-20260807-094337.db -> ...itemcode-restored.db
counts: {'heads': 11, 'subheads': 46, 'groups': 890, 'items': 1951, 'erp_items': 2677}
restore looks healthy: groups and items are both non-zero.
```

**`backup.drive_folder` is currently empty** — set it to a real
Google-Drive-for-Desktop-synced path before relying on this; until then
nothing is actually being copied anywhere. If tier 2 (a dedicated office
machine) doesn't exist yet either, this machine can run the backup itself
in the meantime (`python manage.py backup run` on a schedule, e.g. Windows
Task Scheduler) — the same folder gets picked up automatically once a real
tier 2 is set up later. Said here explicitly rather than left silently
unbacked, per the brief's instruction.

## 7. The single biggest residual risk: some legacy routes are still open

`routes/create.py` and `routes/erp.py` kept their pre-existing, un-versioned
paths exactly as Agent 0 relocated them — same URL, same (unwrapped)
response shape — so the current UI (`web/app.js`) didn't need a simultaneous
rewrite. That was the right call for a LAN-only tool. **It stops being
obviously fine the moment this is reachable on the public internet**, because
these are still live and still completely unauthenticated:

```
POST /api/commit        POST /api/resolve         POST /api/resolve_batch
POST /api/ingest         POST /api/alias/add        POST /api/erp/pull
```

`tests/smoke.py` enumerates these every run (see "legacy unauthenticated
POST routes still live" in its output) so this is visible, not buried. This
agent did not add `require_session` to them — that's someone else's file,
and "never weaken/alter another agent's file without coordinating" cuts both
ways. **Recommendation, in order of preference**: (a) migrate `web/app.js`
onto the `/api/v1/*` equivalents (they already exist, side by side) and
delete the legacy paths entirely, or (b) if that's not feasible before the
VPS goes live, add `require_session` to just these six handlers as a
stopgap. Either way, do this before the VPS is public — a plain-HTTP LAN
tool being unauthenticated on these paths was always a smaller bet than a
public one being unauthenticated on them.

## 8. Other things worth your attention, stated plainly

* **`D.log()` doesn't call `commit()` itself** (Agent B's finding, still
  true). Every call site this agent touched commits explicitly right after;
  several other modules do too, but it's a shared, easy-to-reintroduce risk
  under real concurrent use — the durable fix is one line inside `D.log()`
  in `core/db.py` (Agent 0's file).
* **`Item Code Specification`'s slot-marking field name is a documented
  guess** (Agent G) — check it against ERPNext's real doctype metadata
  before `erp.populate_specs` is ever turned on.
* **`core/resolve.py::commit()` passes our own taxonomy name as ERPNext's
  `item_group`**, and ERPNext has ~23 groups to our 889 — every real push
  will currently be correctly refused until someone adds an operator-facing
  "map this to a real ERPNext group" choice to the create screen (Agent G's
  finding, not this agent's file).
* **The seed process has a known non-determinism** (Agent 0's finding):
  group/item counts can land on slightly different totals across
  consecutive `--rebuild` runs, most likely a Python string-hash-randomized
  iteration order somewhere in the fuzzy-dedup path. Not something this
  agent's packet touched.
* **This is not a git repository.** No version control exists for any of
  this work — noted as a gap, not something fixed here (initializing one
  wasn't this agent's call to make unilaterally).

## 9. Runbook

**Restart**: §2 above.

**Restore from backup**:
```bash
python install/backup.py restore-verify <path>\itemcode-YYYYMMDD-HHMMSS.db
# once satisfied, to actually restore in place (server must be stopped first):
copy <path>\itemcode-YYYYMMDD-HHMMSS.db data\itemcode.db
```

**Add a user**:
```bash
python manage.py adduser <username> --name "Full Name" [--admin]
```
Prints a one-time generated password — hand it over directly, it's not
shown again and not logged anywhere.

**Disable a user / reset a password**:
```bash
python manage.py disable <username>
python manage.py resetpw <username>
```

**Rotate the LLM or ERPNext key**: log in as an admin, open Settings
(`/settings.html`), paste the new key, save. Never edit `config.json` for
this — secrets live only in the `settings` table (`agents/CONTRACTS.md` §5).

**Check which tier is authoritative right now**:
```bash
python manage.py tier-status
```

**Rebuild the dictionary cache** (for offline fuzzy matching):
```bash
python manage.py refresh-cache
```

**Run a backup by hand**:
```bash
python manage.py backup run
```

## 10. Done-when, checked against this delivery

Everything under `/api/v1` answers in the documented shape — yes, verified
by `tests/smoke.py`. `/api/docs` is generated — yes. The full 401 sweep
passes — yes, endpoint by endpoint. The local server (tier 2) runs the
whole app today with no VPS — yes, this is what's actually running.
Killing tier 1 fails over to tier 2 within a couple of seconds and says
so — yes, proven with two real processes, not yet visible in the running
UI banner pending the `server.py` patch in §4. Killing both leaves the app
read-only with no way to mint — yes (`offline` status, ~3s). Bringing tier 1
back hands authority over and triggers reconciliation without a restart —
yes, proven the same way. The installer produces a working shortcut and
contains no secret — yes, verified. A backup lands in the Drive-synced
folder and a restore has actually been tried — the restore mechanism is
verified; the folder itself is empty until Anuraag names a real one (§6).
This document exists.
