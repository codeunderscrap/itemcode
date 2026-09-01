"""core/mmos_client — Item Code Studio's own MM OS SSO client.

Not a vendored copy of `packages/mmos-client-py` (the MM OS repo's canonical
client): that package is FastAPI/httpx/python-jose and this project is a
stdlib `http.server` app with a small, already-approved dependency list
(agents/CONTRACTS.md house rule 1: rapidfuzz, openpyxl, requests, pymupdf,
paddleocr — no web framework, no JWT library). This package is a from-scratch
reimplementation of the same wire contract (MM OS docs/03-api-contract.md,
docs/04-auth-flow.md) on `requests` plus pure-Python RS256 verification
(`_verify.py`), matching the retrofit brief's explicit fallback: "implement
equivalent offline RS256/JWKS verification + deny-list polling if this
repo's tiny framework makes vendoring awkward."

    from core.mmos_client import MMOSClient, TokenError
"""
from .client import MMOSClient
from ._verify import TokenError

__all__ = ["MMOSClient", "TokenError"]
