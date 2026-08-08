import csv
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db as D
from core import codes as C

def s(v):
    return "" if v is None else str(v).strip()

def code2(v):
    v = s(v).replace(".0", "")
    return v.zfill(2) if v.isdigit() else ""

def load_csv_dictionary(con, icm_rows):
    heads, subs, groups = {}, {}, {}
    cur_gid = None
    n_g = n_v = 0
    schema = 1 # 1: Specs at col 5, 2: Name at col 5 and Specs at col 7
    
    current_head = ""
    current_sub = ""

    def get_head(name):
        name = name.strip()
        if not name: return None, None
        if name in heads: return heads[name]
        r = con.execute("SELECT id,code2 FROM head WHERE name=?", (name,)).fetchone()
        if r:
            heads[name] = (r["id"], r["code2"])
            return heads[name]
        c2 = C.mint_head_code(con, name)
        cur = con.execute("INSERT INTO head(name,code2) VALUES(?,?)", (name, c2))
        heads[name] = (cur.lastrowid, c2)
        return heads[name]

    def get_sub(head_name, name):
        name = name.strip()
        if not name: return None, None
        key = (head_name, name)
        if key in subs: return subs[key]
        hid, _ = get_head(head_name)
        if not hid: return None, None
        r = con.execute("SELECT id,code2 FROM subhead WHERE head_id=? AND name=?", (hid, name)).fetchone()
        if r:
            subs[key] = (r["id"], r["code2"])
            return subs[key]
        c2 = C.mint_subhead_code(con, hid, name)
        cur = con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(?,?,?)", (hid, name, c2))
        subs[key] = (cur.lastrowid, c2)
        return subs[key]

    for row in icm_rows:
        r = list(row) + [None] * (20 - len(row))
        
        # Detect headers
        if "Chemistry" in s(r[5]):
            schema = 1
            continue
        if "Name" in s(r[5]) or "Grade" in s(r[7]) or "Specification-1" in s(r[7]):
            schema = 2
            continue
            
        # Ignore empty or header-like rows
        if not "".join([s(x) for x in r]):
            continue
        if s(r[0]) == "SL" or "Group" in s(r[3]) or "Final Code" in s(r[17]):
            continue

        if s(r[1]):
            current_head = s(r[1])
        if s(r[2]):
            current_sub = s(r[2])

        if not current_head:
            current_head = "Raw Materials" # Fallback
        if not current_sub:
            current_sub = "General" # Fallback

        group_name = ""
        spec_slots = []
        uom = s(r[18])
        labels = {}

        if schema == 1:
            # Group is col 3
            if s(r[3]):
                group_name = s(r[3])
                labels = {"1": "Chemistry", "2": "Type", "3": "Spec 3", "4": "Spec 4", "vendor": "Vendor/Brand"}
                if s(r[15]):
                    labels["5"] = "Stage"
            
            # Specs are at 5, 7, 9, 11, 13 (vendor), 15
            spec_slots = [
                (1, 5, 6),
                (2, 7, 8),
                (3, 9, 10),
                (4, 11, 12),
                (5, 13, 14), # Vendor usually goes to spec slot 5 internally for standard parsing, but let's just map it to 5
            ]
        elif schema == 2:
            # Group is actually the "Name" in col 5
            if s(r[5]):
                group_name = s(r[5])
                labels = {"1": "Spec 1", "2": "Spec 2", "3": "Spec 3", "4": "Spec 4"}
            
            # Specs are at 7, 9, 11, 13, 15
            spec_slots = [
                (1, 7, 8),
                (2, 9, 10),
                (3, 11, 12),
                (4, 13, 14)
            ]

        if group_name:
            sid, _ = get_sub(current_head, current_sub)
            if sid:
                key = (sid, group_name)
                exist = con.execute("SELECT id FROM grp WHERE subhead_id=? AND name=?", (sid, group_name)).fetchone()
                if exist:
                    cur_gid = exist["id"]
                    groups[key] = cur_gid
                else:
                    c3, _ = C.next_group_code(con, sid)
                    cur = con.execute(
                        "INSERT INTO grp(subhead_id,name,code3,uom,labels) VALUES(?,?,?,?,?)",
                        (sid, group_name, c3, uom or None, json.dumps(labels)))
                    cur_gid = cur.lastrowid
                    groups[key] = cur_gid
                    n_g += 1

        if cur_gid:
            # Add spec values
            for slot, vcol, ccol in spec_slots:
                val, cc = s(r[vcol]), code2(r[ccol])
                if val and val.lower() != "na":
                    if not cc:
                        cc = C.next_spec_code(con, cur_gid, slot)
                    try:
                        con.execute(
                            "INSERT OR IGNORE INTO specval(grp_id,slot,value,code2) VALUES(?,?,?,?)",
                            (cur_gid, slot, val, cc))
                        n_v += 1
                    except ValueError:
                        pass # Spec slot full

    con.commit()
    return n_g, n_v

def main():
    csv_path = r"d:\MINIMINES\ITEMCODE_GENERATOR\Item_Code_Master.xlsx - Legend (original Final-Done).csv"
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
        
    con = D.connect()
    
    n_g, n_v = load_csv_dictionary(con, rows[1:])
    print(f"Successfully loaded {n_g} groups and {n_v} spec values from CSV.")

if __name__ == "__main__":
    main()
