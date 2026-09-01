"""RS256 JWT verification, pure Python standard library — no `cryptography`,
no `python-jose`, no `PyJWT`. Item Code Studio is stdlib-only plus a short,
already-approved list (agents/CONTRACTS.md house rule 1); the reference MM OS
client (`ATT_Platform/backend/vendor/mmos_client`) is FastAPI/httpx/jose and
does not fit this project's tiny `http.server` framework, so this is the
"implement the equivalent offline verification" option the retrofit brief
allows explicitly, matching MM OS's token contract byte-for-byte
(MM OS repo docs/04-auth-flow.md "Verification, in the exact order the
client library does it"):

    1. kid in header resolves against cached JWKS
    2. RS256 signature valid
    3. iss == configured MM OS issuer
    4. aud == this service's slug
    5. exp / iat within a clock-skew window
    6. sub / jti not in the deny-list
    7. roles returned as-is on the claims; the caller maps them

RSA-2048 signature verification is just modular exponentiation on public
values (n, e) — no secret-holding operation, so plain Python big integers are
exactly as correct as a C crypto library here, just slower (irrelevant at
this request volume). `alg` is checked against "RS256" before anything else
touches the token, so the token header can never pick the algorithm (the
classic alg-confusion hole — same discipline as the vendored ATT client).

Any failure raises TokenError with a short machine-readable reason and never
echoes token contents back to a caller.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

# DigestInfo ASN.1 prefix for SHA-256, PKCS#1 v1.5 (RFC 8017 / RFC 3447).
# Fixed, well-known bytes — not a secret, not configuration.
_SHA256_DIGESTINFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class TokenError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _b64url_decode(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    pad = b"=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _b64url_json(s):
    try:
        return json.loads(_b64url_decode(s))
    except Exception:                                              # noqa: BLE001
        raise TokenError("malformed_token")


def _rsa_pkcs1v15_sha256_verify(message: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5 verify, RFC 8017 §8.2.2, hash fixed to SHA-256."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k or k < len(_SHA256_DIGESTINFO_PREFIX) + 32 + 11:
        return False
    sig_int = int.from_bytes(signature, "big")
    if sig_int >= n:
        return False
    m_int = pow(sig_int, e, n)
    em = m_int.to_bytes(k, "big")

    digest = hashlib.sha256(message).digest()
    t = _SHA256_DIGESTINFO_PREFIX + digest
    ps_len = k - len(t) - 3
    if ps_len < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def verify_token(token, *, jwks_cache, issuer, audience, skew_seconds, denylist):
    if not token or not isinstance(token, str):
        raise TokenError("missing_token")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed_token")
    header_b64, payload_b64, sig_b64 = parts

    header = _b64url_json(header_b64)
    if header.get("alg") != "RS256":
        # Never let the token header choose the algorithm.
        raise TokenError("alg_not_allowed")

    kid = header.get("kid")
    if not kid:
        raise TokenError("missing_kid")

    jwk = jwks_cache.get_key(kid)
    if jwk is None:
        raise TokenError("unknown_kid")

    try:
        n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    except Exception:                                              # noqa: BLE001
        raise TokenError("bad_key")

    try:
        signature = _b64url_decode(sig_b64)
    except Exception:                                              # noqa: BLE001
        raise TokenError("malformed_token")

    signed_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if not _rsa_pkcs1v15_sha256_verify(signed_input, signature, n, e):
        raise TokenError("bad_signature")

    claims = _b64url_json(payload_b64)

    # 3 — iss
    if claims.get("iss") != issuer:
        raise TokenError("bad_issuer")

    # 4 — aud (a perfectly signed token for another service is rejected here)
    if claims.get("aud") != audience:
        raise TokenError("bad_audience")

    # 5 — exp / iat with skew
    now = time.time()
    exp = claims.get("exp")
    if exp is None or now > float(exp) + skew_seconds:
        raise TokenError("expired")
    iat = claims.get("iat")
    if iat is not None and float(iat) > now + skew_seconds:
        raise TokenError("not_yet_valid")

    # 6 — deny-list (sub or jti)
    sub = claims.get("sub")
    jti = claims.get("jti")
    if denylist.is_revoked(sub=sub, jti=jti):
        raise TokenError("revoked")

    # 7 — roles: returned on the claims as-is; the caller maps them.
    return claims
