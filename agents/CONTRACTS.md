# FROZEN CONTRACTS — read before writing any code

**Every agent must obey this file. Nobody may change it.**
If you believe something here is wrong, stop and report it — do not edit it and
do not work around it. A silent divergence here breaks every other agent.

---

## 1. Decisions already locked by Anuraag

These are settled. Do not re-litigate, do not "improve", do not ask again.

| # | Decision |
|---|---|
| 1 | **Variable-length codes, never padded.** Interior gap = `00`, trailing gaps dropped. Valid lengths exactly 7, 9, 11, 13, 15, 17 |
| 2 | **Vendor is last, only in groups that declare it.** Read from the invoice **line**, never the header |
| 3 | **Spec slot meaning is per group.** Slot 1 of Air Freshener is "Type"; slot 1 of Battery Pack is "Form Factor" |
| 4 | **Group numbers restart inside each sub-head** |
| 5 | **Vacated numbers are claimed BY QUEUE, lowest-first.** The next arrival takes the lowest free number so no gap ever persists. *(This reverses an earlier semantic-matching design — see §4)* |
| 6 | **Freeze on first use.** A code live in ERPNext is NEVER rewritten by this tool |
| 7 | **No approval workflow.** Preview, attribution, version history and revert replace permission |
| 8 | **Two tiers, not roles.** Public read; login to create |
| 9 | **Accounts are provisioned by an admin.** No sign-up, no email reset, no OTP |
| 10 | **LLM-first matching, rules as guardrail and fallback.** The rules build the shortlist and hold the veto |
| 11 | **The API key is entered in Settings by the admin**, never hardcoded, never committed. Until a key exists the app runs **fuzzy-only** |
| 12 | **ERPNext gets Item access only.** No delete, no cancel, no transactions, ever |
| 13 | **No bulk migration to ERPNext.** Items are created one at a time, on demand |
| 14 | **No taxonomy mapping.** We do not push our 889 groups into ERPNext |
| 15 | **The app is a locally installed desktop application** doing its own OCR and matching, talking to a **cloud VPS** that owns the database and mints every code |
| 16 | **A local LAN server is the failsafe** when the VPS is unreachable. Offline minting is allowed **only** from a leased number block, or as a clearly-marked provisional code. Never an ERPNext write from the failsafe |
| 17 | **HTTPS is mandatory** and no secret ever lands on a desktop machine |

---

## 2. Deployment model — what you are building

**Decided 6 August 2026: the database lives on a cloud VPS, not on any desktop.**

```
┌──────────────────────────────────────────────────────────────────┐
│  CREATOR'S MACHINE — the installed desktop application           │
│                                                                  │
│   OCR (local)  ·  normaliser + fuzzy matching (local, on a       │
│   cached read-only copy of the dictionary)  ·  the login         │
│                                                                  │
│   HOLDS NO SECRETS. No LLM key, no ERPNext credential.           │
└────────────────────────────┬─────────────────────────────────────┘
                             │  HTTPS  (TLS mandatory)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  CLOUD VPS — the single authority                                │
│   • the SQLite database: ledger, master, dictionary              │
│   • the ONLY place a code is minted                              │
│   • proxies the LLM call and the ERPNext call, so the keys       │
│     live here and only here                                      │
│   • serves the public READ-ONLY page: decoder + directory        │
└──────────────────────────────────────────────────────────────────┘
```

**Why the split falls this way.** Anuraag wants OCR and the analytical engine
local — so they are. But a code may only be minted where the ledger is, and a
secret may only live where it can be protected. So: heavy local processing,
central authority, no keys on any desktop.

**SQLite stays.** One server process means one writer, which is exactly what
SQLite is good at. No Postgres, no new dependency.

**Never put the SQLite file on a network share.** Its locking is unreliable over
SMB and concurrent writers corrupt the file rather than merely conflicting. The
database is opened only by the process running on the VPS.

### Three tiers, and a failsafe — decided 6 August 2026

The VPS is not provisioned yet and may not always be reachable, so work must not
stop when it is down. There are **three tiers**:

```
  TIER 1   CLOUD VPS            the authority whenever it is reachable
     ▲                          (not yet provisioned — build vendor-neutral)
     │ syncs when it returns
  TIER 2   LOCAL LAN SERVER     ONE nominated machine on the office network.
     ▲                          Full replica. Becomes the authority only while
     │                          the VPS is unreachable.
  TIER 3   DESKTOP CLIENTS      never mint a code. Always talk to tier 1, or
                                to tier 2 when tier 1 is down.
```

**Exactly one machine may act as tier 2.** Not every desktop. A desktop client
that cannot reach either tier is read-only — it decodes and searches from its
cache and refuses to issue.

### How offline minting stays safe

Two authorities minting at once would produce duplicate codes, and a duplicate
that reaches ERPNext is permanent. Two mechanisms prevent it, and **both are
required**:

**1. Number leases.** While the VPS is reachable, the local server holds a
**lease** on a disjoint block of numbers per sub-head and group — for example
"positions 42–51 under AOHK041 belong to the local server". Offline, it mints
**only** from its lease. The VPS never issues a leased number to anyone else, so
the two number spaces cannot overlap. Leases renew on every sync.

**2. Provisional codes.** If the lease is exhausted, or none exists, an offline
code is issued **provisionally** — `item.provisional = 1` — and is:

* shown clearly as provisional in the interface, never presented as final;
* **blocked from being pushed to ERPNext**, which is unreachable anyway;
* replaced with a final code on the next sync, with the operator shown exactly
  what changed.

**The safety property this rests on:** when the VPS is unreachable, ERPNext is
unreachable too, so nothing minted offline can freeze while offline. Every
offline code stays re-writable until it syncs. Do not break this — never allow an
ERPNext write from tier 2.

**Unused leased numbers are returned on sync** and become vacancies, which the
queue-claim rule then fills. So leasing does not leave permanent holes.

### Configuration

```json
"ledger": {
  "mode": "server" | "local_server" | "client",
  "server_url": "https://…",        // the VPS, when it exists
  "local_url":  "http://…:8756",    // tier 2
  "lease_size": 10
}
```

`db.connect()` resolves the tier. Everyone else just calls it and does not care.
Agent H owns the resolution and failover; Agent D owns leases and provisional
numbering; Agent G owns the reconciling sync.

**Build tier 2 first.** It works today with no VPS at all, so nothing waits on
infrastructure that does not exist yet. Point `server_url` at the VPS later and
the same code takes over.

### Security consequences of being on the public internet

This is no longer a LAN tool, and the earlier "plaintext on our own network is
fine" reasoning is dead.

* **HTTPS is mandatory.** Valid certificate, HTTP redirects to HTTPS, HSTS. No
  exceptions, including for testing against the real server.
* **Rate-limit the login endpoint** and lock an account briefly after repeated
  failures. It is now reachable by anyone.
* **Secrets live only on the VPS**, in the `settings` table, never in
  `config.json`, never in an installer, never on a desktop.
* The public decoder and directory are internet-reachable by design. They expose
  item codes and names — no prices, no suppliers, no quantities. Keep it that
  way: **never widen a public endpoint to return commercial data.**

---

## 3. Code grammar — the single source of truth

```python
# core/codes.py — Agent D owns this. Signature is frozen.

def assemble(head2: str, sub2: str, grp3: str,
             slots: list[str|None],      # exactly 4 entries
             vendor: str|None) -> str:
    """HEAD2+SUB2+GRP3 then, for i in 1..L where L is the last defined
    position among (slots + [vendor]): the 2-digit code, or '00' if that
    position is undefined. Positions after L are omitted entirely."""

def parse(code: str) -> dict:
    """Structural decode. Raises ValueError if len(code) not in
    {7,9,11,13,15,17} or the shape is wrong."""

VALID_LENGTHS = {7, 9, 11, 13, 15, 17}
```

Nobody except Agent D writes code-assembly logic. If you need a code built, call
`assemble`. Do not reimplement it "just for the preview" — that is exactly how
the two halves drift apart.

---

## 4. Number reservation — QUEUE, lowest-first

**This changed. The old semantic-matching rule (≥88% name match) is dead.**

```python
# core/codes.py — Agent D

def next_group_code(con, subhead_id) -> tuple[str, str|None]:
    """Lowest free 3-digit number under this sub-head.
    A number freed by a move is immediately available and is handed to the
    NEXT arrival. Returns (code3, freed_from_name or None)."""

def next_item_position(con, grp_id, spec_tuple) -> str:
    """Same rule one level down: the lowest free position inside the group."""
```

The intent, in Anuraag's words: *"there should be no number vacant ever in
between."* The number space stays dense. A vacancy is a free slot, not a
reservation held for a matching newcomer.

`grp_vacancy.former_name` is kept **for display only** — so the activity screen
can say "041, freed by Tissue". It must not influence who gets the number.

---

## 5. Database

**Agent 0 owns `core/db.py` exclusively.** No other agent creates, alters or
drops a table. If you need a column, ask Agent 0 — do not add it yourself.

Existing tables, unchanged: `head`, `subhead`, `grp`, `grp_vacancy`, `specval`,
`alias`, `item`, `code_ledger`, `code_mapping`, `audit`, `erp_item`, `settings`.

Agent 0 adds:

```sql
CREATE TABLE app_user(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE, display_name TEXT,
  pw_hash BLOB, salt BLOB, is_admin INT DEFAULT 0, active INT DEFAULT 1,
  created_at TEXT, created_by TEXT);

CREATE TABLE session(
  token_hash TEXT PRIMARY KEY, username TEXT, issued_at TEXT, expires_at TEXT);

CREATE TABLE item_version(
  id INTEGER PRIMARY KEY, item_id INT, version_no INT, snapshot TEXT,
  changed_by TEXT, changed_at TEXT, summary TEXT,
  UNIQUE(item_id, version_no));

CREATE TABLE item_vacancy(
  id INTEGER PRIMARY KEY, grp_id INT, position TEXT, spec_tuple TEXT,
  former_item TEXT, ts TEXT, released INT DEFAULT 0,
  UNIQUE(grp_id, position));

CREATE TABLE llm_cache(
  key TEXT PRIMARY KEY, question TEXT, answer TEXT, provider TEXT, ts TEXT);

CREATE TABLE sync_log(
  id INTEGER PRIMARY KEY, ts TEXT, direction TEXT, doctype TEXT,
  found INT, changed INT, conflicts TEXT, ok INT, detail TEXT);
```

`settings` is a plain key/value table. Reserved keys:
`llm.provider`, `llm.api_key`, `llm.model`, `match.mode` (`fuzzy`|`llm`),
`match.threshold`, `erp.enabled`, `erp.dry_run`, `erp.base_url`, `erp.api_key`,
`erp.api_secret`, `sync.times` (default `"09:00,17:00"`).

**Secrets live in `settings`, never in `config.json`, never in git.**

---

## 6. HTTP API

Everything under `/api/v1`. Two envelopes, no exceptions:

```jsonc
{ "ok": true,  ...payload }
{ "ok": false, "error": { "code": "...", "message": "...", "detail": {...} } }
```

Error codes: `AUTH_REQUIRED` `FORBIDDEN` `BAD_CODE` `NOT_FOUND` `AMBIGUOUS`
`CONFLICT` `FROZEN` `VALIDATION` `UPSTREAM` `RATE_LIMITED` `INTERNAL`.

**No stack trace ever reaches the client.** Log it, return `INTERNAL`.

### Route ownership — do not add a route outside your own module

| Module | Agent | Prefix |
|---|---|---|
| `routes/public.py` | A | `/api/v1/decode`, `/dictionary/*`, `/directory/*`, `/meta` |
| `routes/auth.py` | B | `/api/v1/auth/*`, `/settings/*` |
| `routes/create.py` | E | `/api/v1/resolve*`, `/ingest`, `/commit`, `/cascade/*` |
| `routes/master.py` | F | `/api/v1/item/*`, `/audit`, `/versions`, `/revert`, `/export`, `/vacancies` |
| `routes/erp.py` | G | `/api/v1/erp/*` |
| `routes/meta.py` | H | `/api/docs`, `/api/v1/health` |

`server.py` is a thin dispatcher owned by Agent 0. It imports each module and
mounts it. **Nobody else edits `server.py`.**

### Auth helper — Agent B provides, everyone uses

```python
from core.auth import require_session, current_user, require_admin
# require_session(handler) -> username, or raises AuthRequired -> 401
```

Every mutating endpoint calls `require_session`. **This is the security boundary
— hiding a button is not.** Public routes never call it.

---

## 7. Matching — Agent C provides, everyone uses

```python
# core/matcher.py
def normalize(text: str) -> str
def similar(a: str, b: str) -> float            # 0-100, the blended score
def rank_groups(con, text, limit=5) -> list[dict]

# core/resolve.py  (Agent C owns the orchestration)
def resolve(con, text, hints=None, user=None) -> dict
```

`resolve()` returns `outcome` ∈ `exists` | `new` | `needs_input`, plus `phases`,
`code`, `blockers`, `matched_by` ∈ `exact` | `rules` | `llm` | `operator`.

**The LLM never mints anything.** It selects from a shortlist the rules produced,
or answers "none". An answer outside the shortlist is rejected and the
deterministic result stands. If no API key is set in Settings, or the provider
returns 429/timeout/error, fall through to rules and stamp `matched_by: rules`.

---

## 8. ERPNext — Agent G only

**Nobody else calls ERPNext. Not for a quick check, not for a test.**

Allowed: `Item` (read, write, create) and its own specification masters
(`Item Code Specification`, `Item Code Vendor`) — create when a needed value does
not exist.

**Refused, permanently:** delete on anything · cancel/amend on anything · every
transaction doctype (Purchase Order/Receipt/Invoice, Sales Order, Delivery Note,
Sales Invoice, Quotation) · all Stock doctypes · all Accounts doctypes · `User`,
`Role`, `DocPerm`, `Custom Field`, `Property Setter`, `Server Script`, `Workflow`.

We do **not** create Item Groups and we do **not** push our taxonomy. The item
group is passed **by name**; if ERPNext has no such group, the write is refused
and the operator is told — we never invent one.

`erp_item` is a local read-only mirror used for phase-1 matching. Items are
created in ERPNext **one at a time, on Submit**, never in bulk.

---

## 9. House rules

1. **Python standard library only**, plus what is already installed:
   `rapidfuzz`, `openpyxl`, `pdfplumber`, `pytesseract`, `requests`. **Do not
   `pip install`** — there is no network for it and users have no admin rights.
2. **Never delete or rewrite another agent's file.** If you need a change there,
   write it in your handover note instead.
3. **Do not commit a secret.** Not in `config.json`, not in a test, not in a
   comment.
4. **Test against the real seeded database** — 889 groups, 1,947 items, 2,677
   live ERP codes. `python seed.py --rebuild` restores it.
5. **The app must still start after your change.** `python server.py` and load
   the page before you call anything done.
6. **British spelling in user-facing text.** Match the existing tone: plain,
   direct, no exclamation marks, no emoji.
7. **Brand:** navy `#001b2e`, teal `#04aed1`, steel `#3b6e93`, panel `#0f2e45`,
   border `#294962`, text `#eaf2f7`. Barlow, falling back to Segoe UI.
   Logo at `web/assets/minimines-logo.svg`.
