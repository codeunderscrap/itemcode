"""JWKS cache: refreshed on an unknown kid, at most once a minute. Same
design as the vendored ATT client (`ATT_Platform/backend/vendor/mmos_client/_jwks.py`),
reimplemented on `requests` (already a dependency here, agents/CONTRACTS.md's
approved list) instead of `httpx`.

If MM OS is unreachable the last known key set is kept — signature
verification then fails closed for a genuinely new key, but does not thrash
the network on every request from an attacker cycling `kid` values, and does
not stop verifying tokens signed with a key it already has.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

logger = logging.getLogger("mmos_client.jwks")


class JWKSCache:
    def __init__(self, jwks_url, *, session=None, min_refresh_seconds=60, timeout=5.0):
        self._url = jwks_url
        self._session = session or requests
        self._min_refresh = min_refresh_seconds
        self._timeout = timeout
        self._keys = {}
        self._last_fetch = 0.0
        self._lock = threading.Lock()

    def get_key(self, kid):
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            if self._can_refetch():
                self._fetch_locked()
            return self._keys.get(kid)

    def _can_refetch(self):
        return (time.monotonic() - self._last_fetch) >= self._min_refresh

    def _fetch_locked(self):
        self._last_fetch = time.monotonic()
        try:
            resp = self._session.get(self._url, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            self._keys = {k["kid"]: k for k in data.get("keys", []) if "kid" in k}
        except Exception as exc:                                    # noqa: BLE001
            logger.warning("mmos: JWKS fetch from %s failed, keeping cached keys (%s)", self._url, exc)

    def force_refresh(self):
        """Test/ops hook: bypass the rate limit once."""
        with self._lock:
            self._last_fetch = 0.0
            self._fetch_locked()
