"""Public face — Agent A. No login, ever. Read-only by construction: no
INSERT/UPDATE/DELETE belongs in this file (agents/CONTRACTS.md §6, §9).

Owns: /api/v1/decode, /dictionary/*, /directory/*, /meta

The pre-existing (un-versioned) bootstrap/groups/group/decode handlers stay
exactly as they were — the current creator UI (web/app.js) still calls them
directly and their response shape is unwrapped JSON, not the ok()/err()
envelope. Everything new lives under /api/v1 and uses the envelope.

Nothing below ever writes. Decoding is never reimplemented here: every
handler calls core.codes.structural_parse (the single source of truth for
code grammar, agents/CONTRACTS.md §3) and looks specification labels up on
the matched group — never from a global list, because slot meaning is
per-group (CONTRACTS §1.3).
"""
import json
import re

from core import db as D
from core import codes as C
from core.api import ok, ApiError
from core.context import ctx


def _rows(sql, args=()):
    return D.rows(ctx.con, sql, args)


def _one(sql, args=()):
    return D.one(ctx.con, sql, args)


def _int(v, default, lo=None, hi=None):
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = default
    if lo is not None:
        n = max(n, lo)
    if hi is not None:
        n = min(n, hi)
    return n


_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def _norm(s):
    """Squash to bare upper-case alphanumerics so 'M-Seal' and 'mseal'
    compare equal — punctuation in an item name should never hide it from a
    search box (agents/AGENT_A_PUBLIC.md done-when: 'mseal' must find it)."""
    return _ALNUM_RE.sub("", (s or "").upper())


# ------------------------------------------------------------- shared decode
# Both the old /api/decode and the new /api/v1/decode build on these two
# helpers so there is exactly one place that turns a parsed code + its group
# row into labelled specifications.

def _group_by_prefix(p):
    return _one("""SELECT g.id,g.name,g.labels,s.name AS sub_name,h.name AS head_name
                FROM grp g JOIN subhead s ON s.id=g.subhead_id JOIN head h ON h.id=s.head_id
                WHERE h.code2=? AND s.code2=? AND g.code3=?""",
                (p["head"], p["sub"], p["group"]))


def _decode_specs(g, p):
    """g needs 'id' and 'labels'; p is a structural_parse() result. Returns
    (specs, labels_dict). The label for a slot always comes from THIS
    group's labels JSON — never a global list (CONTRACTS §1.3)."""
    labels = json.loads(g["labels"] or "{}")
    specs = []
    for slot, key in ((1, "s1"), (2, "s2"), (3, "s3"), (4, "s4"), (5, "vendor")):
        cc = p[key]
        if cc is None:
            continue
        label = labels.get("vendor" if slot == 5 else str(slot))
        if cc == "00":
            specs.append({"slot": slot, "label": label, "code": "00",
                          "value": "(not applicable)"})
            continue
        sv = _one("SELECT value FROM specval WHERE grp_id=? AND slot=? AND code2=?",
                  (g["id"], slot, cc))
        specs.append({"slot": slot, "label": label, "code": cc,
                      "value": sv["value"] if sv else "(unknown value)"})
    return specs, labels


# ============================================================= pre-existing
# Relocated verbatim by Agent 0. Old, un-versioned paths; unwrapped JSON.
# Kept working exactly as before because web/app.js (the creator UI) still
# calls them directly.

def bootstrap(req):
    con, cfg, erpc = ctx.con, ctx.cfg, ctx.erp
    return {
        "app": cfg.get("app_name"),
        "counts": {k: con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                   for k, t in (("heads", "head"), ("subheads", "subhead"),
                                ("groups", "grp"), ("specs", "specval"),
                                ("items", "item"), ("erp", "erp_item"),
                                ("reserved", "code_ledger"))},
        "heads": _rows("SELECT id,name,code2 FROM head WHERE active=1 ORDER BY name"),
        "subheads": _rows("""SELECT s.id,s.name,s.code2,s.head_id,h.name AS head_name,
                                     h.code2 AS head_code
                              FROM subhead s JOIN head h ON h.id=s.head_id
                              WHERE s.active=1 ORDER BY h.name,s.name"""),
        "llm": {"provider": (cfg.get("llm") or {}).get("provider", "none"),
                "threshold": cfg.get("match_threshold", 60)},
        "erp": {"enabled": erpc.enabled, "dry_run": erpc.dry_run, "base": erpc.base},
        "seeded_at": D.get_setting(con, "seeded_at"),
    }


def groups(req):
    q = req.query
    sql = """SELECT g.id,g.name,g.code3,g.labels,g.status,g.uom,
                    s.name AS sub_name,s.code2 AS sub_code,s.id AS sub_id,
                    h.name AS head_name,h.code2 AS head_code,
                    (SELECT COUNT(*) FROM item WHERE grp_id=g.id) AS n_items,
                    (SELECT COUNT(*) FROM specval WHERE grp_id=g.id) AS n_specs
             FROM grp g JOIN subhead s ON s.id=g.subhead_id
             JOIN head h ON h.id=s.head_id WHERE g.status='active'"""
    args = []
    if q.get("q"):
        sql += " AND (g.name LIKE ? OR s.name LIKE ? OR h.name LIKE ?)"
        args += [f"%{q['q']}%"] * 3
    if q.get("sub_id"):
        sql += " AND s.id=?"
        args.append(q["sub_id"])
    sql += " ORDER BY h.code2,s.code2,g.code3 LIMIT ?"
    args.append(int(q.get("limit", 300)))
    out = _rows(sql, args)
    for g in out:
        g["labels"] = json.loads(g["labels"] or "{}")
        g["prefix"] = g["head_code"] + g["sub_code"] + g["code3"]
    return out


def group_detail(req):
    gid = int(req.params["id"])
    g = _one("""SELECT g.*, s.name AS sub_name,s.code2 AS sub_code,s.id AS sub_id,
                       h.name AS head_name,h.code2 AS head_code
                FROM grp g JOIN subhead s ON s.id=g.subhead_id
                JOIN head h ON h.id=s.head_id WHERE g.id=?""", (gid,))
    if not g:
        return 404, {"error": "not found"}
    g["labels"] = json.loads(g["labels"] or "{}")
    g["prefix"] = g["head_code"] + g["sub_code"] + g["code3"]
    g["specs"] = {str(s): _rows(
        "SELECT id,value,code2 FROM specval WHERE grp_id=? AND slot=? ORDER BY code2",
        (gid, s)) for s in (1, 2, 3, 4, 5)}
    g["items"] = _rows("SELECT code,name,status,frozen,decodable FROM item "
                       "WHERE grp_id=? ORDER BY code LIMIT 400", (gid,))
    return g


def decode(req):
    code = (req.query.get("code") or "").upper().strip()
    p = C.structural_parse(code)
    if not p:
        return {"code": code, "wellformed": False,
                "why": "expected 4 letters + 3 digits + 0-5 pairs (length 7/9/11/13/15/17)"}
    g = _group_by_prefix(p)
    out = {"code": code, "wellformed": True, "segments": p, "known": bool(g)}
    if g:
        out.update({"head": g["head_name"], "subhead": g["sub_name"], "group": g["name"]})
        out["specs"], _ = _decode_specs(g, p)
    it = _one("SELECT code,name,status FROM item WHERE code=?", (code,)) or \
        _one("SELECT code,name,'live ERPNext' AS status FROM erp_item WHERE code=?", (code,))
    out["item"] = it
    return out


# ==================================================================== /api/v1
# Real routes for the public page. Envelope everything through ok()/ApiError
# (agents/CONTRACTS.md §6) — this is the boundary that must never write.

def decode_v1(req):
    """GET /api/v1/decode?code=  — structural + dictionary decode.

    A malformed code raises BAD_CODE with a plain-English reason (never the
    bare error code — agents/AGENT_A_PUBLIC.md task 2). A well-formed code
    with no item still decodes structurally; `issued` says whether anything
    has actually been created against it.
    """
    code = (req.query.get("code") or "").strip().upper()
    if not code:
        raise ApiError("VALIDATION", "enter a code to decode")

    p = C.structural_parse(code)
    if p is None:
        ln = len(code)
        if ln not in C.VALID_LENGTHS:
            raise ApiError("BAD_CODE",
                f"{ln} characters expected 7, 9, 11, 13, 15 or 17")
        raise ApiError("BAD_CODE",
            "expected 2 letters (head) + 2 letters (sub-head) + 3 digits "
            "(group), then pairs of digits for each specification")

    out = {"code": code, "wellformed": True, "segments": p}
    g = _group_by_prefix(p)
    out["known"] = bool(g)
    if not g:
        out["note"] = ("well-formed, but no such head / sub-head / group "
                        "exists in the dictionary")
        return ok(out)

    out["head"] = g["head_name"]
    out["subhead"] = g["sub_name"]
    out["group"] = g["name"]
    out["specs"], _ = _decode_specs(g, p)

    it = _one("SELECT code,name,status FROM item WHERE code=?", (code,))
    if not it:
        er = _one("SELECT code,name FROM erp_item WHERE code=?", (code,))
        if er:
            it = {"code": er["code"], "name": er["name"], "status": "live ERPNext"}
    out["issued"] = bool(it)
    out["item"] = it
    if not it:
        out["note"] = "well-formed and known, but nothing has been issued against this code yet"
    return ok(out)


def directory_list(req):
    """GET /api/v1/directory?q=&limit=&offset=

    Searches code, name and description across every item. Punctuation is
    squashed on both sides of the comparison so 'mseal' finds 'M-Seal'.
    Response is always paginated — never the whole table.
    """
    q = (req.query.get("q") or "").strip()
    limit = _int(req.query.get("limit"), 25, 1, 100)
    offset = _int(req.query.get("offset"), 0, 0)

    all_rows = _rows("""SELECT i.code,i.name,i.description,i.uom,i.hsn,i.status,
                                g.name AS group_name, s.name AS sub_name, h.name AS head_name
                         FROM item i
                         LEFT JOIN grp g ON g.id=i.grp_id
                         LEFT JOIN subhead s ON s.id=g.subhead_id
                         LEFT JOIN head h ON h.id=s.head_id
                         ORDER BY i.code""")
    if q:
        qn = _norm(q)
        all_rows = [r for r in all_rows if qn in _norm(r["code"])
                    or qn in _norm(r["name"]) or qn in _norm(r["description"])]

    total = len(all_rows)
    page = all_rows[offset:offset + limit]
    for r in page:
        r.pop("description", None)   # listing is deliberately light; detail has it

    return ok({"items": page, "total": total, "limit": limit, "offset": offset, "q": q})


def directory_item(req):
    """GET /api/v1/directory/<code> — read-only detail card for one item."""
    code = (req.params.get("code") or "").strip().upper()
    it = _one("""SELECT i.code,i.name,i.description,i.uom,i.alt_uom,i.hsn,i.status,
                        i.frozen,i.decodable,i.grp_id,
                        g.name AS group_name, g.code3 AS group_code,
                        s.name AS sub_name, s.code2 AS sub_code,
                        h.name AS head_name, h.code2 AS head_code
                 FROM item i
                 LEFT JOIN grp g ON g.id=i.grp_id
                 LEFT JOIN subhead s ON s.id=g.subhead_id
                 LEFT JOIN head h ON h.id=s.head_id
                 WHERE i.code=?""", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item found for {code}")

    specs = []
    p = C.structural_parse(code)
    if p and it.get("grp_id"):
        g = _one("SELECT id,labels FROM grp WHERE id=?", (it["grp_id"],))
        if g:
            specs, _ = _decode_specs(g, p)
    it.pop("grp_id", None)
    return ok({"item": it, "specs": specs})


def dictionary_groups(req):
    """GET /api/v1/dictionary/groups?q=&limit=&offset= — browse the 889
    groups. Same punctuation-squashed search as the directory."""
    q = (req.query.get("q") or "").strip()
    limit = _int(req.query.get("limit"), 30, 1, 100)
    offset = _int(req.query.get("offset"), 0, 0)

    all_rows = _rows("""SELECT g.id,g.name,g.code3,g.uom,
                                s.name AS sub_name, s.code2 AS sub_code,
                                h.name AS head_name, h.code2 AS head_code,
                                (SELECT COUNT(*) FROM item WHERE grp_id=g.id) AS n_items
                         FROM grp g JOIN subhead s ON s.id=g.subhead_id
                                    JOIN head h ON h.id=s.head_id
                         WHERE g.status='active'
                         ORDER BY h.code2,s.code2,g.code3""")
    for g in all_rows:
        g["prefix"] = g["head_code"] + g["sub_code"] + g["code3"]

    if q:
        qn = _norm(q)
        all_rows = [g for g in all_rows if qn in _norm(g["name"])
                    or qn in _norm(g["sub_name"]) or qn in _norm(g["head_name"])
                    or qn in _norm(g["prefix"])]

    total = len(all_rows)
    page = all_rows[offset:offset + limit]
    return ok({"groups": page, "total": total, "limit": limit, "offset": offset, "q": q})


def dictionary_group_detail(req):
    """GET /api/v1/dictionary/group/<id> — one group's spec slots, each with
    that group's own label and every value with its two-digit code."""
    try:
        gid = int(req.params.get("id"))
    except (TypeError, ValueError):
        raise ApiError("VALIDATION", "group id must be a number")

    g = _one("""SELECT g.*, s.name AS sub_name, s.code2 AS sub_code,
                       h.name AS head_name, h.code2 AS head_code
                FROM grp g JOIN subhead s ON s.id=g.subhead_id
                           JOIN head h ON h.id=s.head_id
                WHERE g.id=?""", (gid,))
    if not g:
        raise ApiError("NOT_FOUND", f"no group with id {gid}")

    labels = json.loads(g["labels"] or "{}")
    specs = []
    for slot in (1, 2, 3, 4):
        label = labels.get(str(slot))
        if not label:
            continue
        values = _rows("SELECT code2 AS code, value FROM specval WHERE grp_id=? AND slot=? ORDER BY code2",
                       (gid, slot))
        for v in values:
            if v["code"] == "00":
                v["value"] = "(not applicable)"
        specs.append({"slot": slot, "label": label, "values": values})

    vendor = None
    if labels.get("vendor"):
        vvals = _rows("SELECT code2 AS code, value FROM specval WHERE grp_id=? AND slot=5 ORDER BY code2",
                      (gid,))
        vendor = {"label": labels["vendor"], "values": vvals}

    n_items = _one("SELECT COUNT(*) c FROM item WHERE grp_id=?", (gid,))["c"]

    return ok({
        "group": {"id": g["id"], "name": g["name"],
                  "prefix": g["head_code"] + g["sub_code"] + g["code3"],
                  "uom": g["uom"], "head": g["head_name"], "subhead": g["sub_name"]},
        "specs": specs,
        "vendor": vendor,
        "item_count": n_items,
    })


ROUTES = [
    # pre-existing, unversioned — do not change shape, web/app.js depends on it
    ("GET", "/api/bootstrap", bootstrap),
    ("GET", "/api/groups", groups),
    ("GET", "/api/group/<id>", group_detail),
    ("GET", "/api/decode", decode),
    # /api/v1 — the real public API
    ("GET", "/api/v1/decode", decode_v1),
    ("GET", "/api/v1/directory", directory_list),
    ("GET", "/api/v1/directory/<code>", directory_item),
    ("GET", "/api/v1/dictionary/groups", dictionary_groups),
    ("GET", "/api/v1/dictionary/group/<id>", dictionary_group_detail),
]
