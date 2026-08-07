# AGENT F — ITEM MASTER, VERSIONS & REVERT

**Start ~20 minutes after Agents C and D begin, or immediately against the frozen
signatures in `CONTRACTS.md`.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You own the living item master and its memory. There is no approval workflow in
this tool by explicit instruction — **reversibility is what replaces it**. If
someone can undo anything in one click, nobody needs permission to try.

That makes your packet the safety net for the whole system.

## What you own

`routes/master.py` · `core/versions.py` (new) · `core/exporter.py` ·
`web/master.js`

## You depend on

```python
from core.codes import preview_code, list_vacancies   # Agent D
from core.auth  import require_session                # Agent B
```

## Tasks

**1. Item master as the editable directory (8 pts).** Same rows and columns as
Agent A's public directory — but every field editable in place, behind login,
with the **same cascading dropdowns** as the create screen (head → sub-head →
group → that group's spec labels; changing a level clears everything below).

Search, filter by status, paginate. 1,947 items today and growing.

**Field edits never change the code.** Enforce that in the API, not just the
interface. Name, description, UoM, HSN and tax are free to change; the code is
not.

**2. Revision history (8 pts).** Every save writes a **new row** in
`item_version` — full snapshot, who, when, and a summary of what changed.
Nothing is ever overwritten in place. Version 1 is the original.

```python
def snapshot(con, item_id, user, summary) -> int      # returns version_no
def versions(con, item_id) -> list[dict]
def revert(con, item_id, version_no, user) -> dict
```

**3. Revert (5 pts).** Restoring version *k* does **not** delete versions
*k+1…n*. It writes *k*'s contents as a **new version n+1** — so a revert is
itself revertible. History only ever grows.

**4. Revert must respect frozen codes (5 pts).** This is the subtle one and it is
where two rules collide.

A code live in ERPNext **cannot** be restored to an earlier value — submitted
Frappe documents are immutable. So revert restores **every field it can**, leaves
the frozen code alone, and **says plainly what it could not restore**:

> *Restored 6 fields. The code RMBS0010206040707 was not changed — it is live in
> ERPNext and is permanent.*

**A silent partial revert would be worse than no revert at all.** Someone who
believes they undid something that is still live will act on that belief.

**5. Activity with diffs (5 pts).** Field-level before and after, filterable by
person, item and date, with **Revert** on each entry. Read from `audit` and
`item_version`. Show `matched_by` where it exists, so it is possible to see how
often the LLM decided versus the rules.

**6. Vacancy visibility (3 pts).** List reserved numbers at both group and item
level via `list_vacancies` — what freed each, and when.

Note the rule changed: vacancies are now claimed **by queue, lowest-first**, so
the next arrival takes the lowest free number. Present it as "next free number"
rather than "reserved for", which would now be misleading.

## Watch for

* **Do not build code strings.** Call `preview_code`.
* A snapshot must capture the whole row, not a diff. Diffs are computed for
  display; storage is snapshots, because reconstructing a row from a chain of
  diffs fails the moment one link is wrong.
* Keep the export working — `Item_Master`, `Item_Code_Master`, `Code_Mapping`,
  `Audit_Trail`. Anuraag uses it. Do not change the sheet names or column shape
  without saying so.
* Editing 1,947 rows must not load 1,947 rows. Paginate server-side.

## Done when

The master edits in place with working cascades; every save creates a version;
reverting restores fields and returns a new version; reverting a frozen item
restores what it can and says clearly what it could not; activity shows real
before-and-after with a working Revert; the export still opens in Excel with all
four sheets.

## Then

Write `agents/done/AGENT_F.md` — what you built, anything thin, and in particular
any case where revert and freeze interacted in a way the contract did not
anticipate.
