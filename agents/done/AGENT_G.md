# AGENT G — ERPNext integration — done

Built against UAT (`https://minimines-uat.m.frappe.cloud`) with `erp.enabled`
defaulting off and `erp.dry_run: true` everywhere, per the brief. Nothing here
needs a code change to go live later — only `erp.enabled`/`erp.dry_run` in
Settings, and a real credential.

## Files

`core/erp.py` (rewritten), `core/sync.py` (new), `routes/erp.py` (v1 routes
added alongside the relocated `/api/erp/ping`+`/pull`),
`tests/test_erp_guardrail.py` (new).

## Task 1 — closed-list guardrail

`ALLOWED` in `core/erp.py` is the exact set from the brief. Every doctype
call funnels through `_resource()`, which calls `_gate()` first — nothing
reaches `_raw()` (the only function that opens a socket) without passing it.
`_raw()` itself only accepts `GET`/`POST`/`PUT`; there is no `DELETE`
handling anywhere, not even unreachable — `tests/test_erp_guardrail.py`
proves this two ways: calling `_raw("DELETE", ...)` raises, and an
AST walk of the whole file confirms the string `"DELETE"` never appears as
live code (only inside this docstring, which the walk deliberately excludes).

**Judgement call, flagged for review:** I restored `rename_item()`, which the
brief's literal `ALLOWED` list omits. `core/restructure.py` (Agent D, landed
mid-session) calls `erp.rename_item(old, new)` from `move_group()` — dropping
it would have broken a live file I don't own. `frappe.client.rename_doc` is
added to a separate `ALLOWED_METHOD_PATHS` set (only 3 entries: login,
get_logged_user, rename_doc), gated the same way, carries the same
provisional/dry_run guardrails as every other write, and matches the
restraints ERPNEXT_API.md §5.3.1 already specified (`merge: 0` always, called
only for a non-frozen code). Worth Anuraag's eyes since it's a deviation from
the brief's literal list, even though I believe it's the correct call.

Field whitelist: `WRITABLE_FIELDS` (update) and `CREATE_FIELDS` (create,
superset) are enforced in `_whitelist()` — anything else is dropped before
the payload is built, not merely ignored after.

Auth: `ERP.refresh(con)` reads `erp.api_key`/`erp.api_secret` from `settings`
live on every call (Settings can change between requests — Agent B's
screen). `config.json`'s `erpnext.username`/`.password` is the UAT fallback
only; token auth is preferred whenever both key and secret are present.

**Compatibility fix worth calling out:** the old `create_item()`/`rename_item()`
never raised — they always returned a dict, and `core/resolve.py::commit()` /
`core/restructure.py::move_group()` (Agent C/D's files) call them directly and
only ever check `.get("ok")`. My first pass had the new guardrail/validation
checks raise `ErpGuardrailError`/`ErpValidationError`, which would have
crashed those call sites into a generic 500 instead of a clean refusal. Fixed:
`create_item()`, `update_item()`, `rename_item()` catch those exceptions (and
a bare `RuntimeError` from a missing credential) internally and return
`{"ok": False, "error": ..., "reason": "guardrail"|"validation"|"not_configured"}`
— same contract as before, guardrail now included. Verified with a scratch-DB
smoke test (see below) that `create_item()` on a provisional code returns a
dict without ever raising or attempting a network call.

## Task 2 — validate before writing

`validate_uom()`, `validate_item_group()`, `validate_hsn()` all run inside
`create_item()`/`update_item()` before a payload is built. UoM and Item Group
are cached in-process for 5 minutes (240 and ~23 records respectively); HSN
is a direct per-value lookup (18,689 records is too many to usefully cache
wholesale). A missing/unknown Item Group is refused with an explicit message
citing decision #14 — we never invent one.

## Task 3 — create spec values on demand

`ensure_specification()` / `ensure_vendor()`, gated behind `erp.populate_specs`
(setting, default `False`). Both match on the **value**, not the code, per
the brief — UAT's `Item Code Vendor` has `Ather` and `Ather Energy` both coded
`11`, so matching on code would silently link the wrong record. Idempotent:
existing record found → linked and logged (`erp-ensure-*-hit`), not found →
created (dry-run prints the payload) and logged (`erp-ensure-*-create`).

**Flagged, not resolved:** the exact field name marking which of the four
slots an `Item Code Specification` record belongs to was never confirmed
against live doctype metadata — ERPNEXT_API.md only says "four checkboxes."
`slot_field = f"item_specification_{slot}"` in `ensure_specification()` is a
documented best guess. Check it against the real doctype meta before
`erp.populate_specs` is ever switched on for real.

## Task 4 — twice-daily sync

`core.sync.sync(con, direction="pull"|"push")`. Pulls, refreshes `erp_item`,
and detects three kinds of drift, every one surfaced in `sync_log.conflicts`
and never resolved automatically:

* `unknown_in_erp` — a code ERPNext has that we've never seen before (also
  dropped into `code_ledger` as `erp-only`, same as the old pull handler did)
* `missing_in_erp` — a code we believe is `in_erp` that no longer appears
* `field_conflict` — `item_name`/`stock_uom` disagree between our record and
  ERPNext's

`direction="push"` additionally attempts to create any local item that's
`confirmed`, not provisional, and never synced — skipping (and recording as
`push_skipped_exists_in_erp`) anything that already exists in ERPNext under
that code rather than overwriting it.

A background daemon thread in `routes/erp.py` (`_start_scheduler()`, started
at import time, after `ctx.init()` per Agent 0's import order) wakes at
`sync.times` (default `09:00,17:00`) and calls `sync(ctx.con, "pull")` under
`ctx.lock`. `core.sync.next_run_in_seconds()` is the pure, directly-testable
half of that. On-demand: `POST /api/v1/erp/sync`, `GET /api/v1/erp/sync/log`.

## Task 5 — reconcile the failsafe on reconnect

`core.sync.reconcile_failsafe(con)`: uploads everything minted locally while
offline, asks the VPS to finalise every provisional code, writes every
old→new pair to `code_mapping`, returns unused leased numbers (via Agent D's
`core.codes.return_lease()`, which turns them into vacancies), and re-leases
(`grant_lease()`) for the next outage. Never resolves a same-code-both-sides
conflict — appends it to `conflicts` and returns it.

Since "the VPS is just another instance of this exact codebase running in
`server` mode" (CONTRACTS.md §2), steps 1–2 are plain HTTP calls to that
instance's own `/api/v1/erp/reconcile/upload` and `/api/v1/erp/reconcile/finalize`
— which I also implemented as the **receiving** handlers in the same
`routes/erp.py`/`core/sync.py`, so whichever node ends up running as tier 1
already serves both ends of the exchange. `receive_finalize()` structurally
decodes a provisional code's head/sub/group prefix and re-runs
`next_item_position()`/`assemble()` (both tiers replicate the same taxonomy,
so the same prefix resolves to the same group on either side) to hand out
the real position under the queue-claim rule.

**No VPS is provisioned yet** (confirmed CONTRACTS.md §2), so
`reconcile_failsafe()` with `ledger.server_url` unset does the honest thing:
it still counts pending provisional items, still writes a clean `sync_log`
row, and reports there's nothing to reconcile against — verified this in the
smoke test below, so the function is exercised today and needs no code
change once a URL exists.

**Wiring gap for whoever owns it:** `core/tier.py`'s `TierClient` accepts an
`on_change(old_status, new_status)` callback that its own docstring names
*"Agent G's reconciliation hook."* I built it —
`core.sync.tier_reconnect_hook(con)` returns a callback that fires
`reconcile_failsafe()` exactly on `LOCAL_FAILSAFE → CONNECTED` and nothing
else — but nothing constructs `TierClient(..., on_change=...)` yet. That
instantiation site is server.py or wherever client-mode starts, neither of
which is a file I own (Agent 0 / Agent H respectively) — flagging rather
than editing.

## Provisional codes never reach ERPNext

`ERP._is_provisional()` checks an explicit `extra["provisional"]` flag and
falls back to looking up the item's own row (`item.provisional`, the column
Agent D's `core/codes.py` adds defensively via `_ensure_provisional_column()`
the first time it's needed — `core/erp.py` and `core/sync.py` both call that
same idempotent helper before querying the column, so it works whether or
not any item has ever been marked provisional yet). `_refuse_provisional()`
is called at the very top of `create_item()`, `update_item()` and
`rename_item()`, **before** any validation or network call — proven in the
smoke test: a provisional code is refused with zero login attempts, even
with no credentials configured (the "not configured" error only appears for
a *non*-provisional code).

## Task 6 — the guardrail test

`tests/test_erp_guardrail.py`, plain functions + `assert` (no pytest — house
rule 1), runnable directly:

```
python tests/test_erp_guardrail.py
```

Two parts:

1. **`test_local_gate_refuses_stock_entry_and_custom_field`** — runs for
   real, today, no credentials needed: proves `core/erp.py`'s own closed list
   refuses Stock Entry, Custom Field, Purchase Order, User, Sales Invoice,
   and a bare `DELETE`, before any request is built.
2. **`test_live_service_account_refused_by_erpnext`** — the actual live
   proof from ERPNEXT_API.md §5.4 (the dedicated service account attempts a
   write to Stock Entry and Custom Field, both must 403). **Skipped** — the
   `itemcode.studio@` service account doesn't exist yet. Reads
   `ITEMCODE_ERP_TEST_BASE_URL`/`_API_KEY`/`_API_SECRET` from the
   environment; once the account exists, set those and delete the skip
   guard, no other change needed.

Both verified: ran clean, 1 passed + 1 skipped, exactly as expected.

## Verification performed this session

No live UAT credential was available to me (config.json ships
`erpnext.password: ""`, and `settings` has no `erp.api_key`/`erp.api_secret`
row in the real database — confirmed by query). So:

* **Confirmed the UAT host is live and enforcing auth**: an unauthenticated
  `GET /api/method/frappe.auth.get_logged_user` came back `403
  PermissionError` (not a connection failure) — the endpoint is reachable and
  the guardrail on ERPNext's own side is doing something, but I could not
  drive an authenticated round trip this session.
* **Everything else was exercised against a throwaway copy of the real
  seeded database** (never `data/itemcode.db` itself — confirmed after: real
  DB's `sync_log` is still empty, `app_user` still has only the one bootstrap
  row, item/group counts unchanged): the full route table (72 routes across
  all six modules) builds and imports clean; `v1_ping`/`v1_status` behave;
  `sync()` and `reconcile_failsafe()` both produce clean skip results with
  ERPNext disabled / no VPS configured; the closed-list gate refuses `Stock
  Entry`; `create_item()` on a provisional code refuses before touching the
  network; `create_item()` with no credentials returns a clean dict rather
  than raising; and with validation stubbed to pass, `create_item()` in
  dry-run printed and returned the exact payload, with an unlisted field
  (`not_a_real_field`) confirmed dropped by the whitelist.
* All touched files byte-compile clean. `tests/test_erp_guardrail.py` and
  Agent D's `tests/test_concurrent.py` both still pass.

## Drift and gaps worth Anuraag's attention

1. **No live credential this session.** I could not exercise an authenticated
   write against UAT — only the guardrail/validation/dry-run logic, which
   doesn't need one. Before anyone trusts this against real UAT data, either
   `intern@m-mines.com`'s password or (preferably) an API key needs entering
   into Settings, and `tests/test_erp_guardrail.py`'s live half needs the
   dedicated `itemcode.studio@` account per ERPNEXT_API.md §5.4.
2. **`core/resolve.py::commit()` passes our own 889-group taxonomy name as
   ERPNext's `item_group`.** With the new `validate_item_group()` in place,
   this will refuse almost every real push — ERPNext only has ~23 groups and
   none of ours will match by name. This isn't something I can fix myself:
   rule 4 forbids inventing or pushing a mapping, and the right fix is an
   operator-facing choice (map our sub-head to a real ERPNext Item Group at
   creation time), which is Agent C/E/F territory, not mine. Before this
   ships, whoever owns the create screen needs to add that field — right now
   every push is a guaranteed, correctly-refused failure.
3. **`Item Code Specification`'s slot-marking field name is unconfirmed**
   (see task 3 above) — needs a look at the live doctype meta before
   `erp.populate_specs` goes on.
4. **`erp.populate_specs` has no Settings UI toggle yet** (Agent B's screen
   covers `erp.enabled`/`.dry_run`/`.base_url`/`sync.times` but not this one
   or the API key/secret pair, which Agent B's own note in `routes/auth.py`
   already reserves for me). Reads via `D.get_setting(con, "erp.populate_specs", False)`
   today — defaults off, works fine, just needs a form field somewhere.
5. **Nothing calls `update_item()` yet.** It's built, guardrailed, tested,
   and ready for Agent F's master editor to wire a "push this edit to
   ERPNext" action — `routes/erp.py` already exposes
   `POST /api/v1/erp/item/<code>/push` for the create-side equivalent.

## Done-when, checked off

`core/erp.py` cannot express a delete (proven structurally, not just by
inspection); the allowed list is enforced in code (`_gate()`, unbypassable
from `_resource()`); UoM, HSN and Item Group are validated before any write;
`dry_run` prints the exact payload and writes nothing (verified); `sync()`
reports drift without resolving it; a provisional item is refused by
`core/erp.py` itself, before any network call (verified); reconnecting after
an offline spell finalises provisional codes and reports every change
(`reconcile_failsafe()`, tested against the not-yet-provisioned-VPS case;
full round trip needs a second running instance to test against for real);
the guardrail test exists, one half runs today and passes, the other is
explicitly skipped with the reason stated.
