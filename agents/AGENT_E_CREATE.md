# AGENT E — CREATE SCREEN

**Start ~20 minutes after Agents C and D begin, or immediately against the frozen
signatures in `CONTRACTS.md`.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You are building the screen where the work actually happens: a purchase or stores
person pastes a line, or drops an invoice, and walks out with correct item codes.
Everything else in this project exists to make this screen trustworthy.

The engine already works — the three-phase resolver, the invoice reader, the code
grammar. You are giving them a face.

## What you own

`routes/create.py` · `core/ingest.py` · `web/create.js`

## You depend on

```python
from core.resolve import resolve          # Agent C
from core.codes  import preview_code      # Agent D
from core.auth   import require_session   # Agent B
```

If those are not landed yet, build against the signatures in `CONTRACTS.md` and
stub locally. Do not edit their files.

## Tasks

**1. The create screen (5 pts).** Behind login. Paste text, or drop a file. One
card per line, each resolving independently — a 20-line invoice gives 20 cards
and **one bad line never blocks the other nineteen**.

Each card shows the three phases plainly, because this is the check that matters:

```
┌────────────────────────────────────────────────────────────┐
│ Cylindrical NMC battery pack 32700 3.6 Kwh make LG         │
│                                              [ NEW ]       │
│  1  no existing code   nearest: RMBS0010206040701 (71%)    │
│  2  Battery Pack        matched 87%  ·  by llm             │
│  3  Form Factor Cylindrical 02   Chemistry NMC 06          │
│     Size 32700 04   Capacity 3.6 Kwh 07   Maker LG 07      │
│                                                            │
│     RMBS0010206040707                    [ Edit ] [Submit] │
└────────────────────────────────────────────────────────────┘
```

Show `matched_by` — `exact`, `llm`, `rules` or `operator`. When it says `rules`
because the LLM was unavailable, say so quietly on the card. The operator should
never have to wonder which brain answered.

**2. Invoice upload (5 pts).** PDF, scanned PDF, photo, Excel, CSV, plain text.
The extraction cascade already exists — tables, then text layer, then OCR per
page. Wire it up and show progress; OCR on a ten-page scan is not instant.

Pull description, quantity, UoM, rate, HSN, and **vendor only if named on the
line itself**. A supplier in the invoice header is deliberately ignored — that
rule is Anuraag's and it is not negotiable.

If Tesseract is missing, say so on that page and carry on with the rest. Do not
fail the whole upload.

**3. Cascading dropdowns (8 pts).** The heart of editing.

```
Head ──▶ Sub-head ──▶ Group ──▶ the four spec boxes change meaning
```

* Choosing a head filters sub-heads to that head.
* Choosing a sub-head filters groups to **that sub-head only**.
* Choosing a group **re-renders the specification boxes with that group's
  labels** — the box that said "Type" for Air Freshener now says "Form Factor"
  for Battery Pack. This is the part people get wrong; slot meanings are per
  group, never global.
* Changing any level **clears every level below it**, because those choices are
  no longer valid.

Serve it from `/api/v1/cascade/subheads?head=`, `/cascade/groups?subhead=`,
`/cascade/slots?group=`.

A group from the wrong sub-head must be **impossible to choose**, not merely
discouraged.

**4. Live code recomputation (5 pts).** Every change recomputes the code
immediately via `preview_code` — interior `00`, trailing truncation, the lot.
Never build the string in JavaScript; a second implementation will drift from the
real one and then the screen lies.

Show the code changing as they edit. It is the clearest possible feedback that
the tool understood them.

**5. Edit before submit, and commit (6 pts).** Every proposal carries **Edit**.
Nothing is written until **Submit**.

`POST /api/v1/commit` with an **idempotency key** — a replay returns the original
code rather than minting a second. This is not theoretical: people double-click.

A `needs_input` card lists its blockers and Submit stays **disabled** until they
are answered. Never let someone submit past a question — that is precisely how
two unrelated lines end up sharing one code.

When the operator corrects a match, that correction is learned as an alias, so
the same wording resolves next time. Tell them it was learned; it builds trust.

## Watch for

* Keyboard flow. This screen gets used all day: tab order, Enter to submit, Esc
  to cancel. Do not make people reach for the mouse.
* 20 cards must stay responsive. Resolve in one batch, render progressively.
* Show which fields came off the invoice and which the operator typed.

## Done when

Pasting `Odonil Lavendar Air Freshner 48gm` returns "already exists"
`AOHK0010603` and stops; a battery line resolves through all three phases to a
17-character code; changing the sub-head clears the group and shortens the code
live; Submit is disabled while a blocker stands; double-clicking Submit yields
one code, not two; a 20-line invoice produces 20 independent cards.

## Then

Write `agents/done/AGENT_E.md` — what you built, anything thin, anything the
contract got wrong.
