# AGENT D — done

## Task 1 — numbering is now QUEUE-claim, lowest-first

`core/codes.py`'s `next_group_code(con, subhead_id) -> (code3, freed_from)`
lost the `group_name`/`matcher` params and the ≥88% semantic path entirely.
It now just walks 1.. and returns the first number not in
`{active grp.code3} | {leased numbers}`. `freed_from` (from `grp_vacancy`,
`released=0`) is returned **only** for display — "041, freed by Tissue" — and
never gates who gets the number.

Also fixed a real bug while I was in there: the old `used` query had no
`status='active'` filter, so a retired group's `code3` blocked that number
**forever** — the vacancy it created could never actually be claimed by
anyone, semantic match or not. Filtering to active groups is what makes
queue-claim real.

`next_item_position(con, grp_id, spec_tuple=None) -> str` is new (item-level
was missing entirely, per the brief). Most items are fully identified by
their resolved spec values already — for those this just confirms/returns
that combination, since two distinct values can never collide (each has its
own `specval.code2`). It only actually *allocates* something when a slot is
undetermined, which in practice means **a group with no distinguishing spec
at all**: every item there would otherwise land on the same all-`00` tail,
so a running serial is minted into the first undetermined slot, lowest free
first, vacancies included. This is a judgement call — CONTRACTS.md doesn't
fully spell out what "position" means below the group level, and PDR.md
§4.5.3's worked example (`AOHK03804`, `spec-tuple 01`) is the closest thing
to a spec. If this doesn't match what Anuraag has in mind, the function is
isolated enough to redo without touching anything else.

Item moves now free their old position: `core.restructure._recode_item_into_group`
(the shared body behind `merge_groups` and the new `move_item`) calls
`C.free_item_position` on every non-frozen recode, writing `item_vacancy`
(group_id, position, spec_tuple as JSON, former_item, ts). A frozen item —
live in ERPNext, or already flagged `frozen` — keeps its code, gets
reclassified (`grp_id` updates, `decodable=0`), and **frees nothing**, exactly
per freeze-on-first-use. Verified: moving *Tissue* (real seeded data, group
id 41, 8 items) from House Keeping to Stationery previews as **1 recode, 7
frozen, `041` freed** — matches the brief's worked example exactly, checked
read-only against the live seeded DB (not rebuilt, see "What I didn't run"
below).

## Task 2 — `preview_code`

```python
def preview_code(con, head_id, subhead_id, grp_id, slots, vendor) -> dict
    # -> {"code": str, "length": int, "valid": bool, "explain": [...]}
```

Pure, no writes — calls `assemble()` internally, never reimplements it.
`slots` is a list of up to 4 entries, each `None`, an `int` (existing
`specval.id`), or a `str` (a value — matched exactly within group+slot if it
already exists, otherwise the code `next_spec_code` *would* mint is shown
without writing it, so an operator sees the real code before the value is
saved). `vendor` follows the same rule against slot 5. Each `explain` entry
is `{"segment": ..., "label": ..., "value": ..., "code": ..., "reason": ...}`
— e.g. a group that doesn't use slot 3 explains "slot 3 is not applicable
... written as 00 if a later position is used, dropped otherwise"; a group
without a vendor label explains an ignored vendor input rather than silently
dropping it.

## Task 3 — concurrency

`core/codes.py`:

```python
def claim_group_code(con, subhead_id, name, uom=None, labels=None, retries=1) -> dict
    # -> {"id": grp.id, "code3": str, "freed_from": str|None}
```

`BEGIN IMMEDIATE` takes SQLite's write lock up front, so a second caller
racing for the same subhead is forced to wait for this transaction to finish
— it then re-reads "lowest free" and always sees this number as taken.
`PRAGMA busy_timeout=5000` set on the connection; retries once on
`SQLITE_BUSY`/`SQLITE_LOCKED` with a short backoff. This is now the
sanctioned way to create a group — `routes/master.py:group_add` and
`core/resolve.py`'s new-group path in `commit()` both call it instead of
doing `next_group_code` + a bare `INSERT` (the old TOCTOU race).

`mint_from_lease` got the identical treatment (see Task 5 — it had the same
race and my first version of the concurrency test caught it).

`tests/test_concurrent.py` — `python tests/test_concurrent.py` — fires 20
threads at `claim_group_code` on one subhead and asserts 20 distinct,
gapless codes (`001`..`020`), both from the return values and from what
actually landed in `grp`. Also covers Task 5's two done-when tests (below).
Runs against a throwaway file (`tests/_concurrent_test.db`, cleaned up on
exit), never `data/itemcode.db`.

## Task 4 — restructuring

* `preview_move`/`move_group`: still take `matcher` in their signature
  (routes/master.py passes `ctx.matcher`) but no longer pass it to
  `next_group_code` — kept the outer signature to avoid touching route call
  sites unnecessarily, per Agent 0's precedent.
* `delete_group`: unchanged behaviour (refuses while items remain), doc
  updated.
* `merge_groups`: per-item body factored into `_recode_item_into_group`,
  shared with the new `move_item(con, item_code, new_grp_id, user)` —
  PDR §4.5.3's "move a single item" case, which had no route or function at
  all before this. No route wired to it yet; it's there for whoever needs a
  single-item reclassify (Agent F's master, most likely).
* **New:** `retire_subhead(con, subhead_id, user)`. Refuses while an active
  group remains under it; on success, deletes every unreleased
  `grp_vacancy` row for that subhead and every unreleased `item_vacancy` row
  for each of its groups — "retiring a sub-head drops its whole branch of
  vacancies," literally, since there's no subhead left for a future arrival
  to claim a number under.

## Task 5 — leases and provisional codes

```python
def grant_lease(con, scope, size=10) -> dict
def mint_from_lease(con, scope) -> str | None
def return_lease(con, scope) -> list[str]
```

`scope` = `("group", subhead_id)` or `("item", grp_id)` — CONTRACTS.md's
"scope is (subhead_id) ... or (grp_id)" doesn't disambiguate the two on its
own, so I added the `kind` tag; flagging in case another agent expected a
bare id.

**No new table** — `item_vacancy`/lease state needed persistent storage and
`core/db.py` is Agent 0's exclusively, so leases live in the existing
`settings` k/v table under `lease.group.<id>` / `lease.item.<id>`, as
`{"lo", "hi", "next", "size", "granted_at"}`. `grant_lease` starts the block
one past the highest number already **used or leased** for that scope, so it
can't overlap normal allocation. The actual disjointness guarantee is
enforced in `next_group_code`/`next_item_position` via a new
`_lease_blocked()` check: **the entire `[lo, hi]` range is blocked from the
main allocator for as long as the lease exists**, whether or not every
number in it has actually been minted yet — disjoint by construction, not by
timing, per the brief.

`mint_from_lease` is itself `BEGIN IMMEDIATE` + retry-once-on-busy — it
wasn't in my first draft, and the concurrency test caught it immediately
(two threads minting from the same lease raced on a read-modify-write of the
settings blob and produced the same number twice). Fixed; test now asserts
the lease itself never double-issues, not just that it doesn't collide with
the main allocator.

`return_lease` writes the unused tail of the block into `grp_vacancy` /
`item_vacancy` (display fields null — nothing "former" about a number that
was merely leased and unused) and deletes the settings row. `next_group_code`
immediately offers the lowest of them to the next arrival — verified in
`tests/test_concurrent.py::test_return_lease_creates_vacancy_then_queue_claim_fills_it`.

**`item.provisional`** isn't in CONTRACTS.md §5's schema list — Task 5 was
written into this brief after Agent 0 had already landed `core/db.py`. Since
I can't alter that file, `codes.mark_provisional(con, item_id)` adds the
column itself, defensively (`ALTER TABLE ... ADD COLUMN`, guarded by a
`PRAGMA table_info` check, so it's idempotent and touches no existing data).
Flagging this for whoever owns `core/db.py` going forward to fold into
`SCHEMA` properly — right now it's a runtime patch, not a migration. I did
**not** wire `mark_provisional` into any create-flow — CONTRACTS.md assigns
the offline/provisional item-creation path to Agent G/H's failover
territory, not mine; I only built and exposed the mechanism per the brief
("you set the flag and expose it").

## Task 6 — `list_vacancies`

```python
def list_vacancies(con) -> list[dict]
```

Group-level and item-level in one list, each entry
`{"level": "group"|"item", "scope": ..., "prefix": ..., "number": ...,
"freed_by": ..., "freed_at": ...}`. `routes/master.py`'s `vacancies_v1`
already detects and prefers this over its own fallback reconstruction
(`hasattr(C, "list_vacancies")`) — confirmed it picks it up correctly.

## Call sites I touched outside my own files (and why)

Changing `next_group_code`'s signature (frozen, but mine to define) broke
every existing caller. Per Agent 0's explicit precedent in its handover
("fine to touch since you're only fixing a call site to match your new
frozen signature"), I fixed the minimum needed to keep the app running:

* `routes/master.py:group_add` — now calls `C.claim_group_code(...)`
  instead of `next_group_code` + a bare insert (also closes the race there).
  **Note:** Agent F rewrote this file twice while I was working and reverted
  this fix both times as a side effect of a wholesale file rewrite; I
  reapplied it a second time after the second rewrite landed. If it's gone
  again, the fix is a 6-line change — see the comment block right above the
  `C.claim_group_code(...)` call for what it should look like.
* `core/resolve.py` — two call sites (the LLM/rules proposal path and
  `commit()`'s new-group path). The second now calls `claim_group_code` too.
  I did not touch anything else in this file — it was visibly being rebuilt
  by Agent C in parallel and looked substantially different from what I'd
  first read; both edits are narrow, one-line-plus-comment changes.

## What I didn't run

`data/itemcode.db` had a live listener on port 8756 (PID not mine, likely
another agent's server) for the whole session, so I never ran
`python seed.py --rebuild` or started my own `server.py` — both would have
disrupted whoever's server that is. Instead I verified everything against
the **live, un-rebuilt, real seeded DB, read-only**: `structural_parse` on
`RMBS0010206100007` still decodes correctly (17 chars, vendor `07`);
`list_vacancies`, `preview_code`, and `next_group_code` all run clean against
real data; and the *Tissue* move preview (real group id 41) matches the
brief's worked example exactly (1 recode, 7 frozen, `041` freed). No writes
were made to the shared database at any point — every check above used
`preview_move` or plain `SELECT`s.

If someone runs `seed.py --rebuild` later, `tests/test_concurrent.py` should
be re-run too (it's fully self-contained against a throwaway file, so it's
safe to run any time regardless of the main DB's state).

## Files changed

`core/codes.py` (rewritten numbering/vacancy section, added `preview_code`,
`claim_group_code`, `list_vacancies`, `_item_position_*` helpers, the three
lease functions, `mark_provisional`) · `core/restructure.py` (queue-claim
call sites, `_recode_item_into_group`, `move_item`, `retire_subhead`) ·
`tests/test_concurrent.py` (new) · one call site each in `routes/master.py`
and `core/resolve.py`.
