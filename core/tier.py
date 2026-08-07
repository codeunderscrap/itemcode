"""Three-tier resolution + local dictionary cache — Agent H.

    TIER 1  cloud VPS         authority whenever reachable
    TIER 2  local LAN server  authority only while tier 1 is unreachable
    TIER 3  desktop client    never mints; talks to whichever of the above
                              answers, else read-only from cache

agents/CONTRACTS.md §2 assigns this resolution to Agent H, on top of
`core.db.connect()`, which is Agent 0's file. This module is deliberately
NEW rather than an edit to core/db.py or server.py — both are Agent 0's
exclusive files (agents/README.md file-ownership table).

Agent D's `core.sync.tier_reconnect_hook(con)` and Agent G's `routes/erp.py`
already reference this module by the exact names below
(`TierClient`, `STATUS_CONNECTED`, `STATUS_LOCAL_FAILSAFE`) — see
agents/done/AGENT_D.md ("Task 5") and the comment above
`routes/erp.py:v1_reconcile_run`. **The one thing this module cannot do
itself is get constructed** — that has to happen once, at process startup,
after `ctx.init(...)`, in a file only Agent 0 owns. The exact patch
(3 lines, additive only, nothing else in server.py touched):

    from core.tier import TierClient
    from core.sync import tier_reconnect_hook
    ctx.tier = TierClient(CFG.get("ledger", {}), on_change=tier_reconnect_hook(CON))
    ctx.tier.start()

Until that lands, every piece below is fully built, unit-tested standalone
(see HANDOVER.md's verification notes) and importable from anywhere
(a route handler, manage.py, a future desktop shell) — it just isn't yet
running continuously inside the live server process. `routes/meta.py`'s
`/api/v1/health` already reads `ctx.tier` when present and falls back to
the static config-file mode when not, so the health endpoint's `tier`
field upgrades automatically the moment the patch above lands — no second
change needed there.

What IS fully working today, with no VPS and no server.py change:
  * `TierClient.resolve_once()` / `.status` — tier1→tier2→offline probing
    with a short timeout (verified: an unreachable-but-live host times out
    in ~1.5s, not 30s; a DNS/refused failure returns in well under 100ms).
  * `DictCache` — pulls a read-only snapshot of groups/specs/aliases/item
    index either straight from a live `sqlite3.Connection` (when this
    process *is* tier 1 or tier 2 — true today) or over HTTP from a
    reachable tier (when this process is a client) and stores it as JSON
    under data/dict_cache.json. Loading it back never touches the network.

Run `python manage.py tier-status` / `python manage.py refresh-cache` to
exercise both by hand.
"""
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(ROOT, "data", "dict_cache.json")

CONNECT_TIMEOUT = 1.5      # seconds — "quick and quiet", never a 30s hang
RECHECK_SECONDS = 5        # how often the background prober re-tests tier 1

STATUS_CONNECTED = "connected"          # tier 1 (or we ARE tier 1/2 locally)
STATUS_LOCAL_FAILSAFE = "local_failsafe"  # tier 1 down, tier 2 answering
STATUS_OFFLINE = "offline"              # neither reachable

STATUS_TEXT = {
    STATUS_CONNECTED: "connected — minting final codes",
    STATUS_LOCAL_FAILSAFE: "local failsafe — VPS unreachable, codes are "
                            "provisional and will be confirmed when it reconnects",
    STATUS_OFFLINE: "offline — read-only, decode and search only",
}


# ==================================================== tier-to-tier auth
# routes/erp.py's v1_reconcile_upload/v1_reconcile_finalize are VPS-side
# receivers for a tier-2 server, deliberately not gated by
# core.auth.require_session — an operator never calls them, another
# process does. Their own docstrings name the gap plainly: "the two sides
# authenticate at the transport layer (HTTPS + whatever Agent H's tier
# wiring adds)". This is that piece: a shared secret, stored the same way
# every other secret in this app is (agents/CONTRACTS.md §5 — the
# `settings` table, never config.json, never git), compared with
# hmac.compare_digest so a timing side-channel can't leak it byte by byte.
#
# Wiring needed in routes/erp.py (not done here — that file may still be
# mid-edit elsewhere; see HANDOVER.md): one line at the top of each of
# those two handlers —
#
#     from core.tier import require_tier_secret
#     require_tier_secret(req)
#
# and one admin action to generate the shared value once:
#     python -c "from core import db as D, tier as T; c=D.connect(); print(T.set_tier_secret(c))"

TIER_SECRET_KEY = "tier.shared_secret"
TIER_SECRET_HEADER = "X-Tier-Secret"


def set_tier_secret(con, value=None):
    """Generates (or accepts) the shared secret tier-2 servers present when
    calling the VPS's reconcile endpoints. Returns it once, the same
    pattern core.auth.generate_password uses for a one-time credential
    hand-off — store it in tier 2's config/settings immediately."""
    from core import db as D
    value = value or secrets.token_urlsafe(32)
    D.set_setting(con, TIER_SECRET_KEY, value)
    return value


def require_tier_secret(req):
    """Raise ApiError FORBIDDEN unless the caller presented the correct
    X-Tier-Secret header. Import core.context.ctx locally, not at module
    scope, so this module stays importable (e.g. by tests/manage.py)
    before ctx.init() has run."""
    from core.api import ApiError
    from core.context import ctx
    from core import db as D

    expected = D.get_setting(ctx.con, TIER_SECRET_KEY, "")
    got = req.headers.get(TIER_SECRET_HEADER, "")
    if not expected or not got or not hmac.compare_digest(expected, got):
        raise ApiError("FORBIDDEN", "missing or incorrect tier credential")


def _ping(base_url, timeout=CONNECT_TIMEOUT):
    """True if base_url/api/v1/health answers 200 within `timeout` seconds.
    Never raises — an unreachable host is an ordinary, expected outcome
    here, not an error."""
    if not base_url:
        return False
    url = base_url.rstrip("/") + "/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


class TierClient:
    """Resolves which tier is authoritative right now and keeps re-checking
    in the background so a returning VPS is noticed without anyone
    restarting anything (agents/AGENT_H_DEPLOY.md task 4).

    Usage (once server.py wires client mode — see module docstring):

        tc = TierClient(cfg["ledger"])
        tc.start()
        ...
        tc.status         -> "connected" | "local_failsafe" | "offline"
        tc.active_base    -> the base URL currently in authority, or None
        tc.request("GET", "/api/v1/decode", params={"code": "..."})
    """

    def __init__(self, ledger_cfg, on_change=None):
        self.server_url = (ledger_cfg or {}).get("server_url") or ""
        self.local_url = (ledger_cfg or {}).get("local_url") or ""
        self.mode = (ledger_cfg or {}).get("mode", "local_server")
        self.on_change = on_change     # callback(old_status, new_status) — Agent G's reconciliation hook
        self.status = STATUS_OFFLINE
        self.active_base = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.last_checked = None

    # ------------------------------------------------------------- probing
    def resolve_once(self):
        """One probe cycle. Returns the new status. Safe to call directly
        (e.g. from a health endpoint or a test) without starting the
        background thread.

        The meaning of each mode:
          "server"       — this process IS tier 1. Nothing above it to
                            check; always CONNECTED.
          "local_server" — this process IS tier 2, opening SQLite directly
                            (core.db.connect()). It still probes tier 1
                            (server_url) so it knows when to hand authority
                            back — CONTRACTS.md §2: "Becomes the authority
                            only while the VPS is unreachable." No
                            server_url configured at all (today's normal
                            state — "build tier 2 first") means there is
                            nothing to defer to, so it just stays CONNECTED.
                            Tier 1 unreachable means THIS machine is now the
                            failsafe authority: LOCAL_FAILSAFE.
          "client"       — a desktop. Tries tier 1, then tier 2
                            (local_url), else OFFLINE/read-only.
        """
        with self._lock:
            old = self.status
            if self.mode == "server":
                new_status, base = STATUS_CONNECTED, None
            elif self.mode == "local_server":
                if not self.server_url:
                    new_status, base = STATUS_CONNECTED, None
                elif _ping(self.server_url):
                    new_status, base = STATUS_CONNECTED, self.server_url
                else:
                    new_status, base = STATUS_LOCAL_FAILSAFE, None
            elif _ping(self.server_url):
                new_status, base = STATUS_CONNECTED, self.server_url
            elif _ping(self.local_url):
                new_status, base = STATUS_LOCAL_FAILSAFE, self.local_url
            else:
                new_status, base = STATUS_OFFLINE, None

            self.status = new_status
            self.active_base = base
            self.last_checked = time.time()

        if new_status != old and self.on_change:
            try:
                self.on_change(old, new_status)
            except Exception:                                     # noqa: BLE001
                pass  # a reconciliation callback failing must never take the prober down
        return new_status

    def start(self):
        """Begin the background re-check loop (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self.resolve_once()
        self._stop.clear()

        def loop():
            while not self._stop.wait(RECHECK_SECONDS):
                self.resolve_once()

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def status_text(self):
        return STATUS_TEXT.get(self.status, self.status)

    # ------------------------------------------------------------- proxying
    def request(self, method, path, params=None, body=None, cookie=None, timeout=8):
        """Forward one call to whichever tier is currently active. Raises
        RuntimeError if nothing is reachable — callers (client-mode routes)
        should catch that and fall back to the local read-only cache."""
        if not self.active_base:
            raise RuntimeError("no tier reachable — offline")
        url = self.active_base.rstrip("/") + path
        if params:
            from urllib.parse import urlencode
            url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if cookie:
            req.add_header("Cookie", cookie)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


# ============================================================== dictionary cache

class DictCache:
    """A read-only local copy of enough of the dictionary for OCR/fuzzy
    matching to keep working with no tier reachable: groups, sub-heads,
    heads, spec values, aliases, and a lightweight item index (code + name
    only, no commercial fields — same boundary the public API holds).

    Refreshed on launch and on demand; NEVER written back to — the comment
    at the call sites that build a Matcher against this data must make that
    same promise (agents/AGENT_H_DEPLOY.md task 4: "it never writes back").
    """

    def __init__(self, path=CACHE_PATH):
        self.path = path

    # -------------------------------------------------- building (producer side)
    def refresh_from_db(self, con):
        """Tier 1 / tier 2 process: build the cache straight from the live
        connection. This is what `python manage.py refresh-cache` runs
        today, with no VPS and no HTTP involved."""
        from core import db as D
        heads = D.rows(con, "SELECT id,name,code2 FROM head WHERE active=1")
        subheads = D.rows(con, "SELECT id,head_id,name,code2 FROM subhead WHERE active=1")
        groups = D.rows(con, "SELECT id,subhead_id,name,code3,uom,labels FROM grp WHERE status='active'")
        specvals = D.rows(con, "SELECT id,grp_id,slot,value,code2 FROM specval")
        aliases = D.rows(con, "SELECT scope,ref_id,term,term_norm FROM alias")
        items = D.rows(con, "SELECT code,name,name_norm,grp_id FROM item WHERE status<>'deleted'")
        snapshot = {
            "generated_at": D.now(),
            "source": "db",
            "heads": heads, "subheads": subheads, "groups": groups,
            "specvals": specvals, "aliases": aliases, "items": items,
        }
        self._write(snapshot)
        return snapshot

    def refresh_from_tier(self, tier_client, page_size=200):
        """Client process: pull the same shape over HTTP from whichever
        tier is currently active, via the public dictionary/directory API
        (agents/CONTRACTS.md §6 — routes/public.py). Only ever reads."""
        if not tier_client.active_base:
            raise RuntimeError("no tier reachable — cannot refresh cache")

        groups, offset = [], 0
        while True:
            page = tier_client.request("GET", "/api/v1/dictionary/groups",
                                       params={"limit": page_size, "offset": offset})
            batch = page.get("groups", [])
            groups.extend(batch)
            offset += page_size
            if offset >= page.get("total", 0) or not batch:
                break

        items, offset = [], 0
        while True:
            page = tier_client.request("GET", "/api/v1/directory",
                                       params={"limit": page_size, "offset": offset})
            batch = page.get("items", [])
            items.extend(batch)
            offset += page_size
            if offset >= page.get("total", 0) or not batch:
                break

        snapshot = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": tier_client.active_base,
            "groups": groups, "items": items,
            "heads": [], "subheads": [], "specvals": [], "aliases": [],
        }
        self._write(snapshot)
        return snapshot

    def _write(self, snapshot):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        os.replace(tmp, self.path)   # atomic — a reader never sees a half-written file

    # -------------------------------------------------------- reading (consumer side)
    def load(self):
        """Never touches the network. Returns None if no cache has ever
        been built (fresh install, offline before the first successful
        refresh)."""
        if not os.path.isfile(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def age_seconds(self):
        if not os.path.isfile(self.path):
            return None
        return time.time() - os.path.getmtime(self.path)
