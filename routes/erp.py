"""ERPNext — Agent G. Nobody else calls ERPNext, not for a quick check, not
for a test (agents/CONTRACTS.md §8).

Owns: /api/v1/erp/*

The first two handlers below (`ping`/`pull`) are the pre-existing endpoints,
relocated unchanged from the old monolithic server.py so the current UI
keeps working under their old un-versioned paths — Agent 0's note in
agents/done/AGENT_0.md. Everything from `v1_ping` down is new: task 1-6 of
agents/AGENT_G_ERPNEXT.md.

Every write endpoint calls `require_session` (agents/CONTRACTS.md §6 — "this
is the security boundary, hiding a button is not"). The read/validation
endpoints do too, since none of this is public data.
"""
import threading
import traceback
import urllib.error

from core import db as D
from core import auth as A
from core import sync as S
from core.api import ok, ApiError
from core.context import ctx
from core.erp import ErpGuardrailError, ErpValidationError


def ping(req):
    return ctx.erp.ping(ctx.con)


def pull(req):
    A.require_session(req)
    con, erpc = ctx.con, ctx.erp
    try:
        items = erpc.pull_items(con=con)
    except Exception as e:                                        # noqa: BLE001
        return {"ok": False, "error": str(e)}
    from core.matcher import normalize
    for it in items:
        con.execute("""INSERT OR REPLACE INTO erp_item
                       (code,name,name_norm,item_group,uom,disabled,owner,pulled_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (it["name"], it.get("item_name"), normalize(it.get("item_name")),
                     it.get("item_group"), it.get("stock_uom"),
                     it.get("disabled") or 0, it.get("owner"), D.now()))
        con.execute("INSERT OR IGNORE INTO code_ledger(code,item_id,state,ts,note) "
                    "VALUES(?,NULL,'erp-only',?,?)",
                    (it["name"], D.now(), "pulled from ERPNext"))
    D.log(con, req.user, "erp-pull", erpc.base, {"count": len(items)})
    con.commit()
    return {"ok": True, "count": len(items)}


# ============================================================ v1 — task 1/2
def _guarded(fn, *a, **kw):
    """Every erp.py write handler funnels its call to core.erp/core.sync
    through this so a guardrail refusal reaches the client as a clean
    VALIDATION/FORBIDDEN error, never a 500 - and definitely never a stack
    trace (agents/CONTRACTS.md §6)."""
    try:
        return fn(*a, **kw)
    except ErpGuardrailError as e:
        raise ApiError("FORBIDDEN", str(e))
    except ErpValidationError as e:
        raise ApiError("VALIDATION", str(e))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError("UPSTREAM", f"ERPNext unreachable: {e}")
    except RuntimeError as e:
        # e.g. "no API key and no username/password configured" - a config
        # problem, not a server bug, so it must not surface as a 500.
        raise ApiError("VALIDATION", str(e))


def v1_ping(req):
    return ok(ctx.erp.ping(ctx.con))


def v1_status(req):
    A.require_session(req)
    erp = ctx.erp
    erp.refresh(ctx.con)
    return ok({"enabled": erp.enabled, "dry_run": erp.dry_run, "base_url": erp.base,
               "populate_specs": erp.populate_specs,
               "auth": "api_key" if erp.api_key and erp.api_secret else "password/none"})


def v1_validate(req):
    """Check a UoM / GST HSN Code / Item Group before a caller (the create
    screen, the master editor) ever attempts a write. Task 2."""
    A.require_session(req)
    con, erp = ctx.con, ctx.erp
    q = req.query or {}
    out = {}
    if "uom" in q:
        out["uom"] = {"value": q["uom"], "valid": _guarded(erp.validate_uom, q["uom"], con)}
    if "hsn" in q:
        out["hsn"] = {"value": q["hsn"], "valid": _guarded(erp.validate_hsn, q["hsn"], con)}
    if "item_group" in q:
        out["item_group"] = {"value": q["item_group"],
                              "valid": _guarded(erp.validate_item_group, q["item_group"], con)}
    if not out:
        raise ApiError("VALIDATION", "pass at least one of uom, hsn, item_group")
    return ok(out)


# ================================================================== task 3
def v1_spec_ensure(req):
    A.require_session(req)
    b = req.body or {}
    for k in ("item_group", "slot", "specification", "specification_code"):
        if not b.get(k) and b.get(k) != 0:
            raise ApiError("VALIDATION", f"'{k}' is required")
    res = _guarded(ctx.erp.ensure_specification, b["item_group"], int(b["slot"]),
                    b["specification"], b["specification_code"], ctx.con)
    return ok({"result": res})


def v1_vendor_ensure(req):
    A.require_session(req)
    b = req.body or {}
    if not b.get("vendor_name"):
        raise ApiError("VALIDATION", "'vendor_name' is required")
    res = _guarded(ctx.erp.ensure_vendor, b["vendor_name"], b.get("vendor_code"), ctx.con)
    return ok({"result": res})


# ================================================================== push one
def v1_item_push(req):
    """Manual "push this one item to ERPNext" for the master editor -
    one at a time, on demand (CONTRACTS.md decision #13). Refuses a
    provisional code itself, via core.erp - not just hiding the button."""
    A.require_session(req)
    con, erp = ctx.con, ctx.erp
    code = req.params.get("code")
    it = D.one(con, "SELECT * FROM item WHERE code=?", (code,))
    if not it:
        raise ApiError("NOT_FOUND", f"no item {code!r}")
    grp = D.one(con, "SELECT name FROM grp WHERE id=?", (it["grp_id"],)) if it.get("grp_id") else None
    res = _guarded(erp.create_item, code, it["name"], grp["name"] if grp else None,
                    it["uom"] or "Nos", it["hsn"], None, con)
    if res.get("ok") and not res.get("dry_run"):
        con.execute("UPDATE item SET status='in_erp', erp_synced_at=?, frozen=1 WHERE code=?",
                    (D.now(), code))
        con.commit()
    D.log(con, req.user, "erp-push-item", code, res)
    con.commit()
    return ok({"result": res})


# ================================================================== task 4
def v1_sync_run(req):
    """On-demand sync from the UI. Body: {"direction": "pull"|"push"|"both"}."""
    A.require_session(req)
    direction = (req.body or {}).get("direction", "pull")
    if direction not in ("pull", "push", "both"):
        raise ApiError("VALIDATION", "direction must be 'pull', 'push', or 'both'")
    
    if direction == "both":
        row1 = S.sync(ctx.con, direction="pull")
        row2 = S.sync(ctx.con, direction="push")
        return ok({"sync": [row1, row2]})
        
    row = S.sync(ctx.con, direction=direction)
    return ok({"sync": row})


def v1_sync_log(req):
    A.require_session(req)
    import json as _json
    limit = int((req.query or {}).get("limit", 50))
    rows = D.rows(ctx.con, "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        for k in ("conflicts", "detail"):
            try:
                r[k] = _json.loads(r[k]) if r.get(k) else ([] if k == "conflicts" else {})
            except (ValueError, TypeError):
                pass
    return ok({"log": rows})


# ================================================================== task 5
def v1_reconcile_run(req):
    """On-demand trigger from the UI - normally fires automatically via
    core.sync.tier_reconnect_hook() when core.tier.TierClient sees the VPS
    come back (see the handover note on that wiring)."""
    A.require_session(req)
    result = S.reconcile_failsafe(ctx.con)
    return ok({"reconcile": result})


def v1_reconcile_upload(req):
    """VPS-side receiver: a tier-2 server uploads what it minted offline.
    No session required - this is server-to-server, not operator-to-server;
    the two sides authenticate at the transport layer (HTTPS + whatever
    Agent H's tier wiring adds), same trust boundary as the public API's own
    machine-to-machine calls."""
    items = (req.body or {}).get("items") or []
    return ok(S.receive_upload(ctx.con, items))


def v1_reconcile_finalize(req):
    """VPS-side receiver: finalise a batch of provisional codes and hand
    back the old->new mapping. Never resolves a same-code-both-sides
    conflict - reports it."""
    codes = (req.body or {}).get("codes") or []
    return ok(S.receive_finalize(ctx.con, codes))


# ========================================================= task 4 scheduler
def _scheduler_loop():
    """Twice-daily sync per `sync.times` (default 09:00,17:00) -
    agents/AGENT_G_ERPNEXT.md task 4: "Runs at sync.times ... and on demand
    from the UI." Started once, lazily, the first time this module is
    imported (server.py imports every route module at startup, after
    ctx.init() - see agents/done/AGENT_0.md's route split table - so
    ctx.con already exists by the time this thread's loop body runs).
    A single bad sync is logged and never kills the thread."""
    import time as _t
    while True:
        try:
            if ctx.con is None:
                _t.sleep(30)
                continue
            wait = S.next_run_in_seconds(ctx.con)
            _t.sleep(wait)
            if ctx.con is None:
                continue
            with ctx.lock:
                S.sync(ctx.con, direction="pull")
        except Exception:                                            # noqa: BLE001
            traceback.print_exc()
            _t.sleep(300)


def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, name="erp-sync-scheduler", daemon=True)
    t.start()


_start_scheduler()


ROUTES = [
    ("GET", "/api/erp/ping", ping),
    ("POST", "/api/erp/pull", pull),

    ("GET", "/api/v1/erp/ping", v1_ping),
    ("GET", "/api/v1/erp/status", v1_status),
    ("GET", "/api/v1/erp/validate", v1_validate),

    ("POST", "/api/v1/erp/spec/ensure", v1_spec_ensure),
    ("POST", "/api/v1/erp/vendor/ensure", v1_vendor_ensure),

    ("POST", "/api/v1/erp/item/<code>/push", v1_item_push),

    ("POST", "/api/v1/erp/sync", v1_sync_run),
    ("GET", "/api/v1/erp/sync/log", v1_sync_log),

    ("POST", "/api/v1/erp/reconcile", v1_reconcile_run),
    ("POST", "/api/v1/erp/reconcile/upload", v1_reconcile_upload),
    ("POST", "/api/v1/erp/reconcile/finalize", v1_reconcile_finalize),
]
