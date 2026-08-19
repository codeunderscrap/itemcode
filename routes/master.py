"""Item master, versions & activity — Agent F.

Owns: /api/v1/item/*, /api/v1/audit, /api/v1/versions, /api/v1/revert,
/api/v1/export, /api/v1/vacancies — plus the pre-existing un-versioned
`/api/...` paths, relocated here verbatim by Agent 0 so the current UI kept
working while this module was rebuilt.

The whole surface in this file sits behind login (agents/CONTRACTS.md
decision 8 draws the public/login line at "read vs create"; Agent A's
`routes/public.py` is the public read-only directory — this file is the
*editable* master, reachable only from the authenticated app shell, so every
handler here calls `require_session`, not only the ones that write. Hiding a
button is not the security boundary; this call is (CONTRACTS.md §6).

**Field edits never change the code.** `item_update`/`item_update_v1` refuse
outright (FROZEN) if the payload tries to touch `code` or any of the columns
that derive it (grp_id, s1..s4, vend). This is enforced here, in the API, not
just by the interface not offering the fields.

**Every write to `item` is versioned.** `core/versions.py` (new, this
packet) is the only thing that ever inserts into `item_version`; nothing in
this file does a raw UPDATE to `item` without calling `versions.snapshot`
straight after, inside the same transaction.
"""
import json
import os

from core import db as D
from core import codes as C
from core import restructure as RS
from core import exporter as EX
from core import versions as V
from core.context import ctx
from core.api import ok, ApiError
from core.auth import require_session


def _rows(sql, args=()):
    return D.rows(ctx.con, sql, args)


def _one(sql, args=()):
    return D.one(ctx.con, sql, args)


# ══════════════════════════════════════════════════════════ legacy (kept as-is)
# Same URL, same response shape, so web/app.js keeps working untouched while
# web/master.js (new) migrates onto the /api/v1 routes below. The only
# behaviour change on this half: every handler now requires a real session
# (they used to trust the forgeable X-User header) and every write is
# versioned.

def master_list(req):
    require_session(req)
    q = req.query
    where, args = [], []
    if q.get("q"):
        where.append("(i.code LIKE ? OR i.name LIKE ? OR i.description LIKE ?)")
        args += [f"%{q['q']}%"] * 3
    if q.get("status"):
        where.append("i.status=?")
        args.append(q["status"])
    if q.get("group_id"):
        where.append("i.grp_id=?")
        args.append(q["group_id"])
    if q.get("undecodable") == "1":
        where.append("i.decodable=0")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    con = ctx.con
    total = con.execute(f"SELECT COUNT(*) c FROM item i {w}", args).fetchone()["c"]
    page, size = int(q.get("page", 1)), min(int(q.get("size", 60)), 500)
    data = _rows(f"""SELECT i.*, g.name AS gname, s.name AS sname, h.name AS hname,
                (SELECT value FROM specval WHERE id=i.s1) AS v1,
                (SELECT value FROM specval WHERE id=i.s2) AS v2,
                (SELECT value FROM specval WHERE id=i.s3) AS v3,
                (SELECT value FROM specval WHERE id=i.s4) AS v4,
                (SELECT value FROM specval WHERE id=i.vend) AS vv
                FROM item i LEFT JOIN grp g ON g.id=i.grp_id
                LEFT JOIN subhead s ON s.id=g.subhead_id
                LEFT JOIN head h ON h.id=s.head_id
                {w} ORDER BY i.code LIMIT ? OFFSET ?""",
                args + [size, (page - 1) * size])
    return {"total": total, "page": page, "size": size, "rows": data}


def audit(req):
    require_session(req)
    return _rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                 (int(req.query.get("limit", 200)),))


def mappings(req):
    require_session(req)
    return _rows("SELECT * FROM code_mapping ORDER BY id DESC LIMIT 500")


def vacancies(req):
    require_session(req)
    return _rows("""SELECT v.*, s.name AS sub_name, h.name AS head_name
                    FROM grp_vacancy v JOIN subhead s ON s.id=v.subhead_id
                    JOIN head h ON h.id=s.head_id
                    WHERE v.released=0 ORDER BY v.ts DESC""")


def export(req):
    user = require_session(req)
    con = ctx.con
    p = EX.export(con, os.path.join(ctx.root, "exports"))
    D.log(con, user, "export", os.path.basename(p))
    con.commit()
    return {"ok": True, "path": p, "file": os.path.basename(p)}


def download(req):
    require_session(req)
    from urllib.parse import unquote
    fn = os.path.basename(unquote(req.params["file"]))
    full = os.path.join(ctx.root, "exports", fn)
    if not os.path.isfile(full):
        return 404, {"error": "no such export"}
    with open(full, "rb") as f:
        data = f.read()
    return (200, data,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            {"Content-Disposition": f'attachment; filename="{fn}"'})


# fields a field-edit is allowed to touch — never the code, never anything
# that derives it (that would silently rewrite a "frozen" code the moment
# someone else looked away)
EDITABLE_FIELDS = ["name", "description", "uom", "alt_uom", "hsn", "tax",
                   "maintain_stock", "allow_sales", "has_batch", "status"]
# explicitly refused, so the refusal is a clear FROZEN error, not just an
# ignored key
CODE_DERIVED_FIELDS = {"code", "grp_id", "s1", "s2", "s3", "s4", "vend"}
SERVER_OWNED_FIELDS = {"id", "frozen", "decodable", "erp_synced_at",
                       "created_at", "created_by", "name_norm", "updated_at"}
BLOCKED_FIELDS = CODE_DERIVED_FIELDS | SERVER_OWNED_FIELDS


def _apply_field_edit(con, it, patch, user):
    """Shared by the legacy and /api/v1 update handlers. `it` is the current
    row (dict). Raises ApiError(FROZEN) if `patch` touches a code-derived or
    server-owned column. Returns (changed: bool, version_no: int|None,
    fields: list[str])."""
    blocked = sorted(k for k in patch if k in BLOCKED_FIELDS)
    if blocked:
        raise ApiError("FROZEN",
            "the code is not editable — it is derived from the item's "
            "classification, never from a field edit",
            {"fields": blocked})

    V.ensure_baseline(con, it["id"], user)

    sets, args, before = [], [], {}
    for k in EDITABLE_FIELDS:
        if k in patch and patch[k] != it.get(k):
            sets.append(f"{k}=?")
            args.append(patch[k])
            before[k] = it.get(k)
    if not sets:
        return False, None, []

    if "name" in before:
        from core.matcher import normalize
        con.execute("UPDATE item SET name_norm=? WHERE id=?",
                    (normalize(patch["name"]), it["id"]))
    sets.append("updated_at=?")
    args.append(D.now())
    args.append(it["id"])
    con.execute(f"UPDATE item SET {', '.join(sets)} WHERE id=?", args)

    fields = sorted(before)
    summary = "changed " + ", ".join(fields)
    vno = V.snapshot(con, it["id"], user, summary)
    D.log(con, user, "edit-item", it["code"],
          {"before": before, "after": {k: patch[k] for k in before}})
    con.commit()
    return True, vno, fields


def item_update(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    code = p.get("code")
    it = _one("SELECT * FROM item WHERE code=?", (code,))
    if not it:
        return 404, {"ok": False, "error": "unknown code"}
    changed, vno, fields = _apply_field_edit(con, it, p, user)
    return {"ok": True, "code": code, "code_changed": False, "changed": changed,
            "version": vno, "fields": fields,
            "note": "field edits never change the code"}


def group_move_preview(req):
    require_session(req)
    p = req.body
    return RS.preview_move(ctx.con, ctx.matcher, int(p["group_id"]), int(p["subhead_id"]))


def group_move(req):
    user = require_session(req)
    p = req.body
    return RS.move_group(ctx.con, ctx.matcher, int(p["group_id"]), int(p["subhead_id"]),
                         user, bool(p.get("push_erp")), ctx.erp)


def group_merge(req):
    user = require_session(req)
    p = req.body
    return RS.merge_groups(ctx.con, ctx.matcher, int(p["source_id"]), int(p["target_id"]), user)


def group_delete(req):
    user = require_session(req)
    p = req.body
    return RS.delete_group(ctx.con, int(p["group_id"]), user, p.get("reason", ""))


def rename(req):
    user = require_session(req)
    p = req.body
    return RS.rename(ctx.con, p["scope"], int(p["id"]), p["name"], user)


def group_labels(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    gid = int(p["group_id"])
    con.execute("UPDATE grp SET labels=? WHERE id=?", (json.dumps(p.get("labels") or {}), gid))
    D.log(con, user, "set-labels", gid, p.get("labels"))
    con.commit()
    return {"ok": True}


def specval_add(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    gid, slot, val = int(p["group_id"]), int(p["slot"]), p["value"].strip()
    ex = _one("SELECT id,code2 FROM specval WHERE grp_id=? AND slot=? AND value=?", (gid, slot, val))
    if ex:
        return {"ok": True, **ex, "existing": True}
    code = C.next_spec_code(con, gid, slot)
    cur = con.execute("INSERT INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                      (gid, slot, val, code))
    D.log(con, user, "create-specval", val, {"group_id": gid, "slot": slot, "code": code})
    con.commit()
    return {"ok": True, "id": cur.lastrowid, "code2": code}


def head_add(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    name = p["name"].strip()
    code = C.mint_head_code(con, name)
    cur = con.execute("INSERT INTO head(name,code2) VALUES(?,?)", (name, code))
    D.log(con, user, "create-head", name, {"code2": code})
    con.commit()
    return {"ok": True, "id": cur.lastrowid, "code2": code}


def subhead_add(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    hid, name = int(p["head_id"]), p["name"].strip()
    code = C.mint_subhead_code(con, hid, name)
    cur = con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(?,?,?)", (hid, name, code))
    D.log(con, user, "create-subhead", name, {"code2": code, "head_id": hid})
    con.commit()
    return {"ok": True, "id": cur.lastrowid, "code2": code}


def group_add(req):
    user = require_session(req)
    p = req.body
    con = ctx.con
    sid, name = int(p["subhead_id"]), p["name"].strip()
    # C.next_group_code dropped its (group_name, matcher) params - numbering
    # is queue-claim, lowest-first, no semantic test (CONTRACTS.md §4).
    # C.claim_group_code is the concurrency-safe allocate+insert (Task 3):
    # BEGIN IMMEDIATE, re-check lowest-free inside it, retry once on
    # SQLITE_BUSY - so two operators adding a group in the same second never
    # receive the same number. This call site updated to match Agent D's new
    # frozen signature; the route itself stays Agent F's (agents/done/AGENT_0.md).
    r = C.claim_group_code(con, sid, name, p.get("uom"), p.get("labels") or {})
    D.log(con, user, "create-group", name,
         {"code3": r["code3"], "reused_vacancy": r["freed_from"]})
    con.commit()
    return {"ok": True, "id": r["id"], "code3": r["code3"], "reused_vacancy_of": r["freed_from"]}


# ══════════════════════════════════════════════════════════════════ /api/v1
# The real surface this packet was asked to build: paginated/filterable item
# list with server-side cascading (head -> sub-head -> group), versioned
# edits, revert-with-frozen-code-honesty, a diffed activity feed, and vacancy
# visibility at both levels. Response envelope throughout: core.api.ok()/err()
# (agents/CONTRACTS.md §6) via ApiError for anything that isn't a plain 200.

def item_list_v1(req):
    """GET /api/v1/item — paginated, server-side filtered. 1,947 items and
    growing; this never loads the whole table (agents/AGENT_F_MASTER.md
    "Watch for"). Filters double as the cascading-dropdown backend: head_id
    -> subhead_id -> group_id, each one narrowing the next."""
    require_session(req)
    q = req.query
    where, args = [], []
    if q.get("q"):
        where.append("(i.code LIKE ? OR i.name LIKE ? OR i.description LIKE ?)")
        args += [f"%{q['q']}%"] * 3
    if q.get("status"):
        where.append("i.status=?")
        args.append(q["status"])
    if q.get("head_id"):
        where.append("h.id=?")
        args.append(q["head_id"])
    if q.get("subhead_id"):
        where.append("s.id=?")
        args.append(q["subhead_id"])
    if q.get("group_id"):
        where.append("i.grp_id=?")
        args.append(q["group_id"])
    if q.get("undecodable") == "1":
        where.append("i.decodable=0")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    con = ctx.con
    joins = """FROM item i LEFT JOIN grp g ON g.id=i.grp_id
               LEFT JOIN subhead s ON s.id=g.subhead_id
               LEFT JOIN head h ON h.id=s.head_id"""
    total = con.execute(f"SELECT COUNT(*) c {joins} {w}", args).fetchone()["c"]
    page = max(1, int(q.get("page", 1)))
    size = min(max(1, int(q.get("size", 60))), 500)
    data = _rows(f"""SELECT i.code, i.name, i.description, i.uom, i.alt_uom, i.hsn, i.tax,
                    i.status, i.decodable, i.frozen, i.maintain_stock, i.allow_sales, i.has_batch,
                    g.id AS group_id, g.name AS gname, s.id AS subhead_id, s.name AS sname,
                    h.id AS head_id, h.name AS hname,
                    (SELECT value FROM specval WHERE id=i.s1) AS v1,
                    (SELECT value FROM specval WHERE id=i.s2) AS v2,
                    (SELECT value FROM specval WHERE id=i.s3) AS v3,
                    (SELECT value FROM specval WHERE id=i.s4) AS v4,
                    (SELECT value FROM specval WHERE id=i.vend) AS vv,
                    (SELECT MAX(version_no) FROM item_version WHERE item_id=i.id) AS version_no
                    {joins} {w} ORDER BY i.code LIMIT ? OFFSET ?""",
                args + [size, (page - 1) * size])
    return ok({"total": total, "page": page, "size": size, "rows": data})


def item_detail_v1(req):
    """GET /api/v1/item/<code> — full row plus resolved spec values and the
    group's labels, so an edit form can show "Type: Aerosol" instead of a
    raw specval id, and can render the read-only classification cascade."""
    require_session(req)
    code = (req.params.get("code") or "").upper()
    it = _one("""SELECT i.*, g.name AS gname, g.id AS group_id, g.labels AS glabels, g.code3 AS gcode,
                        s.id AS subhead_id, s.name AS sname, s.code2 AS scode,
                        h.id AS head_id, h.name AS hname, h.code2 AS hcode
                 FROM item i LEFT JOIN grp g ON g.id=i.grp_id
                 LEFT JOIN subhead s ON s.id=g.subhead_id
                 LEFT JOIN head h ON h.id=s.head_id
                 WHERE i.code=?""", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item with code {code}")
    labels = json.loads(it.pop("glabels") or "{}")
    specs = []
    for slot, col in ((1, "s1"), (2, "s2"), (3, "s3"), (4, "s4"), (5, "vend")):
        sid = it.get(col)
        label = labels.get("vendor" if slot == 5 else str(slot))
        sv = _one("SELECT value, code2 FROM specval WHERE id=?", (sid,)) if sid else None
        specs.append({"slot": slot, "label": label, "value": sv["value"] if sv else None,
                      "code2": sv["code2"] if sv else None})
    it["specs"] = specs
    it["frozen_effective"] = bool(it.get("frozen")) or it.get("status") == "in_erp"
    vc = ctx.con.execute("SELECT COUNT(*) c FROM item_version WHERE item_id=?",
                         (it["id"],)).fetchone()["c"]
    it["version_count"] = vc
    return ok({"item": it})


def item_update_v1(req):
    """POST /api/v1/item/<code>/update — same rule as the legacy handler,
    versioned and FROZEN-guarded through the shared _apply_field_edit."""
    user = require_session(req)
    code = (req.params.get("code") or "").upper()
    con = ctx.con
    it = _one("SELECT * FROM item WHERE code=?", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item with code {code}")
    changed, vno, fields = _apply_field_edit(con, it, req.body or {}, user)
    return ok({"code": code, "code_changed": False, "changed": changed,
               "version": vno, "fields": fields,
               "note": "field edits never change the code"})


def item_push_v1(req):
    """POST /api/v1/item/<code>/push — manual push to ERPNext."""
    from core.erp import ERP
    from core.db import now
    user = require_session(req)
    code = (req.params.get("code") or "").upper()
    con = ctx.con
    
    it = _one("SELECT * FROM item WHERE code=?", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item with code {code}")
        
    patch = req.body or {}
    _apply_field_edit(con, it, patch, user)
    
    it = _one("""SELECT i.*, g.name AS gname
                 FROM item i LEFT JOIN grp g ON g.id=i.grp_id
                 WHERE i.code=?""", (code,))
                 
    erp = ERP().refresh(con)
    if not erp.enabled:
        raise ApiError("FAILED", "ERPNext integration is disabled")
        
    extra = {}
    has_specs = False
    for i in range(1, 6):
        sid = it.get(f"s{i}" if i < 5 else "vend")
        if sid:
            val = con.execute("SELECT value FROM specval WHERE id=?", (sid,)).fetchone()
            if val:
                if i < 5:
                    extra[f"item_specification_{i}"] = val["value"]
                    has_specs = True
                else:
                    extra["item_vendor"] = val["value"]
    if has_specs:
        extra["has_item_specification"] = 1
        
    exists = False
    try:
        check = erp._resource("GET", "Item", name=code)
        if check and check.get("data"):
            exists = True
    except Exception:
        pass

    if exists:
        # If it already exists, update it rather than creating it (which would fail)
        up_fields = {
            "gst_hsn_code": it.get("hsn")
        }
        if it.get("tax"):
            up_fields["taxes"] = [{"item_tax_template": it.get("tax")}]
        res = erp.update_item(code, up_fields, con=con)
    else:
        res = erp.create_item(code, it["name"], it["gname"], it.get("uom") or "Nos",
                              it.get("hsn"), extra=extra, tax_template=it.get("tax"), con=con)
                          
    if not res.get("ok"):
        raise ApiError("FAILED", f"ERPNext push failed: {res.get('error', 'unknown error')}")
        
    con.execute("UPDATE item SET status='in_erp', erp_synced_at=?, frozen=1 WHERE id=?",
                (now(), it["id"]))
    con.commit()
    
    return ok({"code": code, "status": "in_erp", "erp_res": res})


def versions_v1(req):
    """GET /api/v1/versions?code=... — the full timeline, each entry already
    carrying its diff against the version before it."""
    user = require_session(req)
    code = (req.query.get("code") or "").upper()
    if not code:
        raise ApiError("VALIDATION", "code query parameter is required")
    it = _one("SELECT id, code, name, frozen, status FROM item WHERE code=?", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item with code {code}")
    con = ctx.con
    V.ensure_baseline(con, it["id"], user)
    tl = V.timeline(con, it["id"])
    return ok({"code": it["code"], "name": it["name"],
               "frozen": bool(it["frozen"]) or it["status"] == "in_erp",
               "versions": tl})


def revert_v1(req):
    """POST /api/v1/revert {code, version_no} — restores every field it can.
    If the item is frozen live in ERPNext, `code` is left untouched and the
    response says so in plain words (agents/AGENT_F_MASTER.md task 4) —
    never a silent partial revert."""
    user = require_session(req)
    p = req.body or {}
    code = (p.get("code") or "").upper()
    vno = p.get("version_no", p.get("version"))
    if not code or vno is None:
        raise ApiError("VALIDATION", "code and version_no are required")
    it = _one("SELECT id FROM item WHERE code=?", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item with code {code}")
    con = ctx.con
    V.ensure_baseline(con, it["id"], user)
    try:
        result = V.revert(con, it["id"], int(vno), user)
    except ValueError as e:
        raise ApiError("NOT_FOUND", str(e))
    return ok(result)


def activity_v1(req):
    """GET /api/v1/audit — filterable, diffed activity feed. Merges two
    sources: `item_version` (real field-level before/after, one row per
    save) and `audit` (everything else — group moves, merges, new specvals,
    exports — plus `matched_by` when the logging caller stamped it, so it is
    possible to see how often the LLM decided versus the rules). Filters:
    user, code (item), from/to (ISO timestamps, compared against the same
    `changed_at`/`ts` column both tables already use)."""
    require_session(req)
    q = req.query
    limit = min(max(1, int(q.get("limit", 100))), 500)
    offset = max(0, int(q.get("offset", 0)))
    f_user, f_code = q.get("user"), (q.get("code") or "").upper() or None
    f_from, f_to = q.get("from"), q.get("to")

    vw, va = [], []
    if f_user:
        vw.append("iv.changed_by=?"); va.append(f_user)
    if f_code:
        vw.append("i.code=?"); va.append(f_code)
    if f_from:
        vw.append("iv.changed_at>=?"); va.append(f_from)
    if f_to:
        vw.append("iv.changed_at<=?"); va.append(f_to)
    vwc = ("WHERE " + " AND ".join(vw)) if vw else ""
    vrows = _rows(f"""SELECT iv.id, iv.item_id, iv.version_no, iv.snapshot, iv.changed_by,
                            iv.changed_at, iv.summary, i.code, i.name
                        FROM item_version iv JOIN item i ON i.id=iv.item_id
                        {vwc} ORDER BY iv.changed_at DESC, iv.id DESC LIMIT 1000""", va)
    events = []
    for r in vrows:
        snap = json.loads(r["snapshot"])
        prev = _one("SELECT snapshot FROM item_version WHERE item_id=? AND version_no=?",
                    (r["item_id"], r["version_no"] - 1))
        prev_snap = json.loads(prev["snapshot"]) if prev else None
        summary = r["summary"] or ""
        action = ("revert" if summary.startswith("reverted")
                  else "baseline" if summary.startswith("baseline") else "edit")
        events.append({
            "kind": "version", "id": f"v{r['id']}", "ts": r["changed_at"], "user": r["changed_by"],
            "action": action, "item_code": r["code"], "item_name": r["name"],
            "version_no": r["version_no"], "summary": summary,
            "diff": V.diff_fields(prev_snap, snap), "revertable": action != "baseline",
        })

    aw, aa = ["action NOT IN ('edit-item','revert-item')"], []
    if f_user:
        aw.append("user=?"); aa.append(f_user)
    if f_code:
        aw.append("target=?"); aa.append(f_code)
    if f_from:
        aw.append("ts>=?"); aa.append(f_from)
    if f_to:
        aw.append("ts<=?"); aa.append(f_to)
    awc = "WHERE " + " AND ".join(aw)
    arows = _rows(f"SELECT * FROM audit {awc} ORDER BY ts DESC, id DESC LIMIT 1000", aa)
    for r in arows:
        detail = r["detail"]
        try:
            detail = json.loads(detail) if detail else None
        except (TypeError, ValueError):
            pass
        matched_by = detail.get("matched_by") if isinstance(detail, dict) else None
        events.append({
            "kind": "audit", "id": f"a{r['id']}", "ts": r["ts"], "user": r["user"],
            "action": r["action"], "item_code": r["target"], "item_name": None,
            "detail": detail, "matched_by": matched_by, "revertable": False,
        })

    events.sort(key=lambda e: e["ts"] or "", reverse=True)
    total = len(events)
    page = events[offset:offset + limit]
    return ok({"total": total, "limit": limit, "offset": offset, "events": page})


def _vacancies_fallback(con):
    """Only used if core.codes.list_vacancies (Agent D) is ever missing —
    it has since landed (level/scope/prefix/number/freed_by/freed_at,
    already identical shape for group- and item-level) and vacancies_v1
    below prefers it unconditionally. This stays only as a defensive
    fallback so the endpoint degrades instead of 500ing if that ever
    regresses; it is not the source of truth (agents/AGENT_F_MASTER.md
    "Watch for": do not re-derive what Agent D owns)."""
    groups = _rows("""SELECT v.id, v.subhead_id, v.code3, v.former_name, v.ts,
                             s.name AS sub_name, s.code2 AS sub_code,
                             h.name AS head_name, h.code2 AS head_code
                      FROM grp_vacancy v JOIN subhead s ON s.id=v.subhead_id
                      JOIN head h ON h.id=s.head_id
                      WHERE v.released=0 ORDER BY h.code2, s.code2, v.code3""")
    for g in groups:
        g["level"] = "group"
        g["next_free_number"] = g["code3"]
        g["scope"] = f"{g['head_name']} / {g['sub_name']}"
        g["freed_by"] = g["former_name"]
    items = _rows("""SELECT v.id, v.grp_id, v.position, v.spec_tuple, v.former_item, v.ts,
                            g.name AS group_name, g.code3 AS grp_code
                     FROM item_vacancy v JOIN grp g ON g.id=v.grp_id
                     WHERE v.released=0 ORDER BY v.ts DESC""")
    for i in items:
        i["level"] = "item"
        i["next_free_number"] = i["position"]
        i["scope"] = i["group_name"]
        i["freed_by"] = i["former_item"]
    return groups, items


def vacancies_v1(req):
    """GET /api/v1/vacancies — group- and item-level, framed as "next free
    number" rather than "reserved for": CONTRACTS.md §4 changed the rule to
    queue-claim, lowest-first, so nothing here is held for a particular
    future arrival — it is just what the next one gets."""
    require_session(req)
    con = ctx.con
    if hasattr(C, "list_vacancies"):
        data = C.list_vacancies(con)
        return ok({"vacancies": data,
                   "groups": [v for v in data if v.get("level") == "group"],
                   "items": [v for v in data if v.get("level") == "item"]})
    groups, items = _vacancies_fallback(con)
    return ok({"vacancies": groups + items, "groups": groups, "items": items,
               "note": "core.codes.list_vacancies isn't landed yet — this is a "
                       "local reconstruction from grp_vacancy/item_vacancy"})


def export_v1(req):
    user = require_session(req)
    con = ctx.con
    p = EX.export(con, os.path.join(ctx.root, "exports"))
    D.log(con, user, "export", os.path.basename(p))
    con.commit()
    return ok({"path": p, "file": os.path.basename(p)})


def erp_items_v1(req):
    """GET /api/v1/erp-items — fetches all items live from ERPNext and returns
    them so the master table can show items that exist in ERP but may not yet
    exist in the local DB."""
    require_session(req)
    from core.erp import ERP
    erp = ERP().refresh(ctx.con)
    if not erp.enabled:
        return ok({"items": [], "note": "ERPNext integration is disabled"})
    try:
        erp.login()
        items = erp.pull_items(limit=5000, con=ctx.con)
        
        erp_groups = erp.pull_item_groups(con=ctx.con)
            
        for item in items:
            grp = item.get("item_group")
            if grp:
                parent = erp_groups.get(grp)
                if parent and parent != "All Item Groups":
                    grandparent = erp_groups.get(parent)
                    if grandparent and grandparent != "All Item Groups":
                        item["subhead_name"] = parent
                        item["head_name"] = grandparent
                    else:
                        item["subhead_name"] = "—"
                        item["head_name"] = parent
                else:
                    item["subhead_name"] = "—"
                    item["head_name"] = "—"

            specs = []
            grp_prefix = (grp or "") + "-"
            for i in range(1, 5):
                sp = item.get(f"item_specification_{i}")
                if sp:
                    if sp.startswith(grp_prefix):
                        sp = sp[len(grp_prefix):]
                    if len(sp) > 3 and sp[-3] == "-":
                        sp = sp[:-3]
                    specs.append(sp)
            item["specs_list"] = specs
                
        return ok({"items": items, "count": len(items)})
    except Exception as e:  # noqa: BLE001
        return ok({"items": [], "error": f"{e.__class__.__name__}: {e}"})


def download_v1(req):
    require_session(req)
    return download(req)


def map_erp_group(req):
    """POST /api/v1/erp-group/map — Maps an ERP-only item group to a local
    subhead. Creates the local group and updates the parent in ERPNext."""
    require_session(req)
    con = ctx.con
    b = req.body or {}
    group_name = b.get("group_name")
    subhead_id = b.get("subhead_id")
    
    if not group_name or not subhead_id:
        raise ApiError("VALIDATION", "Group name and subhead are required")
        
    subhead_id = int(subhead_id)
    
    # Check if group already exists locally
    from core.db import _one
    existing = _one(con, "SELECT id, code3 FROM grp WHERE name=?", (group_name,))
    if existing:
        return ok({"message": "Group already exists locally", "id": existing["id"]})
        
    sub = _one(con, "SELECT s.name AS subhead_name, h.name AS head_name FROM subhead s JOIN head h ON h.id=s.head_id WHERE s.id=?", (subhead_id,))
    if not sub:
        raise ApiError("NOT_FOUND", "Sub-head not found")
        
    from core.codes import claim_group_code
    try:
        grp = claim_group_code(con, subhead_id, group_name, uom="Nos")
    except Exception as e:
        raise ApiError("CONFLICT", str(e))
        
    # Update ERPNext
    from core.erp import ERP
    erp = ERP().refresh(con)
    if erp.enabled:
        try:
            erp.login()
            erp._ensure_item_group(group_name, con)
        except Exception as e:
            # We created it locally, but ERP failed. Print a warning.
            print(f"Warning: Failed to update ERPNext item group parent for {group_name}: {e}")
            
    return ok({"id": grp["id"], "code3": grp["code3"]})


def missing_erp_taxonomy(req):
    """GET /api/v1/erp-taxonomy/missing — Finds Heads, Subheads, and Groups in ERPNext
    that are not registered in the local generator database."""
    require_session(req)
    con = ctx.con
    from core.erp import ERP
    erp = ERP().refresh(con)
    if not erp.enabled:
        return ok({"heads": [], "subheads": [], "groups": []})
        
    erp.login()
    groups_dict = erp.pull_item_groups(con) # { name: parent }
    
    from core.db import D
    local_heads = {r["name"]: r["id"] for r in D.rows(con, "SELECT id, name FROM head")}
    local_subs = {r["name"]: {"id": r["id"], "head_name": r["hname"]} for r in D.rows(con, "SELECT s.id, s.name, h.name as hname FROM subhead s JOIN head h ON h.id=s.head_id")}
    local_grps = {r["name"]: r["sname"] for r in D.rows(con, "SELECT g.name, s.name as sname FROM grp g JOIN subhead s ON s.id=g.subhead_id")}
    
    root_parent = "All Item Groups"
    for lh in local_heads:
        if lh in groups_dict and groups_dict[lh]:
            root_parent = groups_dict[lh]
            break
            
    erp_heads = set()
    erp_subs = {}
    erp_grps = {}
    
    for name, parent in groups_dict.items():
        if parent == root_parent:
            erp_heads.add(name)
            
    for name, parent in groups_dict.items():
        if parent in erp_heads or parent in local_heads:
            erp_subs[name] = parent
            
    for name, parent in groups_dict.items():
        if parent in erp_subs or parent in local_subs:
            erp_grps[name] = parent
            
    missing_heads = [{"name": h} for h in erp_heads if h not in local_heads]
    
    missing_subs = []
    for s, p in erp_subs.items():
        if s not in local_subs:
            # Check if parent is local, provide its ID
            p_id = local_heads.get(p)
            missing_subs.append({"name": s, "parent": p, "parent_id": p_id})
            
    missing_grps = []
    for g, p in erp_grps.items():
        if g not in local_grps:
            p_id = local_subs[p]["id"] if p in local_subs else None
            missing_grps.append({"name": g, "parent": p, "parent_id": p_id})
    
    return ok({
        "heads": sorted(missing_heads, key=lambda x: x["name"]),
        "subheads": sorted(missing_subs, key=lambda x: x["name"]),
        "groups": sorted(missing_grps, key=lambda x: x["name"])
    })


def classify_erp_item(req):
    """POST /api/v1/erp-items/<code>/classify — Assigns an ERP-only item to a local
    group and sets its specifications in ERPNext, without renaming it."""
    require_session(req)
    con = ctx.con
    import urllib.parse
    code = urllib.parse.unquote(req.params.get("code") or "")
    b = req.body or {}
    group_id = b.get("group_id")
    specs = b.get("specs") or []
    
    if not code or not group_id:
        raise ApiError("VALIDATION", "Code and group_id are required")
        
    from core.db import _one
    grp = _one(con, "SELECT name FROM grp WHERE id=?", (group_id,))
    if not grp:
        raise ApiError("NOT_FOUND", "Group not found")
        
    item_group = grp["name"]
    
    from core.erp import ERP
    erp = ERP().refresh(con)
    if not erp.enabled:
        raise ApiError("FAILED", "ERP connection is disabled")
        
    erp.login()
    
    payload = {"item_group": item_group}
    for i in range(1, 5):
        val = specs[i-1] if i <= len(specs) else None
        if val:
            sv = _one(con, "SELECT code2 FROM specval WHERE grp_id=? AND slot=? AND value=?", (group_id, i, val))
            code2 = sv["code2"] if sv else "00"
            erp._ensure_item_specification(item_group, val, code2, i)
            payload[f"item_specification_{i}"] = f"{item_group}-{val}-{code2}"
        else:
            payload[f"item_specification_{i}"] = ""
            
    try:
        res = erp._resource("PUT", "Item", name=code, payload=payload)
    except Exception as e:
        raise ApiError("FAILED", f"Failed to update ERP item: {e}")
        
    return ok({"message": "ERP Item classified successfully"})


ROUTES = [
    # ── legacy, unversioned, unchanged shape — web/app.js still calls these
    ("GET", "/api/master", master_list),
    ("GET", "/api/audit", audit),
    ("GET", "/api/mappings", mappings),
    ("GET", "/api/vacancies", vacancies),
    ("GET", "/api/export", export),
    ("GET", "/api/download/<file>", download),
    ("POST", "/api/item/update", item_update),
    ("POST", "/api/group/move/preview", group_move_preview),
    ("POST", "/api/group/move", group_move),
    ("POST", "/api/group/merge", group_merge),
    ("POST", "/api/group/delete", group_delete),
    ("POST", "/api/rename", rename),
    ("POST", "/api/group/labels", group_labels),
    ("POST", "/api/specval/add", specval_add),
    ("POST", "/api/head/add", head_add),
    ("POST", "/api/subhead/add", subhead_add),
    ("POST", "/api/group/add", group_add),

    # ── /api/v1 — web/master.js
    ("GET", "/api/v1/item", item_list_v1),
    ("GET", "/api/v1/item/<code>", item_detail_v1),
    ("POST", "/api/v1/item/<code>/update", item_update_v1),
    ("POST", "/api/v1/item/<code>/push", item_push_v1),
    ("GET", "/api/v1/versions", versions_v1),
    ("POST", "/api/v1/revert", revert_v1),
    ("GET", "/api/v1/audit", activity_v1),
    ("GET", "/api/v1/vacancies", vacancies_v1),
    ("GET", "/api/v1/export", export_v1),
    ("GET", "/api/v1/download/<file>", download_v1),
    ("GET", "/api/v1/erp-items", erp_items_v1),
    ("GET", "/api/v1/erp-taxonomy/missing", missing_erp_taxonomy),
    ("POST", "/api/v1/erp-group/map", map_erp_group),
    ("POST", "/api/v1/erp-items/<str>/classify", classify_erp_item),
]
