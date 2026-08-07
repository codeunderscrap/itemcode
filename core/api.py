"""HTTP response envelopes — agents/CONTRACTS.md §6. Every route returns
through these; nobody hand-rolls a response dict.

    { "ok": true,  ...payload }
    { "ok": false, "error": { "code": "...", "message": "...", "detail": {...} } }

Kept dependency-free (no import of server.py) so every route module can pull
these in without risking a circular import — server.py imports the route
modules, so the route modules cannot import server.py back.
"""

ERROR_STATUS = {
    "AUTH_REQUIRED": 401,
    "FORBIDDEN": 403,
    "BAD_CODE": 400,
    "NOT_FOUND": 404,
    "AMBIGUOUS": 409,
    "CONFLICT": 409,
    "FROZEN": 409,
    "VALIDATION": 400,
    "UPSTREAM": 502,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
}


class ApiError(Exception):
    """Raise this from any handler; server.py's dispatcher turns it into an
    err() envelope at the right HTTP status. Never let a route send its own
    ad-hoc error shape."""

    def __init__(self, code, message, detail=None, status=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status = status or ERROR_STATUS.get(code, 400)


def ok(payload=None, **extra):
    body = {"ok": True}
    if payload:
        body.update(payload)
    if extra:
        body.update(extra)
    return body


def err(code, message, detail=None):
    e = {"code": code, "message": message}
    if detail is not None:
        e["detail"] = detail
    return {"ok": False, "error": e}
