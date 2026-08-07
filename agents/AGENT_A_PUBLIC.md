# AGENT A — PUBLIC FACE

**Start after Agent 0 reports done. Runs in parallel with B, C, D, G, H.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You are building the page **everyone in the company sees**. Most people never
create an item code; they only need to look one up or work out what one means.
That is your page, and it needs no login at all.

This is also the only part served to the LAN. Creation lives in the installed
desktop application; your page is the read-only window onto the same data.

## What you own

`routes/public.py` · `web/public.html` · `web/public.js` · you may extend
`web/theme.css` (Agent 0 created it)

## The page

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⬢ MiniMines   Item Code Studio                          [ Log in ]  │
├───────────────────────────────┬──────────────────────────────────────┤
│  DECODE A CODE                │  FIND AN ITEM                        │
│  ┌─────────────────────────┐  │  ┌────────────────────────────────┐  │
│  │ RMBS0010206100007       │  │  │ search code, name, description │  │
│  └─────────────────────────┘  │  └────────────────────────────────┘  │
│  RM   Raw Materials           │  AOHK0010603  Air Freshener –        │
│  BS   Battery Scrap           │               Odonil Lavender        │
│  001  Battery Pack            │  COMS268      M-Seal 100 g           │
│  02   Form Factor Cylindrical │  RMBS00102…   Battery Pack –         │
│  06   Chemistry   NMC         │               Cylindrical NMC        │
│  10   Size        Gen 3 pack  │                                      │
│  00   Capacity    (not appl.) │  browse the dictionary →             │
│  07   Maker       LG          │                                      │
└───────────────────────────────┴──────────────────────────────────────┘
```

Two halves of equal weight. **Log in** top-right — it goes to the login page but
does nothing else; Agent B owns what happens next.

**Remove the stats block** that currently sits in the bottom-left corner of the
existing UI. Anuraag asked for it gone. Nothing should shift or leave a hole.

## Tasks

**1. Split landing page (8 pts).** Responsive: side by side on a normal screen,
stacked on a narrow one. Uses Agent 0's CSS variables — do not hardcode colours.
The logo is `web/assets/minimines-logo.svg`.

**2. Decoder half (3 pts).** `GET /api/v1/decode?code=`. Show head, sub-head,
group, then every specification with **its label in that group** (slot 1 is
"Type" for Air Freshener but "Form Factor" for Battery Pack — the label comes
from the group, never from a global list). `00` renders as "not applicable", not
as a blank or a zero.

A well-formed code with no item still decodes structurally — show the meaning and
note that nothing has been issued against it. A malformed code explains why in
plain words: *"17 characters expected 7, 9, 11, 13, 15 or 17"*, not `BAD_CODE`.

Verify against a real one: `RMBS0010206100007` → Raw Materials · Battery Scrap ·
Battery Pack · Cylindrical · NMC · Gen 3 pack · Capacity not applicable · LG.

**3. Directory half (5 pts).** `GET /api/v1/directory?q=&limit=&offset=`.
Searches code, name and description across 1,947 items. Debounce about 200 ms.
Show code, name, group, UoM, HSN, status. Clicking a row opens a read-only detail
card. Paginate — never dump all rows.

**4. Dictionary browse (3 pts).** `GET /api/v1/dictionary/groups` and
`/dictionary/group/{id}`. Search 889 groups; opening one shows its four
specification slots with **that group's** labels and every value with its
two-digit code. Reachable from the directory half.

**5. Read-only, enforced (5 pts).** Your routes never call `require_session` —
they are public by design. But they must also be **incapable of writing**: no
INSERT, no UPDATE, no DELETE anywhere in `routes/public.py`. Nothing on your page
links to a creator screen except the Log in button.

## Watch for

* **Do not reimplement decoding.** Call `core.codes.parse` and look labels up
  from the group. A second decoder in JavaScript will drift from the real one.
* The label for a slot is per group. Getting this wrong makes the decoder subtly
  lie, which is worse than it failing.
* Long names and 17-character codes must not break the layout.
* Test logged out, in a private window. If any creator function is reachable,
  that is a bug and it is yours.

## Done when

Both halves work against the real seeded database; `RMBS0010206100007` decodes
correctly including the interior `00`; search returns sensible results for
`odonil`, `mseal` and `battery`; the stats block is gone; the page is usable on a
narrow window; nothing writes.

## Then

Write `agents/done/AGENT_A.md` — what you built, anything thin, anything the
contract got wrong.
