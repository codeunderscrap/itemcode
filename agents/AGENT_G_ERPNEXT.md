# AGENT G — ERPNEXT INTEGRATION

**Start after Agent 0 reports done. Runs in parallel with A, B, C, D, H.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — especially §8, which is the tightest contract
in this project.

You are the only agent allowed to touch a live business system. MiniMines runs
real purchasing and stock on ERPNext. A mistake here is not a bug on a screen —
it is a wrong number in an audited system. Work accordingly: **`dry_run: true`
until Anuraag says otherwise, and never widen your own access.**

## What you own

`routes/erp.py` · `core/erp.py` · `core/sync.py` (new)

## Verified facts — probed live on 6 August 2026, build on these

* `Item.autoname = field:item_code` — **our code becomes the record's primary key
  directly.** No naming series to fight.
* Only three mandatory fields: `item_code`, `item_group`, `stock_uom`.
* `Item` carries 17 custom fields including `item_specification_1…4` (Link →
  *Item Code Specification*, 62 records), `item_vendor` (Link → *Item Code
  Vendor*, 28 records), `gst_hsn_code` (Link → *GST HSN Code*, 18,689 records).
* **ERPNext has 23 Item Groups. We have 889.** They are not the same taxonomy.
* Wahni's spec framework is barely populated: of 108 UAT items, 35 set
  `item_specification_1`, 17 set `item_vendor`. `Item Code Vendor` has duplicate
  codes — *Ather* and *Ather Energy* are both `11`.
* UAT: `https://minimines-uat.m.frappe.cloud`, login `intern@m-mines.com`.

## The rules Anuraag has set

1. **Item access only.** Nothing else. Ever.
2. **No delete. No cancel. No amend.** Not exposed, not implemented.
3. **No bulk migration.** Items are created **one at a time, on Submit**.
4. **No taxonomy push.** We do not create Item Groups and we do not upload our
   889 groups. The item group goes **by name**; if ERPNext has no such group, the
   write is **refused and the operator is told**. We never invent one.
5. **If a specification value does not exist in ERPNext, create it** — that is a
   defined process, not an improvisation. See task 3.

The local `erp_item` table is a **reference mirror for matching**. We match
locally first, then check ERPNext, then act on a single item.

## Tasks

**1. Item-only client with hard guardrails (5 pts).** Rewrite `core/erp.py` so
that the allowed operations are a **closed list**, enforced in code rather than
by convention:

```python
ALLOWED = {
  ("GET",  "Item"), ("POST", "Item"), ("PUT", "Item"),
  ("GET",  "Item Code Specification"), ("POST", "Item Code Specification"),
  ("GET",  "Item Code Vendor"),        ("POST", "Item Code Vendor"),
  ("GET",  "Item Group"), ("GET", "UOM"), ("GET", "GST HSN Code"),
}
```

Anything not on that list raises before a request is made. **No DELETE verb
anywhere in the module** — not even unreachable. A field whitelist applies on
update: only `item_name`, `description`, `gst_hsn_code`, `stock_uom`, `disabled`
and the specification links. Everything else is dropped, so a bug in the master
editor can never reach ERPNext's costing or stock fields.

Authenticate with an **API key from the `settings` table**
(`erp.api_key` / `erp.api_secret`), falling back to username/password for UAT
testing. Never commit a credential.

**2. Validate before writing (3 pts).** Check `stock_uom` against the 240 UOM
records and `gst_hsn_code` against the 18,689 GST HSN Code records **before** any
write. Bad values are refused locally with a clear message rather than bounced
back by Frappe.

Also check the item group exists **by name**. If it does not, refuse and report —
per rule 4.

**3. Create specification values on demand (5 pts).** When an item carries a
specification value ERPNext does not have, create the `Item Code Specification`
record, then link it. Its name format is
`{item_group}-{specification}-{specification_code}`.

Make this a deliberate, logged, idempotent process: check, create if absent, link,
record what you did. Never create a duplicate. Be aware their existing data has
duplicate vendor codes, so **match on the value, not the code.**

Gate this behind a setting (`erp.populate_specs`, default **off**) — Anuraag has
said to create them, but it should be switchable without a code change.

**4. Twice-daily sync (8 pts).** New requirement.

```python
def sync(con, direction="pull") -> dict   # -> sync_log row
```

Runs at `sync.times` (default `09:00,17:00`) and on demand from the UI. It:

* pulls the live Item list and refreshes `erp_item`;
* **detects drift** — items in ERPNext we do not have, items we have that are not
  there, and fields that disagree;
* **never auto-resolves a conflict.** It records it in `sync_log` and surfaces it
  for a human. A silent overwrite in either direction is the worst outcome
  available here.

Anuraag's reason: if a code is changed directly in ERPNext, we need to know. Make
that visible, not automatic.

**5. Reconcile the failsafe on reconnect (8 pts) — NEW.** When the VPS returns
after the local LAN server has been acting as authority, you reconcile:

* upload everything the local server minted while offline;
* ask the VPS to **finalise every provisional code** — it assigns the real number
  by the queue-claim rule and returns an old→new mapping;
* write every pair to `code_mapping` and **show the operator exactly what
  changed**, because they may have written a provisional code on a document;
* return unused leased numbers so they become vacancies;
* re-lease for the next outage.

**Never auto-resolve a genuine conflict.** If the same code was somehow issued on
both sides, record it and surface it — do not pick a winner.

**A provisional code is never pushed to ERPNext.** Refuse it in `core/erp.py`,
not merely in the interface. Agent D sets `item.provisional`; you enforce it.
This is what keeps offline codes re-writable, and it is the property the whole
failsafe rests on.

**6. Prove the guardrail (blocked, but write the test now) (3 pts).**
`tests/test_erp_guardrail.py` attempts one write to **Stock Entry** and one to
**Custom Field**, and asserts both fail. Until the dedicated service account
exists it will not run for real — write it, mark it skipped, and say so in your
handover.

*A guardrail nobody has tried is not a guardrail.*

## Blocked on Anuraag — build against UAT with `dry_run: true`

Live PROD writes, the dedicated `itemcode.studio@` service account, and the
decision on populating Wahni's spec fields. Build everything so that flipping
`erp.enabled` and `erp.dry_run` is the **only** change needed.

## Done when

`core/erp.py` cannot express a delete; the allowed list is enforced in code; UoM,
HSN and item group are validated before any write; `dry_run` prints the exact
payload and writes nothing; `sync()` reports drift without resolving it; a
provisional item is refused by `core/erp.py` itself; reconnecting after an
offline spell finalises provisional codes and reports every change; the guardrail
test exists even if skipped.

## Then

Write `agents/done/AGENT_G.md` — the drift you found on the live UAT during
testing is genuinely useful to Anuraag, so record it.
