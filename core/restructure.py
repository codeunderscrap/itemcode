"""Structural edits to the master, with a preview before anything moves.

The governing rule is FREEZE-ON-FIRST-USE:

  * an item code that is live in ERPNext (or has been marked frozen) is
    permanent - a structural change never rewrites it, it only records that the
    code no longer decodes to where the item now sits
  * an item code that has never left this app is regenerated freely

Numbers freed by a move or a delete - group numbers AND, new here, item
positions inside a group - are QUEUE-claimed, lowest-first (CONTRACTS.md
§4). A vacancy is a free slot, not a reservation: the next arrival, of any
name, takes the lowest free number. Nothing here decides who gets a number
by matching; `former_name`/`former_item` are display-only.
"""
import json

from . import codes as C
from .db import now, log


def _group(con, gid):
    r = con.execute("""SELECT g.*, s.id AS sub_id, s.name AS sub_name, s.code2 AS sub_code,
                              h.id AS head_id, h.name AS head_name, h.code2 AS head_code
                       FROM grp g JOIN subhead s ON s.id=g.subhead_id
                       JOIN head h ON h.id=s.head_id WHERE g.id=?""", (gid,)).fetchone()
    return dict(r) if r else None


def _item_code_for(con, item, head2, sub2, grp3):
    slots = []
    for col in ("s1", "s2", "s3", "s4"):
        sid = item[col]
        slots.append(con.execute("SELECT code2 FROM specval WHERE id=?", (sid,)).fetchone()["code2"]
                     if sid else None)
    vend = None
    if item["vend"]:
        vend = con.execute("SELECT code2 FROM specval WHERE id=?", (item["vend"],)).fetchone()["code2"]
    return C.assemble(head2, sub2, grp3, slots, vend)


def preview_move(con, matcher, gid, new_subhead_id):
    """What a move would do - no writes.

    `matcher` is accepted for call-site compatibility (routes/master.py
    passes ctx.matcher) but is no longer used to pick the new group number -
    that is now queue-claim, lowest-first, with no semantic test at all
    (CONTRACTS.md §4)."""
    g = _group(con, gid)
    if not g:
        return {"error": "unknown group"}
    ns = con.execute("""SELECT s.*, h.code2 AS head_code, h.name AS head_name
                        FROM subhead s JOIN head h ON h.id=s.head_id WHERE s.id=?""",
                     (new_subhead_id,)).fetchone()
    if not ns:
        return {"error": "unknown target sub-head"}
    new_code3, reused = C.next_group_code(con, new_subhead_id)
    items = [dict(r) for r in con.execute("SELECT * FROM item WHERE grp_id=?", (gid,))]

    changes, frozen = [], []
    for it in items:
        new_code = _item_code_for(con, it, ns["head_code"], ns["code2"], new_code3)
        row = {"item": it["name"], "old_code": it["code"], "new_code": new_code,
               "frozen": bool(it["frozen"]) or it["status"] == "in_erp"}
        (frozen if row["frozen"] else changes).append(row)

    return {
        "group": g["name"],
        "from": f"{g['head_name']} / {g['sub_name']}",
        "to": f"{ns['head_name']} / {ns['name']}",
        "old_prefix": f"{g['head_code']}{g['sub_code']}{g['code3']}",
        "new_prefix": f"{ns['head_code']}{ns['code2']}{new_code3}",
        "new_group_code": new_code3,
        "reused_vacancy_of": reused,
        "vacancy_created": {"subhead": g["sub_name"], "code3": g["code3"]},
        "will_recode": changes,
        "will_keep_code": frozen,
        "counts": {"recode": len(changes), "frozen": len(frozen), "total": len(items)},
    }


def move_group(con, matcher, gid, new_subhead_id, user, push_erp=False, erp=None):
    pv = preview_move(con, matcher, gid, new_subhead_id)
    if pv.get("error"):
        return pv
    g = _group(con, gid)
    old_sub, old_code3 = g["sub_id"], g["code3"]

    con.execute("UPDATE grp SET subhead_id=?, code3=? WHERE id=?",
                (new_subhead_id, pv["new_group_code"], gid))
    con.execute("""INSERT OR IGNORE INTO grp_vacancy(subhead_id,code3,former_name,ts)
                   VALUES(?,?,?,?)""", (old_sub, old_code3, g["name"], now()))
    if pv["reused_vacancy_of"]:
        C.release_group_vacancy(con, new_subhead_id, pv["new_group_code"])

    erp_results = []
    for row in pv["will_recode"]:
        con.execute("UPDATE item SET code=?, updated_at=? WHERE code=?",
                    (row["new_code"], now(), row["old_code"]))
        con.execute("UPDATE code_ledger SET state='retired', note=? WHERE code=?",
                    (f"moved to {row['new_code']}", row["old_code"]))
        con.execute("""INSERT OR REPLACE INTO code_ledger(code,item_id,state,ts,note)
                       VALUES(?,(SELECT id FROM item WHERE code=?),'issued',?,?)""",
                    (row["new_code"], row["new_code"], now(), f"group move by {user}"))
        con.execute("""INSERT INTO code_mapping(old_code,new_code,reason,user,ts)
                       VALUES(?,?,?,?,?)""",
                    (row["old_code"], row["new_code"], "group moved", user, now()))
        if push_erp and erp:
            erp_results.append(erp.rename_item(row["old_code"], row["new_code"]))
    for row in pv["will_keep_code"]:
        con.execute("UPDATE item SET decodable=0, updated_at=? WHERE code=?", (now(), row["old_code"]))

    log(con, user, "move-group", g["name"],
        {"from": pv["from"], "to": pv["to"], "recoded": pv["counts"]["recode"],
         "kept": pv["counts"]["frozen"]})
    con.commit()
    pv["erp"] = erp_results
    pv["applied"] = True
    return pv


def delete_group(con, gid, user, reason=""):
    """Retire a group. Refuses while items still hang off it - move or merge
    them first. Frees its number into grp_vacancy; under the queue-claim
    rule that number is picked up by whatever group is created next under
    this sub-head, named or not, not held for a semantic match."""
    g = _group(con, gid)
    if not g:
        return {"error": "unknown group"}
    n = con.execute("SELECT COUNT(*) c FROM item WHERE grp_id=?", (gid,)).fetchone()["c"]
    if n:
        return {"error": f"{n} item(s) still sit in this group - move or merge them first"}
    con.execute("UPDATE grp SET status='retired' WHERE id=?", (gid,))
    con.execute("INSERT OR IGNORE INTO grp_vacancy(subhead_id,code3,former_name,ts) VALUES(?,?,?,?)",
                (g["subhead_id"], g["code3"], g["name"], now()))
    log(con, user, "delete-group", g["name"], {"code3": g["code3"], "reason": reason})
    con.commit()
    return {"ok": True, "vacancy": {"subhead": g["sub_name"], "code3": g["code3"]}}


def retire_subhead(con, subhead_id, user):
    """Retire a whole sub-head. Refuses while an active group remains under
    it. Drops its WHOLE branch of vacancies at once - every unclaimed
    group-level vacancy in this sub-head, and every unclaimed item-level
    vacancy inside any of its groups - since there is no sub-head left for a
    future arrival to claim a number under."""
    sub = con.execute("SELECT * FROM subhead WHERE id=?", (subhead_id,)).fetchone()
    if not sub:
        return {"error": "unknown sub-head"}
    active = con.execute("SELECT COUNT(*) c FROM grp WHERE subhead_id=? AND status='active'",
                         (subhead_id,)).fetchone()["c"]
    if active:
        return {"error": f"{active} active group(s) remain under this sub-head - "
                          "retire or move them first"}
    grp_ids = [r["id"] for r in con.execute("SELECT id FROM grp WHERE subhead_id=?", (subhead_id,))]

    con.execute("UPDATE subhead SET active=0 WHERE id=?", (subhead_id,))
    group_vac = con.execute("SELECT COUNT(*) c FROM grp_vacancy WHERE subhead_id=? AND released=0",
                            (subhead_id,)).fetchone()["c"]
    con.execute("DELETE FROM grp_vacancy WHERE subhead_id=?", (subhead_id,))
    item_vac = 0
    for gid in grp_ids:
        item_vac += con.execute("SELECT COUNT(*) c FROM item_vacancy WHERE grp_id=? AND released=0",
                                (gid,)).fetchone()["c"]
        con.execute("DELETE FROM item_vacancy WHERE grp_id=?", (gid,))

    log(con, user, "retire-subhead", sub["name"],
        {"groups": len(grp_ids), "group_vacancies_dropped": group_vac,
         "item_vacancies_dropped": item_vac})
    con.commit()
    return {"ok": True, "sub_head": sub["name"], "groups_retired": len(grp_ids),
            "group_vacancies_dropped": group_vac, "item_vacancies_dropped": item_vac}


def _recode_item_into_group(con, it, dst_gid, dst, reason, user):
    """Move a single item's row into `dst` (dict from _group()). Shared by
    merge_groups and move_item, and the one place item-level freeze-on-
    first-use and item-level vacancy-freeing both live.

    * a code live in ERPNext (or flagged frozen) is never rewritten - it
      keeps its code, is reclassified into the new group, flagged stale
      (decodable=0), and FREES NOTHING: it did not leave, only its
      classification did (CONTRACTS.md §1.6, PDR §4.5.4).
    * a code that never reached ERPNext is recoded freely, and its OLD
      position under the OLD group is freed into item_vacancy - the next
      item to land in that old group takes it, queue-claim, lowest first.
    """
    frozen = bool(it["frozen"]) or it["status"] == "in_erp"
    if frozen:
        con.execute("UPDATE item SET grp_id=?, decodable=0, updated_at=? WHERE id=?",
                    (dst_gid, now(), it["id"]))
        return {"item": it["name"], "code": it["code"], "recoded": False,
                "why": "frozen in ERP - kept its code, flagged stale"}

    new_slots = {}
    for slot in (1, 2, 3, 4, 5):
        col = "vend" if slot == 5 else f"s{slot}"
        if not it[col]:
            continue
        val = con.execute("SELECT value FROM specval WHERE id=?", (it[col],)).fetchone()["value"]
        row = con.execute("SELECT id,code2 FROM specval WHERE grp_id=? AND slot=? AND value=?",
                          (dst_gid, slot, val)).fetchone()
        if row:
            new_slots[slot] = (row["id"], row["code2"])
        else:
            code2 = C.next_spec_code(con, dst_gid, slot)
            cur = con.execute("INSERT INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                              (dst_gid, slot, val, code2))
            new_slots[slot] = (cur.lastrowid, code2)
    codes = [new_slots.get(s, (None, None))[1] for s in (1, 2, 3, 4)]
    new_code = C.assemble(dst["head_code"], dst["sub_code"], dst["code3"], codes,
                          new_slots.get(5, (None, None))[1])
    if not C.code_is_free(con, new_code):
        con.execute("UPDATE item SET grp_id=?, decodable=0, updated_at=? WHERE id=?",
                    (dst_gid, now(), it["id"]))
        return {"item": it["name"], "code": it["code"], "recoded": False,
                "why": f"{new_code} already taken"}

    old_position, old_values = C._item_position_and_values(con, it)
    old_grp_id = it["grp_id"]
    con.execute("""UPDATE item SET grp_id=?, code=?, s1=?, s2=?, s3=?, s4=?, vend=?, updated_at=?
                   WHERE id=?""",
                (dst_gid, new_code,
                 new_slots.get(1, (None,))[0], new_slots.get(2, (None,))[0],
                 new_slots.get(3, (None,))[0], new_slots.get(4, (None,))[0],
                 new_slots.get(5, (None,))[0], now(), it["id"]))
    C.free_item_position(con, old_grp_id, old_position, old_values, it["name"])
    new_position = "".join(("00" if c in (None,) else c) for c in codes)
    C.release_item_vacancy(con, dst_gid, new_position)
    con.execute("UPDATE code_ledger SET state='retired', note=? WHERE code=?",
                (f"{reason} -> {new_code}", it["code"]))
    con.execute("""INSERT OR REPLACE INTO code_ledger(code,item_id,state,ts,note)
                   VALUES(?,?,?,?,?)""", (new_code, it["id"], "issued", now(), reason))
    con.execute("INSERT INTO code_mapping(old_code,new_code,reason,user,ts) VALUES(?,?,?,?,?)",
                (it["code"], new_code, reason, user, now()))
    return {"item": it["name"], "old_code": it["code"], "new_code": new_code, "recoded": True}


def merge_groups(con, matcher, src_gid, dst_gid, user):
    """Fold a duplicate group into the one that should have existed."""
    src, dst = _group(con, src_gid), _group(con, dst_gid)
    if not src or not dst:
        return {"error": "unknown group"}
    items = [dict(r) for r in con.execute("SELECT * FROM item WHERE grp_id=?", (src_gid,))]
    dst_labels = json.loads(dst["labels"] or "{}")
    reason = f"group merge {src['name']}->{dst['name']}"
    moved, kept = [], []

    for it in items:
        r = _recode_item_into_group(con, it, dst_gid, dst, reason, user)
        if r["recoded"]:
            moved.append({"item": r["item"], "old_code": r["old_code"], "new_code": r["new_code"]})
        else:
            kept.append({"item": r["item"], "code": r["code"], "why": r["why"]})

    con.execute("UPDATE grp SET status='retired' WHERE id=?", (src_gid,))
    con.execute("INSERT OR IGNORE INTO grp_vacancy(subhead_id,code3,former_name,ts) VALUES(?,?,?,?)",
                (src["subhead_id"], src["code3"], src["name"], now()))
    log(con, user, "merge-group", f"{src['name']} -> {dst['name']}",
        {"moved": len(moved), "kept": len(kept)})
    con.commit()
    return {"ok": True, "merged_into": dst["name"], "recoded": moved,
            "kept_code": kept, "labels_of_target": dst_labels}


def move_item(con, item_code, new_grp_id, user):
    """Move a single item to a different group (PDR §4.5.3's 'Move a single
    ITEM instead' case) - the item-level twin of move_group. Frozen items
    keep their code and free nothing; everything else recodes and frees its
    old position into item_vacancy for the next item that lands there."""
    it = con.execute("SELECT * FROM item WHERE code=?", (item_code,)).fetchone()
    if not it:
        return {"error": "unknown item"}
    it = dict(it)
    dst = _group(con, new_grp_id)
    if not dst:
        return {"error": "unknown target group"}
    r = _recode_item_into_group(con, it, new_grp_id, dst, f"item moved to {dst['name']}", user)
    log(con, user, "move-item", it["name"],
        {"from_group": it["grp_id"], "to_group": new_grp_id, "recoded": r["recoded"]})
    con.commit()
    return r


def rename(con, scope, ref_id, new_name, user):
    """Labels only - renaming never touches a code."""
    table = {"head": "head", "subhead": "subhead", "group": "grp", "specval": "specval"}[scope]
    col = "value" if scope == "specval" else "name"
    old = con.execute(f"SELECT {col} AS n FROM {table} WHERE id=?", (ref_id,)).fetchone()
    if not old:
        return {"error": "not found"}
    con.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (new_name, ref_id))
    # the old wording becomes a search alias so past invoices still match
    from .matcher import normalize
    con.execute("""INSERT OR IGNORE INTO alias(scope,ref_id,term,term_norm,user,ts)
                   VALUES(?,?,?,?,?,?)""",
                (scope, ref_id, old["n"], normalize(old["n"]), user, now()))
    log(con, user, "rename", f"{scope}:{ref_id}", {"from": old["n"], "to": new_name})
    con.commit()
    return {"ok": True, "from": old["n"], "to": new_name, "codes_changed": 0}
