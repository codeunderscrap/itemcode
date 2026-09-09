import sys

with open('d:/MINIMINES/ITEMCODE_GENERATOR/core/erp.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        except Exception as e:                                        # noqa: BLE001
            if getattr(e, 'code', None) == 409:
                return {"ok": False, "error": "This item code already exists in ERPNext. Please click 'Sync from ERPNext' to update your local status.", "payload": payload}
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "payload": payload}'''

new = '''        except Exception as e:                                        # noqa: BLE001
            if getattr(e, 'code', None) == 409:
                return {"ok": False, "error": "This item code already exists in ERPNext. Please click 'Sync from ERPNext' to update your local status.", "payload": payload}
            err_msg = f"{e.__class__.__name__}: {e}"
            if hasattr(e, 'read'):
                try:
                    err_msg += " - " + e.read().decode('utf-8')
                except:
                    pass
            return {"ok": False, "error": err_msg, "payload": payload}'''

if old in content:
    content = content.replace(old, new)
    with open('d:/MINIMINES/ITEMCODE_GENERATOR/core/erp.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Old block not found')
