"""Heartbeat loop: liveness only. Ported from the vendored ATT client's
`_heartbeat.py`, trimmed — Item Code Studio has its own independent LLM
provider/settings (`routes/auth.py`'s Settings screen, `core/matcher.py`),
which is a separate concern from MM OS's own LLM control plane, so this does
not wire `llm_guard()`/`report_usage()` (MM OS docs/05-service-integration.md
§"Reporting LLM usage") — out of scope for an SSO-only retrofit. What this
keeps: `POST {os_url}/api/agent/heartbeat` every few minutes with the service
key, satisfying integration contract item 3 ("send a heartbeat every 5
minutes") and proving the service key is valid without a human watching logs.

Best-effort only: a failed heartbeat is logged and retried next interval,
never raised into a request path.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("mmos_client.heartbeat")


class Heartbeat:
    def __init__(self, *, session, service_key, version, interval_seconds=300, timeout=5.0):
        self._session = session
        self._service_key = service_key
        self._version = version
        self._interval = interval_seconds
        self._timeout = timeout
        self._thread = None
        self._stop = threading.Event()
        self.last_ok = None

    def beat_once(self):
        """Returns True on success. Call directly in tests instead of waiting."""
        try:
            resp = self._session.post(
                "/api/agent/heartbeat",
                json={"version": self._version},
                headers={"Authorization": f"Bearer {self._service_key}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            self.last_ok = True
            return True
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("mmos: heartbeat failed (%s)", exc)
            self.last_ok = False
            return False

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="mmos-heartbeat")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self.beat_once()
            self._stop.wait(self._interval)
