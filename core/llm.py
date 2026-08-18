"""Provider-neutral LLM client for the resolver (Bharat Router).

Hardcoded to use Bharat Router API with GPT-OSS 120B model (auto-discovered).
Implements a 100 calls/day global rate limit.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import datetime
import threading

from . import db as D

TIMEOUT = 20
RETRY_BACKOFF = 1.5
REASONS = ("no_key", "rate_limited", "timeout", "bad_json", "provider_error")

_RL_LOCK = threading.Lock()
DAILY_LIMIT = 100

# Model candidates tried in order. The first one that works is cached.
# These are Bharat Router model IDs (NOT provider-prefixed).
_MODEL_CANDIDATES = [
    "gpt-oss-120b",
    "gpt-oss-20b",
    "qwen3.5-9b",
    "llama-3.1-8b-instruct",
    "qwen2.5-7b-instruct",
]
_MODEL_LOCK = threading.Lock()
_discovered_model = None  # set on first successful probe

class _Unavailable(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

# --------------------------------------------------------------- settings
def get_config(con, cfg=None):
    file_llm = (cfg or {}).get("llm") or {}
    api_key = D.get_setting(con, "llm.api_key", None) or file_llm.get("api_key") or ""
    return {
        "provider": "bharatrouter",
        "api_key": api_key.strip(),
        "model": _get_model(),
        "base_url": "https://api.bharatrouter.com/v1",
    }


def _get_model():
    """Return the cached discovered model name, defaulting to the first candidate."""
    global _discovered_model
    with _MODEL_LOCK:
        return _discovered_model or _MODEL_CANDIDATES[0]


def probe_model(api_key):
    """Try each candidate model with a cheap 1-token request.
    Caches and returns the first one that works, or None if all fail."""
    global _discovered_model
    base = "https://api.bharatrouter.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; ItemCodeStudio/1.0)",
        "Authorization": f"Bearer {api_key}",
    }
    with _MODEL_LOCK:
        if _discovered_model:
            return _discovered_model
        for candidate in _MODEL_CANDIDATES:
            payload = json.dumps({
                "model": candidate, "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}]
            }).encode()
            try:
                req = urllib.request.Request(base, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    r.read()
                _discovered_model = candidate
                print(f"[llm] discovered working model: {candidate}")
                return candidate
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    print(f"[llm] model not found: {candidate}")
                    continue
                if e.code == 403:
                    # Cloudflare or auth error — stop trying
                    print(f"[llm] auth/cloudflare error probing {candidate}: {e.code}")
                    break
            except Exception as ex:
                print(f"[llm] probe error for {candidate}: {ex}")
                break
        return None

def get_mode(con, cfg=None):
    return (D.get_setting(con, "match.mode", None)
            or (cfg or {}).get("match_mode") or "llm").strip().lower()

def get_threshold(con, cfg=None):
    v = D.get_setting(con, "match.threshold", None)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    return int((cfg or {}).get("match_threshold", 60))

def available(con, cfg=None):
    c = get_config(con, cfg)
    return bool(c["api_key"])

def enabled(con, cfg=None):
    return get_mode(con, cfg) == "llm" and available(con, cfg)

def _check_rate_limit(con):
    with _RL_LOCK:
        today = datetime.date.today().isoformat()
        current_date = D.get_setting(con, "llm.rl_date", "")
        current_calls = D.get_setting(con, "llm.rl_calls", "0")
        
        try:
            current_calls = int(current_calls)
        except ValueError:
            current_calls = 0
            
        if current_date != today:
            current_date = today
            current_calls = 0
            
        if current_calls >= DAILY_LIMIT:
            raise _Unavailable("rate_limited")
            
        current_calls += 1
        D.set_setting(con, "llm.rl_date", current_date)
        D.set_setting(con, "llm.rl_calls", str(current_calls))

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
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (compatible; ItemCodeStudio/1.0)",
                 **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def _post(url, payload, headers, timeout=TIMEOUT):
    try:
        return _request(url, payload, headers, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429 or 500 <= e.code < 600:
            time.sleep(RETRY_BACKOFF)
            try:
                return _request(url, payload, headers, timeout)
            except urllib.error.HTTPError as e2:
                raise _Unavailable("rate_limited" if e2.code == 429 else "provider_error")
            except Exception:
                raise _Unavailable("provider_error")
        raise _Unavailable("provider_error")
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e)).lower()
        raise _Unavailable("timeout" if "time" in reason else "provider_error")
    except TimeoutError:
        raise _Unavailable("timeout")
    except Exception:
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
def _bharatrouter(c, prompt):
    base = c["base_url"]
    # Probe and cache the working model on first call
    model = c["model"]
    if c["api_key"]:
        discovered = probe_model(c["api_key"])
        if discovered:
            model = discovered
    d = _post(f"{base}/chat/completions",
              {"model": model, "max_tokens": 4000, "temperature": 0,
               "messages": [{"role": "user", "content": prompt}]},
              {"Authorization": f"Bearer {c['api_key']}"})
    try:
        return d["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise _Unavailable("bad_json")

# ------------------------------------------------------------------- ask
def ask_json(con, prompt, cfg=None):
    c = get_config(con, cfg)
    prov = c["provider"]
    if not c["api_key"]:
        return None, prov, "no_key"
    
    try:
        _check_rate_limit(con)
        raw = _bharatrouter(c, prompt)
        parsed = _extract_json(raw)
        return parsed, prov, None
    except _Unavailable as e:
        print(f"[llm] fallback reason={e.reason} provider={prov}")
        return None, prov, e.reason
    except Exception as e:
        print(f"[llm] fallback reason=provider_error provider={prov} err={e}")
        return None, prov, "provider_error"
