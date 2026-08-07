# AGENT 0 — FOUNDATION

**Run this FIRST and ALONE. Eight other agents start the moment you finish.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

You are laying the foundation for eight agents working in parallel on one
codebase. Your job is not features — it is making sure they never collide. Read
`agents/CONTRACTS.md` first; it is frozen and you must implement it exactly.

Item Code Studio is a working internal tool that mints item codes for MiniMines
from a positional grammar, checks them against live ERPNext, and reads invoices.
The engine already works. What is being added now is a public face, a login, an
editable master with version history, and local packaging.

## What you own — nobody else touches these

`core/db.py` · `server.py` · `seed.py` · `web/theme.css` · `web/assets/*`

## Tasks

**1. Schema (8 pts).** Add exactly the tables in `CONTRACTS.md` §5:
`app_user`, `session`, `item_version`, `item_vacancy`, `llm_cache`, `sync_log`.

Migration must be **idempotent and non-destructive** — `CREATE TABLE IF NOT
EXISTS` only. There is real seeded data in `data/itemcode.db` (889 groups, 1,947
items, 2,677 ERP codes) and losing it costs a re-seed. Run `python seed.py
--rebuild` afterwards and confirm the counts still match.

Add `db.get_setting(key, default=None)` and `db.set_setting(key, value)`.
Settings are the only home for secrets.

**2. Split `server.py` into route modules (8 pts).** This is the important one.
Right now every route lives in one file; eight agents editing it simultaneously
would be chaos.

Create `routes/` with `__init__.py`, `public.py`, `auth.py`, `create.py`,
`master.py`, `erp.py`, `meta.py`. Each exposes:

```python
ROUTES = [("GET", "/api/v1/decode", handler), ...]
```

`server.py` becomes a thin dispatcher: it imports each module, builds one table,
matches method + path, and serves static files from `web/`. It must also provide
the two response envelopes from `CONTRACTS.md` §6 as helpers
(`ok(payload)` / `err(code, message, detail=None)`) and catch every unhandled
exception into `INTERNAL` — **never leak a stack trace to the client**.

Move the existing handlers into whichever module owns them per §6. Keep them
working; do not redesign them. Anything under the old un-versioned `/api/...`
paths should keep responding for now so the current UI does not break.

**3. Settings store + `ledger` config (3 pts).** Add to `config.json`:

```json
"ledger": {
  "mode": "local_server",
  "server_url": "",
  "local_url": "",
  "lease_size": 10
}
```

Three modes (`CONTRACTS.md` §2): `server` is the cloud VPS, `local_server` is the
office failsafe machine, `client` is a desktop install. The first two open SQLite
directly; `client` proxies over HTTPS. **Agent H builds the failover between
them** — you need the config keys and a `db.connect()` that reads them and works
correctly when opening the file directly.

Default to `local_server`: **the VPS does not exist yet**, and everything must run
today without it.

The database lives on a **cloud VPS**, never on a desktop and never on a network
share (SQLite locking is unreliable over SMB and concurrent writers corrupt the
file). One server process, one writer — which is what SQLite is good at.

**4. Theme + assets (2 pts).** Create `web/theme.css` holding the MiniMines
palette as CSS variables — navy `#001b2e`, panel `#0f2e45`, teal `#04aed1`,
steel `#3b6e93`, border `#294962`, text `#eaf2f7`, dim `#9db4c4`, plus
`#27ae60` / `#f39c12` / `#e74c3c` for good, warning and bad. Barlow with a
Segoe UI fallback. Include a light variant under `[data-theme='light']`.

Copy `C:\Users\Anura\ATT_Platform\frontend\public\minimines-logo.svg` to
`web/assets/`. Do not redraw it.

**Do not** restyle the existing pages — Agent A owns the public face and will
build on your variables.

## Done when

* `python seed.py --rebuild` completes and the counts are unchanged
* `python server.py` starts and the existing app still loads and works
* Every new table exists and is empty
* `routes/` has all seven modules and `server.py` is under ~120 lines
* An unhandled exception in a handler returns `{"ok":false,...,"code":"INTERNAL"}`
  and never a traceback

## Then

Write `agents/done/AGENT_0.md`: the final table list, the route-module map with
which paths landed where, and anything the other agents must know. They are
waiting on you, so keep it short and precise.
