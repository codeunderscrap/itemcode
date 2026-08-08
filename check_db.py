import sqlite3
import os

db_path = r'd:\MINIMINES\ITEMCODE_GENERATOR\data\itemcode.db'
if not os.path.exists(db_path):
    print("Database file does not exist.")
else:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    
    print("Tables in the local SQLite database and their row counts:\n")
    for table in tables:
        tname = table[0]
        count = cur.execute(f"SELECT count(*) FROM {tname}").fetchone()[0]
        if count > 0:
            print(f" - {tname}: {count} rows")
    
    print("\nEmpty tables (0 rows):")
    empty_tables = []
    for table in tables:
        tname = table[0]
        count = cur.execute(f"SELECT count(*) FROM {tname}").fetchone()[0]
        if count == 0:
            empty_tables.append(tname)
    print(", ".join(empty_tables))
