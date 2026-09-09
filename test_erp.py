import sqlite3
import json
from core.erp import ERP

con = sqlite3.connect('d:/MINIMINES/ITEMCODE_GENERATOR/master.db')
con.row_factory = sqlite3.Row
erp = ERP().refresh(con)
erp.login()

# Let's try to update AOHI0005 directly
payload = {
    'item_group': 'Acid',
    'item_specification_1': 'Acid-1000ml-00'
}
try:
    res = erp._put('Item/AOHI0005', payload)
    print('Result:', json.dumps(res, indent=2))
except Exception as e:
    print('Error:', e)
