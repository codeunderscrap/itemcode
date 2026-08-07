# Item Code Studio — Preliminary Design Review

**Prepared for Anuraag · 6 August 2026 · v2.0 — final before go-ahead**
**Delivery: five build runs, one sitting, deployed to an internal server this evening.**

Companion documents: [Sprint_Plan.xlsx](Sprint_Plan.xlsx) (81 stories, 356 points),
[PRODUCT.md](PRODUCT.md) (what the engine already does).

---

## 1. Scope, stated honestly

You asked for all 285 points completed today. Here is the true arithmetic after
adding your new interface requirements:

| | Points | |
|---|---:|---|
| Already built and verified | 114 | The engine, matching, invoice reading, master, restructuring, export |
| **Buildable today, runs R1–R5** | **181** | Landing page, login, LLM-first matching, cascading edit, number reservation, version control, API, deploy |
| **Blocked on something only you can supply** | **103** | Listed in §11 and on the *Needs Your Input* tab |
| Backlog, not needed for go-live | 8 | |
| **Total** | **406** | |

Your edits added 50 points: inverting the matcher to LLM-first with a fallback
(18), item-level number reservation (11), and the ERPNext guardrail work the live
probe turned up (21).

**The 90 blocked points are not padding.** They are an LLM API key, ten real
scanned invoices, written go-ahead to write into ERPNext, an ERP login per
creator, a nominated host machine, and your judgement on which of 889 groups are
duplicates. I can build every screen that consumes those things; I cannot supply
the things themselves. Give me any of them and the work moves in the same
sitting.

**What "internal grade" means here.** Not production-grade: no TLS, no high
availability, no load testing, one box. It will be correct, attributed,
reversible and backed up. That is the right bar for a tool used by five to ten
people on your own network, and it is the bar I am building to.

---

## 2. The interface

### 2.1 Landing page — split, no login

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⬢ MiniMines   Item Code Studio                          [ Log in ]  │
├───────────────────────────────┬──────────────────────────────────────┤
│  DECODE A CODE                │  FIND AN ITEM                        │
│                               │                                      │
│  ┌─────────────────────────┐  │  ┌────────────────────────────────┐  │
│  │ RMBS0010206100007       │  │  │ search code, name, description │  │
│  └─────────────────────────┘  │  └────────────────────────────────┘  │
│                               │                                      │
│  RM   Raw Materials           │  AOHK0010603  Air Freshener –        │
│  BS   Battery Scrap           │               Odonil Lavender        │
│  001  Battery Pack            │  AOHK00104    Air Freshener –        │
│  02   Form Factor Cylindrical │               Odonil Fresh           │
│  06   Chemistry   NMC         │  COMS268      M-Seal 100 g           │
│  10   Size        Gen 3 pack  │  RMBS00102…   Battery Pack –         │
│  00   Capacity    (n/a)       │               Cylindrical NMC        │
│  07   Maker       LG          │                                      │
│                               │  browse the dictionary →             │
└───────────────────────────────┴──────────────────────────────────────┘
```

Two halves, equal weight, because they are the two things most people ever need.
No account, no session. **Log in** sits top-right and is the only door to
creation.

MiniMines palette taken from your existing platform — navy `#001b2e`, teal
`#04aed1`, steel `#3b6e93` — Barlow typeface, `minimines-logo.svg` in the header.
The stats block in the bottom-left corner is removed, as instructed.

### 2.2 After login

Three destinations: **Create**, **Item master**, **Activity**. The landing page
stays reachable throughout — logging in adds capability, it does not take the
decoder away.

**Accounts are provisioned, never self-registered.** I generate the admin account
at install and hand it to you. The admin then creates each creator with
`manage.py adduser` and gives them their credentials directly. There is no
sign-up page, no email reset and no one-time-code channel to maintain — with five
to ten creators, an admin issuing a credential by hand is simpler and safer than
any flow we could build, and there is no delivery channel to go wrong.

---

## 3. How matching works — for explaining to others

Three ways of saying the same thing. Use whichever suits the audience.

### 3.1 In words

The system never compares raw text. It first **rewrites** both sides into a
common form: same case, units spelled one way, `48gm` split into `48 GM`, known
synonyms collapsed (`Mseal` → `M SEAL`, `LiFePO4` → `LFP`), noise words like
`QTY` and `SUPPLY OF` thrown away.

Then it scores similarity **three different ways at once**, because any single
measure can be fooled:

* one that asks *does one contain the other* — good at "Odonil" inside "Odonil
  Lavender Air Freshener";
* one that ignores word order and tolerates typos — good at "Air Freshner";
* one that punishes length mismatch — the safety catch.

The three are blended. **A single measure is not enough, and we know this from a
real failure:** a junk row in your ERP literally named `NMC` scored a perfect
100% against "cylindrical NMC battery pack 32700" on the containment measure
alone. Blended, it scores 55 and is correctly rejected.

For **groups** there is one more trick, and it is what makes the system usable:
a group is judged not only by its own name but by **the names of the items
already inside it**. "Mseal epoxy sealant" resembles no group name in your
dictionary, but it strongly resembles items already sitting in the right group.
Those item names count at a 10% discount, so one odd item cannot hijack a group.

### 3.1a Who decides — LLM first, rules as guardrail and fallback

**Per your instruction, the LLM is the primary matcher and the arithmetic above
is its guardrail and its fallback.** The order of operations on every line:

1. **The rules run first, always.** They normalise the text, score every
   candidate, and produce a ranked shortlist plus the hard constraints — which
   slots this group declares, which values are legal in each, whether a vendor is
   allowed at all.
2. **The LLM decides, group first.** It is given the line, the shortlist and the
   constraints, and asked in order: which group fits best, then which value fits
   each slot that group declares. It reasons about the specification, not the
   spelling — which is the point of using it.
3. **The rules then check the LLM's answer.** An answer outside the shortlist, a
   value not legal in that slot, or a vendor on a group that declares none is
   rejected and the deterministic result stands. The LLM proposes; the rules hold
   the veto.
4. **If the LLM is rate-limited, over quota, slow or unreachable, the
   deterministic result is used directly** and the line is marked
   `matched_by: rules`. The tool never stops working because a provider is down.

Two shortcuts keep this affordable and fast, and neither weakens it:

* An **exact hit in phase 1 never calls the LLM.** If the item already has a
  code, there is no judgement to make.
* Results are **cached against the normalised text**, so the same wording on next
  month's invoice costs nothing, and a 20-line invoice goes in **one call**, not
  twenty.

**The honest trade.** LLM-first buys real understanding of specifications; it
costs money per line, adds a second or two, and makes the system
non-deterministic — the same line can in principle resolve two ways on two days.
The rules veto, the cache and the `matched_by` stamp on every decision are what
keep that in bounds, and every line remains reviewable by the operator before
Submit.

### 3.2 In mathematics

Let `N(·)` be the normaliser and `q` the incoming text.

**Blended similarity** between `q` and a candidate string `c`:

```
S(q,c) = 0.45 · T_set(q,c)  +  0.30 · T_sort(q,c)  +  0.25 · J_soft(q,c)
```

* `T_set` — token-set ratio. Rewards containment.
* `T_sort` — token-sort ratio. Order-invariant, typo-tolerant.
* `J_soft` — soft Jaccard. With `M` the greedy one-to-one matching of tokens
  whose pairwise ratio is ≥ 85:

```
J_soft(q,c) = |M| / (|q| + |c| − |M|)
```

`J_soft` is the term that punishes length mismatch: one shared token out of six
scores low no matter how perfectly that one token matches.

**Group score** for group `g`:

```
G(q,g) = max ⎧ S(q, name(g))
             ⎨ max over aliases a of g:  S(q, a)
             ⎩ 0.9 · max over items i in g:  S(q, name(i))
```

The `0.9` is the discount on evidence borrowed from members.

**Decisions.** Let `Λ` be the LLM's answer, `⊥` if it is unavailable, and let
`θ = 60` (calibratable), `θ_stop = 90`. Write `Shortlist(q) = top-5 by G`.

```
Phase 1   if max over items i of S(q, name(i)) ≥ θ_stop   → STOP, return that code
                                                            (no LLM call)
Phase 2   g_rules = argmax G(q,g)
          g_llm   = Λ(q, Shortlist(q), constraints)
          g*      = g_llm       if  g_llm ∈ Shortlist(q)          ← LLM decides
                    g_rules     if  g_llm = ⊥  or  g_llm invalid  ← rules veto / fallback
          if G(q,g*) < θ and g_llm = ⊥   → propose a new group

Phase 3   for each slot k declared by g*:
          v_rules = argmax S(q, value)
          v*      = Λ(q, values(g*,k))   if legal in slot k
                    v_rules              if Λ = ⊥ or illegal
          if no candidate reaches θ and Λ gives none  → ASK THE OPERATOR
                                                        (never auto-number)
```

Two invariants hold whatever the LLM says: **its answer must lie inside the
shortlist the rules produced**, and **a value must be legal in that slot of that
group**. It can never mint a code, invent a group, or place a vendor on a group
that declares none. Every stored decision records `matched_by: llm | rules |
exact | operator`, so the mix is auditable rather than assumed.

**Code assembly.** Let `x = (x₁,x₂,x₃,x₄,x₅)` be the four spec slots plus vendor,
each either defined or not. Let

```
L = max { i : xᵢ is defined }      (L = 0 if none are)

code = HEAD ‖ SUB ‖ GRP ‖ ⧺(i=1..L)  f(xᵢ)        where f(xᵢ) = code(xᵢ) if defined, else "00"

length = 7 + 2L        L ∈ {0,…,5}   →   length ∈ {7, 9, 11, 13, 15, 17}
```

That single line *is* the whole grammar. Everything before `L` is materialised —
which is why an absent middle specification becomes `00`. Everything after `L`
does not exist — which is why trailing absences are simply dropped. And because
vendor is `x₅`, any code carrying a vendor necessarily has `L = 5` and is 17
characters.

### 3.3 As a picture

```
   invoice line
        │
        ▼
   ┌─────────────┐
   │ NORMALISE   │  case · units · synonyms · noise removed
   └──────┬──────┘
          ▼
   ┌─────────────────────────────────────────────┐
   │ PHASE 1   does a code already exist?        │
   │   code in the text · exact · exact in ERP   │
   │   · semantic ≥ 90                           │
   └──────┬──────────────────────────┬───────────┘
       hit│                          │miss
          ▼                          ▼
      ══ STOP ══            ┌─────────────────────┐
   return existing code     │ PHASE 2  which      │
                            │ group?              │
                            │  name ∨ alias ∨     │
                            │  0.9 · members      │
                            └───┬────────┬────────┘
                          ≥60   │        │  <60
                                ▼        ▼
                            accept    LLM picks from top 5
                                │        │ or "none" → new group
                                └───┬────┘
                                    ▼
                     ┌──────────────────────────────┐
                     │ PHASE 3  for each slot the   │
                     │ group declares               │
                     │   ≥60 accept · <60 ASK       │
                     └──────────────┬───────────────┘
                                    ▼
                            assemble · show · EDIT
                                    ▼
                            operator presses Submit
                                    ▼
                    code minted · attributed · frozen on ERP write
```

---

## 4. How editing works — the conditional cascade

This is the second thing you asked to be explainable to others.

### 4.1 In words

The taxonomy is a tree, and each level of the tree **filters the level below
it**. Pick a head and you may only pick sub-heads belonging to that head. Pick a
sub-head and you may only pick groups belonging to that sub-head. Pick a group
and the four specification boxes **change what they mean**, because slot meanings
are defined per group — the same box that said "Type" for Air Freshener now says
"Chemistry" for a battery.

Change anything and everything below it is cleared, because it is no longer
valid. Then the code is rebuilt immediately, in front of the person, using the
same rule as §3.2. Nothing is saved until Submit.

### 4.2 In mathematics

Let the tree be `Head → Sub-head → Group → Slot → Value`. The choices offered at
each level are exactly:

```
D(sub-head | head h)          = { s : parent(s) = h }
D(group    | sub-head s)      = { g : parent(g) = s }
D(slots    | group g)         = labels(g)                    ← the boxes change meaning
D(values   | group g, slot k) = { v : group(v)=g, slot(v)=k } ← scoped to the group
```

On a change at level `L`, every level below is cleared and the code recomputed:

```
change at L  ⟹  x_j := undefined  for all j > L  ⟹  recompute code
```

That single invalidation rule is why the interface can never produce an
inconsistent code. A group from the wrong sub-head is not something the operator
has to remember not to do — it is not offered.

### 4.3 As a picture

```
  Head          Sub-head            Group              Slots become…
  ┌──────────┐  ┌────────────────┐  ┌───────────────┐  ┌─────────────────────┐
  │ Raw      │─▶│ Battery Scrap  │─▶│ Battery Pack  │─▶│ 1 Form Factor  ▾    │
  │ Materials│  │ Chemicals      │  │ Battery Module│  │ 2 Chemistry    ▾    │
  │ ─────────│  │ Electrode      │  │ Cell          │  │ 3 Size         ▾    │
  │ Admin &  │  │ Jelly Roll     │  │ Metal Scrap   │  │ 4 Capacity     ▾    │
  │ Office   │  │                │  │ …             │  │ V Maker        ▾    │
  └──────────┘  └────────────────┘  └───────────────┘  └─────────────────────┘
   choose one    only this head's    only this sub-      only this group's
                 sub-heads shown     head's groups       slots and values

  ── change the sub-head ─────────────────────────────────────────────▶
     group clears, slots clear, code shortens to RMCH———  live
```

---

## 4.5 What happens to the number when something moves

You were right that this was missing. It applies at **two levels**, and the rule
is the same at both: *a number that is freed is reserved, not recycled.*

### 4.5.1 The rule

**Level 1 — a group moves to another sub-head.**
It takes the next free 3-digit number under its new sub-head. Its old number is
written to the vacancy ledger against the **old head + sub-head**, together with
the name of the group that left.

**Level 2 — an item moves to another group.** *(This is the part that was
missing.)*
The item is re-coded into the new group and takes the next free position there.
Its old position — the exact `(head, sub-head, group, spec-tuple)` it occupied —
is written to the vacancy ledger against **that specific head and sub-head**.

**In both cases:**

* The freed number is **not given to the next thing that arrives.** New arrivals
  always take a fresh number, `max + 1`.
* It is released **only to a later arrival that matches what left** — a group
  whose name scores ≥ 88% against the departed group, or an item that resolves to
  the same specification tuple.
* It stays reserved **until that head and sub-head are revoked.** Retiring the
  sub-head drops its whole branch of reservations at once.

### 4.5.2 Why, in one sentence

In a positional code a number is not merely a number — it carries the meaning of
whatever held it. Give `041` to something unrelated next week and an old label,
an old GRN and a new one all read differently for the rest of time.

### 4.5.3 Worked example

```
Start                         AOHK 041  Tissue          (House Keeping)
                              AOHK 04101  Tissue – Clean Plus
                              AOHK 04102  Tissue – Hand          ← live in ERP
                              AOHK 04103  Tissue – Toilet Roll   ← live in ERP

Move the GROUP to Stationery
                              AOST 047  Tissue          ← next free under Stationery
                              AOST 04701  Tissue – Clean Plus   (re-coded, app-only)
                              AOHK 04102  unchanged             (frozen, flagged stale)
                              AOHK 04103  unchanged             (frozen, flagged stale)

  vacancy ledger  +  (Admin & Office, House Keeping, 041, "Tissue")

Next new group under House Keeping — "Mop"
                              AOHK 042   ← fresh number. 041 is NOT offered.

Later, a group "Tissue Paper" is created under House Keeping
                              name match vs "Tissue" = 91%  ≥ 88%
                              AOHK 041   ← the reservation is claimed

────────────────────────────────────────────────────────────────────────
Move a single ITEM instead
  AOST 04701 (Clean Plus) is moved from Tissue into group "Wipes" (AOHK 038)
                              AOHK 03804  ← next free position under Wipes
  vacancy ledger  +  (Admin & Office, Stationery, 047, spec-tuple 01)

Next new Tissue item under Stationery
                              AOST 04702  ← fresh. 04701 is NOT offered.

Later, an item resolving to the same spec-tuple 01 arrives in AOST 047
                              AOST 04701  ← the reservation is claimed
```

### 4.5.4 Where it meets freeze-on-first-use

A moved item that is **live in ERPNext keeps its old code** and is flagged
`stale code`. It does not vacate anything, because it did not leave — only its
classification did. Only codes that never reached ERPNext are re-issued, and only
those create a vacancy. ⚑

### 4.5.5 The one ambiguity I need you to settle ⚑

"Used next time rather than the next immediate" reads two ways, and they behave
very differently:

* **(a) Claimed by matching** — the reservation waits indefinitely for something
  that genuinely matches what left. This is what I have built, and it is
  consistent with the group rule you gave me earlier.
* **(b) Claimed by queue** — the reservation goes to the *second* arrival
  regardless of what it is, simply skipping the immediate next one.

I have implemented **(a)**, because (b) hands a meaningful number to an arbitrary
item and re-creates the exact problem the reservation exists to prevent. If you
meant (b), it is a small change — but say so before R3.

---

## 5. Version control on the master

The item master is **the same table as the public directory, editable**. Same
rows, same columns, the same cascading dropdowns from §4.

Every save writes a **new version** rather than overwriting the row:

```
item_version(item_id, version_no, snapshot, changed_by, changed_at, summary)
```

* Nothing is ever destroyed. Version `n` is always recoverable.
* **Revert to version k** does not delete versions `k+1…n`. It writes the
  contents of `k` as a **new version `n+1`**, so a revert can itself be reverted.
* Activity shows field-level before-and-after, filterable by person, item and
  date, with **Revert** on each entry.

**Where this collides with ERPNext, and how it is resolved.** A revert cannot
restore a code that ERPNext has already frozen — submitted Frappe documents are
immutable. So a revert restores **every field it can**, leaves the frozen code
alone, and **says plainly what it could not restore**. A silent partial revert
would be worse than no revert at all. ⚑

This is what replaces an approval workflow. You chose no gate; reversibility with
a full trail is the honest substitute.

---

## 6. The code, restated

```
HEAD(2) SUB(2) GROUP(3) SPEC1(2) SPEC2(2) SPEC3(2) SPEC4(2) VENDOR(2)
```

1. Head and sub-head are two letters each; the 4-letter prefix is company-unique.
2. Group is three digits, restarting inside each sub-head.
3. Each group declares what its four slots mean, and may declare none.
4. Vendor is last, only where the group declares it — currently four battery groups.
5. A gap between two present values is `00` — "this group has this
   specification, and for this item it does not apply".
6. Trailing absences are dropped, never padded.

Valid lengths: **7, 9, 11, 13, 15, 17**.

```
AOHK00106            9   Admin & Office · House Keeping · Air Freshener · Odonil Lavender
RMBS0010206100007   17   Raw Materials · Battery Scrap · Battery Pack
                         Form Factor 02 Cylindrical · Chemistry 06 NMC · Size 10 Gen-3
                         Capacity 00 not applicable · Maker 07 LG
```

The second is from your live ERPNext. The grammar describes what you already do.

---

## 7. API

Base `/api/v1`. JSON both ways. Success `{"ok":true,…}`; failure
`{"ok":false,"error":{"code","message","detail"}}` with stable machine codes —
`AUTH_REQUIRED`, `BAD_CODE`, `NOT_FOUND`, `AMBIGUOUS`, `CONFLICT`, `FROZEN`,
`VALIDATION`, `UPSTREAM`.

### Public — no authentication

| Method | Path | Purpose |
|---|---|---|
| GET | `/decode?code=` | Decode into meaning |
| GET | `/dictionary/groups` · `/dictionary/group/{id}` | Browse the dictionary |
| GET | `/directory` · `/directory/{code}` | Search items |
| GET | `/meta` | Heads, sub-heads, valid lengths |

### Authentication

`POST /auth/login` · `/auth/logout` · `GET /auth/me` · `POST /auth/password`.
A wrong username and a wrong password return the same message, so the endpoint
cannot be used to discover who has an account.

### Creator — session required

| Method | Path | Purpose |
|---|---|---|
| POST | `/resolve` · `/resolve/batch` | Text → proposal. Writes nothing |
| POST | `/ingest` | Upload an invoice → extracted lines |
| POST | `/commit` | Accept a proposal → mint the code |
| GET | `/cascade/subheads?head=` · `/cascade/groups?subhead=` · `/cascade/slots?group=` | Feeds the dropdowns in §4 |
| POST | `/item/{code}` | Edit fields — never the code |
| GET | `/item/{code}/versions` · POST `/item/{code}/revert` | §5 |
| POST | `/head` · `/subhead` · `/group` · `/specval` · `/rename` · `/alias` | Dictionary changes |
| POST | `/group/{id}/move[/preview]` · `/merge[/preview]` · `/retire` | Restructuring |
| GET | `/audit` · `/mappings` · `/vacancies` · `/export` | Trails and export |
| POST | `/erp/pull` · GET `/erp/ping` | ERPNext |

### The two calls that matter

```jsonc
// POST /api/v1/resolve
{ "text": "Cylindrical NMC battery pack 32700 3.6 Kwh make LG",
  "hints": { "hsn": "85076000", "uom": "Nos", "vendor": "LG" } }

{ "ok": true, "proposal_id": "p_8f3a1c",
  "outcome": "new",                       // exists | new | needs_input
  "code": "RMBS0010206040707",
  "phases": {
    "1": { "result": "not_found", "near_misses": [ … ] },
    "2": { "result": "matched", "confidence": 87, "method": "fuzzy",
           "group": { "id": 412, "name": "Battery Pack", "code": "001" } },
    "3": { "slots": [ { "slot": 1, "label": "Form Factor", "value": "Cylindrical",
                        "code": "02", "confidence": 100, "method": "exact" }, … ],
           "vendor": { "label": "Maker", "value": "LG", "code": "07" } } },
  "blockers": [], "llm_used": false }
```

`outcome` has exactly three values and drives the whole interface: **`exists`**
show it and stop · **`needs_input`** blockers listed, Submit disabled ·
**`new`** Submit enabled.

```jsonc
// POST /api/v1/commit
{ "proposal_id": "p_8f3a1c", "idempotency_key": "c_2f91b0",
  "overrides": { "name": "Battery Pack - Cylindrical NMC 32700 3.6kWh (LG)" },
  "push_to_erp": true }

{ "ok": true, "code": "RMBS0010206040707", "item_id": 1948, "version": 1,
  "created_by": "anuraag", "created_at": "2026-08-06T18:12:07",
  "erp": { "pushed": true, "dry_run": false },
  "learned": [ { "type": "alias", "term": "3.6 KWH", "bound_to": "Capacity/07" } ] }
```

Replaying an idempotency key returns the original code rather than minting a
second. A stale proposal — the dictionary moved underneath it — is rejected with
`CONFLICT`, forcing a re-resolve rather than a write against an expired
assumption.

**For the larger system: never construct a code string yourself.** Always
`/resolve` then `/commit`. There is deliberately no endpoint that accepts a
hand-built code — that is the guarantee that keeps the master trustworthy.
Machine-to-machine callers will need service tokens rather than the browser
cookie; not designed yet, because it depends on what the larger system turns out
to be. ⚑

---

## 8. Operator instructions

The text for the one-page sheet beside their screen.

**Steps.** Log in → paste the description or drop the invoice → read the outcome
on each row → check the three phases → fix anything wrong with the dropdowns →
Submit.

**Do**

* Trust "already exists". At 90% or better it is almost certainly the same item —
  read the name it matched before overriding.
* Read the near-misses when nothing is found. The right answer often sits in the
  eighties, just below the automatic threshold.
* Give a specification an honest value, **or mark it not applicable**. Both are
  fine. Leaving it unanswered is what blocks you.
* Name the item the way the next person will search for it: brand, what it is,
  size.

**Do not**

* **Do not create a new group because the wording differs.** "Air Freshner" and
  "Air Freshener" are the same group. This is the most damaging mistake available
  to you, and undoing it means merging groups and re-coding items.
* **Do not put a vendor on an item whose group does not ask for one.** Only the
  battery groups carry a maker.
* **Do not take the supplier from the top of the invoice.** Only a vendor named
  on the line counts.
* **Do not force a value into a slot just to unblock Submit.** If it does not
  apply, mark it not applicable — that is what `00` means.
* **Do not re-key a code into ERPNext by hand.** Submit does it correctly.

**If unsure, stop and ask.** An item created wrongly and then transacted cannot
be re-coded — its code is frozen from that moment.

---

## 9. Data model

| Table | Holds |
|---|---|
| `head`, `subhead` | Taxonomy top; the 4-letter prefix is unique |
| `grp` | 889 groups; `code3` unique per sub-head; `labels` names the four slots |
| `specval` | 2,490 values, scoped to `(group, slot)` |
| `item` | 1,947 items; `frozen` drives freeze-on-first-use; `stale_code` marks post-move drift |
| **`item_version`** | **New.** Full snapshot per save — the whole of §5 |
| `code_ledger` | 2,946 reservations. Every code ever issued or seen live. Reuse impossible by construction |
| `vacancy` | Parked group numbers with the departed name, for the 88% claim test |
| `alias` | Learned wording — what makes corrections stick |
| `audit` | Who, when, what, before, after |
| **`user`, `session`** | **New.** scrypt hashes; HMAC-signed session tokens |

---

## 10. Security posture

| Concern | Position |
|---|---|
| Password storage | scrypt, per-user salt. Never plaintext, never recoverable |
| Passwords on the wire | **In the clear** — plain HTTP on the LAN. Accepted for an internal tool. Do not reuse passwords from elsewhere |
| Session | HMAC-signed random token, HttpOnly, SameSite=Strict, 12h |
| Authorisation | Two tiers, enforced server-side on every mutating endpoint, not by hiding buttons |
| Attribution | From the session only. The forgeable `X-User` header is deleted |
| SQL injection | Parameterised queries throughout |
| Uploads | Type and size capped; parsed, never executed |
| ERP credentials | Per-creator before PROD. A shared account is a blocker, not a preference |
| Backup | Daily copy off the host. The host is a single point of failure by design |

---

## 11. What I need from you

| Input | Unblocks | Pts |
|---|---|---:|
| An LLM API key — Gemini Flash is free at this volume | **The primary matcher.** Without it the tool runs on rules alone | 11 |
| ERP admin rights to create the service account and role | The whole guardrail ([ERPNEXT_API.md](ERPNEXT_API.md) §5) | 8 |
| A decision on Wahni's parallel spec framework in ERPNext | Whether the decode lives in ERPNext too | 5 |
| Ten real scanned or photographed invoices | OCR proven end to end | 10 |
| Written go-ahead to write into ERPNext UAT | Items actually created | 15 |
| An ERPNext login per creator | PROD go-live | 8 |
| Which machine hosts it, with admin rights | Deployment | 9 |
| Your judgement on which of 889 groups are duplicates | The clean-up | 23 |
| CA verification of specialised HSN codes | HSN backfill | 5 |
| Two weeks after go-live, and the creators' time | Training and the watch | 11 |
| | **Total blocked** | **90** |

**If you unblock only one thing, make it the group de-duplication judgement.**
Every month it waits, more codes freeze and the clean-up becomes permanently
more expensive. It is the only item on this list that gets worse with time.

---

## 12. Decisions needing your word ⚑

| # | Decision | My recommendation |
|---|---|---|
| 1 | **Accounts are provisioned, never self-registered.** I generate the admin account at install; the admin creates each creator and hands over the credentials in person. No sign-up, no email reset, no OTP channel | Confirmed with you — building this |
| 2 | **Freeze on first use** — codes live in ERPNext are never rewritten, only flagged stale | Adopt |
| 3 | **A revert restores fields, never a frozen code**, and says so plainly | Adopt |
| 4 | **Vacancies are claimed by matching, not by queue position** (§4.5.5) | Adopt (a) |
| 5 | **LLM provider** — now the primary matcher, so quota matters more | Gemini Flash; keep Ollama configured as the offline fallback |
| 6 | **The 614 non-grammar codes** — leave frozen, or one-time re-code | Leave frozen |
| 7 | **Populate Wahni's ERPNext specification fields?** ([ERPNEXT_API.md](ERPNEXT_API.md) §1.1) | Populate — retires a half-built parallel system |
| 8 | **Map our taxonomy onto ERP Item Groups at sub-head level?** | Yes — 889 groups would wreck their reporting tree |
| 9 | **Service tokens** for machine callers from the larger system | Design when that system's shape is known |

---

## 13. Delivery

| Run | Delivers | Pts |
|---|---|---:|
| **R1** | MiniMines theme and logo, split landing page, stats block removed, public API scope locked | 28 |
| **R2** | Login, sessions, attribution from session, user management, creator shell | 18 |
| **R3** | Create screen with the three phases, invoice upload, **LLM-first matching with fallback**, cascading dropdowns, live code recomputation, **item-level number reservation**, edit before submit | 50 |
| **R4** | Item master as editable directory, revision history, one-click revert, activity with diffs, vacancy visibility, LLM caching | 39 |
| **R5** | `/api/v1` frozen with a real error contract, sub-head→Item Group mapping, UoM/HSN validation against ERP, smoke suite, deployed to the internal server, starts on boot, daily backup | 46 |
| | | **181** |

**Every run leaves the app working.** If we stop after R3 you still have a usable
tool, just without version control. Nothing is left half-wired between runs.

**Where this is thinner than the six-sprint plan:** less testing per feature. I
will exercise each run against the real seeded data — 889 groups, 1,947 items,
2,677 live ERP codes — and anything thin will be named in the handover rather
than quietly hoped over.
