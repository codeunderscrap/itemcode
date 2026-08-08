import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db as D
from core import codes as C
from core.context import ctx

def seed_dummy():
    con = D.connect()
    D.init(con)
    ctx.init(os.path.dirname(os.path.abspath(__file__)), {}, con, None, None)
    
    heads_to_add = {
        "Civil Works": ["Construction", "Maintenance", "Interiors"],
        "Capital Equipment": ["Machinery", "Electrical", "IT Hardware"],
        "Consumables": ["Office Supplies", "House Keeping", "Safety Gear"],
        "Raw Materials": ["Chemicals", "Metals", "Plastics"]
    }
    
    for head_name, subheads in heads_to_add.items():
        # Check if exists
        ex_h = con.execute("SELECT id FROM head WHERE name=?", (head_name,)).fetchone()
        if ex_h:
            h_id = ex_h["id"]
        else:
            h_code = C.mint_head_code(con, head_name)
            cur = con.execute("INSERT INTO head(name,code2) VALUES(?,?)", (head_name, h_code))
            h_id = cur.lastrowid
            
        for sh_name in subheads:
            ex_sh = con.execute("SELECT id FROM subhead WHERE head_id=? AND name=?", (h_id, sh_name)).fetchone()
            if not ex_sh:
                sh_code = C.mint_subhead_code(con, h_id, sh_name)
                con.execute("INSERT INTO subhead(head_id,name,code2) VALUES(?,?,?)", (h_id, sh_name, sh_code))
                
    con.commit()
    print("Dummy Head and Sub-head data inserted successfully.")

if __name__ == "__main__":
    seed_dummy()
