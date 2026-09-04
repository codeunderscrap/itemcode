"""MMOSClient — the one object routes/mmos.py talks to.

Deliberately much smaller than the vendored ATT client (`ATT_Platform/backend/
vendor/mmos_client`): no FastAPI middleware, no `Depends`, no route
auto-audit, because Item Code Studio is not FastAPI. What survives is the
token contract itself (verify order, JWKS cache, deny-list poll, heartbeat)
and the "unconfigured => inert, never a silent grant" posture, which is the
part that actually matters for security.

`MMOSClient.configured` is False whenever `enabled` is false or `slug`/
`os_url`/`service_key` is missing. Every caller in routes/mmos.py checks it
and returns a plain 404/disabled response rather than ever falling through to
"verification skipped, allow anyway" — that fallthrough is exactly the bug
this file exists to make structurally impossible.
"""
from __future__ import annotations

import requests

from ._denylist import DenyList, DenyListPoller
from ._heartbeat import Heartbeat
from ._jwks import JWKSCache
from ._verify import TokenError, verify_token

__all__ = ["MMOSClient", "TokenError"]


class _BaseUrlSession:
    """`requests` has no `httpx`-style `base_url` — this is the two methods
    the rest of this package needs, with the base URL prefixed once here
    instead of at every call site."""

    def __init__(self, base_url, timeout=5.0):
        self._base = base_url.rstrip("/")
        self._s = requests.Session()

    def get(self, path, **kw):
        return self._s.get(self._base + path, **kw)

    def post(self, path, **kw):
        return self._s.post(self._base + path, **kw)


class MMOSClient:
    def __init__(
        self,
        *,
        enabled,
        slug,
        os_url,
        service_key,
        issuer=None,
        version="0.0.0",
        poll_after_seconds=60,
        clock_skew_seconds=60,
        heartbeat_seconds=300,
        jwks_min_refresh_seconds=60,
    ):
        self.slug = (slug or "").strip()
        self.os_url = (os_url or "").strip().rstrip("/")
        self.service_key = service_key or ""
        self.issuer = (issuer or self.os_url or "").rstrip("/")
        self.version = version
        self.clock_skew_seconds = clock_skew_seconds

        # Fail closed: "enabled" alone is not enough. Every field the token
        # boundary actually needs must be present, or this client is inert.
        self.configured = bool(enabled and self.slug and self.os_url)

        self._session = None
        self._jwks = None
        self._denylist = DenyList()
        self.poller = None
        self.heartbeat = None

        if self.configured:
            self._session = _BaseUrlSession(self.os_url)
            self._jwks = JWKSCache(
                "/.well-known/jwks.json",
                session=self._session,
                min_refresh_seconds=jwks_min_refresh_seconds,
            )
            self.poller = DenyListPoller(
                session=self._session,
                service_key=self.service_key,
                denylist=self._denylist,
                default_interval_seconds=poll_after_seconds,
            )
            if self.service_key:
                self.heartbeat = Heartbeat(
                    session=self._session,
                    service_key=self.service_key,
                    version=self.version,
                    interval_seconds=heartbeat_seconds,
                )

    def verify(self, token):
        """claims dict, or raises TokenError. Never called unless
        `self.configured` — callers must check that first."""
        if not self.configured:
            raise TokenError("mmos_not_configured")
        return verify_token(
            token,
            jwks_cache=self._jwks,
            issuer=self.issuer,
            audience=self.slug,
            skew_seconds=self.clock_skew_seconds,
            denylist=self._denylist,
        )

    def start_background(self):
        """Starts the deny-list poller (and heartbeat, if a service key is
        set). Safe to call even when unconfigured — a no-op then."""
        if not self.configured:
            return
        if self.poller:
            self.poller.start()
        if self.heartbeat:
            self.heartbeat.start()
