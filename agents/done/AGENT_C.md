# AGENT C — done

LLM-first matching landed. Rules build the shortlist and the constraints,
run first always, and hold the veto; the LLM decides group-then-slots from
that shortlist only, or answers "none"; on any failure - no key, fuzzy mode,
rate limit, timeout, bad JSON, provider error, or a vetoed answer - the
deterministic rules result stands and is stamped accordingly. The old
"fuzzy first, LLM escalates below 60%" design is gone from `core/matcher.py`
entirely, not just bypassed.

## Files

* `core/matcher.py` - trimmed to pure rules: `normalize()`, `Matcher.similar()`,
  `Matcher.rank()`, `Matcher.decide()` (now threshold-only, `layer` always
  `"rules"`), plus new `rank_groups(con, text, limit=5)` per CONTRACTS.md §7.
  All four provider backends (`_anthropic`/`_openai_compatible`/`_gemini`/
  `_ollama`) and `_ask_llm()` were **removed** from this file - that was the
  old escalation design and it's what the brief said to invert away from.
  `Matcher(llm_config=None, threshold=60)` constructor signature is
  unchanged (server.py still builds it from `config.json`'s `llm` block) but
  `llm_config` is no longer read for actual calls - kept only for backward
  compatibility with how `server.py` constructs it.
* `core/llm.py` - **new**. One interface, four backends
  (`anthropic`/`gemini`/`openai`/`ollama`) plus `none`. `urllib` only, no
  `requests`. Reads `llm.provider` / `llm.api_key` / `llm.model` /
  `llm.base_url` from the **settings table** via `db.get_setting`, falling
  back to `config.json`'s `llm` block only when nothing has been saved yet.
  20s timeout per attempt, one retry on 429/5xx with a 1.5s backoff, then
  gives up - timeouts and connection errors are never retried, they've
  already cost the wait once. **`ask_json()` never raises** - always returns
  `(parsed, provider, reason)`, `parsed is None` on any failure, `reason` in
  `{no_key, rate_limited, timeout, bad_json, provider_error}`, printed to
  stdout as `[llm] fallback reason=... provider=...` on every fallback.
  Also owns the `llm_cache` helpers (`cache_key`, `cache_get`, `cache_put`)
  and the two settings readers `get_mode()` (`match.mode`, default
  `"fuzzy"`) / `get_threshold()` (`match.threshold`, falls back to
  `config.json`'s `match_threshold`) / `available()` / `enabled()`.
* `core/resolve.py` - rewritten. `phase1_exists`, `load_groups`,
  `group_aliases`, `group_exemplars`, `specvals`, `one_sub` unchanged.
  Phase 2/3 no longer decide anything themselves - they've become
  `_build_line_context()`, which is pure rules and produces the shortlist +
  constraints (a list of candidate groups, each with its rule score and,
  per group, its declared slots' value pools and its vendor pool - nothing
  the LLM later sees is invented anywhere else). The LLM layer
  (`_build_batch_prompt`, `_apply_answer`) and the rules
  fallback/veto (`_rules_decision`, `_apply_forced`) sit on top of that.
  `commit()` is **untouched** - it only ever reads a settled proposal's
  group/slots/vendor, never re-decides, never calls the LLM.

## Signatures (Agent E builds its screen directly on these)

```python
# core/resolve.py

def resolve(con, matcher, payload, user=None) -> dict
    """Single-line entry point. payload: {"text", "name", "hints", "vendor",
    "uom", "hsn", "description", "tax"}. This is what routes/create.py's
    existing /api/resolve already calls (R.resolve(ctx.con, ctx.matcher,
    req.body)) - the call signature was kept exactly so that wiring did not
    need to change. Internally it is now `resolve_batch(con, matcher,
    [payload])[0]`."""

def resolve_batch(con, matcher, payloads, user=None) -> list[dict]
    """THE ONE TO WIRE UP FOR REAL BATCHING. One call per payload list, not
    per payload - a 20-line invoice submitted here costs at most ONE LLM
    request, however many lines need a decision. routes/create.py's current
    resolve_batch handler loops calling R.resolve() once per line instead -
    that still works (each call falls through the same logic) but pays for
    up to N LLM requests instead of one. Swap that loop for one call to
    R.resolve_batch(ctx.con, ctx.matcher, [{"text":...,"hints":...}, ...])
    to get the real batching. I did not make this change myself -
    routes/create.py is Agent E's file."""

def commit(con, matcher, proposal, user, push_erp=False, erp=None) -> dict
    """Unchanged signature and behaviour."""
```

## Return shape (per line, from both `resolve()` and each entry of `resolve_batch()`)

Kept close to the pre-existing shape on purpose - `commit()` and the current
`web/app.js` both already read `phase2`/`phase3`/`action`/`code`/`segments`/
`input`, and rebuilding those was out of scope. What's new is `matched_by`
at every level:

```python
{
  "input": <the original payload>,
  "phase1": {"phase": 1, "checked": [...], "hit": {...} | None, "near": [...]},
  "phase2": {
    "phase": 2, "group": <group dict> | None,
    "steps": [{"level": "group", "status": "match"|"new", "layer": <matched_by>,
               "matched_by": <matched_by>, "value": <name>|None, "score": int,
               "alternatives": [...]}],
    # when group is None: "head", "subhead", "suggested_subheads" as before
  },
  "phase3": {
    "phase": 3,
    "slots": [{"slot": int, "label": str, "status": "match"|"undetermined",
               "layer": <matched_by>, "matched_by": <matched_by>,
               "value": str|None, "code": str|None, "specval_id": int|None,
               "options": [...]}],
    "vendor": {...same shape, or "not named on the invoice line"} | None,
  },
  "code": str | None,
  "action": "existing" | "create" | None,
  "matched_by": <matched_by of the group decision>,   # top-level summary
  "blockers": [str, ...],
  "segments": {"head","sub","group","specs","vendor"},  # when action == "create"
  "new_group": bool, "new_spec_values": [...],
}
```

`matched_by` ∈ `exact | llm | rules | operator`, per CONTRACTS.md §7, and is
recorded independently for the group, for **each** declared slot, and for
the vendor - not just once per line. An exact phase-1 hit short-circuits to
`{"action": "existing", "code": ..., "matched_by": "exact", "phase2": None,
"phase3": None, ...}` and never builds a shortlist or reaches the LLM at
all.

## How the inversion actually works

1. `phase1_exists()` - unchanged, deterministic, stops at 90 not 60.
2. `_build_line_context()` - rules-only. Builds the group shortlist
   (`Matcher.rank` over name + aliases + exemplars-at-0.9), and for each
   shortlisted group, its declared slots' value pools and vendor pool
   (also rules-only ranking). This is the "shortlist + constraints" - the
   LLM never sees anything beyond it and can never invent a group, value,
   or code.
3. If `match.mode != "llm"` or no usable provider/key is configured
   (`core.llm.enabled()`), the LLM is skipped entirely and
   `_rules_decision()` (top shortlist entry if its score clears the
   threshold, else "new"/undetermined) is used, stamped `matched_by:
   "rules"`. **This is the default state** (`match.mode` defaults to
   `"fuzzy"`) and was verified end-to-end against the seeded DB.
4. Otherwise, all lines needing a decision (skipping phase-1 hits and lines
   fully answered by `llm_cache`) go into **one** prompt
   (`_build_batch_prompt`), asking, per line, for a group index (or `null`)
   and then, per declared slot of the chosen group, a value index (or
   `null`) - explicitly told it may only choose from the numbered list or
   answer `null`, and that a different size/grade/chemistry/brand is not
   the same item. One call to `core.llm.ask_json()`.
5. `_apply_answer()` validates the reply against **that line's own**
   shortlist: an out-of-range group index, or an out-of-range slot/vendor
   index for the chosen group, is rejected and `_rules_decision()`'s
   opinion stands instead - the veto, applied per field, not just per line.
   A slot the LLM's JSON simply didn't mention falls back to the rules'
   opinion for that one field alone.
6. `_apply_forced()` always runs last: any slot/group the operator
   explicitly picked via `hints` (`group_id`, `s1..s4`, `vendor_value`)
   overrides whatever the LLM or the rules said, stamped `"operator"`. A
   line that is *fully* operator-specified (forced group, every declared
   slot forced, vendor forced or not applicable) is detected up front and
   never reaches the LLM at all - this matters because the existing UI
   re-resolves on every dropdown change.
7. A declared slot nobody could determine - not the operator, not the LLM,
   not the rules' own threshold - is left `value: None, status:
   "undetermined"` and becomes a blocker line ("Choose a value for
   '<label>'"). It is never auto-allocated a number. Verified directly:
   forcing only one of two declared slots leaves the other correctly
   `undetermined`/`matched_by: "rules"`, not silently inheriting the
   group's `"operator"` label (an early version of this had that bug - a
   slot nobody touched must not read as if the operator touched it).

## Caching

`llm_cache.key = sha256(normalize(text) + shortlist signature)`, where the
signature is the candidate group ids plus, per group, the *ids* of every
slot/vendor option offered - so a dictionary change (a group renamed, a
spec value added) naturally misses the cache rather than serving a stale
answer. Verified: an identical batch run twice makes the network call only
on the first pass; the second is entirely `llm_cache` hits with zero calls.

## Verified against the real seeded DB (via an isolated copy, to avoid
lock contention with other agents' running servers on the shared file)

* `"Odonil Lavendar Air Freshner 48gm"` → `AOHK0010603`, `matched_by:
  "exact"`, phase 1 only, no shortlist built, no LLM touched.
* Default state (no settings saved, `match.mode` unset ⇒ `"fuzzy"`):
  resolves a genuinely new line end-to-end through rules alone, correct
  blockers, no crash, one printed `[llm] fallback reason=no_key note=...`
  line and nothing else.
* `matcher.similar('cylindrical NMC battery pack 32700', 'NMC')` → **55**
  (blended token_set/token_sort/soft-Jaccard), still correctly rejected by
  any ≥60/≥90 threshold. Blend weights (`0.45/0.30/0.25`) untouched.
* 20-line batch, `match.mode="llm"`, mocked provider that raises mid-call
  (simulating the network dying) → **exactly one** provider call attempted,
  then all 20 lines fall back cleanly with `matched_by: "rules"` - the tool
  never stops working.
* 20-line batch with a working mocked provider → **exactly one** call
  regardless of line count.
* Malformed/out-of-shortlist LLM answer (`"group": 999`) → rejected, rules
  result stands, `matched_by: "rules"`.
* Operator-forced hints (`group_id` + all declared `s{slot}` values) → zero
  LLM calls, `matched_by: "operator"` throughout.
* `python -m py_compile` clean on `core/matcher.py`, `core/llm.py`,
  `core/resolve.py`, and every module that imports them
  (`routes/create.py`, `routes/master.py`, `routes/erp.py`,
  `routes/public.py`, `core/restructure.py`, `core/codes.py`, `server.py`).
  `import server` (module-level, not `main()`) succeeds cleanly with all six
  route modules loading - nothing else broke.

## Left exactly alone, as instructed

* `core/codes.py` - not touched. Note for whoever does touch it later:
  `next_group_code()` still does its own semantic-match reuse of vacated
  group numbers (`matcher.similar(...) >= 88`) - CONTRACTS.md §4 says this
  design is dead and vacancies should go **by queue, lowest-first**
  instead. That's Agent D's file and out of my packet; flagging it since it
  contradicts a frozen contract.
* `assemble()` / code strings - never built here; `resolve.py` calls
  `C.assemble()` exactly as before, only via the same tail-end logic that
  was already there.
* `routes/create.py` - not edited (not my file). Its `/api/resolve` and
  `/api/resolve_batch` handlers still work unchanged against the new
  `resolve()`/`resolve_batch()` - see the note under "Signatures" above
  about swapping the batch handler's loop for a single `resolve_batch()`
  call to get the real one-call-per-invoice benefit through the API.

## Judgement calls worth knowing about

* `Matcher(llm_config, threshold)`'s constructor keeps accepting
  `llm_config` for backward compatibility with `server.py`, but it is now
  inert - real LLM config lives in `settings` and is read by `core/llm.py`
  at call time. If Agent B's Settings screen writes `llm.provider` etc. to
  the settings table, everything here already picks it up with no further
  change; `config.json`'s `llm` block only matters before that screen has
  ever been used.
* The batch prompt includes, per line, every shortlisted group's full slot
  and vendor option pools (not just the winning group) so the LLM can pick
  a group and immediately resolve that group's slots in the same reply,
  in one HTTP call. For an unusually large invoice with many
  high-ambiguity lines this makes for a large prompt; no cap was added
  beyond the existing shortlist limits (5 groups, 6 options per slot) since
  the seeded dictionary's group/spec pools are small. Worth watching if a
  future dictionary grows much larger.
