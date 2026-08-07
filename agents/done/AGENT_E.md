# AGENT E — done

## What I built

**`routes/create.py`** — kept the relocated legacy handlers untouched
(`/api/resolve`, `/api/resolve_batch`, `/api/commit`, `/api/ingest`,
`/api/alias/add`) and added the real `/api/v1` surface alongside them:

* `POST /api/v1/resolve`, `/resolve_batch` — wrap `core.resolve.resolve()`,
  add `outcome` (`exists`|`new`|`needs_input`) and `matched_by`
  (`exact`|`rules`|`llm`|`operator`) on top of whatever `resolve()` itself
  returns, per CONTRACTS.md §7 — see "Contract vs reality" below for why
  this lives here rather than in `resolve()`.
* `POST /api/v1/resolve/preview` — deterministic recompute for the
  cascading-dropdown editor. Given a `group_id` and a specval id (or
  `"new:<text>"`) per slot/vendor, it resolves each to a two-digit segment
  and calls `core.codes.assemble` directly — no matching, no guessing. This
  is what makes live editing possible without a second, drifting
  implementation of the code grammar in JavaScript.
* `POST /api/v1/ingest`, `POST /api/v1/commit` — enveloped, behind
  `require_session`. Commit requires an `idempotency_key`; a replay of the
  same key returns the original `{code, item_id, erp}` instead of minting a
  second item. The key → result mapping is stored in the existing `audit`
  table (`action='commit-idem'`, `target=key`) rather than a new table,
  since Agent 0 owns `core/db.py`'s schema exclusively and nothing here
  needed one — this also means a replay survives a server restart, unlike a
  pure in-memory cache.
* `POST /api/v1/alias/add` — same as the legacy handler, enveloped.
* `GET /api/v1/cascade/heads|subheads|groups|slots` — read-only, each
  filtered strictly to its parent (a sub-head from the wrong head is never
  in the list the UI can choose from — CONTRACTS.md's "impossible to
  choose, not merely discouraged").

**`core/ingest.py`** — one change: `ingest()`'s top-level dispatch is now
wrapped in `try/except`, so a corrupt/unreadable file degrades to an empty
line list with a note instead of a 500 that would take the whole upload
down. Everything else (table → text-layer → per-page OCR cascade, vendor
read only from the line itself, Tesseract-missing note per page) was
already correct and needed no change.

**`web/create.js`** — new, self-contained (wrapped in an IIFE so nothing
collides with `web/app.js`'s top-level `let`/`const` bindings, since classic
`<script>` tags share one lexical scope). At runtime it replaces
`#v-create`'s contents; it never edits `web/index.html`'s markup on disk.
One line was added to `web/index.html` — `<script src="/create.js">` — the
only edit to a file outside my three.

* Paste or drop an invoice; the extraction cascade's progress shows as a
  spinner + elapsed timer (true per-page progress isn't possible over a
  single synchronous stdlib HTTP response — documented under "thin spots").
* Resolve fires one request per line concurrently (pool of 4), rendering
  each card as its own result lands — one failing line never blocks the
  rest, and 20 cards appear as skeletons immediately rather than after a
  single 20-line round trip completes.
* Each card shows the three phases in the compact form the brief's mock-up
  asked for, including `matched_by` and — when it says `rules` because no
  LLM is configured — a quiet "LLM not configured" note read from
  `llm_available`.
* A blocked spec slot renders inline as a small select/input right there in
  the phase-3 line (no need to open Edit for the common case: just answer
  the question). **Edit** opens the full Head → Sub-head → Group cascade;
  choosing a group re-renders the spec boxes with that group's own labels;
  changing any level clears everything below it and the code shortens live,
  driven entirely by `/resolve/preview`.
* Submit is disabled while blockers stand, disabled again mid-flight, and
  reuses one `idempotency_key` per proposal version — a double-click cannot
  mint two codes (verified live, see below).
* Correcting a match (picking a different existing group while editing)
  calls `/api/v1/alias/add` on submit and tells the operator it was
  learned.
* Keyboard: Ctrl+Enter reads pasted text, Enter inside a card submits it
  (unless focus is in a select/textarea/quick-fill field, where Enter does
  its own job), Esc backs out of Edit mode.

## Contract vs reality — what I found once I actually read the other agents' files

`agents/CONTRACTS.md` §7 froze `resolve(con, text, hints=None, user=None) ->
dict` with a top-level `outcome`/`matched_by`. Agent C's actual
`core/resolve.py` (already substantial when I started) has a different,
richer signature — `resolve(con, matcher, payload)` — and returns `action`
(`existing`|`create`) plus per-phase `layer` (`fuzzy`|`llm`|`human`), not a
top-level `outcome`/`matched_by`. I did not edit `resolve.py` — CONTRACTS.md
says nobody but Agent C does — so `_augment()` in `routes/create.py`
computes the promised fields on top of what actually comes back:
`outcome` from `action`/`blockers`, `matched_by` collapsing per-phase
`layer` + any `"chosen by operator"` status into the one word CONTRACTS.md
promises the card (operator > llm > rules; exact only for a phase-1 stop).
Flagging in case the frozen signature was meant literally elsewhere.

My own brief named `core.codes.preview_code` for live recomputation; it
doesn't exist, and CONTRACTS.md §3's frozen `core/codes.py` signatures don't
include it either — only `assemble`/`parse`/`next_group_code`. I used
`assemble` directly (via my own `/resolve/preview` endpoint) instead of
adding a same-shaped wrapper function to `core/codes.py`, since I don't own
that file. Also note §3 names the parser `parse()`; the actual function is
`structural_parse()` (Agent A's public routes already call it by that real
name, so this is consistent across the code, just not the frozen doc).

**`alias/add`'s `scope='specval'` is stored but currently inert.**
`phase2_taxonomy`'s group matching folds `alias`-table entries into its
scoring (`group_aliases()`); `phase3_specs` does not do the equivalent
lookup for spec values, so learning a `scope='specval'` alias today has no
effect on future matches. I only wired the create-screen's "learned"
messaging to the group level, where it actually does something. Worth
Agent C's attention if spec-value learning matters.

## Auth — built against the frozen stub, then Agent B landed for real mid-task

`core/auth.py` didn't exist when I started, so `routes/create.py` imports
`require_session` inside a `try/except ImportError` with a local fallback
(reads the old `X-User` header). Agent B's real module landed while I was
mid-build — the `except` branch simply stopped firing, no edit needed on my
side. I did have to rewrite `web/create.js`'s identity/fetch layer once I
noticed: Agent B replaced the forgeable `X-User`/`localStorage` pattern in
`web/app.js` with real `HttpOnly` cookie sessions and a `location.href =
'/login.html?expired=1'` redirect on 401. `create.js` now matches that
exactly — `credentials: 'same-origin'` on every fetch (the cookie rides
along automatically) and the same redirect-on-401 behaviour, rather than
the name-prompt flow I'd originally built against the pre-auth state of the
repo.

## Verified live (server started, tested with curl through a real logged-in
session via a throwaway `manage.py adduser` account, then stopped cleanly —
port 8756 confirmed free before and after; all test rows removed afterwards)

* `resolve` on `"Odonil Lavendar Air Freshner 48gm"` → `outcome: "exists"`,
  `code: "AOHK0010603"`, stops at phase 1 (the done-when example, verbatim).
* `resolve` on the battery line → phase 2 finds Battery Pack (RMBS/BS/001),
  phase 3 fills all four specs, code `RMBS00102060407` (15 chars, no
  vendor since "make LG" wasn't parsed — that only happens via `/ingest`,
  which was also tested and correctly extracted `vendor: "LG"` from the
  line, never a header).
* `/cascade/heads|subheads|groups|slots` all filter correctly and cascade.
* `/resolve/preview` with all four specs → `RMBS00102060407`; adding a new
  vendor value → 17 chars, `RMBS0010206040714`; switching to a different
  group → `RMBS002` with four blockers listed, one per unanswered spec —
  the "changing sub-head clears the group and shortens the code live"
  done-when, confirmed end to end.
* `/commit` without `idempotency_key` → `VALIDATION`, rejected. `/commit`
  with blockers present → `VALIDATION`, rejected (never lets a blocked
  proposal through). Two identical `/commit` calls with the same key →
  first mints `RMBS00102061907`, second returns the same code with
  `"idempotent": true` — no second item row created (checked directly).
* `/resolve` with no session cookie at all → `401 AUTH_REQUIRED`.
* `python -m py_compile` clean on every touched `.py` file; `node --check
  web/create.js` clean.

## Thin spots, for whoever picks this up next

* **OCR progress is a spinner + elapsed timer, not real per-page
  progress.** A single synchronous stdlib HTTP request can't stream partial
  results without chunked transfer encoding, which felt like scope creep
  for this packet. The response does carry `pages`/`ocr_lines` so the UI
  shows an honest summary once it's done.
* **The "20 lines, one round trip" reading in the brief** I implemented as
  20 concurrent single-line requests (pool of 4) rather than one batch call
  fanned out server-side, so that a card can render the moment its own
  request lands rather than waiting for the slowest line in a single batch
  response. `/api/v1/resolve_batch` still exists and is used nowhere by
  `create.js` itself — kept for any other caller that genuinely wants one
  round trip and is fine waiting for all of them together.
* **Idempotency storage piggybacks on `audit`** rather than a dedicated
  table, per the "don't touch Agent 0's schema" rule. It works and is
  simple, but a purpose-built `commit_idempotency(key, result, ts)` table
  with an index would be cheaper to query at scale than filtering `audit`
  by `action`.
* Per Agent B's done-note: `D.log()` doesn't call `commit()` itself.
  `commit_v1` and `alias_add_v1` both call `con.commit()` explicitly right
  after their `D.log()` calls, so they're not exposed to the lock-wait bug
  Agent B found — flagging again here since it affects several modules.
