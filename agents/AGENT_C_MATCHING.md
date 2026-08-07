# AGENT C — MATCHING & LLM

**Start after Agent 0 reports done. Runs in parallel with A, B, D, G, H.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You own the judgement of this system: given "Odonil Lavendar Air Freshner 48gm",
decide whether it already has a code, and if not, which group and which
specification values it belongs to.

**The design was recently inverted and this is the main change.** It used to be
deterministic-first with the LLM as an escalation below 60% confidence. Anuraag
now wants the **LLM as the primary matcher, with the rules as its guardrail and
its fallback**. Do not implement the old behaviour.

A deterministic matcher already exists in `core/matcher.py` and works well. You
are not replacing it — you are putting the LLM in front of it and keeping it as
the safety net.

## What you own

`core/matcher.py` · `core/llm.py` (new) · `core/resolve.py`

## How it must work

```
line of text
     │
     ▼
  RULES run first, always
  normalise · score · rank · collect the constraints
  (which slots this group declares, which values are legal, is a vendor allowed)
     │
     ├── exact hit in phase 1? ──▶ STOP. no LLM call. nothing to judge.
     │
     ▼
  LLM decides, group first, then each slot
  given: the line, the shortlist, the constraints
     │
     ▼
  RULES check the answer  ── outside the shortlist? illegal in that slot?
     │                       vendor on a group that declares none?
     │                       ──▶ REJECT, deterministic result stands
     ▼
  result, stamped matched_by: exact | llm | rules | operator
```

And the rule that matters most: **if the LLM is rate-limited, out of quota, slow,
misconfigured or unreachable, use the deterministic result and stamp
`matched_by: rules`.** The tool must never stop working because a provider is
down. Anuraag was explicit: *"the fallback is always there."*

## Tasks

**1. Provider client (5 pts).** `core/llm.py`, one interface, four backends:
`anthropic`, `gemini`, `openai`, `ollama`, plus `none`. Read provider, key and
model from the **`settings` table** (Agent B's screen writes them) — never from
`config.json`, never hardcoded.

Standard library `urllib` only; `requests` is available if you prefer. Hard
timeout of about 20 seconds. Retry once on 429 or 5xx with backoff, then give up
and fall back. Never raise into the caller — return `None` and let the rules win.

**2. Invert to LLM-first (8 pts).** Rework `resolve.py` so the LLM decides group
then specification values, with the rules producing its shortlist and holding the
veto. The prompt must state that a different size, grade, chemistry or brand is
**not** the same item, and that it may only choose from the list or answer
`"none"`. Ask for strict JSON and parse defensively — a malformed reply is a
fallback, not a crash.

Every decision records `matched_by`, so the mix is auditable rather than assumed.

**3. Fallback and the fuzzy toggle (5 pts).** When `match.mode` is `fuzzy`, or
no API key is set, skip the LLM entirely and use the rules. This is the **default
state** and it must be a good experience, not a degraded one — everything works,
the hard lines just become questions for the operator.

Log every fallback with its reason (`no_key`, `rate_limited`, `timeout`,
`bad_json`, `provider_error`) so it is possible to tell "the LLM is off" from
"the LLM is broken".

**4. Cache and batch (5 pts).** Cache answers in `llm_cache` keyed on a hash of
the normalised text plus the shortlist — the same wording next month costs
nothing. Send a **20-line invoice as one call, not twenty**. Exact phase-1 hits
never call out at all.

## Keep these — they were hard-won

* The blend is `0.45·token_set + 0.30·token_sort + 0.25·soft_jaccard`. A junk ERP
  row literally named `NMC` scores **100** against "cylindrical NMC battery pack"
  on `token_set` alone. Blended it scores 55 and is correctly rejected. **Do not
  simplify this back to one metric.**
* A group is scored against its own name, its aliases, **and the names of items
  already inside it at a 0.9 discount**. That last term is what lets "Mseal epoxy
  sealant" find the right group when no group name resembles it.
* Phase 1 stops at **90**, not 60. Wrongly accepting an existing code is worse
  than creating a duplicate: a duplicate is visible and mergeable, a wrong
  acceptance silently books stock against the wrong item.
* **A declared slot with no determinable value is a question, never an
  auto-allocated number.** Without this, two unrelated invoice lines silently
  receive the same code. This happened during development.

## Do not

* Do not call `assemble()` yourself or build code strings — that is Agent D.
* Do not touch `core/codes.py`.
* Do not let the LLM invent a group, a value or a code. Shortlist or "none".

## Done when

`resolve()` returns the contract shape with `matched_by` on every decision; with
no API key everything still works fuzzy-only; killing the network mid-call falls
back cleanly rather than erroring; a 20-line invoice makes one call; the `NMC`
case is still rejected; `"Odonil Lavendar Air Freshner 48gm"` still returns
`AOHK0010603` from phase 1.

## Then

Write `agents/done/AGENT_C.md` — the `resolve()` signature and return shape
verbatim, since Agent E builds its screen directly on it.
