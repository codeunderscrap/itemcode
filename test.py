import sqlite3
from core.erp import ERP
con = sqlite3.connect('d:/MINIMINES/ITEMCODE_GENERATOR/master.db')
con.row_factory = sqlite3.Row
erp = ERP().refresh(con)
erp.login()
groups = erp.pull_item_groups(con)
heads = con.execute('SELECT name FROM head').fetchall()
for h in heads:
    print(f"{h['name']}: parent is {groups.get(h['name'])}")
