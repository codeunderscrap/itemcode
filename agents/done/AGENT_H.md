# AGENT H — API, packaging & deployment — done

Full detail, verification steps and open items: **[HANDOVER.md](../../HANDOVER.md)**
(restart/restore/adduser/rotate-key runbook lives there, not repeated here).

## What was built

**`/api/v1` frozen + `/api/docs`.** `routes/meta.py`: `GET /api/v1/health`
(liveness + tier status) and `GET /api/docs` (HTML, or `?format=json`) —
generated live from the actual route table every request (method, path,
auth requirement via source introspection for `require_session`/
`require_admin`), never hand-maintained. Six other agents' `/api/v1/*`
routes were checked against this and all use `ok()`/`err()` correctly.

**`tests/smoke.py`** — stdlib only, 86 checks, ~2s against an already-running
server (up to ~15s if it has to start one itself), reuses a healthy server
or starts/stops its own, guards against the stale-listener trap Agent 0 hit.
Covers every done-when item literally: full 401 sweep (endpoint by endpoint,
driven off `/api/docs` so it never goes stale), login + cookie flags, decode
of `RMBS0010206100007`, a full resolve/preview → commit → decode round trip
with idempotency-replay check, public-page creator-function audit, and the
error envelope on a deliberately bad request. Creates and tears down its own
throwaway account; adds one clearly-named test item per run (documented, not
hidden — no delete-item API exists yet to remove it).

**Three-tier failover — `core/tier.py` (new).** `TierClient` (tier1→tier2→
offline resolution, ~1.5s timeout, background re-check, `on_change` hook)
and `DictCache` (read-only groups/specs/aliases/item-index snapshot to
`data/dict_cache.json`, refreshable from a live DB today or over HTTP later).
Matches the exact interface Agents D and G already coded against
(`STATUS_CONNECTED`/`STATUS_LOCAL_FAILSAFE`, `core.sync.tier_reconnect_hook`)
— confirmed by running two real server processes on separate databases
(ports 8756/8760) and watching a standalone `TierClient` flip
connected→local_failsafe→connected on the real process being killed and
restarted, firing `reconcile_failsafe()` on reconnect (wrote a real
`sync_log` row). **Not yet wired continuously into `server.py`** — that's a
documented 3-line patch (see `core/tier.py`'s docstring) to a file only
Agent 0 owns; `/api/v1/health` already reads `ctx.tier` when present and
degrades to the static config mode when not.

**Tier-to-tier auth — `core.tier.require_tier_secret`/`set_tier_secret`
(new).** Agent G's `v1_reconcile_upload`/`finalize` are deliberately
sessionless (server-to-server) and named the gap in their own docstring;
this is the shared-secret guard for it. **Not yet wired** — one import + one
line in each of those two handlers, plus the same header in
`core/sync.py`'s `_vps_call` — left undone rather than editing G's file
after they'd reported done without coordinating first.

**VPS deployment — `install/vps/`.** systemd unit (non-root, hardened,
restart-on-failure), Caddyfile (automatic HTTPS + HTTP→HTTPS redirect +
explicit HSTS, forwards real client IPs so Agent B's rate limiter stops
degrading to one shared bucket), `setup.sh` (non-root user, firewall
everything but 443, SSH key-only — manual on purpose, unattended lockout is
worse). **Not run against a real box** — no host/domain confirmed yet
(`agents/README.md`: "not urgent"); vendor-neutral, ready to run.

**Local installer — `install/install.ps1`.** Tested end to end: no admin
rights, no `pip install`, checks Python (fails clearly if absent) and
Tesseract (warns, continues), copies the app to `%LOCALAPPDATA%`, writes
`config.json` in client mode with **zero secrets** (verified by grep),
creates Desktop + Start Menu shortcuts. Deliberately does not ship
`data/itemcode.db` — see its header for why an empty DB is a safer failure
mode than a look-alike duplicate one, given client-mode wiring isn't live yet.

**Backup — `install/backup.py` + `manage.py backup`.** WAL-safe (SQLite's
own online-backup API, not a file copy), daily DB snapshot + weekly Excel
export into `config.json → backup.drive_folder` (a Google-Drive-for-Desktop
folder — no OAuth, no credential in this repo), prunes to 14/8. **Restore
was actually verified**: ran a real backup, restored it into a scratch
folder, opened it as an independent connection, confirmed non-zero
groups/items counts. `drive_folder` is empty until Anuraag names a real
synced path.

**`manage.py`** gained `tier-status`, `refresh-cache`, `backup` subcommands,
appended after Agent B's user commands without touching them.

## Integration run (all of A–G done, this session)

`seed.py --rebuild` → clean baseline (889/1947/2677) → `server.py` starts
clean → `tests/smoke.py` 86/86 → public page confirmed creator-free logged
out → `POST /api/v1/commit` with no cookie confirmed 401 → full login→issue→
edit→revert done live over the real API → group move confirmed queue-claim
(created two throwaway groups, moved one out, created a third, it claimed
the freed number, not the next sequential one; cleaned up after) → ERPNext
confirmed `enabled: false` → tier failover/reconciliation simulated with two
real processes as described above → both tiers down confirmed offline/
read-only in ~3s, not a hang.

## Straight talk — what's thin or left for Anuraag

1. **Legacy un-versioned mutating routes are still unauthenticated**:
   `POST /api/commit`, `/api/resolve`, `/api/resolve_batch`, `/api/ingest`,
   `/api/alias/add`, `/api/erp/pull`. Deliberate (Agent 0, so the old UI
   doesn't break) but a real exposure the moment this is on a public VPS —
   see HANDOVER.md for the recommendation.
2. **`core/tier.py`'s continuous wiring into `server.py`, and the tier-secret
   header into `routes/erp.py`/`core/sync.py`, are both written and tested
   standalone but not connected** — see above and HANDOVER.md for the exact
   patches.
3. **The VPS itself doesn't exist.** Host/domain need confirming with
   Anuraag before `install/vps/` is ever run for real.
4. `D.log()` not auto-committing (Agent B's finding) is still present;
   every call site I touched commits explicitly, but it's a shared risk.
