"""ERPNext client — Item access only. agents/CONTRACTS.md §8 is the frozen
contract; agents/AGENT_G_ERPNEXT.md is the task packet. Nobody but this
module calls ERPNext — not for a quick check, not for a test.

Every rule below is enforced IN CODE, not by convention:

  * ALLOWED is a closed list of (HTTP method, doctype) pairs. Anything not
    on it raises ErpGuardrailError before a request is ever built — see
    _gate(). There is no way to reach the network with an unlisted pair.
  * There is no DELETE verb anywhere in this module. Not a helper, not a
    constant, not dead code behind a flag. grep this file for "DELETE":
    zero hits, and that absence is the point (task 1 / task 6).
  * A field whitelist (WRITABLE_FIELDS) applies to every update. Anything
    not on it is dropped before the payload is built, so a bug in the
    master editor can never reach ERPNext's costing or stock fields.
  * A provisional item is refused HERE, by this module itself, not merely
    hidden from a button in the UI — see _refuse_provisional() and its
    call at the top of create_item()/update_item(). This is the property
    the whole offline failsafe (CONTRACTS.md §2) rests on: an offline code
    must stay re-writable until it is finalised, which means it must be
    structurally incapable of reaching ERPNext beforehand.
  * dry_run is the default. When set, every write method logs and returns
    the exact payload it would have sent, and performs only the read
    calls needed to validate that payload first — no POST/PUT is issued.
  * Validation (UoM, GST HSN Code, Item Group) always runs before a write,
    dry_run or not, so a dry-run preview reflects a payload that would
    actually have been accepted.

Authentication: an API key/secret pair read live from the `settings` table
(`erp.api_key` / `erp.api_secret`) is always preferred. `config.json`'s
`erpnext.username`/`erpnext.password` is a fallback used only for
interactive UAT testing (session-cookie login) — never commit a real
credential to either place; config.json ships with `"password": ""`.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar

from . import db as D

# --------------------------------------------------------------- closed list
# The only (HTTP method, doctype) pairs this module will ever address.
# agents/AGENT_G_ERPNEXT.md task 1 / agents/CONTRACTS.md §8.
ALLOWED = {
    ("GET", "Item"), ("POST", "Item"), ("PUT", "Item"),
    ("GET", "Item Code Specification"), ("POST", "Item Code Specification"),
    ("GET", "Item Code Vendor"), ("POST", "Item Code Vendor"),
    ("GET", "Item Group"), ("POST", "Item Group"), ("GET", "UOM"), ("GET", "GST HSN Code"),
    ("GET", "Item Tax Template"),
}

# The non-doctype "method" endpoints permitted. Nothing else under
# /api/method/... is ever called, and in particular never anything that
# deletes, cancels or amends a document (decision #12 / CONTRACTS.md §8).
# frappe.client.rename_doc is here for one reason only: core/restructure.py
# (Agent D) calls ERP.rename_item() when a group move recodes a non-frozen
# item, per the restraints ERPNEXT_API.md §5.3.1 already documented (called
# only for a code that is not frozen; `merge` is always 0 so a rename can
# never silently destroy a record). rename_item() carries the same
# provisional/dry_run guardrails as every other write below.
ALLOWED_METHOD_PATHS = {
    "/api/method/login",
    "/api/method/frappe.auth.get_logged_user",
    "/api/method/frappe.client.rename_doc",
}

# Update: only these fields are ever written to an existing Item. Anything
# else in a caller's payload is dropped, not passed through.
WRITABLE_FIELDS = {
    "item_name", "description", "gst_hsn_code", "stock_uom", "disabled",
    "item_specification_1", "item_specification_2",
    "item_specification_3", "item_specification_4", "item_vendor",
}

# Create additionally needs the mandatory/standard fields documented in
# ERPNEXT_API.md §3.2. Still no field outside this union is ever sent.
CREATE_ONLY_FIELDS = {
    "item_code", "item_group", "is_stock_item", "is_purchase_item",
    "is_sales_item", "has_batch_no", "has_item_specification",
}
CREATE_FIELDS = WRITABLE_FIELDS | CREATE_ONLY_FIELDS


class ErpGuardrailError(Exception):
    """This module refused to even build a request — an operation is not on
    the closed ALLOWED list, is a provisional code, or is a disallowed HTTP
    verb. Never raised because of anything ERPNext itself said."""


class ErpValidationError(Exception):
    """A write was refused locally because a referenced value (UoM, GST
    HSN Code, Item Group) does not exist in ERPNext (task 2). Always raised
    before any write request — a bad value is caught here, not bounced back
    by Frappe after the fact."""


def _gate(method, doctype):
    if (method, doctype) not in ALLOWED:
        raise ErpGuardrailError(
            f"refused: {method} {doctype!r} is not on the ALLOWED list "
            f"(agents/CONTRACTS.md §8) — Item access only")


def _gate_method_path(path):
    if path not in ALLOWED_METHOD_PATHS:
        raise ErpGuardrailError(f"refused: method endpoint {path!r} is not permitted")


class ERP:
    def __init__(self, cfg=None):
        cfg = cfg or {}
        # config.json fallback, used only until Settings (Agent B's screen)
        # holds a real API key, and even then only base_url/username/
        # password for UAT testing — see module docstring. Never a live
        # secret in a real deployment.
        self.base = (cfg.get("base_url") or "").rstrip("/")
        self.user = cfg.get("username") or ""
        self.pwd = cfg.get("password") or ""
        self.dry_run = bool(cfg.get("dry_run", True))
        self.enabled = bool(cfg.get("enabled", False)) and bool(self.base)
        self.populate_specs = False
        self.api_key = ""
        self.api_secret = ""

        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self._logged_in = False

        # small in-process caches for the validation lookups (task 2), each
        # (value-set, fetched-at) so a write does not re-pull 240 UOM/23
        # Item Group rows on every single call.
        self._uom_cache = (None, 0.0)
        self._group_cache = (None, 0.0)
        self._cache_ttl = 300

    # ------------------------------------------------------------ settings
    def refresh(self, con=None):
        """Re-read live config from the `settings` table (CONTRACTS.md §5 /
        decision #11) — the API key first, config.json only as a UAT
        fallback. Call this at the top of every public method that talks to
        ERPNext: Settings can change between calls (Agent B's screen), and a
        stale enabled/dry_run flag here is exactly the kind of silent drift
        this whole packet exists to avoid. Never logs the secret values."""
        con = con or _ctx_con()
        if con is None:
            return self
        self.base = D.get_setting(con, "erp.base_url", None) or self.base
        self.user = D.get_setting(con, "erp.username", "") or self.user
        self.pwd = D.get_setting(con, "erp.password", "") or self.pwd
        self.api_key = D.get_setting(con, "erp.api_key", "") or ""
        self.api_secret = D.get_setting(con, "erp.api_secret", "") or ""
        self.dry_run = bool(D.get_setting(con, "erp.dry_run", self.dry_run))
        self.enabled = bool(D.get_setting(con, "erp.enabled", self.enabled)) and bool(self.base)
        self.populate_specs = bool(D.get_setting(con, "erp.populate_specs", False))
        return self

    @property
    def _token_auth(self):
        return bool(self.api_key and self.api_secret)

    # ------------------------------------------------------------- plumbing
    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token_auth:
            h["Authorization"] = f"token {self.api_key}:{self.api_secret}"
        return h

    def _raw(self, method, path, payload=None, timeout=30, retries=2):
        """The one function that ever opens a socket to ERPNext. Every
        caller reaches this only through _resource() (doctype, gated) or
        _method() (the two whitelisted auth endpoints) — never directly."""
        if method not in ("GET", "POST", "PUT"):
            raise ErpGuardrailError(f"refused: HTTP method {method!r} is not permitted")
        url = self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        last_err = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, data=data, method=method,
                                              headers=self._headers())
                with self.opener.open(req, timeout=timeout) as r:
                    body = r.read().decode("utf-8", "ignore")
                return json.loads(body) if body.strip().startswith(("{", "[")) else {"raw": body}
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise
                if e.code in (429, 502, 503) and attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    last_err = e
                    continue
                raise
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
                    last_err = e
                    continue
                raise
        raise last_err  # pragma: no cover - loop always returns or raises

    def _method(self, path, payload=None):
        _gate_method_path(path)
        return self._raw("POST", path, payload)

    def _resource(self, method, doctype, name=None, payload=None, query=""):
        """Every doctype call funnels through here so _gate() cannot be
        bypassed. `name` is the record name (e.g. an item_code) for a
        GET/PUT on a single document."""
        _gate(method, doctype)
        seg = urllib.parse.quote(doctype)
        path = f"/api/resource/{seg}"
        if name is not None:
            path += "/" + urllib.parse.quote(str(name))
        if query:
            path += ("&" if "?" in path else "?") + query
        return self._raw(method, path, payload)

    def login(self):
        if self._token_auth:
            return True  # stateless per-request auth, no session needed
        if self._logged_in:
            return True
        if not self.enabled:
            raise RuntimeError("ERPNext connection is disabled")
        if not (self.user and self.pwd):
            raise RuntimeError("no API key and no username/password configured")
        self._method("/api/method/login", {"usr": self.user, "pwd": self.pwd})
        self._logged_in = True
        return True

    def ping(self, con=None):
        self.refresh(con)
        try:
            if not self.enabled:
                return {"ok": False, "error": "ERPNext disabled", "base": self.base}
            self.login()
            d = self._method("/api/method/frappe.auth.get_logged_user")
            return {"ok": True, "user": d.get("message"), "base": self.base,
                    "dry_run": self.dry_run, "auth": "api_key" if self._token_auth else "password"}
        except Exception as e:                                        # noqa: BLE001
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "base": self.base}

    # ----------------------------------------------------------------- read
    def pull_items(self, limit=20000, con=None):
        self.refresh(con)
        self.login()
        out, start, page = [], 0, 500
        fields = urllib.parse.quote(json.dumps(
            ["name", "item_name", "item_group", "stock_uom", "disabled", "owner"]))
        while start < limit:
            d = self._resource("GET", "Item",
                                query=f"fields={fields}&limit_start={start}&limit_page_length={page}")
            rows = d.get("data") or []
            out.extend(rows)
            if len(rows) < page:
                break
            start += page
        return out

    def item_exists(self, code, con=None):
        self.refresh(con)
        self.login()
        try:
            d = self._resource("GET", "Item", name=code)
            return bool(d.get("data"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def get_tax_templates(self, con=None):
        self.refresh(con)
        try:
            self.login()
            res = self._resource("GET", "Item Tax Template", query="fields=[\"name\"]&limit_page_length=200")
            return [r["name"] for r in res.get("data", [])]
        except urllib.error.HTTPError:
            return []

    def _uom_set(self, con=None, force=False):
        vals, ts = self._uom_cache
        if vals is not None and not force and (time.time() - ts) < self._cache_ttl:
            return vals
        self.login()
        d = self._resource("GET", "UOM", query="fields=[\"name\"]&limit_page_length=0")
        vals = {r["name"] for r in (d.get("data") or [])}
        self._uom_cache = (vals, time.time())
        return vals

    def _group_set(self, con=None, force=False):
        vals, ts = self._group_cache
        if vals is not None and not force and (time.time() - ts) < self._cache_ttl:
            return vals
        self.login()
        d = self._resource("GET", "Item Group", query="fields=[\"name\"]&limit_page_length=0")
        vals = {r["name"] for r in (d.get("data") or [])}
        self._group_cache = (vals, time.time())
        return vals

    def validate_uom(self, uom, con=None):
        if not uom:
            return False
        return uom in self._uom_set(con)

    def validate_item_group(self, item_group, con=None):
        """We never create Item Groups and never push our taxonomy
        (CONTRACTS.md decision #14 / §8). The group goes by name; if
        ERPNext has no such group this returns False and the caller must
        refuse the write and tell the operator — never invent one."""
        if not item_group:
            return False
        return item_group in self._group_set(con)

    def validate_hsn(self, hsn, con=None):
        """18,689 records is too many to usefully cache wholesale, so this
        is a direct existence lookup by name rather than a cached set."""
        if not hsn:
            return True  # gst_hsn_code is optional, not mandatory
        self.login()
        try:
            d = self._resource("GET", "GST HSN Code", name=hsn)
            return bool(d.get("data"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise

    def _ensure_item_group(self, item_group, con):
        if self.validate_item_group(item_group, con):
            return True
            
        row = D.one(con, "SELECT g.name AS g_name, s.name AS s_name, h.name AS h_name "
                         "FROM grp g JOIN subhead s ON g.subhead_id=s.id "
                         "JOIN head h ON s.head_id=h.id WHERE g.name=?", (item_group,))
        
        h_name = row["h_name"] if row else None
        s_name = row["s_name"] if row else None
        
        def _create_if_missing(name, parent, is_group):
            if not self.validate_item_group(name, con):
                if not self.dry_run:
                    self._resource("POST", "Item Group", payload={
                        "item_group_name": name,
                        "parent_item_group": parent,
                        "is_group": is_group
                    })
                self._group_cache = (None, 0.0)

        self.login()
        if h_name:
            _create_if_missing(h_name, "All Item Groups", 1)
        if s_name:
            _create_if_missing(s_name, h_name or "All Item Groups", 1)
        _create_if_missing(item_group, s_name or h_name or "All Item Groups", 0)
        
        return True

    # ------------------------------------------------------------ guardrail
    @staticmethod
    def _is_provisional(code, extra, con):
        """A provisional code must never reach ERPNext (CONTRACTS.md §2 —
        "never allow an ERPNext write from tier 2", and this is the
        structural enforcement of it, not just an interface hint). Checked
        two ways: an explicit `provisional` flag in `extra` (so a caller
        that already knows can say so directly), and a lookup of the
        item's own row — Agent D's core/codes.py adds `item.provisional`
        defensively the first time it is needed (_ensure_provisional_column),
        so on a database where no item has ever been marked provisional the
        column may not exist yet; the try/except below degrades to False in
        that case rather than raising, so nothing is ever wrongly refused."""
        if extra and extra.get("provisional"):
            return True
        if con is None:
            con = _ctx_con()
        if con is None:
            return False
        try:
            row = D.one(con, "SELECT * FROM item WHERE code=?", (code,))
        except Exception:                                            # noqa: BLE001
            return False
        return bool(row and row.get("provisional"))

    def _refuse_provisional(self, code, extra, con):
        if self._is_provisional(code, extra, con):
            raise ErpGuardrailError(
                f"refused: {code} is a provisional code (offline-minted, not yet "
                f"finalised) — provisional codes are never pushed to ERPNext "
                f"(CONTRACTS.md §2). Reconcile first.")

    @staticmethod
    def _whitelist(payload, allowed):
        return {k: v for k, v in (payload or {}).items() if k in allowed}

    # ---------------------------------------------------------------- write
    def create_item(self, code, item_name, item_group, uom="Nos", hsn=None, extra=None, tax_template=None, con=None):
        """The only way an Item is ever created. One at a time, on Submit —
        never in bulk (CONTRACTS.md decision #13). Validates item_group,
        stock_uom and gst_hsn_code before building the payload; refuses a
        provisional code outright; drops any field not in CREATE_FIELDS.

        Like the version this replaces, this NEVER raises for an expected
        refusal - it always returns a dict with "ok": False and a "reason"
        (core/resolve.py and core/restructure.py, Agent C/D's files, call
        this directly and only ever check `.get("ok")`; they predate the
        guardrail/validation exceptions below and must not have to catch
        them). A raised ErpGuardrailError/ErpValidationError/RuntimeError
        from the checks below is caught here and turned into that same
        shape; only a genuine bug gets to escape as a real exception."""
        self.refresh(con)
        con = con or _ctx_con()
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "ERPNext disabled"}

        try:
            self._refuse_provisional(code, extra, con)
            self._ensure_item_group(item_group, con)
            if not self.validate_item_group(item_group, con) and not self.dry_run:
                raise ErpValidationError(
                    f"refused: Item Group {item_group!r} could not be automatically created in ERPNext.")
            if not self.validate_uom(uom, con):
                raise ErpValidationError(f"refused: stock_uom {uom!r} is not a known UOM in ERPNext")
            if hsn and not self.validate_hsn(hsn, con):
                raise ErpValidationError(f"refused: gst_hsn_code {hsn!r} is not a known GST HSN Code in ERPNext")
        except ErpGuardrailError as e:
            return {"ok": False, "error": str(e), "reason": "guardrail"}
        except ErpValidationError as e:
            return {"ok": False, "error": str(e), "reason": "validation"}
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "reason": "not_configured"}

        payload = {
            "doctype": "Item", "item_code": code,
            "item_name": (item_name or code)[:140],
            "item_group": item_group, "stock_uom": uom,
            "is_stock_item": 1, "is_purchase_item": 1, "is_sales_item": 0, "has_batch_no": 0,
        }
        if hsn:
            payload["gst_hsn_code"] = hsn
        if tax_template:
            payload["taxes"] = [{"item_tax_template": tax_template}]
        payload.update(self._whitelist(extra, CREATE_FIELDS))

        if self.dry_run:
            print(f"[erp dry-run] would POST Item: {json.dumps(payload)}")
            return {"ok": True, "dry_run": True, "would_post": payload, "at": D.now()}
        try:
            self.login()
            d = self._resource("POST", "Item", payload=payload)
            return {"ok": True, "dry_run": False, "name": (d.get("data") or {}).get("name"), "at": D.now()}
        except Exception as e:                                        # noqa: BLE001
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "payload": payload}

    def update_item(self, code, fields, con=None):
        """Update the small fixed set of fields ERPNEXT_API.md §5.3.4
        allows. Never touches costing or stock — anything else in `fields`
        is dropped before it is ever serialised. Never raises for an
        expected refusal - see create_item()'s docstring for why."""
        self.refresh(con)
        con = con or _ctx_con()
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "ERPNext disabled"}

        try:
            self._refuse_provisional(code, fields, con)
            payload = self._whitelist(fields, WRITABLE_FIELDS)
            if not payload:
                return {"ok": False, "error": "no writable fields in request"}
            if "stock_uom" in payload and not self.validate_uom(payload["stock_uom"], con):
                raise ErpValidationError(f"refused: stock_uom {payload['stock_uom']!r} is not a known UOM in ERPNext")
            if "gst_hsn_code" in payload and payload["gst_hsn_code"] and \
                    not self.validate_hsn(payload["gst_hsn_code"], con):
                raise ErpValidationError(f"refused: gst_hsn_code {payload['gst_hsn_code']!r} is not known in ERPNext")
        except ErpGuardrailError as e:
            return {"ok": False, "error": str(e), "reason": "guardrail"}
        except ErpValidationError as e:
            return {"ok": False, "error": str(e), "reason": "validation"}
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "reason": "not_configured"}

        if self.dry_run:
            print(f"[erp dry-run] would PUT Item/{code}: {json.dumps(payload)}")
            return {"ok": True, "dry_run": True, "would_put": payload, "at": D.now()}
        try:
            self.login()
            d = self._resource("PUT", "Item", name=code, payload=payload)
            return {"ok": True, "dry_run": False, "name": (d.get("data") or {}).get("name"), "at": D.now()}
        except Exception as e:                                        # noqa: BLE001
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "payload": payload}

    def rename_item(self, old, new, con=None):
        """Rename an Item that has never been transacted (ERPNEXT_API.md
        §3.2/§5.3.1) — called only by core/restructure.py's group-move/merge
        path for a code that was never frozen. `merge` is always 0: a
        merging rename silently destroys a record and this tool must never
        do that. Same provisional/dry_run guardrails as every other write —
        a provisional old_name or new_name is refused outright. Never raises
        for an expected refusal - core/restructure.py (Agent D) calls this
        directly and only checks `.get("ok")`, same reasoning as
        create_item()'s docstring."""
        self.refresh(con)
        con = con or _ctx_con()
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "ERPNext disabled"}
        try:
            self._refuse_provisional(old, None, con)
            self._refuse_provisional(new, None, con)
        except ErpGuardrailError as e:
            return {"ok": False, "error": str(e), "reason": "guardrail"}
        if self.dry_run:
            print(f"[erp dry-run] would rename Item {old} -> {new}")
            return {"ok": True, "dry_run": True, "would_rename": [old, new]}
        try:
            self.login()
            d = self._method("/api/method/frappe.client.rename_doc",
                              {"doctype": "Item", "old_name": old, "new_name": new, "merge": 0})
            return {"ok": True, "dry_run": False, "result": d.get("message")}
        except Exception as e:                                        # noqa: BLE001
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}

    # ------------------------------------------------- spec/vendor on demand
    def ensure_specification(self, item_group, slot, specification, specification_code, con=None):
        """Task 3. Deliberate, logged, idempotent: check for an existing
        `Item Code Specification` matching this VALUE (not code — their data
        has duplicate codes, e.g. Ather/Ather Energy both '11', so matching
        on code would silently link the wrong record), create it if absent,
        return its name either way. Gated behind `erp.populate_specs`
        (default off) so Anuraag can switch the behaviour on without a code
        change. Name format: {item_group}-{specification}-{specification_code}
        (agents/AGENT_G_ERPNEXT.md task 3).

        NOTE the exact checkbox field name marking which of the four slots a
        record belongs to was not confirmed against live doctype metadata
        during the 6 Aug 2026 UAT probe (ERPNEXT_API.md only describes it as
        "four checkboxes"); `slot_field` below is a best guess
        (`item_specification_{n}`) and must be checked against the real
        Item Code Specification meta before this is switched on for real."""
        self.refresh(con)
        con = con or _ctx_con()
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "ERPNext disabled"}
        if not self.populate_specs:
            return {"ok": False, "skipped": True, "reason": "erp.populate_specs is off"}
        if not self.validate_item_group(item_group, con):
            raise ErpValidationError(f"refused: Item Group {item_group!r} does not exist in ERPNext")

        self.login()
        existing = self._resource(
            "GET", "Item Code Specification",
            query="filters=" + urllib.parse.quote(json.dumps(
                [["item_group", "=", item_group], ["specification", "=", specification]])))
        rows = existing.get("data") or []
        if rows:
            name = rows[0]["name"]
            D.log(con, "system", "erp-ensure-spec-hit", name,
                  {"item_group": item_group, "specification": specification, "matched": "value"})
            con.commit()
            return {"ok": True, "created": False, "name": name}

        name = f"{item_group}-{specification}-{specification_code}"
        slot_field = f"item_specification_{slot}"  # best guess - see docstring
        payload = {"doctype": "Item Code Specification", "item_group": item_group,
                   "specification": specification, "specification_code": specification_code,
                   slot_field: 1}
        if self.dry_run:
            print(f"[erp dry-run] would POST Item Code Specification: {json.dumps(payload)}")
            D.log(con, "system", "erp-ensure-spec-would-create", name, payload)
            con.commit()
            return {"ok": True, "dry_run": True, "created": True, "would_post": payload, "name": name}
        d = self._resource("POST", "Item Code Specification", payload=payload)
        created_name = (d.get("data") or {}).get("name", name)
        D.log(con, "system", "erp-ensure-spec-create", created_name, payload)
        con.commit()
        return {"ok": True, "created": True, "name": created_name}

    def ensure_vendor(self, vendor_name, vendor_code=None, con=None):
        """Same idempotent shape as ensure_specification(), also matching on
        the vendor NAME rather than the code for the same reason — Item
        Code Vendor has duplicate codes in live UAT data."""
        self.refresh(con)
        con = con or _ctx_con()
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "ERPNext disabled"}
        if not self.populate_specs:
            return {"ok": False, "skipped": True, "reason": "erp.populate_specs is off"}

        self.login()
        existing = self._resource(
            "GET", "Item Code Vendor",
            query="filters=" + urllib.parse.quote(json.dumps([["vendor_name", "=", vendor_name]])))
        rows = existing.get("data") or []
        if rows:
            name = rows[0]["name"]
            D.log(con, "system", "erp-ensure-vendor-hit", name,
                  {"vendor_name": vendor_name, "matched": "value"})
            con.commit()
            return {"ok": True, "created": False, "name": name}

        payload = {"doctype": "Item Code Vendor", "vendor_name": vendor_name}
        if vendor_code:
            payload["vendor_code"] = vendor_code
        if self.dry_run:
            print(f"[erp dry-run] would POST Item Code Vendor: {json.dumps(payload)}")
            D.log(con, "system", "erp-ensure-vendor-would-create", vendor_name, payload)
            con.commit()
            return {"ok": True, "dry_run": True, "created": True, "would_post": payload, "name": vendor_name}
        d = self._resource("POST", "Item Code Vendor", payload=payload)
        created_name = (d.get("data") or {}).get("name", vendor_name)
        D.log(con, "system", "erp-ensure-vendor-create", created_name, payload)
        con.commit()
        return {"ok": True, "created": True, "name": created_name}


def _ctx_con():
    """Lazy import to dodge a circular import (core.context has no
    dependency on core.erp, but importing it at module scope here would
    still run before ctx is populated in some import orders). Every public
    ERP method accepts an explicit con for callers who have one (routes) and
    falls back to this for older call sites (core/resolve.py,
    core/restructure.py) that call ctx.erp.create_item(...) without one."""
    try:
        from core.context import ctx
        return ctx.con
    except Exception:                                                # noqa: BLE001
        return None
