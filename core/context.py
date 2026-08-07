"""The runtime singletons every route module reads from.

server.py populates this once at startup (`ctx.init(...)`), before it imports
the route modules. Route handlers then do:

    from core.context import ctx
    ctx.con      # the one shared SQLite connection - single writer
    ctx.lock     # hold while touching ctx.con
    ctx.cfg      # parsed config.json
    ctx.matcher  # core.matcher.Matcher instance
    ctx.erp      # core.erp.ERP instance

Nobody constructs their own DB connection, Matcher or ERP client - that would
give two writers to one SQLite file, which is exactly what CONTRACTS.md §2
says never to do.
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

    def init(self, root, cfg, con, matcher, erp):
        self.root = root
        self.web = os.path.join(root, "web")
        self.cfg = cfg
        self.con = con
        self.matcher = matcher
        self.erp = erp


ctx = _Context()
