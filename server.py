"""Item Code Studio - process entry point (Python standard library only).

Thin by design: this file owns startup order only - load config, open the
one shared database connection, wire the shared context, build the route
table, start listening. Every actual endpoint lives in routes/*.py, one
module per agent, so eight people can work on this codebase at once without
touching the same file. HTTP protocol mechanics live in core/dispatch.py.
See agents/CONTRACTS.md §6 and §9.

Runs on one desktop (or the tier-2 local server); everyone else on the same
Wi-Fi points a browser at http://<that-desktop-ip>:8756. One SQLite file
behind it means two people can never be handed the same code.

    python server.py
"""
import json
import os
import socket
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db as D                                     # noqa: E402
from core.context import ctx                                 # noqa: E402
from core.dispatch import Router, make_handler               # noqa: E402
from core.erp import ERP                                     # noqa: E402
from core.matcher import Matcher                              # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))

CON = D.connect()
D.init(CON)
MATCHER = Matcher(CFG.get("llm"), CFG.get("match_threshold", 60))
ERPC = ERP(CFG.get("erpnext", {})).refresh(CON)
ctx.init(ROOT, CFG, CON, MATCHER, ERPC)

# Imported after ctx.init() so route modules - which read core.context.ctx at
# call time, not at import time - always see a ready context. This is also
# why no route module may import server.py: it would be circular.
from routes import public, auth, create, master, erp as erp_routes, meta  # noqa: E402

ROUTER = Router((public, auth, create, master, erp_routes, meta))
Handler = make_handler(WEB, ROUTER)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:                                          # noqa: BLE001
        return "127.0.0.1"


def main():
    host = CFG.get("host", "0.0.0.0")
    port = int(CFG.get("port", 8756))
    srv = ThreadingHTTPServer((host, port), Handler)
    ip = lan_ip()
    print("=" * 66)
    print(f"  {CFG.get('app_name')}  -  running")
    print("=" * 66)
    print(f"  this computer   http://localhost:{port}")
    print(f"  same Wi-Fi      http://{ip}:{port}")
    n = CON.execute("SELECT COUNT(*) c FROM item").fetchone()["c"]
    g = CON.execute("SELECT COUNT(*) c FROM grp").fetchone()["c"]
    e = CON.execute("SELECT COUNT(*) c FROM erp_item").fetchone()["c"]
    print(f"  loaded          {n} coded items | {g} groups | {e} live ERP codes")
    print(f"  matching        fuzzy >= {CFG.get('match_threshold', 60)}%, "
          f"LLM below (provider: {(CFG.get('llm') or {}).get('provider', 'none')})")
    print(f"  ERPNext         {'ON' if ERPC.enabled else 'OFF'}"
          f"{' (dry-run)' if ERPC.enabled and ERPC.dry_run else ''}")
    print(f"  ledger tier     {(CFG.get('ledger') or {}).get('mode', 'local_server')}")
    print("  Ctrl+C to stop")
    print("=" * 66)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
