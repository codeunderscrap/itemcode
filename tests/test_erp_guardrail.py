"""Agent G - Task 6: prove the ERPNext guardrail.

"A guardrail nobody has tried is not a guardrail." (agents/AGENT_G_ERPNEXT.md)

Two things are proven here, and they are NOT the same claim:

  1. test_local_gate_refuses_stock_entry_and_custom_field() - core/erp.py's
     own ALLOWED closed list refuses to even BUILD a request to Stock Entry
     or Custom Field (or issue a DELETE, on anything). This runs today, for
     real, no network and no credentials needed - it is exercising our own
     code, not ERPNext's.

  2. test_live_service_account_refused_by_erpnext() - the live check from
     ERPNEXT_API.md §5.4: the dedicated `itemcode.studio@` service account
     attempts a raw write to Stock Entry and to Custom Field and BOTH must
     come back 403 from ERPNext's own permission system - proof the ERPNext
     side of the guardrail (the role/permission grant) is real, not just
     our client refusing to try. This one is marked SKIPPED: the dedicated
     service account does not exist yet (agents/AGENT_G_ERPNEXT.md,
     "Blocked on Anuraag"). Once it does, delete the skip and point
     SERVICE_ACCOUNT_KEY / SERVICE_ACCOUNT_SECRET at it - the test body
     needs no other change.

Plain functions + assert, no pytest (agents/CONTRACTS.md §9 house rule 1 -
stdlib only). Run directly:

    python tests/test_erp_guardrail.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import erp as E                                            # noqa: E402

# Set these once the dedicated service account exists (never commit real
# values - read from the environment, exactly like every other secret in
# this project must be).
SERVICE_BASE_URL = os.environ.get("ITEMCODE_ERP_TEST_BASE_URL", "")
SERVICE_ACCOUNT_KEY = os.environ.get("ITEMCODE_ERP_TEST_API_KEY", "")
SERVICE_ACCOUNT_SECRET = os.environ.get("ITEMCODE_ERP_TEST_API_SECRET", "")


# --------------------------------------------------------------- part 1
def test_local_gate_refuses_stock_entry_and_custom_field():
    """Our own client refuses before a request is ever built - no network
    call happens for any of these, they raise synchronously."""
    refused = []

    for method, doctype in (("GET", "Stock Entry"), ("POST", "Stock Entry"),
                             ("GET", "Custom Field"), ("POST", "Custom Field"),
                             ("POST", "Purchase Order"), ("POST", "User"),
                             ("POST", "Sales Invoice")):
        try:
            E._gate(method, doctype)
            raise AssertionError(f"{method} {doctype} should have been refused, was not")
        except E.ErpGuardrailError:
            refused.append((method, doctype))

    assert len(refused) == 7, refused

    # There is no DELETE verb anywhere in this module - _raw() refuses the
    # HTTP method itself, independent of doctype, before _gate() is even
    # reached.
    erpc = E.ERP({"base_url": "https://example.invalid", "enabled": True, "dry_run": True})
    try:
        erpc._raw("DELETE", "/api/resource/Item/ANY-CODE")
        raise AssertionError("DELETE should have been refused, was not")
    except E.ErpGuardrailError:
        pass

    # structural check: the string "DELETE" never appears as a live code
    # value anywhere in core/erp.py (a tuple entry, a comparison, ...) -
    # only docstrings/comments are allowed to talk ABOUT it. Uses ast so a
    # docstring that explains this very guarantee (this file has one) can
    # never trip a false positive.
    import ast
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "core", "erp.py")
    with open(src_path, "r", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    docstring_ids = set()

    def _mark_docstrings(node):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant):
            docstring_ids.add(id(body[0].value))
        for child in ast.iter_child_nodes(node):
            _mark_docstrings(child)

    _mark_docstrings(tree)
    live_delete_literals = [n for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and n.value == "DELETE"
                            and id(n) not in docstring_ids]
    assert not live_delete_literals, \
        "core/erp.py must never reference the DELETE HTTP verb as live code, not even unreachable"

    print("test_local_gate_refuses_stock_entry_and_custom_field OK - "
          f"refused {len(refused)} doctype writes + DELETE + confirmed no DELETE literal in source")


# --------------------------------------------------------------- part 2
def test_live_service_account_refused_by_erpnext():
    """ERPNEXT_API.md §5.4's live proof. SKIPPED until the dedicated
    `itemcode.studio@` service account exists - see the module docstring."""
    if not (SERVICE_BASE_URL and SERVICE_ACCOUNT_KEY and SERVICE_ACCOUNT_SECRET):
        print("test_live_service_account_refused_by_erpnext SKIPPED - dedicated "
              "itemcode.studio@ service account not provisioned yet "
              "(agents/AGENT_G_ERPNEXT.md, blocked on Anuraag). Set "
              "ITEMCODE_ERP_TEST_BASE_URL / _API_KEY / _API_SECRET to run this for real.")
        return "SKIPPED"

    headers = {"Content-Type": "application/json",
               "Authorization": f"token {SERVICE_ACCOUNT_KEY}:{SERVICE_ACCOUNT_SECRET}"}

    def _attempt(doctype, payload):
        url = SERVICE_BASE_URL.rstrip("/") + f"/api/resource/{doctype}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        try:
            urllib.request.urlopen(req, timeout=15)
            return None  # unexpectedly succeeded
        except urllib.error.HTTPError as e:
            return e.code

    stock_status = _attempt("Stock Entry", {"doctype": "Stock Entry", "stock_entry_type": "Material Issue"})
    assert stock_status == 403, f"expected 403 writing Stock Entry, got {stock_status!r}"

    field_status = _attempt("Custom Field", {"doctype": "Custom Field", "dt": "Item", "fieldname": "x_test"})
    assert field_status == 403, f"expected 403 writing Custom Field, got {field_status!r}"

    print("test_live_service_account_refused_by_erpnext OK - both writes came back 403")


if __name__ == "__main__":
    test_local_gate_refuses_stock_entry_and_custom_field()
    result = test_live_service_account_refused_by_erpnext()
    print("\nALL TESTS PASSED" + (" (1 skipped)" if result == "SKIPPED" else ""))
