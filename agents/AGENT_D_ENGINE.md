# AGENT D — CODE ENGINE & NUMBER RESERVATION

**Start after Agent 0 reports done. Runs in parallel with A, B, C, G, H.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You own the grammar and the numbering. Everything else in this system is a way of
showing or storing what you produce, so correctness here is worth more than speed
anywhere else.

**Your main task is a behaviour change Anuraag asked for, and it reverses what is
currently implemented.** Read task 1 carefully.

## What you own

`core/codes.py` · `core/restructure.py`

## Task 1 — numbering becomes QUEUE-claim, lowest-first (8 pts)

**Currently implemented (now wrong):** a number freed by a move is *parked* and
handed only to a future group whose name matches the departed one at ≥88%,
otherwise numbering continues past it. This leaves permanent holes.

**What Anuraag now wants:** *"ideally it should be the next number, because
there's a number vacant it will be given that, so that there is no number vacant
ever in between… it is going to be claimed by queue."*

So: **a freed number is a free slot, not a reservation.** The next arrival takes
the lowest free number. The number space stays dense and gapless.

```python
def next_group_code(con, subhead_id) -> tuple[str, str|None]:
    """Lowest free 3-digit number under this sub-head, vacancies included.
    Returns (code3, freed_from_name or None)."""

def next_item_position(con, grp_id, spec_tuple) -> str:
    """Same rule one level down: lowest free position inside the group."""
```

Delete the ≥88% semantic-claim path and the `matcher` parameter from
`next_group_code`. Keep `grp_vacancy.former_name` — but **for display only**, so
Activity can say "041, freed by Tissue". It must not influence who gets it.

**Item-level vacancy is new and was missing entirely.** When an item moves to
another group, its old position under that head/sub-head/group is freed into
`item_vacancy` and becomes immediately available to the next item there.

**One thing must not change:** an item whose code is **live in ERPNext is
frozen**. It keeps its code, is flagged `stale code`, and **frees nothing** —
it did not leave, only its classification did. Only codes that never reached
ERPNext are re-issued and only those create a vacancy.

## Task 2 — live recomputation (5 pts)

Expose a pure function the create screen and the master can both call:

```python
def preview_code(con, head_id, subhead_id, grp_id, slots, vendor) -> dict
    # -> {"code": str, "length": int, "valid": bool, "explain": [...]}
```

No writes, no side effects, safe to call on every keystroke. `explain` gives a
per-position account — position 3 is `00` because Size is not applicable, and so
on — so the interface can show *why* a code looks as it does.

Agents E and F both depend on this. It is the only sanctioned way to build a code
string outside `assemble()`.

## Task 3 — concurrency (5 pts)

Two creators submitting in the same second must not receive the same code.

Wrap allocation and insert in one `BEGIN IMMEDIATE` transaction; re-check
uniqueness inside it; retry once on `SQLITE_BUSY`. Set a busy timeout.

**Prove it.** Write `tests/test_concurrent.py` that fires ~20 simultaneous
allocations from threads and asserts every code is distinct. A claim without a
test is not evidence, and this is the kind of bug that only appears in
production.

## Task 4 — keep restructuring honest (5 pts)

`move`, `merge`, `retire` and `rename` already work with impact previews. Update
them for the new numbering, and keep the guarantees:

* Preview lists exactly what re-codes and what stays frozen, and writes nothing.
* Every old→new pair goes to `code_mapping`.
* `retire` refuses while items remain in the group.
* Retiring a sub-head drops its whole branch of vacancies.

Verified example to re-check after your change — moving group *Tissue* from House
Keeping to Stationery: 1 code re-issued, 7 kept frozen, `041` freed. Under the new
rule `041` now goes to the **next** group created under House Keeping, whatever
it is called.

## Task 5 — number leases and provisional codes (8 pts) — NEW

The VPS may be unreachable, and a **local LAN server** takes over as the
authority when it is (`CONTRACTS.md` §2). Two authorities minting at once would
produce duplicate codes, and a duplicate that reaches ERPNext is permanent. You
own the mechanism that makes this impossible.

```python
def grant_lease(con, scope, size=10) -> dict
    """VPS side: reserve a disjoint block of positions for the local server.
    scope is (subhead_id) for group numbers or (grp_id) for item positions."""

def mint_from_lease(con, scope) -> str | None
    """Local-server side: next number from our own leased block.
    None when the lease is exhausted -> caller falls back to provisional."""

def return_lease(con, scope) -> list[str]
    """On sync: give back what we did not use. Returned numbers become
    vacancies and the queue-claim rule then fills them - so leasing leaves
    no permanent holes."""
```

**Leased numbers are never issued by the VPS to anyone else.** That is the whole
guarantee — the two number spaces are disjoint by construction, not by timing.

When no lease exists or it is spent, mint a **provisional** code: set
`item.provisional = 1`. A provisional item must be visibly provisional, and must
never be pushed to ERPNext. Agent G enforces the second half; you set the flag
and expose it.

**The property that makes all of this safe:** when the VPS is unreachable,
ERPNext is unreachable too, so nothing minted offline can freeze while offline.
Every offline code stays re-writable until it syncs. Do not write any code that
assumes an offline code is final.

## Task 6 — vacancy listing (3 pts)

```python
def list_vacancies(con) -> list[dict]
```

Group-level and item-level, each with what freed it and when. Agent F displays
this; you provide it.

## Watch for

* `assemble()` and `parse()` signatures are frozen in `CONTRACTS.md` §3. Other
  agents import them. Do not change them.
* Valid lengths are exactly 7, 9, 11, 13, 15, 17. Anything else is malformed.
* Interior gap `00`, trailing gaps dropped. Vendor is position 5, so any code
  with a vendor is 17 characters.
* Never re-use a code in `code_ledger`, even for a retired or disabled item.

## Done when

`next_group_code` returns the lowest free number with no semantic test anywhere;
an item move frees its position and the next item there takes it;
`python tests/test_concurrent.py` passes; the *Tissue* move still keeps 7 frozen
codes; a leased block cannot be issued twice — **write a test that mints from a
lease and from the main allocator simultaneously and asserts no overlap**;
returning a lease creates vacancies that the next arrivals fill; `python seed.py
--rebuild` then a full decode of `RMBS0010206100007` still reads correctly.

## Then

Write `agents/done/AGENT_D.md` — the exact signatures of `preview_code`,
`list_vacancies` and the three lease functions. Agents E, F, G and H all build on
them.
