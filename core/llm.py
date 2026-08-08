"""Provider-neutral LLM client for the resolver.

One interface, six backends (`anthropic`, `gemini`, `openai`, `ollama`,
`grok`, `groq`) plus `none`. Provider, key, model and base_url are read from the **settings
table** (`llm.provider`, `llm.api_key`, `llm.model`, `llm.base_url`) - the
screen Agent B builds writes them there. `config.json`'s `llm` block is
consulted only as a last-resort default (e.g. before Settings has ever been
saved) because CONTRACTS.md says settings-table values win and secrets must
never live in config.json.

Standard library only (`urllib`). Hard timeout ~20s. One retry on 429/5xx
with a short backoff, then give up. **This module never raises out of
`ask_json()`** - a caller that gets `answer is None` just falls back to the
rules, which is the whole point of "the fallback is always there"
(CONTRACTS.md §7, AGENT_C brief). Every fallback is logged with a reason so
it is possible to tell "the LLM is off" from "the LLM is broken":
`no_key`, `rate_limited`, `timeout`, `bad_json`, `provider_error`.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request

from . import db as D

TIMEOUT = 20            # seconds, per attempt
RETRY_BACKOFF = 1.5      # seconds, before the single retry on 429/5xx
REASONS = ("no_key", "rate_limited", "timeout", "bad_json", "provider_error")


class _Unavailable(Exception):
    """Internal only - always caught inside this module."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


# --------------------------------------------------------------- settings
def get_config(con, cfg=None):
    """Resolve provider/key/model/base_url: settings table wins, config.json
    is the last-resort default, nothing is ever hardcoded."""
    file_llm = (cfg or {}).get("llm") or {}
    provider = D.get_setting(con, "llm.provider", None) or file_llm.get("provider") or "none"
    api_key = D.get_setting(con, "llm.api_key", None) or file_llm.get("api_key") or ""
    model = D.get_setting(con, "llm.model", None) or file_llm.get("model") or ""
    base_url = D.get_setting(con, "llm.base_url", None) or file_llm.get("base_url") or ""
    return {
        "provider": (provider or "none").strip().lower(),
        "api_key": api_key or "",
        "model": model or "",
        "base_url": base_url or "",
    }


def get_mode(con, cfg=None):
    """'fuzzy' or 'llm'. Default is fuzzy - the good, non-degraded default
    state described in the brief - until an operator switches it on."""
    return (D.get_setting(con, "match.mode", None)
            or (cfg or {}).get("match_mode") or "fuzzy").strip().lower()


def get_threshold(con, cfg=None):
    v = D.get_setting(con, "match.threshold", None)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return int((cfg or {}).get("match_threshold", 60))


def available(con, cfg=None):
    """True only when there is a real provider configured with a key
    (ollama needs no key, everything else does)."""
    c = get_config(con, cfg)
    if c["provider"] in ("", "none"):
        return False
    if c["provider"] == "ollama":
        return True
    return bool(c["api_key"])


def enabled(con, cfg=None):
    """The two gates the brief names together: fuzzy-mode toggle AND a
    usable provider. Either being false means 'skip the LLM entirely'."""
    return get_mode(con, cfg) == "llm" and available(con, cfg)


# ------------------------------------------------------------------- cache
def cache_key(question_text, shortlist_sig):
    h = hashlib.sha256()
    h.update(question_text.encode("utf-8", "ignore"))
    h.update(b"\x1f")
    h.update(json.dumps(shortlist_sig, sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def cache_get(con, key):
    r = con.execute("SELECT answer FROM llm_cache WHERE key=?", (key,)).fetchone()
    if not r:
        return None
    try:
        return json.loads(r["answer"])
    except (TypeError, ValueError):
        return None


def cache_put(con, key, question, answer, provider):
    con.execute(
        "INSERT INTO llm_cache(key,question,answer,provider,ts) VALUES(?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET answer=excluded.answer, provider=excluded.provider, "
        "ts=excluded.ts",
        (key, question[:4000], json.dumps(answer), provider, D.now()))
    con.commit()


# ------------------------------------------------------------------- http
def _request(url, payload, headers, timeout=TIMEOUT):
    # A real User-Agent, not urllib's default "Python-urllib/3.x" - several
    # providers (Groq confirmed live, 7 August 2026: a bare Cloudflare 403
    # "error code: 1010" with a valid key and correct payload, gone the
    # moment a normal-looking UA was added) sit behind bot protection that
    # blocks the default one outright, unrelated to auth or the request body.
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (compatible; ItemCodeStudio/1.0)",
                 **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, payload, headers, timeout=TIMEOUT):
    """One attempt, then one retry on 429/5xx only, then give up. Timeouts
    and connection errors are not retried - they already cost the full
    timeout once and a fallback is always cheaper than a second wait."""
    try:
        return _request(url, payload, headers, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429 or 500 <= e.code < 600:
            time.sleep(RETRY_BACKOFF)
            try:
                return _request(url, payload, headers, timeout)
            except urllib.error.HTTPError as e2:
                raise _Unavailable("rate_limited" if e2.code == 429 else "provider_error")
            except Exception:                                    # noqa: BLE001
                raise _Unavailable("provider_error")
        raise _Unavailable("provider_error")
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).lower()
        raise _Unavailable("timeout" if "time" in reason else "provider_error")
    except TimeoutError:
        raise _Unavailable("timeout")
    except Exception:                                             # noqa: BLE001
        raise _Unavailable("provider_error")


def _extract_json(text):
    if not text:
        raise _Unavailable("bad_json")
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        raise _Unavailable("bad_json")
    try:
        return json.loads(m.group(0))
    except (TypeError, ValueError):
        raise _Unavailable("bad_json")


# --------------------------------------------------------------- backends
def _anthropic(c, prompt):
    d = _post("https://api.anthropic.com/v1/messages",
              {"model": c["model"] or "claude-haiku-4-5-20251001", "max_tokens": 4000,
               "messages": [{"role": "user", "content": prompt}]},
              {"x-api-key": c["api_key"], "anthropic-version": "2023-06-01"})
    try:
        return d["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")


def _openai(c, prompt):
    base = c["base_url"] or "https://api.openai.com/v1"
    d = _post(f"{base}/chat/completions",
              {"model": c["model"] or "gpt-4o-mini", "max_tokens": 4000, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]},
              {"Authorization": f"Bearer {c['api_key']}"})
    try:
        return d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")


def _gemini(c, prompt):
    model = c["model"] or "gemini-2.0-flash"
    d = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={c['api_key']}",
        {"contents": [{"parts": [{"text": prompt}]}]}, {})
    try:
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")


def _ollama(c, prompt):
    base = c["base_url"] or "http://localhost:11434"
    d = _post(f"{base}/api/generate",
              {"model": c["model"] or "llama3.1", "prompt": prompt, "stream": False,
               "format": "json"}, {}, timeout=90)
    try:
        return d["response"]
    except (KeyError, TypeError):
        raise _Unavailable("bad_json")


def _grok(c, prompt):
    # xAI's own model. Not to be confused with Groq (below) - different
    # company, different key format (xAI keys aren't "gsk_"-prefixed),
    # different default model. Both APIs happen to be OpenAI-compatible.
    base = c["base_url"] or "https://api.x.ai/v1"
    d = _post(f"{base}/chat/completions",
              {"model": c["model"] or "grok-4", "max_tokens": 4000, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]},
              {"Authorization": f"Bearer {c['api_key']}"})
    try:
        return d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")


def _groq(c, prompt):
    # Groq (groq.com) - LPU-hosted inference for open models (Llama,
    # Kimi, etc, whatever c["model"] names), OpenAI-compatible API, keys
    # prefixed "gsk_". Easy to mix up with xAI's Grok by name alone - kept
    # as a fully separate backend/provider rather than reusing _grok's
    # defaults, since the endpoint, key format and model catalogue are
    # all different even though the request/response shape matches.
    base = c["base_url"] or "https://api.groq.com/openai/v1"
    d = _post(f"{base}/chat/completions",
              {"model": c["model"] or "llama-3.3-70b-versatile", "max_tokens": 4000,
               "temperature": 0, "messages": [{"role": "user", "content": prompt}]},
              {"Authorization": f"Bearer {c['api_key']}"})
    try:
        return d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")


_BACKENDS = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini,
             "ollama": _ollama, "grok": _grok, "groq": _groq}


# ------------------------------------------------------------------- ask
def ask_json(con, prompt, cfg=None):
    """Send one prompt, get back parsed JSON.

    Returns (parsed, provider, reason). `parsed` is None whenever anything
    went wrong - no key, disabled, rate-limited, timed out, unreachable, or
    a reply that was not valid JSON - and `reason` says which. Never raises.
    """
    c = get_config(con, cfg)
    prov = c["provider"]
    if prov in ("", "none"):
        return None, prov, "no_key"
    if prov != "ollama" and not c["api_key"]:
        return None, prov, "no_key"
    backend = _BACKENDS.get(prov)
    if backend is None:
        return None, prov, "provider_error"
    try:
        raw = backend(c, prompt)
        parsed = _extract_json(raw)
        return parsed, prov, None
    except _Unavailable as e:
        print(f"[llm] fallback reason={e.reason} provider={prov}")
        return None, prov, e.reason
    except Exception as e:                                        # noqa: BLE001
        print(f"[llm] fallback reason=provider_error provider={prov} err={e}")
        return None, prov, "provider_error"
