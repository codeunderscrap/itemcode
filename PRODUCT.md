# Item Code Studio — product description

**Prepared for Anuraag · 5 August 2026 · v1.0 (working build, not a mock-up)**

A small application that sits open beside ERPNext on a purchase or stores
person's screen. They paste a line or drop an invoice; it works out whether the
item already has a code, and if not, builds one from the company's own coding
logic and writes it into ERPNext once they press Submit.

Everything below is implemented and running. Numbers quoted are from your actual
files, not estimates.

---

## 1. What problem it solves

Right now the coding logic lives in a spreadsheet and in a few people's heads.
The consequences are visible in your live ERPNext data:

| What the live system looks like today | Count |
|---|---|
| Items in ERPNext | 2,677 |
| Codes matching the intended grammar | 1,678 |
| Codes made outside the grammar (`COMR0112`, `COST0006`, 4-letter + 4-digit) | 614 |
| Codes that are not codes at all (`LCO-1`, `IBC TANKS`, `Iron`, `08204`, `A4 s…`) | 175 |
| Items disabled | 707 |

The 614 non-grammar codes were created by `purchase.team@`, `prashanth@` and
`purchase@` — people doing their job with no tool to do it correctly. That is
the gap this fills. It is not a governance problem, it is a tooling problem.

**Who uses it:** anyone who creates items — purchase, stores, spoke operations.
No approval workflow, no gatekeeper, by your instruction. Everyone can create a
head, sub-head, group or spec value. What keeps it safe is not permission, it is
that every action is previewed, attributed, reversible and logged.

---

## 2. The code grammar

```
HEAD(2)  SUB(2)  GROUP(3)  SPEC1(2) SPEC2(2) SPEC3(2) SPEC4(2)  VENDOR(2)
  A O      H K     001        06                                          →  AOHK00106      (9)
  R M      B S     001        02       06       10       00        07     →  RMBS0010206100007 (17)
```

**Length is variable, never padded.**

* A specification that is missing *between* two present ones is written `00`.
* Specifications missing at the *tail* are simply dropped — the code ends there.
* Vendor is the last position, so any code carrying a vendor is necessarily the
  full 17 characters.
* Valid lengths are therefore exactly **7, 9, 11, 13, 15, 17**. Anything else is
  rejected as malformed.

This is not a new rule — it is already how your live data behaves.
`RMBS0010206100007` from your ERPNext export decodes as Battery Pack /
Cylindrical / NMC / Gen-3 pack / **Capacity = 00, not applicable** / Maker = LG.
The tool reads your existing codes correctly on day one.

**Specification slots mean different things in different groups.** Air Freshener
uses Type and Size. Battery Pack uses Form Factor, Chemistry, Size, Capacity and
Maker. Desktop uses Make, Model, RAM, ROM. The meaning of each slot is stored
per group, and the decoder reports it in words.

**Vendor is part of item identity, and only where the group says so.** Of your
889 groups, 4 declare a vendor slot (labelled "Maker") — the battery ones. Same
material from two makers gets two codes there, one code everywhere else. Vendor
is read from the invoice **line**; a supplier named only in the invoice header is
deliberately ignored.

**Prefix collisions resolve automatically.** `Capital Equipment / Electrical` and
`Capital Equipment / Engineering` both want `CEEL`/`CEEN`-style initials. The
tool walks a candidate ladder — initials of the first two words, first two
letters, first letter plus each following letter — and takes the first
combination not already used by any group *or* by any code already live in
ERPNext. Your existing oddity `CEEU` for "Equipment & Machinery" is preserved
exactly as-is, because seeded prefixes are read from the codes you already
issued, never re-derived.

**Group numbers restart inside each sub-head**, so `001` under House Keeping and
`001` under Battery Scrap are different groups. 999 groups per sub-head; you are
currently at 46 sub-heads and 889 groups.

The `-R` / `-W` / `-F` state suffixes are gone, as instructed.

---

## 3. The three phases

Every line runs the same pipeline, and the operator sees all three before
anything is written.

### Phase 1 — does a code already exist?

If yes, the pipeline **stops**. An existing code is never changed. Four checks
in order:

1. A well-formed code appearing in the text itself.
2. Exact match after normalisation, against the app master.
3. Exact match after normalisation, against **live ERPNext** (all 2,677 codes are
   loaded, including the 175 junk ones — so the tool can never mint a code that
   collides with something already live).
4. Semantic match at ≥ 90 %.

Below 90 % the close calls are still shown, ranked, with a one-click *use this*
so the operator can accept a match the machine was not confident enough to take.

Working example: `"Odonil Lavendar Air Freshner 48gm"` — two spelling mistakes
and different word order — returns `AOHK0010603` at 96 % and stops.

### Phase 2 — do the head, sub-head and group exist?

The group is resolved first, because it carries the most signal. It is matched
against three things: the group's own name, any aliases learned from past
corrections, and the names of items **already inside that group** (at a 10 %
discount, so one odd item cannot hijack a group). That last input is what lets
"Mseal epoxy sealant" find the group that already holds M-Seal rather than
latching onto a group literally called "Seal".

If no group is close enough, a new one is proposed. The *home* for it is not
guessed by comparing invoice text to the word "Consumables" — that scores near
zero and always will. Instead it is inherited from the neighbourhood of the
closest group candidates, and the operator confirms it from a dropdown.

### Phase 3 — what are the specifications?

For every slot the group declares, the value is matched against that group's
existing values, and a two-digit number is either found or allocated.

**A slot the group declares but whose value cannot be determined is a question,
never an auto-allocated number.** This matters: without that rule, two unrelated
invoice lines silently receive the same code. (It happened during the build, and
it is exactly the class of error this catches.)

Working example: `"Cylindrical NMC battery pack 32700 3.6 Kwh make LG"` resolves
Form Factor = Cylindrical (02), Chemistry = NMC (06), Size = 32700 (04),
Capacity = 3.6 Kwh (07), Maker = LG (07) → `RMBS0010206040707`.

Nothing is written until Submit. Then the item is stamped with the operator's
name, the code is entered in a ledger, and — if enabled — the Item is created in
ERPNext.

---

## 4. Semantic matching — three layers

**Layer 1, deterministic normaliser.** Case, punctuation and unit spelling are
folded (`ML/millilitre`, `GM/G/grams`, `NOS/PCS/EA/units`, and about forty
more), numbers are separated from units (`48gm` → `48 GM`), domain synonyms are
collapsed (`Mseal → M SEAL`, `LiFePO4 → LFP`, `HDD → HARD DISK`, `Stationary →
Stationery`), and noise words are dropped (`QTY`, `MRP`, `INCL`, `SUPPLY OF`).

**Layer 2, fuzzy.** Three views blended rather than one:

```
score = 0.45 × token_set_ratio      (rewards subsets)
      + 0.30 × token_sort_ratio     (survives typos and word order)
      + 0.25 × soft_jaccard         (punishes length mismatch)
```

The blend exists for a reason. `token_set_ratio` alone scores the junk ERP row
literally named `NMC` at **100 %** against "cylindrical NMC battery pack 32700".
Blended, it scores 55 and is correctly rejected. Soft-Jaccard counts a token as
shared if its closest counterpart is within 85 % — so a typo still counts, but a
subset does not.

**Layer 3, LLM — only below 60 %**, exactly as you specified. Above 60 %, fuzzy
decides and the LLM is never called. The prompt sends the invoice text and the
ranked shortlist and asks for one JSON answer; it is told that a different size,
grade, chemistry or brand is **not** the same thing. It can only pick from the
shortlist or say "none of these" — it can never invent a code.

Provider is a one-line config change: `anthropic` (Haiku 4.5, roughly ₹0.02 per
call), `gemini` (Flash has a free tier), `openai`, or `ollama` for fully local
and free. **Currently set to `none`** — the tool runs on fuzzy alone until you
choose. Nothing else changes when you switch it on.

**It learns.** When an operator overrides a match, the wording they rejected or
accepted is stored as an alias against that entity, so the same phrasing
resolves correctly next time without an LLM call. Renaming anything keeps the
old wording as an alias automatically, so historic invoices still match.

---

## 5. Invoice intake

Drop a file or paste text. Supported: **text PDF, scanned PDF, photo
(JPG/PNG/TIFF), Excel, CSV, plain text.**

PDFs are read table-first, then text-layer, then OCR per page — only pages with
no text layer go through OCR, which keeps it fast. Photos go straight to OCR.

Per line the tool extracts **description, quantity, UoM, rate, HSN/SAC, and
vendor-if-named-on-the-line**. Header rows, GST lines, bank details, totals and
signatures are filtered out. A 20-line invoice produces 20 independent
proposals, each resolved separately, each submitted separately — one bad line
does not block the other nineteen.

*OCR needs Tesseract-OCR installed on the host machine (free, one installer). If
it is absent the tool says so on that page rather than failing silently.*

---

## 6. Item master

The working master lives in the app, not in a spreadsheet. Searchable by code,
name or description; filterable by status; exportable at any time to an Excel
workbook with four sheets — **Item_Master**, **Item_Code_Master** (the full
dictionary with per-group spec legends), **Code_Mapping** (every old → new code)
and **Audit_Trail**. Same shape as the workbook you use now, so it drops into
existing habits.

Field edits — name, description, UoM, HSN, tax — are recorded and **never change
the code**. That is enforced in the API, not just the UI.

---

## 7. Structural changes — your workaround, assessed

You asked me to check your model and offer two alternatives. Here is the honest
read.

### Your model

> When a group moves to another sub-head, it takes the next free number there.
> The number it leaves behind is left alone until an item arrives that matches
> that old group, and then it is given to that.

**This is sound, and it is what I built.** The reasoning: your codes are
positional and decodable, so a vacated number is not just a number — it carries
the meaning of the group that left. Handing `041` under House Keeping to an
unrelated group next week means an old printed label, an old GRN and a new one
all read differently. Parking it until something semantically close arrives
preserves that. Implemented as a *vacancy* record; a future group claims it only
when its name scores ≥ 88 % against the group that left. Otherwise numbering
continues past it.

**The one gap in your model — and the real risk you were pointing at.** Moving a
group changes the *prefix*, so every item code inside it changes. If those codes
are already on submitted ERPNext documents, rewriting them is either impossible
(submitted documents are immutable in Frappe) or expensive (a rename cascades
across every linked document). Your rule says what happens to the group number
but not what happens to the items.

**So the model I implemented adds one rule: freeze on first use.**

* An item code **live in ERPNext** is permanent. A structural change never
  rewrites it. It is kept, and flagged `stale code` — meaning it no longer
  decodes to where the item now sits.
* An item code that has **never left the app** is regenerated freely.
* Every change is previewed before it runs, and every old → new pair is written
  to Code_Mapping.

Tested on your real data — moving group *Tissue* from House Keeping to
Stationery:

```
AOHK041  →  AOST047
  1 code re-issued        AOHK04101 → AOST04701
  7 codes kept            live in ERPNext, flagged stale
  number 041 parked under House Keeping
```

That is the correct outcome. Seven items that are already transacting are left
alone; one that only exists in the app is corrected.

### Alternative A — never re-code (append-only)

The code is a meaningless permanent ID the moment it is issued. Structural
changes update the group, head and sub-head *fields* but never touch a single
character of any code. Decoding is done by lookup, not by reading the string.

*Advantage:* zero risk, ever. Nothing downstream can break.
*Cost:* codes drift from meaning permanently. Within a year the prefix stops
telling you anything, and you lose the property that made you design a
positional code in the first place.

### Alternative B — dual key

The item carries two identifiers: a permanent opaque ID that ERPNext uses as
`item_code` and that never changes, plus a *display code* that is regenerated
freely on every structural change and shown on reports, labels and screens.

*Advantage:* full freedom to restructure with zero transactional risk. This is
how large ERP installations handle it.
*Cost:* two numbers per item forever. Your team currently reads the code
directly off the ERPNext screen; this puts a lookup between them and the
meaning, and people will start quoting the wrong one.

### Recommendation

**Stay with your model plus freeze-on-first-use** — what is built. It gives you
the decodable code you want, protects live transactions absolutely, and the
`stale code` flag makes the drift visible and countable instead of silent. If
the count of stale codes ever grows uncomfortable, Alternative B is the upgrade
path, and the data model already supports it.

**One thing to plan for now, while the master is still small:** you said current
grouping is too wide with heavy duplication. The clean-up is far cheaper today
than after another 1,000 items are transacted, because today most codes are not
yet frozen. The tool has **merge** for exactly this — fold a duplicate group into
the right one, spec values are matched or added, codes re-issued except where
frozen.

Also available: rename (labels only, old wording kept as a search alias), retire
(refuses while items still sit in the group), and move.

---

## 8. ERPNext integration

Read is used constantly, write only on Submit.

* **Read** — pulls the full Item list so phase 1 checks against live truth. Your
  2,677 codes are loaded now from the export; one click re-pulls them live.
* **Write** — creates the Item with your code as `item_code`, plus item name,
  item group, stock UoM and HSN.
* **Rename** — used only when a structural change re-codes an unfrozen item.

Three states in `config.json`: **off** (default), **dry-run** (shows the exact
payload it would post, writes nothing), and **live**. My recommendation: run
dry-run against UAT for a week, then live on UAT, then PROD.

Credentials are per-installation today. Making each operator authenticate with
their own ERPNext login — so the ERP audit trail names the real person rather
than a shared account — is a small change and worth doing before PROD.

---

## 9. How it runs

You did not want a central server, and you wanted colleagues on the same Wi-Fi
to be able to use it. Both are satisfied:

```
python server.py

  this computer   http://localhost:8756
  same Wi-Fi      http://192.168.x.x:8756
```

One desktop hosts it. Everyone else opens that address in a browser — nothing to
install on their machine. **One SQLite file behind it is the single source of
truth, which is what makes it impossible for two people to be handed the same
code.** All writes are serialised.

**Python standard library only** — no pip install, no npm, no build step, no
internet needed. `rapidfuzz` sharpens matching and `pdfplumber` / `pytesseract`
handle PDFs and OCR; all three are already on your machine, and the app degrades
gracefully rather than crashing if any is missing.

**Worth being straight about:** "no central server" and "everyone uses it over
Wi-Fi" are the same thing wearing different clothes. The host desktop *is* the
server. Practically that means: if that machine is off or asleep, nobody can
issue a code, and the database lives on one machine's disk. Mitigations —
`data/itemcode.db` copies safely while running (WAL mode), and the Excel export
is a complete rebuildable snapshot. If it becomes important, the same code runs
unchanged on a small always-on box.

---

## 10. What is loaded right now

| | |
|---|---|
| Heads / sub-heads | 11 / 46 |
| Item groups | 889 |
| Specification values | 2,490 |
| Coded items | 1,948 |
| Live ERPNext codes registered | 2,677 |
| Codes reserved against reuse | 2,947 |

Sources: `_Item master .xlsx` (sheets `final Item_Code_Master` and
`Item_Master`), `Item.xlsx`, and `Item code specification.xlsx`.

Re-seeding at any time is one command: `python seed.py --rebuild`.

---

## 11. Open items

1. **10 items have no matching group** — their head/sub-head/group text does not
   line up with the dictionary. Visible in the master; worth a look.
2. **175 junk ERP codes** (`LCO-1`, `IBC TANKS`, `Iron`, bare numbers). They are
   registered so nothing collides with them, but they should be disabled or
   re-coded in ERPNext.
3. **614 non-grammar codes** created by the internal team. They work, they are
   frozen, they do not decode. Leave them and let the tool prevent more, or plan
   a one-time re-code — that is a business call, not a technical one.
4. **LLM provider not chosen.** Fuzzy-only until you pick.
5. **Group clean-up** — your own point about width and duplication. Best done
   now, before more codes freeze.
6. **Tesseract-OCR** to be installed on the host machine for scanned invoices.
7. **Per-user ERPNext credentials** before any PROD write.

---

## 12. Scope note

Delivered as a working application against your real data, not a prototype. Two
areas are deliberately shallow and should be exercised before rollout: **OCR
accuracy on your actual scanned invoices** (untested — I have no sample), and
**LLM layer behaviour**, which cannot be judged until a provider is configured.
Everything else in this document has been run end to end.
