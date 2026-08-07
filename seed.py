"""Build data/itemcode.db from the four source workbooks.

    1. _Item master .xlsx  ->  sheet 'final Item_Code_Master'   dictionary
    2. _Item master .xlsx  ->  sheet 'Item_Master'              1,947 coded items
    3. Item.xlsx           ->  live ERPNext item list           2,677 codes (freeze list)
    4. Item code specification.xlsx                             extra spec values

Run:  python seed.py            (uses the paths in config.json)
      python seed.py --rebuild  (throws the old DB away first)
"""
import json
import os
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db as D                      # noqa: E402
from core import codes as C                   # noqa: E402
from core.matcher import normalize            # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
SRC = CFG["sources"]


def rows_of(path, sheet):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return list(ws.iter_rows(values_only=True))


def s(v):
    return "" if v is None else str(v).strip()


def code2(v):
    v = s(v).replace(".0", "")
    return v.zfill(2) if v.isdigit() else ""


# ---------------------------------------------------------------- 1. ERP list
def load_erp(con):
    if not os.path.exists(SRC["erp_item_list"]):
        print("  ! ERP item list not found, skipping")
        return 0
    rows = rows_of(SRC["erp_item_list"], None)
    n = 0
    for r in rows[1:]:
        code, name = s(r[1]), s(r[3])
        if not code:
            continue
        con.execute("""INSERT OR REPLACE INTO erp_item
                       (code,name,name_norm,item_group,uom,disabled,owner,pulled_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (code, name, normalize(name), s(r[5]), s(r[6]),
                     1 if s(r[10]) == "1" else 0, s(r[11]), D.now()))
        n += 1
    con.commit()
    return n


# ------------------------------------------------- 2. prefix map from codes
def prefix_map(rows):
    """prefix4 -> (head name, sub-head name), taken from codes already issued."""
    m = {}
    for r in rows[1:]:
        code, head, sub = s(r[0]).upper(), s(r[2]), s(r[3])
        if len(code) >= 4 and code[:4].isalpha() and head and sub:
            m.setdefault(code[:4], (head, sub))
    return m


# ---------------------------------------------------- 3. dictionary from ICM
def load_dictionary(con, icm_rows, pmap):
    by_names = {(h, sb): p for p, (h, sb) in pmap.items()}
    heads, subs, groups = {}, {}, {}
    cur_gid = None
    n_g = n_v = 0

    def get_head(name):
        if name in heads:
            return heads[name]
        r = con.execute("SELECT id,code2 FROM head WHERE name=?", (name,)).fetchone()
        if r:
            heads[name] = (r["id"], r["code2"])
            return heads[name]
        pref = next((p for (h, _sb), p in by_names.items() if h == name), None)
        c2 = pref[:2] if pref else C.mint_head_code(con, name)
        if con.execute("SELECT 1 FROM head WHERE code2=?", (c2,)).fetchone():
            c2 = C.mint_head_code(con, name)
        cur = con.execute("INSERT INTO head(name,code2) VALUES(?,?)", (name, c2))
        heads[name] = (cur.lastrowid, c2)
        return heads[name]

    def get_sub(head_name, name):
        key = (head_name, name)
        if key in subs:
            return subs[key]
        hid, _h2 = get_head(head_name)
        r = con.execute("SELECT id,code2 FROM subhead WHERE head_id=? AND name=?",
                        (hid, name)).fetchone()
        if r:
            subs[key] = (r["id"], r["code2"])
            return subs[key]
        pref = by_names.get(key)
        c2 = pref[2:4] if pref else C.mint_subhead_code(con, hid, name)
        if con.execute("SELECT 1 FROM subhead WHERE head_id=? AND code2=?", (hid, c2)).fetchone():
            c2 = C.mint_subhead_code(con, hid, name)
        cur = con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(?,?,?)", (hid, name, c2))
        subs[key] = (cur.lastrowid, c2)
        return subs[key]

    for r in icm_rows[1:]:
        r = list(r) + [None] * (18 - len(r))
        is_group = s(r[0]) not in ("",) and s(r[3]) != ""
        if is_group:
            head, sub, gname = s(r[1]), s(r[2]), s(r[3])
            c3 = s(r[4]).replace(".0", "").zfill(3)
            if not (head and sub and gname and c3.isdigit()):
                cur_gid = None
                continue
            sid, _s2 = get_sub(head, sub)
            labels = {}
            for slot, col in ((1, 5), (2, 7), (3, 9), (4, 11)):
                if s(r[col]):
                    labels[str(slot)] = s(r[col])
            if s(r[13]):
                labels["vendor"] = s(r[13])
            key = (sid, gname)
            if key in groups:
                cur_gid = groups[key]
                continue
            exist = con.execute("SELECT id FROM grp WHERE subhead_id=? AND name=?",
                                (sid, gname)).fetchone()
            if exist:
                cur_gid = groups[key] = exist["id"]
                continue
            if con.execute("SELECT 1 FROM grp WHERE subhead_id=? AND code3=?", (sid, c3)).fetchone():
                c3, _ = C.next_group_code(con, sid)
            cur = con.execute(
                "INSERT INTO grp(subhead_id,name,code3,uom,labels) VALUES(?,?,?,?,?)",
                (sid, gname, c3, s(r[17]) or None, json.dumps(labels)))
            cur_gid = groups[key] = cur.lastrowid
            n_g += 1
        elif cur_gid:
            for slot, vcol, ccol in ((1, 5, 6), (2, 7, 8), (3, 9, 10), (4, 11, 12), (5, 13, 14)):
                val, cc = s(r[vcol]), code2(r[ccol])
                if val and cc:
                    con.execute(
                        "INSERT OR IGNORE INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                        (cur_gid, slot, val, cc))
                    n_v += 1
    con.commit()
    return n_g, n_v


# --------------------------------------------- 4. extra spec-value dictionary
def load_spec_extras(con):
    p = SRC.get("spec_dictionary")
    if not p or not os.path.exists(p):
        return 0
    added = 0
    for r in rows_of(p, None)[1:]:
        gname, val, cc = s(r[0]), s(r[1]), code2(r[2])
        if not (gname and val and cc):
            continue
        slot = next((i for i in (1, 2, 3, 4) if s(r[2 + i])), 1)
        hits = con.execute("SELECT id FROM grp WHERE name=? AND status='active'", (gname,)).fetchall()
        if len(hits) != 1:
            continue                       # ambiguous group name - leave it alone
        gid = hits[0]["id"]
        if con.execute("SELECT 1 FROM specval WHERE grp_id=? AND slot=? AND value=?",
                       (gid, slot, val)).fetchone():
            continue
        if con.execute("SELECT 1 FROM specval WHERE grp_id=? AND slot=? AND code2=?",
                       (gid, slot, cc)).fetchone():
            cc = C.next_spec_code(con, gid, slot)
        con.execute("INSERT OR IGNORE INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                    (gid, slot, val, cc))
        added += 1
    con.commit()
    return added


# ------------------------------------------------------------- 5. item master
def load_items(con, rows):
    erp_codes = {r["code"] for r in con.execute("SELECT code FROM erp_item")}
    n = skipped = 0
    for r in rows[1:]:
        r = list(r) + [None] * (18 - len(r))
        code, name = s(r[0]).upper(), s(r[1])
        if not code or con.execute("SELECT 1 FROM item WHERE code=?", (code,)).fetchone():
            continue
        head, sub, gname = s(r[2]), s(r[3]), s(r[4])
        g = con.execute("""SELECT g.id,g.code3 FROM grp g JOIN subhead s ON s.id=g.subhead_id
                           JOIN head h ON h.id=s.head_id
                           WHERE g.name=? AND s.name=? AND h.name=?""",
                        (gname, sub, head)).fetchone()
        gid = g["id"] if g else None
        parsed = C.structural_parse(code)
        slot_ids = {}
        if gid and parsed:
            for slot, key in ((1, "s1"), (2, "s2"), (3, "s3"), (4, "s4"), (5, "vendor")):
                cc = parsed[key]
                if cc and cc != "00":
                    sv = con.execute(
                        "SELECT id FROM specval WHERE grp_id=? AND slot=? AND code2=?",
                        (gid, slot, cc)).fetchone()
                    if sv:
                        slot_ids[slot] = sv["id"]
        if not gid:
            skipped += 1
        frozen = 1 if code in erp_codes else 0
        con.execute("""INSERT INTO item(code,name,name_norm,description,grp_id,s1,s2,s3,s4,vend,
                        uom,alt_uom,hsn,tax,maintain_stock,allow_sales,has_batch,
                        origin,frozen,decodable,status,created_by,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'seed-master',?,?,?,?,?,?)""",
                    (code, name, normalize(name), s(r[5]), gid,
                     slot_ids.get(1), slot_ids.get(2), slot_ids.get(3), slot_ids.get(4),
                     slot_ids.get(5), s(r[6]), s(r[7]), s(r[8]), s(r[9]),
                     1 if s(r[10]) == "1" else 0, 1 if s(r[11]) == "1" else 0,
                     1 if s(r[12]) == "1" else 0, frozen,
                     1 if (parsed and gid) else 0,
                     "in_erp" if frozen else "confirmed",
                     "seed", D.now(), D.now()))
        con.execute("INSERT OR REPLACE INTO code_ledger(code,item_id,state,ts,note) VALUES(?,"
                    "(SELECT id FROM item WHERE code=?),'issued',?,?)",
                    (code, code, D.now(), "seeded from finalised item master"))
        n += 1

    # ERP codes that the master does not know about: reserve them so the app can
    # never re-issue a code that is already live in ERPNext
    res = 0
    for r in con.execute("SELECT code,name FROM erp_item"):
        if con.execute("SELECT 1 FROM code_ledger WHERE code=?", (r["code"],)).fetchone():
            continue
        con.execute("INSERT INTO code_ledger(code,item_id,state,ts,note) VALUES(?,NULL,?,?,?)",
                    (r["code"], "erp-only", D.now(), f"live in ERPNext: {r['name']}"))
        res += 1
    con.commit()
    return n, skipped, res


def main():
    if "--rebuild" in sys.argv and os.path.exists(D.DB_PATH):
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(D.DB_PATH + suf)
            except OSError:
                pass
        print("removed old database")
    os.makedirs(os.path.dirname(D.DB_PATH), exist_ok=True)
    con = D.connect()
    D.init(con)

    print("1/5 live ERPNext item list ...")
    n_erp = load_erp(con)
    print(f"      {n_erp} live codes registered")

    im_rows = rows_of(SRC["item_master"], "Item_Master")
    icm_sheet = SRC.get("dictionary_sheet", "final Item_Code_Master")
    icm_rows = rows_of(SRC["item_master"], icm_sheet)

    print("2/5 head / sub-head / group dictionary ...")
    n_g, n_v = load_dictionary(con, icm_rows, prefix_map(im_rows))
    print(f"      {n_g} groups, {n_v} spec values")

    print("3/5 extra spec dictionary ...")
    print(f"      {load_spec_extras(con)} additional spec values")

    print("4/5 coded items ...")
    n_i, skipped, reserved = load_items(con, im_rows)
    print(f"      {n_i} items ({skipped} without a matching group), {reserved} ERP-only codes reserved")

    print("5/5 summary")
    for q, label in ((("SELECT COUNT(*) c FROM head"), "heads"),
                     (("SELECT COUNT(*) c FROM subhead"), "sub-heads"),
                     (("SELECT COUNT(*) c FROM grp"), "groups"),
                     (("SELECT COUNT(*) c FROM specval"), "spec values"),
                     (("SELECT COUNT(*) c FROM item"), "items"),
                     (("SELECT COUNT(*) c FROM erp_item"), "live ERP items"),
                     (("SELECT COUNT(*) c FROM code_ledger"), "codes reserved")):
        print(f"      {con.execute(q).fetchone()['c']:>6}  {label}")
    D.set_setting(con, "seeded_at", D.now())
    con.close()
    print(f"\ndatabase ready -> {D.DB_PATH}")


if __name__ == "__main__":
    main()
