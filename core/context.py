"""The runtime singletons every route module reads from.

server.py populates this once at startup (`ctx.init(...)`), before it imports
the route modules. Route handlers then do:

    from core.context import ctx
    ctx.con      # the one shared SQLite connection - single writer
    ctx.lock     # hold while touching ctx.con
    ctx.cfg      # parsed config.json
    ctx.matcher  # core.matcher.Matcher instance
    ctx.erp      # core.erp.ERP instance
    ctx.mmos     # core.mmos_client.MMOSClient instance, or None (mmos-retrofit)

Nobody constructs their own DB connection, Matcher or ERP client - that would
give two writers to one SQLite file, which is exactly what CONTRACTS.md §2
says never to do.

`ctx.mmos` is set separately, after `ctx.init(...)` (see server.py's call
site), so this class's constructor contract is untouched. It defaults to None
so any code written before the MM OS retrofit keeps working unchanged;
routes/mmos.py treats "ctx.mmos is None" the same as "not configured".
"""
import os
import threading


class _Context:
    def __init__(self):
        self.root = None
        self.web = None
        self.cfg = None
        self.con = None
        self.lock = threading.Lock()
        self.matcher = None
        self.erp = None
        self.mmos = None

    def init(self, root, cfg, con, matcher, erp):
        self.root = root
        self.web = os.path.join(root, "web")
        self.cfg = cfg
        self.con = con
        self.matcher = matcher
        self.erp = erp


ctx = _Context()
