"""Create screen — Agent E. Behind login (once Agent B's require_session
lands); this is where a resolved line becomes a committed item.

Owns: /api/v1/resolve*, /ingest, /commit, /cascade/*

The handlers below are the pre-existing resolve/commit/ingest endpoints,
relocated unchanged from the old monolithic server.py so the current UI
keeps working under their old un-versioned paths. `/api/alias/add` moved
here too - it is exactly the "operator correction learned as an alias" this
screen is meant to do (agents/AGENT_E_CREATE.md).

Everything below `# ============ /api/v1` is new: the real, authenticated,
enveloped API the create screen (web/create.js) actually talks to.
"""
import json

from core import db as D
from core import resolve as R
from core import codes as C
from core import ingest as ING
from core.api import ok, ApiError
from core.context import ctx

try:
    from core.auth import require_session          # Agent B
except ImportError:                                  # not landed yet
    def require_session(req):
        """Stand-in until core/auth.py exists (agents/CONTRACTS.md §6).

        Falls back to the forgeable X-User header dispatch.py already
        parses, so the screen is usable today. This is not a security
        boundary - it exists only so the create screen has *something*
        gating it before Agent B's real session cookie/token lands. The
        `except ImportError` above means the swap to the real thing is
        automatic: once core/auth.py exists this branch simply stops
        executing, no edit needed here.
        """
        u = getattr(req, "user", None)
        if not u or u == "unknown":
            raise ApiError("AUTH_REQUIRED", "sign in to create codes")
        return u


# ============================================================= pre-existing
# Relocated verbatim by Agent 0. Old, un-versioned paths, unwrapped JSON -
# web/app.js still calls these directly. Do not change their shape.

def resolve_one(req):
    require_session(req)
    return R.resolve(ctx.con, ctx.matcher, req.body)


def resolve_batch(req):
    require_session(req)
    out = []
    for line in req.body.get("lines", []):
        out.append(R.resolve(ctx.con, ctx.matcher, {
            "text": line.get("description") or line.get("raw"),
            "name": line.get("name"), "hsn": line.get("hsn"),
            "uom": line.get("uom"), "vendor": line.get("vendor"),
            "hints": line.get("hints") or {}}))
    return {"results": out}


def commit(req):
    require_session(req)
    try:
        res = R.commit(ctx.con, ctx.matcher, req.body.get("proposal") or {}, req.user,
                       push_erp=bool(req.body.get("push_erp")), erp=ctx.erp)
        return {"ok": True, **res}
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}


def ingest(req):
    require_session(req)
    if req.files:
        fn, data = next(iter(req.files.values()))
        lines, note = ING.ingest(data, fn)
        return {"lines": lines, "note": note, "source": fn}
    text = req.fields.get("text") or (req.body or {}).get("text") or ""
    return {"lines": ING.from_text(text), "note": "", "source": "pasted text"}


def alias_add(req):
    require_session(req)
    from core.matcher import normalize
    p = req.body
    con = ctx.con
    con.execute("""INSERT OR IGNORE INTO alias(scope,ref_id,term,term_norm,user,ts)
                   VALUES(?,?,?,?,?,?)""",
                (p["scope"], int(p["ref_id"]), p["term"], normalize(p["term"]),
                 req.user, D.now()))
    D.log(con, req.user, "learn-alias", p["term"], {"scope": p["scope"], "ref": p["ref_id"]})
    con.commit()
    return {"ok": True}


# ==================================================================== /api/v1
# The real create-screen API: enveloped (core.api.ok/err), behind
# require_session, and the only thing web/create.js talks to.

def _llm_available():
    """True only when a provider is actually configured (CONTRACTS.md
    decision 11 - fuzzy-only until a key exists). Settings table wins over
    config.json since that is where the admin pastes the key at runtime."""
    cfg_llm = ctx.cfg.get("llm") or {}
    provider = D.get_setting(ctx.con, "llm.provider", cfg_llm.get("provider", "none"))
    api_key = D.get_setting(ctx.con, "llm.api_key", cfg_llm.get("api_key", ""))
    return bool(provider and provider != "none" and api_key)


def _matched_by(res):
    """Collapse resolve()'s per-phase layers ('fuzzy'|'llm'|'human' plus a
    'chosen by operator' status) into the single word CONTRACTS.md §7 wants
    on the card: exact | rules | llm | operator. Precedence: an explicit
    operator choice anywhere always wins the label, then any LLM
    involvement, else rules (fuzzy layer, or LLM genuinely unavailable -
    'rules' is the honest word either way; llm_available tells the UI which
    of those two it was)."""
    if res.get("action") == "existing":
        how = ((res.get("phase1") or {}).get("hit") or {}).get("how", "")
        return "exact" if how != "semantic match" else "rules"

    layers = []
    p2 = res.get("phase2") or {}
    for step in p2.get("steps", []):
        layers.append((step.get("status"), step.get("layer")))
    p3 = res.get("phase3") or {}
    for s in p3.get("slots", []):
        layers.append((s.get("status"), s.get("layer")))
    v = p3.get("vendor")
    if v:
        layers.append((v.get("status"), v.get("layer")))

    if any(status == "chosen by operator" for status, _layer in layers):
        return "operator"
    if any(layer == "llm" for _status, layer in layers):
        return "llm"
    return "rules"


def _augment(res):
    """Attach the outcome/matched_by/llm_available fields CONTRACTS.md §7
    promises on top of whatever resolve() itself returned, without touching
    resolve.py (Agent C's file - see agents/done/AGENT_E.md for the note on
    why this lives here instead)."""
    blockers = res.get("blockers") or []
    if res.get("action") == "existing":
        res["outcome"] = "exists"
    elif blockers:
        res["outcome"] = "needs_input"
    else:
        res["outcome"] = "new"
    res["matched_by"] = _matched_by(res)
    res["llm_available"] = _llm_available()
    return res


def resolve_v1(req):
    """POST /api/v1/resolve - one line, full three-phase resolution."""
    require_session(req)
    res = R.resolve(ctx.con, ctx.matcher, req.body or {})
    return ok(_augment(res))


def resolve_batch_v1(req):
    """POST /api/v1/resolve_batch - many lines, one line's failure never
    takes down the rest (agents/AGENT_E_CREATE.md done-when: a 20-line
    invoice produces 20 independent cards)."""
    require_session(req)
    out = []
    for line in (req.body or {}).get("lines", []):
        try:
            payload = {
                "text": line.get("description") or line.get("raw") or line.get("text"),
                "name": line.get("name"), "hsn": line.get("hsn"),
                "uom": line.get("uom"), "vendor": line.get("vendor"),
                "hints": line.get("hints") or {},
            }
            out.append(_augment(R.resolve(ctx.con, ctx.matcher, payload)))
        except Exception as e:                                # noqa: BLE001
            out.append({
                "input": line, "action": None, "outcome": "error",
                "matched_by": None, "code": None, "phase1": None, "phase2": None,
                "phase3": None, "blockers": [f"could not resolve this line ({e.__class__.__name__})"],
            })
    return ok({"results": out})


def resolve_preview(req):
    """POST /api/v1/resolve/preview - deterministic recompute for the
    cascading-dropdown editor. No matching happens here; every segment is
    either a specval id the operator picked from a /cascade/* dropdown, or
    literal new-value text they just typed (sent as "new:<text>"). The code
    itself is always produced by core.codes.assemble - CONTRACTS.md §3:
    "nobody except Agent D writes code-assembly logic. If you need a code
    built, call assemble." This is what makes editing "live": every
    dropdown change round-trips here and the screen shows exactly what the
    real grammar produces, never a JS guess at it.
    """
    require_session(req)
    con = ctx.con
    b = req.body or {}
    gid = b.get("group_id")
    if not gid:
        raise ApiError("VALIDATION", "choose a group first")
    g = D.one(con, """SELECT g.id,g.name,g.code3,g.uom,g.labels,
                             s.code2 AS sub_code, h.code2 AS head_code
                      FROM grp g JOIN subhead s ON s.id=g.subhead_id
                                 JOIN head h ON h.id=s.head_id
                      WHERE g.id=?""", (int(gid),))
    if not g:
        raise ApiError("NOT_FOUND", "no such group")
    labels = json.loads(g["labels"] or "{}")

    blockers = []
    slots, detail = [], []
    for slot in (1, 2, 3, 4):
        label = labels.get(str(slot))
        sel = b.get(f"s{slot}")
        if not label:
            slots.append(None)
            detail.append({"slot": slot, "label": None, "code": None})
            continue
        if not sel:
            slots.append(None)
            detail.append({"slot": slot, "label": label, "code": None, "value": None})
            continue
        if isinstance(sel, str) and sel.startswith("new:"):
            code2 = C.next_spec_code(con, g["id"], slot)
            slots.append(code2)
            detail.append({"slot": slot, "label": label, "code": code2,
                           "value": sel[4:], "pending_new": True})
        else:
            sv = D.one(con, "SELECT value,code2 FROM specval WHERE id=? AND grp_id=? AND slot=?",
                      (int(sel), g["id"], slot))
            if not sv:
                raise ApiError("VALIDATION", f"unknown value chosen for '{label}'")
            slots.append(sv["code2"])
            detail.append({"slot": slot, "label": label, "code": sv["code2"], "value": sv["value"]})

    vend, vend_detail = None, None
    if labels.get("vendor"):
        selv = b.get("vendor")
        if not selv:
            vend_detail = {"label": labels["vendor"], "code": None, "value": None}
        elif isinstance(selv, str) and selv.startswith("new:"):
            vend = C.next_spec_code(con, g["id"], 5)
            vend_detail = {"label": labels["vendor"], "code": vend, "value": selv[4:], "pending_new": True}
        else:
            sv = D.one(con, "SELECT value,code2 FROM specval WHERE id=? AND grp_id=? AND slot=5",
                      (int(selv), g["id"]))
            if not sv:
                raise ApiError("VALIDATION", "unknown vendor chosen")
            vend = sv["code2"]
            vend_detail = {"label": labels["vendor"], "code": vend, "value": sv["value"]}

    code = C.assemble(g["head_code"], g["sub_code"], g["code3"], slots, vend)
    free = C.code_is_free(con, code)
    if not free:
        blockers.append(f"{code} is already issued - adjust a spec value or pick the existing item.")

    return ok({
        "code": code, "free": free, "blockers": blockers,
        "group": {"id": g["id"], "name": g["name"], "code3": g["code3"], "uom": g["uom"]},
        "slots": detail, "vendor": vend_detail, "matched_by": "operator",
        # same shape as resolve()'s out["segments"], so the client's one
        # code-bar renderer works for both a matched proposal and a manual edit
        "segments": {"head": g["head_code"], "sub": g["sub_code"], "group": g["code3"],
                     "specs": slots, "vendor": vend},
    })


def ingest_v1(req):
    """POST /api/v1/ingest - text, or a multipart file (PDF / scan / photo /
    Excel / CSV / plain text). A bad or unreadable file returns an empty
    line list with an explanatory note rather than a 500 - the extraction
    cascade in core/ingest.py already isolates per-page OCR failures the
    same way (missing Tesseract on one page never loses the other pages)."""
    require_session(req)
    if req.files:
        fn, data = next(iter(req.files.values()))
        lines, note = ING.ingest(data, fn)
        pages = sorted({l["page"] for l in lines if l.get("page")})
        ocr_lines = sum(1 for l in lines if l.get("ocr"))
        return ok({"lines": lines, "note": note, "source": fn,
                   "pages": pages, "ocr_lines": ocr_lines})
    text = (req.fields or {}).get("text") or (req.body or {}).get("text") or ""
    if not text.strip():
        raise ApiError("VALIDATION", "paste some text, or choose a file")
    return ok({"lines": ING.from_text(text), "note": "", "source": "pasted text"})


def commit_v1(req):
    """POST /api/v1/commit - the only place anything is written. Requires
    an idempotency_key; a replay of the same key returns the original
    result instead of minting a second code (agents/AGENT_E_CREATE.md task
    5 - "people double-click"). The key -> result mapping is kept in the
    existing `audit` table (action='commit-idem', target=key) rather than a
    new table, since Agent 0 owns core/db.py's schema and nothing here
    needs one; it also means a replay survives a server restart, which a
    pure in-memory cache would not.
    """
    user = require_session(req)
    body = req.body or {}
    proposal = body.get("proposal") or {}
    idem_key = body.get("idempotency_key") or req.headers.get("Idempotency-Key")
    if not idem_key:
        raise ApiError("VALIDATION", "missing idempotency_key - resend the same key on retry")

    blockers = proposal.get("blockers") or []
    if blockers:
        raise ApiError("VALIDATION", "this line still has open questions - answer them before submitting",
                       detail={"blockers": blockers})

    prior = D.one(ctx.con, """SELECT detail FROM audit WHERE action='commit-idem' AND target=?
                              ORDER BY id DESC LIMIT 1""", (idem_key,))
    if prior:
        detail = prior["detail"]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (TypeError, ValueError):
                detail = {}
        return ok({**detail, "idempotent": True})

    try:
        res = R.commit(ctx.con, ctx.matcher, proposal, user,
                       push_erp=bool(body.get("push_erp")), erp=ctx.erp)
    except ValueError as e:
        raise ApiError("CONFLICT", str(e))

    D.log(ctx.con, user, "commit-idem", idem_key, res)
    ctx.con.commit()
    return ok({**res, "idempotent": False})


def alias_add_v1(req):
    """POST /api/v1/alias/add - the operator's correction, learned so the
    same wording resolves automatically next time (agents/AGENT_E_CREATE.md
    task 5). Currently only scope='group' is actually consulted by
    resolve.py's matching (phase2_taxonomy's `gkey`); a scope='specval'
    alias is stored the same way but phase3_specs does not yet look aliases
    up for spec values - flagged in agents/done/AGENT_E.md for Agent C."""
    user = require_session(req)
    from core.matcher import normalize
    p = req.body or {}
    scope, ref_id, term = p.get("scope"), p.get("ref_id"), p.get("term")
    if not (scope and ref_id and term):
        raise ApiError("VALIDATION", "scope, ref_id and term are all required")
    con = ctx.con
    con.execute("""INSERT OR IGNORE INTO alias(scope,ref_id,term,term_norm,user,ts)
                   VALUES(?,?,?,?,?,?)""",
                (scope, int(ref_id), term, normalize(term), user, D.now()))
    D.log(con, user, "learn-alias", term, {"scope": scope, "ref": ref_id})
    con.commit()
    return ok({"learned": True, "scope": scope, "ref_id": int(ref_id), "term": term})


def cascade_taxes(req):
    """GET /api/v1/cascade/taxes - fetches Item Tax Templates from ERPNext"""
    require_session(req)
    if not ctx.erp or not ctx.erp.enabled:
        return ok({"taxes": []})
    return ok({"taxes": ctx.erp.get_tax_templates(ctx.con)})


# ---------------------------------------------------------------- cascade
def cascade_heads(req):
    require_session(req)
    return ok({"heads": D.rows(ctx.con, "SELECT id,name,code2 FROM head WHERE active=1 ORDER BY name")})


def cascade_subheads(req):
    """GET /api/v1/cascade/subheads?head=<id> - sub-heads filtered to one
    head. Choosing a head must filter sub-heads to that head; a sub-head
    from the wrong head is never even in this list, so it cannot be picked
    by the UI (agents/AGENT_E_CREATE.md task 3: "impossible to choose, not
    merely discouraged")."""
    require_session(req)
    head_id = req.query.get("head")
    if not head_id:
        raise ApiError("VALIDATION", "head is required")
    rows = D.rows(ctx.con, """SELECT id,name,code2 FROM subhead
                              WHERE head_id=? AND active=1 ORDER BY name""", (int(head_id),))
    return ok({"subheads": rows})


def cascade_groups(req):
    """GET /api/v1/cascade/groups?subhead=<id> - groups filtered to one
    sub-head only (never the whole dictionary)."""
    require_session(req)
    sub_id = req.query.get("subhead")
    if not sub_id:
        raise ApiError("VALIDATION", "subhead is required")
    rows = D.rows(ctx.con, """SELECT id,name,code3,uom FROM grp
                              WHERE subhead_id=? AND status='active' ORDER BY name""", (int(sub_id),))
    return ok({"groups": rows})


def cascade_slots(req):
    """GET /api/v1/cascade/slots?group=<id> - this group's own spec labels
    and each slot's existing values. Slot meaning is per group, never
    global (CONTRACTS.md §1.3) - the label for slot 1 of "Air Freshener" is
    "Type"; slot 1 of "Battery Pack" is "Form Factor". Nothing here reuses
    another group's labels."""
    require_session(req)
    gid = req.query.get("group")
    if not gid:
        raise ApiError("VALIDATION", "group is required")
    g = D.one(ctx.con, "SELECT id,name,code3,uom,labels FROM grp WHERE id=?", (int(gid),))
    if not g:
        raise ApiError("NOT_FOUND", "no such group")
    labels = json.loads(g["labels"] or "{}")
    specs = {}
    for slot in (1, 2, 3, 4, 5):
        key = "vendor" if slot == 5 else str(slot)
        if labels.get(key):
            specs[key] = D.rows(ctx.con, "SELECT id,value,code2 FROM specval WHERE grp_id=? AND slot=? ORDER BY code2",
                                (int(gid), slot))
    return ok({"group": {"id": g["id"], "name": g["name"], "code3": g["code3"], "uom": g["uom"]},
              "labels": labels, "specs": specs})


ROUTES = [
    # pre-existing, unversioned - do not change shape, web/app.js depends on it
    ("POST", "/api/resolve", resolve_one),
    ("POST", "/api/resolve_batch", resolve_batch),
    ("POST", "/api/commit", commit),
    ("POST", "/api/ingest", ingest),
    ("POST", "/api/alias/add", alias_add),

    # /api/v1 - the real create-screen API (web/create.js)
    ("POST", "/api/v1/resolve", resolve_v1),
    ("POST", "/api/v1/resolve_batch", resolve_batch_v1),
    ("POST", "/api/v1/resolve/preview", resolve_preview),
    ("POST", "/api/v1/ingest", ingest_v1),
    ("POST", "/api/v1/commit", commit_v1),
    ("POST", "/api/v1/alias/add", alias_add_v1),
    ("GET", "/api/v1/cascade/heads", cascade_heads),
    ("GET", "/api/v1/cascade/subheads", cascade_subheads),
    ("GET", "/api/v1/cascade/groups", cascade_groups),
    ("GET", "/api/v1/cascade/slots", cascade_slots),
    ("GET", "/api/v1/cascade/taxes", cascade_taxes),
]
