# AGENT F — done

## What was built

**`core/versions.py`** (new). Snapshot-based history, per CONTRACTS.md §5's
`item_version` table:

```python
snapshot(con, item_id, user, summary="") -> version_no
ensure_baseline(con, item_id, user="system") -> bool   # lazy "version 1" for pre-existing rows
versions(con, item_id) -> list[dict]                    # newest first
timeline(con, item_id) -> list[dict]                    # versions(), each with .diff vs the one before
diff_fields(before, after) -> list[{field, before, after}]
revert(con, item_id, version_no, user) -> dict
```

`snapshot()` always writes the *whole* current row (`SELECT * FROM item`,
`json.dumps`), never a diff — a diff chain breaks completely the moment one
link is wrong; a snapshot doesn't have that failure mode. Diffs exist only
for display (`diff_fields`, `timeline`).

`ensure_baseline()` solves the "version 1 is the original" problem for the
1,947 items that predate this feature: rather than a slow migration
touching every row, the first time any item is edited or its history is
opened, its *current* state is captured as version 1. That's the best
available "original" for rows with no earlier record.

**`routes/master.py`** — rebuilt. Kept every pre-existing un-versioned
`/api/...` path exactly as Agent 0 relocated it (same URL, same response
shape — `web/app.js`'s Create/Dictionary/Decoder tabs never had to change),
and added the real `/api/v1` surface:

```
GET  /api/v1/item                    paginated + filtered (q, status, head_id,
                                      subhead_id, group_id, undecodable) — server-side,
                                      never loads all 1,947+ rows
GET  /api/v1/item/<code>             full detail incl. resolved spec values,
                                      classification, frozen_effective, version_count
POST /api/v1/item/<code>/update      field edit — see "field edits never
                                      change the code" below
GET  /api/v1/versions?code=...       full timeline with diffs
POST /api/v1/revert                  {code, version_no} -> see "revert" below
GET  /api/v1/audit                   merged, diffed, filterable activity feed
GET  /api/v1/vacancies               group- and item-level, "next free number"
GET  /api/v1/export, /download/<file>
```

Every handler in this file — legacy and v1, reads and writes — calls
`core.auth.require_session`. The whole editable master sits behind login
(Agent A's `routes/public.py` is the separate, actually-public read-only
directory); hiding a nav button was never the boundary, this call is.

**Field edits never change the code — enforced in the API.** A shared
`_apply_field_edit()` helper (used by both the legacy and v1 update
handlers) raises `ApiError("FROZEN", ...)` outright if the payload contains
`code`, `grp_id`, `s1..s4`, `vend`, or any server-owned column
(`frozen`, `decodable`, `erp_synced_at`, `created_at`, `created_by`,
`name_norm`, `id`). Verified with a live curl call — sending
`{"code": "...", "grp_id": 99}` returns `FROZEN` with the offending field
names, not a silently-ignored write.

**Every write to `item` is versioned.** `_apply_field_edit` calls
`V.ensure_baseline` then `V.snapshot` in the same transaction as the
`UPDATE`. Confirmed live: editing `description`+`hsn` on a real seeded item
returned `"version": 2"`, and `GET /api/v1/versions` showed version 1
(baseline, auto-created) and version 2 with the exact field diff.

**Revert (`core.versions.revert`) — the frozen-code interaction.** Verified
with a real frozen (`in_erp`) item:

- Reverting to a version whose snapshot differs only in ordinary fields
  restores them all cleanly and reports the exact count.
- Reverting to a version whose snapshot carries a **different `code`**
  (simulated for the test — see "Anything thin" below for how) restores
  every other field and explicitly refuses the code, returning:
  `restored_fields: ["description"], skipped_frozen: ["code"]`,
  `message: "Restored 1 field. The code AOHK0010101 was not changed — it
  is live in ERPNext and is permanent."` — matching the contract's example
  message almost verbatim.
- Revert never deletes or rewrites later versions. Reverting to version 1
  three times in testing produced versions 3, 5 and 6 — history only grew,
  never shrank, and each revert was itself revertible.
- A genuine bug caught during this testing: the first draft of `revert()`
  compared *every* column between the target snapshot and the current row,
  including `updated_at` — since that timestamp is always different, it
  was silently counted as a "restored field" and even (briefly, before
  being overwritten two lines later) written backwards. Fixed by adding
  `updated_at` to `_NEVER_RESTORE` alongside `id`/`created_at`/
  `created_by`. Worth flagging because it's exactly the kind of silent
  partial/over-claimed revert the brief warns about, just in the other
  direction (over-counting rather than under-restoring) — caught only
  because I ran a real revert against a real row and read the field count,
  not by inspection.

**Activity with diffs (`activity_v1` / `GET /api/v1/audit`).** Merges two
sources into one feed, sorted by timestamp:
- `item_version` rows — real field-level before/after (via `diff_fields`
  against the previous `version_no` for that item), `revertable: true`,
  carries `version_no` so the UI's Revert button targets it directly.
- `audit` rows for everything else (group moves/merges, new spec values,
  exports, …), with `action IN ('edit-item','revert-item')` excluded since
  those are already represented — and better represented, with real diffs
  — on the `item_version` side. `matched_by` is read from
  `detail.matched_by` when present, so a request built with
  `?code=RMBS...` shows how often the LLM decided versus the rules for
  that item's history, once Agent E's commit path starts stamping it
  (see "Depends on" below).
- Filters: `user`, `code`, `from`/`to` (ISO timestamps), `limit`/`offset`.
  Verified live with `?code=AOHK0010101` — returned the full edit/revert
  history for that one item with correct diffs.

**Vacancy visibility (`vacancies_v1`).** Calls `core.codes.list_vacancies`
(Agent D) directly — it landed mid-packet with exactly the frozen shape
(`level`, `scope`, `prefix`, `number`, `freed_by`, `freed_at`, already
identical for group- and item-level, no branching needed). A defensive
local fallback (`_vacancies_fallback`, reading `grp_vacancy`/
`item_vacancy` directly) stays in the file only in case that function is
ever missing — I updated `web/master.js` to the real field names once I
saw the landed shape rather than the ones I'd guessed while blocked on it.
Verified end-to-end: created a throwaway group, deleted it, and watched
the vacancy show up correctly as `{"level":"group","number":"047",
"freed_by":"AgentF Vacancy Test Group", ...}` — "next free number", never
"reserved for", per the queue-claim rule (CONTRACTS.md §4).

**`web/master.js`** (new) — the item master, edit modal (with a History
tab and per-version Revert), and the rebuilt Activity screen (filters,
diffed feed, Revert per row, re-issued codes, next-free-number tables at
both levels). Cascading filters (head → sub-head → group, each clearing
the ones below it) reuse Agent A's existing `/api/groups?sub_id=` — this
screen filters the taxonomy, it never re-derives or mutates it.

**Never builds a code string anywhere.** `routes/master.py` and
`core/versions.py` never touch `assemble()`/`preview_code()` and never
concatenate a code by hand; the item edit surface only ever writes to
non-code-derived columns, so there was no code-preview surface needed on
this screen at all.

## A judgement call on "cascading dropdowns" (task 1)

The brief's task 1 says the master needs "the same cascading dropdowns as
the create screen" in the same sentence as "field edits never change the
code." Taken literally, a head→sub-head→group→spec-labels cascade *editing*
an item would let someone reclassify it — which is exactly what changes the
code, contradicting the very next sentence. I read this as: the cascade
belongs to the master's **filter toolbar** (head → sub-head → group,
narrowing the item list, spec labels shown as informational columns), not
to the per-item edit form. The edit modal only ever offers
name/description/UoM/alt UoM/HSN/tax/status — reclassification (which does
legitimately need to recode items) already exists as the group move/merge
routes Agent 0 relocated into this same file, and stays there, separate
from field-level editing. If Anuraag meant something more literal, the
cascade UI pieces already exist (`populateSubOptions`/`populateGroupOptions`
in `web/master.js`) and would need to move into the edit modal instead of
the toolbar — a small relocation, not a rebuild.

## Anything thin

- **Browser UI wasn't visually exercised.** Every endpoint was verified
  with real `curl` calls against the live seeded DB (list, detail, update,
  blocked-field rejection, versions, revert — including the frozen-code
  case — activity feed, vacancies, export), but I couldn't drive an actual
  Chrome session through a login: `core.auth.cookie_header()` sets the
  session cookie `Secure`, and this test server runs over plain
  `http://localhost:8756`. A real browser silently refuses to store a
  `Secure` cookie over an insecure origin, so the login flow can't
  round-trip in Chrome until either the dev server is fronted with TLS or
  `Secure` is made conditional on the request scheme. Not my file to fix
  (`core/auth.py`, Agent B) but worth flagging for whoever sets up local
  HTTPS or a dev exception — I got around it for testing only by inserting
  a `session` row directly with a known token and passing it as a `Cookie`
  header via curl, which bypasses browser cookie policy entirely.
- **The frozen-code revert path was tested with a fabricated fixture, not
  organic data.** The seeded DB has no item that has ever actually had two
  different codes across its version history (nothing has moved/merged in
  a way that both changed the code *and* left a version record yet, since
  this feature is brand new). I proved the guard by directly editing
  version 1's stored snapshot to carry a different `code` value, then
  reverting — confirmed the live code was untouched and the message named
  it exactly. I reverted that fixture back to the true original afterwards
  (see below) so the shared DB's history stays honest. The logic path
  itself is exercised for real by an in-place field edit + revert either
  way; only the *frozen* half needed a fixture to trigger, since nothing
  else in the running system currently produces a version with a stale
  code.
- **`matched_by` extraction is speculative.** It reads `detail.matched_by`
  as a flat top-level key from `audit.detail`. I haven't seen Agent E's
  actual commit-logging shape yet (their packet started after mine), so if
  it nests `matched_by` somewhere else, the field will just read as absent
  rather than error — worth a one-line fix in `activity_v1` once
  `routes/create.py` lands its logging.
- **Item-level vacancies are real but currently always empty** in the
  seeded DB — nothing has moved an item between groups yet with the new
  numbering engine, so `item_vacancy` has no rows to show. The endpoint and
  UI table are both wired and tested against the (populated) group-level
  case; the item-level table will start showing rows the moment something
  exercises that path.

## Left the shared DB as I found it (mostly)

Testing added real, honest version/audit history to one seeded item
(`AOHK0010101`) and one throwaway retired group ("AgentF Vacancy Test
Group", now `status='retired'`, invisible to every `status='active'`
query including Agent A's public directory). I did **not** run
`python seed.py --rebuild` — other agents are working against this same
live database right now, and a full rebuild would have discarded their
in-progress accounts/sessions/settings along with my test data. Instead I
surgically reverted the one fabricated fixture (the fake old-code snapshot
on version 1) and restored the item's `description` to its seeded value
through the real `versions.snapshot()`/`db.log()` path — so that
correction is itself an honest, visible version (v7) rather than a silent
rewrite. Final state: `python server.py` starts clean, no stray listener
left on 8756.
