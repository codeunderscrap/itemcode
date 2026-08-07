# ERPNext API contract and guardrails

**Prepared for Anuraag · 6 August 2026 · v1.0**
**Every fact below was verified live against `minimines-uat.m.frappe.cloud` on 6 August 2026, not recalled.**

Companion to [PDR.md](PDR.md) §7.

---

## 1. What I found, and why it changes the design

Before writing a line of integration code I probed your live UAT. Three findings
matter enough to decide on before R5.

### 1.1 Wahni has already built a parallel version of this system inside ERPNext

The `Item` doctype carries **17 custom fields**, and several are an unfinished
implementation of the very thing we are building:

| Custom field | Type | Points at | Records |
|---|---|---|---|
| `item_specification_1…4` | Link | **Item Code Specification** | 62 |
| `item_vendor` | Link | **Item Code Vendor** | 28 |
| `product_code` | Link | **Item Code Chemistry** | 16 |
| `item_stage` | Link | **Item Code Stage** | 3 |
| `has_item_specification` | Check | — | — |
| `specification_entry` | Table | Item Specification Entry | — |
| `battery_specification` | Link | Battery Specification | 4 |
| `gst_hsn_code` | Link | GST HSN Code | 18,689 |

`Item Code Specification` has exactly our shape — `item_group`,
`specification`, `specification_code`, and four checkboxes marking which slot the
value belongs to. It is the same design as your `Item code specification.xlsx`.

**But it is barely populated and partly broken:**

* 62 specification records against our 2,490.
* Of 108 items in UAT, only **35** set `item_specification_1`, **17** set
  `item_vendor`, **21** `product_code`, **16** `item_stage`. **4** set
  `has_item_specification`.
* `Item Code Vendor` has **duplicate codes** — `Ather` and `Ather Energy` are
  both `11`.
* `Item Code Stage` holds three incoherent rows: `Mixed/1`, `Not Mixed/0`,
  `STAGE1/STG1`.

**The decision this forces.** Either we populate Wahni's fields as we issue
codes — so the decode lives inside ERPNext and their screens light up — or we
treat those fields as abandoned and carry the meaning only in Item Code Studio,
writing just `item_code`. ⚑ **My recommendation: populate them.** It costs us
one extra write per specification value, it makes ERPNext self-describing, and
it retires a half-built parallel system instead of leaving two. But it means we
own cleaning up their 62 records, the duplicate vendor codes, and the stage list.

### 1.2 ERPNext's Item Group tree is *not* our group taxonomy

ERPNext has **23** Item Groups. We have **889**. And they are not the same kind
of thing — ERP's are coarse buckets (`Raw Material`, `Scrap`, `Products`,
`Services`, `Sub Assemblies`, `Finished product`, and a typo, `Bantry`), only 13
of which are actually in use.

`item_group` is **mandatory** on Item. So every code we push must map our group
to one of theirs.

**Recommendation:** map at the **sub-head** level, not the group level — our 46
sub-heads fold cleanly onto their two dozen buckets, and we keep the real
taxonomy in Item Code Studio where it belongs. Creating 889 Item Groups in
ERPNext would wreck their reporting tree for no gain. ⚑

### 1.3 The good news

* `Item.autoname = field:item_code` — **our code becomes the record's primary
  key directly.** No naming series to fight, no translation layer.
* Only **three** mandatory fields: `item_code`, `item_group`, `stock_uom`.
* `UOM` has 240 records and `GST HSN Code` has 18,689 — so we can **validate**
  UoM and HSN against ERPNext instead of guessing, and stop bad values at source.

---

## 2. Authentication

```http
POST /api/method/login
Content-Type: application/x-www-form-urlencoded

usr=<user>&pwd=<password>
```

Returns a session cookie. Verified working.

**For the service, use an API key instead of a password:**

```http
Authorization: token <api_key>:<api_secret>
```

Generated per user in ERPNext under *User → API Access*. Keys are what we should
ship with — they can be revoked without changing anyone's login, and they never
appear in a browser.

---

## 3. The endpoints we use

### 3.1 Read — used constantly

| Call | Purpose |
|---|---|
| `GET /api/resource/Item?fields=["name","item_name","item_group","stock_uom","disabled"]&limit_page_length=0` | Pull every live code for phase 1 |
| `GET /api/method/frappe.client.get_count?doctype=Item` | Cheap existence and count check |
| `GET /api/resource/Item/{item_code}` | One item in full |
| `GET /api/resource/Item Group?fields=["name","is_group"]` | The 23 buckets we map onto |
| `GET /api/resource/UOM?filters=[["enabled","=",1]]` | Validate UoM before writing |
| `GET /api/resource/GST HSN Code/{hsn}` | Validate HSN before writing |
| `GET /api/resource/Item Code Specification?filters=[["item_group","=",…]]` | Wahni's spec records |
| `GET /api/resource/Item Code Vendor` | Wahni's vendor codes |

### 3.2 Write — only on Submit

**Create an item:**

```http
POST /api/resource/Item
{
  "item_code":     "RMBS0010206040707",     ← becomes the record name
  "item_name":     "Battery Pack - Cylindrical NMC 32700 3.6kWh (LG)",
  "item_group":    "Raw Material",           ← mapped from our sub-head
  "stock_uom":     "Nos",                    ← validated against UOM
  "description":   "…",
  "gst_hsn_code":  "85076000",               ← validated against GST HSN Code
  "is_stock_item": 1,
  "is_purchase_item": 1,
  "is_sales_item": 0,
  "has_batch_no":  0,

  // only if decision §1.1 is "populate":
  "has_item_specification": 1,
  "item_specification_1": "Battery Pack-Cylindrical-02",
  "item_specification_2": "Battery Pack-NMC-06",
  "item_vendor":          "LG"
}
```

**Update a small, fixed set of fields:**

```http
PUT /api/resource/Item/{item_code}
{ "item_name": "…", "description": "…", "gst_hsn_code": "…", "stock_uom": "…" }
```

**Rename** — only ever for an item that has *not* been transacted:

```http
POST /api/method/frappe.client.rename_doc
{ "doctype": "Item", "old_name": "AOHK04101", "new_name": "AOST04701", "merge": 0 }
```

---

## 4. What ERPNext can and cannot do for us

### Can

* Accept our code as the record's own primary key — no translation layer.
* Validate UoM and HSN against real master data before we write anything wrong.
* Store the full decode in `item_specification_1…4` and `item_vendor`, so the
  meaning lives in ERPNext too.
* Rename an item and cascade the change across linked documents.
* Tell us authoritatively what already exists — the basis of phase 1.

### Cannot

* **Cannot change an item code once it appears on a submitted document.**
  Submitted Frappe documents are immutable. This is not a setting; it is the
  platform. It is the entire reason freeze-on-first-use exists.
* **Cannot hold 889 item groups without wrecking the reporting tree.** Hence
  mapping at sub-head level.
* **Cannot enforce our grammar.** ERPNext will happily accept `LCO-1` or
  `IBC TANKS` — and did, 175 times. The grammar can only be enforced upstream, by
  us. That is the whole argument for this tool.
* **Cannot express "specification slot 3 of this group means Size".** Wahni's
  four checkboxes mark which slot a value belongs to, but nothing names the slot
  per group. That legend stays in Item Code Studio.
* **Cannot be trusted to be self-consistent today** — duplicate vendor codes,
  three-row incoherent stage list, a typo'd item group.

---

## 5. The guardrail — exactly what to grant, and what to refuse

Create **one dedicated ERPNext user** for the service, e.g.
`itemcode.studio@m-mines.com`, with **one custom role**, `Item Code Studio`.
Do not reuse `spokeops@`, `intern@` or anyone's personal login.

### 5.1 Grant — the whole list

| Doctype | Read | Write | Create | Delete | Why |
|---|:--:|:--:|:--:|:--:|---|
| Item | ✓ | ✓ | ✓ | ✗ | The core job |
| Item Code Specification | ✓ | ✓ | ✓ | ✗ | New spec values as they are coined |
| Item Code Vendor | ✓ | ✓ | ✓ | ✗ | New makers |
| Item Group | ✓ | ✗ | ✗ | ✗ | Read-only — we map onto it, never reshape it |
| UOM | ✓ | ✗ | ✗ | ✗ | Validation only |
| GST HSN Code | ✓ | ✗ | ✗ | ✗ | Validation only |
| Item Tax Template | ✓ | ✗ | ✗ | ✗ | Validation only |
| Brand | ✓ | ✗ | ✗ | ✗ | Validation only |
| Item Code Chemistry / Stage | ✓ | ✗ | ✗ | ✗ | Read until §1.1 is decided |
| Supplier | ✓ | ✗ | ✗ | ✗ | Resolving a line-level vendor name |

**Delete is refused everywhere, including on Item.** Nothing this tool does
requires deletion; an item that should go away is *disabled*, which is
reversible. A service account that cannot delete cannot cause a catastrophe.

### 5.2 Refuse — and be explicit about it

| Doctype family | Why it must be refused |
|---|---|
| Purchase Order, Purchase Receipt, Purchase Invoice | Nothing about creating an item code justifies touching a transaction |
| Sales Order, Delivery Note, Sales Invoice, Quotation | Same |
| Stock Entry, Stock Ledger Entry, Bin, Stock Reconciliation | Stock is never ours to move. A bug here is unrecoverable |
| Journal Entry, Payment Entry, GL Entry | Money |
| Work Order, BOM, Production Plan | Manufacturing |
| User, Role, DocPerm, Custom Field, Property Setter | A service account must never be able to widen its own permissions |
| Workflow, Workflow State, Server Script, Scheduled Job | Changing how the ERP behaves is not this tool's business |
| File, Email Account, Notification | No reason |

### 5.3 Additional restraints

1. **Rename is called only when `frozen = 0`.** The tool checks its own ledger
   first and refuses otherwise — belt and braces, since ERPNext would allow the
   rename and cascade it expensively.
2. **`merge: 0` always.** A merging rename silently destroys a record.
3. **Writes go through one function** in `core/erp.py`, with `dry_run` as the
   default. The exact payload is logged before every call.
4. **Field whitelist on update.** Only `item_name`, `description`,
   `gst_hsn_code`, `stock_uom`, `disabled` and the specification links may be
   written. Anything else is dropped, not passed through — so a bug in our master
   editor can never reach into ERPNext's costing or stock fields.
5. **Rate limit and retry.** Serial writes, exponential backoff on 429/502, and
   a hard stop after three failures on the same item rather than a retry storm.
6. **UAT before PROD.** Same key shape, different host, and PROD stays `enabled:
   false` until a clean UAT week.

### 5.4 The check that proves the guardrail

Before go-live, the service account attempts one write to **Stock Entry** and one
to **Custom Field**. Both must fail with 403. A guardrail nobody has tried is not
a guardrail.

---

## 6. Decisions I need from you ⚑

| # | Question | My recommendation |
|---|---|---|
| 1 | Populate Wahni's `item_specification_1…4` / `item_vendor`, or leave them abandoned and write only `item_code`? | **Populate.** Retires a half-built parallel system rather than leaving two |
| 2 | Map our taxonomy onto ERP's Item Groups at **sub-head** level? | **Yes.** 889 groups would wreck their reporting tree |
| 3 | Who cleans up their 62 spec records, duplicate vendor codes and the 3-row stage list? | Us, as part of the group de-duplication campaign |
| 4 | Dedicated service user + `Item Code Studio` role, API key not password? | **Yes.** Blocks PROD go-live until it exists |
| 5 | `Item Code Chemistry` (16 records, 2-letter codes like `BP`) — is this a live requirement or abandoned? | Need your read; it overlaps our head/sub-head prefix and I do not know if anything depends on it |
