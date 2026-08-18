"""Twice-daily ERPNext sync + failsafe reconciliation on VPS reconnect —
Agent G, agents/AGENT_G_ERPNEXT.md tasks 4 and 5.

Two entry points. Both write exactly one row to `sync_log`
(agents/CONTRACTS.md §5) and **never resolve a conflict themselves** —
every disagreement found is recorded and handed to a human. A silent
overwrite in either direction is precisely the failure this file exists to
prevent (Anuraag's own words: "if a code is changed directly in ERPNext, we
need to know. Make that visible, not automatic.").

    sync(con, direction="pull")   -> ERPNext drift check (task 4)
    reconcile_failsafe(con)       -> tier-2 -> tier-1 reconnect (task 5)

`next_run_in_seconds()` is the pure scheduling helper a background thread
(started from routes/erp.py, which is mine — see the note there) uses to
wake up at `sync.times` (default "09:00,17:00").
"""
import datetime
import json
import urllib.error
import urllib.request

from . import db as D
from . import codes as C
from .erp import ErpGuardrailError, ErpValidationError

# ------------------------------------------------------------------ logging
def _write_sync_log(con, direction, doctype, found, changed, conflicts, ok, detail):
    ts = D.now()
    conflicts_json = json.dumps(conflicts, default=str)
    detail_json = json.dumps(detail, default=str)
    cur = con.execute(
        """INSERT INTO sync_log(ts,direction,doctype,found,changed,conflicts,ok,detail)
           VALUES(?,?,?,?,?,?,?,?)""",
        (ts, direction, doctype, found, changed, conflicts_json, int(bool(ok)), detail_json))
    con.commit()
    return {"id": cur.lastrowid, "ts": ts, "direction": direction, "doctype": doctype,
            "found": found, "changed": changed, "conflicts": conflicts, "ok": bool(ok),
            "detail": detail}


# ---------------------------------------------------------------- task 4
def sync(con, direction="pull", erp=None):
    """Pulls the live Item list, refreshes `erp_item`, and detects drift:

      * items in ERPNext we do not have    -> "unknown_in_erp"
      * items we have that are not there   -> "missing_in_erp"
      * fields that disagree               -> "field_conflict"

    Nothing here writes the discrepancy away. `direction="push"` additionally
    attempts to create, one at a time, any local item that is fully resolved
    (confirmed, not provisional, never yet synced) and does NOT already
    exist in ERPNext under that code — a genuine collision there is recorded
    as a conflict and the item is skipped, not overwritten.
    """
    if erp is None:
        from core.context import ctx
        erp = ctx.erp
    erp.refresh(con)
    C._ensure_provisional_column(con)  # idempotent - see core/codes.py; task 4's push
                                        # path filters on COALESCE(provisional,0)=0 below
    ts = D.now()

    if not erp.enabled:
        return _write_sync_log(con, direction, "Item", 0, 0, [], 0,
                                {"skipped": True, "reason": "erp.enabled is off"})
    try:
        pulled = erp.pull_items(con=con)
    except Exception as e:                                            # noqa: BLE001
        return _write_sync_log(con, direction, "Item", 0, 0, [], 0,
                                {"error": f"{e.__class__.__name__}: {e}"})

    from core.matcher import normalize
    pulled_by_code = {r["name"]: r for r in pulled if r.get("name")}
    prior_mirror = {r["code"]: r for r in D.rows(con, "SELECT * FROM erp_item")}
    prior_known = set(prior_mirror) | {r["code"] for r in D.rows(con, "SELECT code FROM item")}

    changed = 0
    for code, it in pulled_by_code.items():
        prev = prior_mirror.get(code)
        new_tuple = (it.get("item_name"), it.get("item_group"), it.get("stock_uom"),
                     int(bool(it.get("disabled"))))
        if prev and (prev.get("name"), prev.get("item_group"), prev.get("uom"),
                     prev.get("disabled")) != new_tuple:
            changed += 1
        con.execute("""INSERT OR REPLACE INTO erp_item
                       (code,name,name_norm,item_group,uom,disabled,owner,pulled_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (code, it.get("item_name"), normalize(it.get("item_name") or ""),
                     it.get("item_group"), it.get("stock_uom"),
                     int(bool(it.get("disabled"))), it.get("owner"), ts))

    conflicts = []

    # drift 1: codes ERPNext has that we have never seen before - created
    # directly in ERPNext, bypassing this tool entirely.
    for code, it in pulled_by_code.items():
        if code not in prior_known:
            conflicts.append({"type": "unknown_in_erp", "code": code,
                               "item_name": it.get("item_name"),
                               "detail": "exists in ERPNext, not in our item master"})
            con.execute("INSERT OR IGNORE INTO code_ledger(code,item_id,state,ts,note) "
                        "VALUES(?,NULL,'erp-only',?,?)", (code, ts, "first seen on sync"))

    # drift 2 & 3: codes we believe are live in ERPNext that no longer
    # appear (someone deleted/renamed it there), or whose fields disagree
    # (someone edited it there directly).
    ours_live = D.rows(con, "SELECT code, name, uom FROM item WHERE status='in_erp'")
    for r in ours_live:
        code = r["code"]
        if code not in pulled_by_code:
            conflicts.append({"type": "missing_in_erp", "code": code, "item_name": r["name"],
                               "detail": "we believe this is live in ERPNext; it did not "
                                         "appear on this pull"})
            continue
        erp_row = pulled_by_code[code]
        if r["name"] and erp_row.get("item_name") and r["name"] != erp_row.get("item_name"):
            conflicts.append({"type": "field_conflict", "code": code, "field": "item_name",
                               "ours": r["name"], "erp": erp_row.get("item_name")})
        if r["uom"] and erp_row.get("stock_uom") and r["uom"] != erp_row.get("stock_uom"):
            conflicts.append({"type": "field_conflict", "code": code, "field": "stock_uom",
                               "ours": r["uom"], "erp": erp_row.get("stock_uom")})

    pushed = []
    if direction == "push":
        # Push hierarchy first
        all_grps = D.rows(con, "SELECT name FROM grp")
        for g in all_grps:
            try:
                erp._ensure_item_group(g["name"], con)
            except Exception as e:                                            # noqa: BLE001
                conflicts.append({"type": "hierarchy_push_error", "group": g["name"], "error": str(e)})

        candidates = D.rows(con, "SELECT * FROM item WHERE status='confirmed' "
                                  "AND COALESCE(provisional,0)=0 AND erp_synced_at IS NULL")
        for it in candidates:
            if it["code"] in pulled_by_code:
                conflicts.append({"type": "push_skipped_exists_in_erp", "code": it["code"],
                                   "detail": "already exists in ERPNext under this code - "
                                             "not overwritten, needs a human look"})
                continue
            grp = D.one(con, "SELECT name FROM grp WHERE id=?", (it["grp_id"],))
            try:
                extra = {}
                has_specs = False
                for i in range(1, 5):
                    s_id = it[f"s{i}"]
                    if s_id:
                        val = D.one(con, "SELECT value FROM specval WHERE id=?", (s_id,))
                        if val:
                            extra[f"item_specification_{i}"] = val["value"]
                            has_specs = True
                if has_specs:
                    extra["has_item_specification"] = 1

                res = erp.create_item(it["code"], it["name"], grp["name"] if grp else None,
                                       it["uom"] or "Nos", it["hsn"], extra=extra, tax_template=it.get("tax"), con=con)
            except (ErpValidationError, ErpGuardrailError) as e:
                res = {"ok": False, "error": str(e)}
            pushed.append({"code": it["code"], "result": res})
            if res.get("ok") and not res.get("dry_run"):
                con.execute("UPDATE item SET status='in_erp', erp_synced_at=?, frozen=1 WHERE code=?",
                            (ts, it["code"]))

    con.commit()
    detail = {"pulled": len(pulled), "changed": changed, "conflicts": len(conflicts),
              "pushed": len(pushed), "push_results": pushed or None,
              "base_url": erp.base, "dry_run": erp.dry_run}
    return _write_sync_log(con, direction, "Item", len(pulled), changed, conflicts, 1, detail)


def next_run_in_seconds(con, now=None):
    """Pure scheduling helper for `sync.times` (default "09:00,17:00").
    Returns seconds until the next scheduled time, always >= 30 so a
    misconfigured value can never spin a tight loop."""
    times = D.get_setting(con, "sync.times", "09:00,17:00") or "09:00,17:00"
    now = now or datetime.datetime.now()
    candidates = []
    for tok in str(times).split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            hh, mm = (int(x) for x in tok.split(":"))
        except ValueError:
            continue
        run_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if run_at <= now:
            run_at += datetime.timedelta(days=1)
        candidates.append(run_at)
    if not candidates:
        return 12 * 3600
    soonest = min(candidates)
    return max(30, int((soonest - now).total_seconds()))


# ---------------------------------------------------------------- task 5
def _known_lease_scopes(con):
    """Every ("group"|"item", ident) pair with a currently-recorded lease -
    core/codes.py stores each as settings key "lease.<kind>.<ident>"."""
    out = []
    for r in D.rows(con, "SELECT k FROM settings WHERE k LIKE 'lease.%'"):
        parts = r["k"].split(".", 2)
        if len(parts) == 3 and parts[1] in ("group", "item"):
            try:
                out.append((parts[1], int(parts[2])))
            except ValueError:
                continue
    return out


def _vps_call(base_url, path, payload, timeout=20):
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore") or "{}")


def reconcile_failsafe(con, vps_url=None, lease_size=None):
    """Runs when the VPS (tier 1) becomes reachable again after this
    machine acted as the tier-2 failsafe authority. agents/CONTRACTS.md §2
    and agents/AGENT_G_ERPNEXT.md task 5:

      1. upload everything minted locally while offline;
      2. ask the VPS to finalise every provisional code (queue-claim rule,
         core/codes.py) and return an old->new mapping;
      3. write every pair to `code_mapping` and hand the whole mapping back
         to the caller so the operator can see exactly what changed;
      4. return unused leased numbers (they become vacancies);
      5. re-lease for the next outage.

    The VPS is "another instance of this exact codebase running in `server`
    mode" (CONTRACTS.md §2), so steps 1-2 are plain HTTP calls to that
    instance's own /api/v1/erp/reconcile/* endpoints (routes/erp.py, this
    same file's owner) — never an ERPNext call, and never a call this
    module makes itself without going through core.erp's guardrail for
    anything that actually touches ERPNext (it does not, here).

    **Never auto-resolves.** Any conflict the VPS reports (the same code
    issued on both sides) is appended to `conflicts` and returned, never
    picked a winner for.

    As of 6 Aug 2026 no VPS exists (CONTRACTS.md §2: "not yet provisioned").
    With `ledger.server_url` unset this still runs the parts that make
    sense with only a local database - it looks for provisional/leased
    items and reports there is nothing to reconcile against yet - so the
    function is exercised and testable today, and starts doing the full job
    the moment a URL is configured, with no code change."""
    C._ensure_provisional_column(con)  # idempotent - see core/codes.py; guarantees the
                                        # column this whole function reads actually exists
    ts = D.now()
    vps_url = vps_url or D.get_setting(con, "ledger.server_url", "") or ""
    lease_size = lease_size or D.get_setting(con, "ledger.lease_size", 10)

    provisional_items = D.rows(
        con, "SELECT * FROM item WHERE COALESCE(provisional,0)=1")

    result = {"ts": ts, "vps_url": vps_url or None, "uploaded": 0,
              "finalized": [], "conflicts": [], "vacancies_returned": [], "releases": []}

    if not vps_url:
        detail = {"skipped": True,
                   "reason": "no VPS configured (ledger.server_url is empty) - this local "
                              "server is the only authority right now, nothing to reconcile "
                              "against yet",
                   "provisional_pending": len(provisional_items)}
        row = _write_sync_log(con, "reconcile", "Item", len(provisional_items), 0, [], 1, detail)
        result.update(ok=True, detail=detail, sync_log_id=row["id"])
        return result

    # 1) upload everything minted while offline - every item this server
    # created (or recoded) since it last synced, provisional or not.
    to_upload = D.rows(
        con, "SELECT * FROM item WHERE erp_synced_at IS NULL OR COALESCE(provisional,0)=1")
    upload_payload = [{"code": it["code"], "name": it["name"], "grp_id": it["grp_id"],
                        "provisional": bool(it.get("provisional"))} for it in to_upload]
    try:
        up = _vps_call(vps_url, "/api/v1/erp/reconcile/upload", {"items": upload_payload})
    except Exception as e:                                            # noqa: BLE001
        detail = {"error": f"upload failed: {e.__class__.__name__}: {e}"}
        row = _write_sync_log(con, "reconcile", "Item", len(upload_payload), 0, [], 0, detail)
        result.update(ok=False, detail=detail, sync_log_id=row["id"])
        return result
    result["uploaded"] = len(upload_payload)
    result["conflicts"].extend(up.get("conflicts") or [])

    # 2) ask the VPS to finalise every provisional code
    prov_codes = [it["code"] for it in provisional_items]
    mapping, vps_conflicts = [], []
    if prov_codes:
        try:
            fin = _vps_call(vps_url, "/api/v1/erp/reconcile/finalize", {"codes": prov_codes})
            mapping = fin.get("mapping") or []
            vps_conflicts = fin.get("conflicts") or []
        except Exception as e:                                        # noqa: BLE001
            result["conflicts"].append(
                {"type": "finalize_failed", "error": f"{e.__class__.__name__}: {e}"})

    # 3) write every pair to code_mapping and apply it locally, so the
    # operator sees exactly what changed even before the next full pull -
    # they may already have written the provisional code on a document.
    for pair in mapping:
        old, new = pair.get("old"), pair.get("new")
        if not old or not new:
            continue
        con.execute("""INSERT INTO code_mapping(old_code,new_code,reason,user,ts,pushed_to_erp)
                       VALUES(?,?,?,?,?,0)""", (old, new, "failsafe-reconcile", "system", ts))
        con.execute("UPDATE item SET code=?, provisional=0, updated_at=? WHERE code=?",
                    (new, ts, old))
        con.execute("UPDATE code_ledger SET state='retired', note=? WHERE code=?",
                    (f"finalised to {new}", old))
        con.execute(
            """INSERT OR REPLACE INTO code_ledger(code,item_id,state,ts,note)
               VALUES(?,(SELECT id FROM item WHERE code=?),'issued',?,?)""",
            (new, new, ts, "finalised by reconcile"))
        D.log(con, "system", "reconcile-finalise", new, {"old": old, "new": new})
        result["finalized"].append({"old": old, "new": new})

    # a code the VPS says was issued on BOTH sides while split - recorded,
    # never resolved.
    result["conflicts"].extend(vps_conflicts)

    # 4) return unused leased numbers -> they become vacancies
    for scope in _known_lease_scopes(con):
        unused = C.return_lease(con, scope)
        if unused:
            result["vacancies_returned"].append({"scope": list(scope), "numbers": unused})

    # 5) re-lease for the next outage
    for scope in _known_lease_scopes(con):
        try:
            lease = C.grant_lease(con, scope, size=lease_size)
            result["releases"].append(lease)
        except ValueError as e:
            result["conflicts"].append(
                {"type": "lease_grant_failed", "scope": list(scope), "error": str(e)})

    con.commit()
    detail = {"uploaded": result["uploaded"], "finalized": len(result["finalized"]),
              "conflicts": len(result["conflicts"]),
              "vacancies_returned": sum(len(v["numbers"]) for v in result["vacancies_returned"])}
    row = _write_sync_log(con, "reconcile", "Item", len(to_upload), len(result["finalized"]),
                           result["conflicts"], 1, detail)
    result.update(ok=True, detail=detail, sync_log_id=row["id"])
    return result


# --------------------------------------------- VPS-side receiving handlers
# These serve the OTHER end of reconcile_failsafe() when THIS instance is
# the one running as tier 1 (ledger.mode="server") and a tier-2 local
# server calls in after an outage. Wired into routes/erp.py's ROUTES, not
# called directly by anything in this file.

def receive_upload(con, items):
    """A tier-2 server's offline-minted items arrive here. A code this VPS
    has never seen is simply recorded (code_ledger, state='pending-merge')
    so finalize() can find it; a code that collides with something already
    minted here is a genuine conflict - never silently overwritten."""
    ts = D.now()
    accepted, conflicts = [], []
    for it in items or []:
        code = it.get("code")
        if not code:
            continue
        existing = D.one(con, "SELECT * FROM item WHERE code=?", (code,))
        if existing and not it.get("provisional") and not existing.get("provisional"):
            conflicts.append({"type": "code_exists_both_sides", "code": code})
            continue
        con.execute("INSERT OR IGNORE INTO code_ledger(code,item_id,state,ts,note) "
                    "VALUES(?,NULL,'pending-merge',?,?)",
                    (code, ts, f"uploaded from tier-2 ({'provisional' if it.get('provisional') else 'leased'})"))
        accepted.append(code)
    con.commit()
    return {"ok": True, "accepted": accepted, "conflicts": conflicts}


def tier_reconnect_hook(con):
    """Returns an `on_change(old_status, new_status)` callback for
    core.tier.TierClient — that module's own docstring names this wiring
    point "Agent G's reconciliation hook" (core/tier.py line ~88). Fires
    reconcile_failsafe() exactly on the transition that matters -
    LOCAL_FAILSAFE -> CONNECTED, i.e. "the VPS just came back after this
    machine was standing in as tier 2" - and does nothing on any other
    transition (OFFLINE -> LOCAL_FAILSAFE has nothing to reconcile yet;
    CONNECTED -> OFFLINE is a disconnect, not a reconnect).

    Whoever constructs the TierClient (core/tier.py is Agent H's; the
    instantiation site — server.py or a client-mode entry point — is not a
    file I own) should pass `on_change=core.sync.tier_reconnect_hook(ctx.con)`."""
    from core import tier as T

    def _on_change(old_status, new_status):
        if old_status == T.STATUS_LOCAL_FAILSAFE and new_status == T.STATUS_CONNECTED:
            try:
                reconcile_failsafe(con)
            except Exception:                                        # noqa: BLE001
                import traceback
                traceback.print_exc()

    return _on_change


def receive_finalize(con, codes):
    """For each provisional code, structurally decode its head/sub/group
    prefix (both tiers replicate the same taxonomy, CONTRACTS.md §2, so the
    same head2+sub2+grp3 resolves to the same group here) and hand out the
    real position under the queue-claim rule (core/codes.py). A code whose
    prefix cannot be resolved locally is a conflict, not a guess."""
    ts = D.now()
    mapping, conflicts = [], []
    for old in codes or []:
        parsed = C.structural_parse(old)
        if not parsed:
            conflicts.append({"type": "unparseable_provisional_code", "code": old})
            continue
        row = D.one(con, """SELECT g.id AS grp_id, g.code3, s.code2 AS sub_code, h.code2 AS head_code
                            FROM grp g JOIN subhead s ON s.id=g.subhead_id
                                       JOIN head h ON h.id=s.head_id
                            WHERE h.code2=? AND s.code2=? AND g.code3=?""",
                    (parsed["head"], parsed["sub"], parsed["group"]))
        if not row:
            conflicts.append({"type": "unknown_group_for_provisional", "code": old})
            continue
        spec_tuple = [parsed["s1"], parsed["s2"], parsed["s3"], parsed["s4"]]
        try:
            position = C.next_item_position(con, row["grp_id"], spec_tuple)
        except ValueError as e:
            conflicts.append({"type": "position_space_exhausted", "code": old, "error": str(e)})
            continue
        new_code = C.assemble(row["head_code"], row["sub_code"], row["code3"],
                               [position[i:i + 2] for i in range(0, min(len(position), 8), 2)],
                               parsed["vendor"])
        if not C.code_is_free(con, new_code):
            conflicts.append({"type": "final_code_collision", "code": old, "would_be": new_code})
            continue
        C.release_item_vacancy(con, row["grp_id"], position)
        mapping.append({"old": old, "new": new_code})
    con.commit()
    return {"ok": True, "mapping": mapping, "conflicts": conflicts}
