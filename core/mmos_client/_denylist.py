"""In-memory deny-list, merged from GET {os_url}/api/agent/revocations?since=.
Ported from the vendored ATT client's `_denylist.py` onto `requests`.

Availability rule (MM OS docs/04-auth-flow.md "Revocation, end to end"): if
MM OS is unreachable, keep the last known list and keep serving — a 15-minute
token plus a firewall is an acceptable risk; logging every MM OS SSO session
out of Item Code Studio because the control plane restarted is not. This is
what makes a revoked grant actually take effect here within the poll window
(default 60s) rather than only at the next token mint.
"""
from __future__ import annotations

import logging
import threading

import requests

logger = logging.getLogger("mmos_client.denylist")


class DenyList:
    def __init__(self):
        self._subs = set()
        self._jtis = set()
        self._since = None
        self._lock = threading.Lock()

    def is_revoked(self, *, sub=None, jti=None):
        with self._lock:
            return (sub is not None and sub in self._subs) or (jti is not None and jti in self._jtis)

    def merge(self, *, revoked_subjects, revoked_jti, now):
        with self._lock:
            for row in revoked_subjects or []:
                s = row.get("sub") if isinstance(row, dict) else row
                if s:
                    self._subs.add(s)
            for row in revoked_jti or []:
                j = row.get("jti") if isinstance(row, dict) else row
                if j:
                    self._jtis.add(j)
            self._since = now

    @property
    def since(self):
        return self._since

    def snapshot(self):
        with self._lock:
            return set(self._subs), set(self._jtis)


class DenyListPoller:
    """Polls /api/agent/revocations (the real router mount per docs/03 and the
    ATT client's own correction note — not the bare /api/revocations path
    docs/05's illustrative snippet uses). Call `poll_once()` directly in
    tests; the background thread just calls the same method on a timer."""

    def __init__(self, *, session, service_key, denylist, default_interval_seconds=60, timeout=5.0):
        self._session = session
        self._service_key = service_key
        self._denylist = denylist
        self._default_interval = default_interval_seconds
        self._next_interval = default_interval_seconds
        self._since = "1970-01-01T00:00:00Z"
        self._timeout = timeout
        self._thread = None
        self._stop = threading.Event()

    @property
    def next_interval_seconds(self):
        return self._next_interval

    def poll_once(self):
        """Returns True on a successful poll, False if MM OS was unreachable
        (deny-list is left untouched either way, per the availability rule)."""
        try:
            resp = self._session.get(
                "/api/agent/revocations",
                params={"since": self._since},
                headers={"Authorization": f"Bearer {self._service_key}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            self._denylist.merge(
                revoked_subjects=data.get("revoked_subjects", []),
                revoked_jti=data.get("revoked_jti", []),
                now=data.get("now", self._since),
            )
            self._since = data.get("now", self._since)
            self._next_interval = int(data.get("poll_after_seconds", self._default_interval))
            return True
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("mmos: revocation poll failed, keeping last known deny-list (%s)", exc)
            self._next_interval = self._default_interval
            return False

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="mmos-denylist-poller")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._next_interval)
