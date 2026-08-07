# Item Code Studio

Internal tool for creating and maintaining item codes, sitting beside ERPNext.
Full write-up: **[PRODUCT.md](PRODUCT.md)**. Frozen contracts for anyone
building against this: **[agents/CONTRACTS.md](agents/CONTRACTS.md)**.
Restart/restore/user/key-rotation runbook: **[HANDOVER.md](HANDOVER.md)**.

## Architecture, briefly

Three tiers (agents/CONTRACTS.md §2). Today, with no VPS provisioned yet,
this machine plays tier 1/2 itself — everything below works with **no VPS
at all**:

```
TIER 1  cloud VPS           the authority whenever reachable (not yet provisioned)
TIER 2  local LAN server    ONE nominated office machine — full replica,
                             authority only while tier 1 is unreachable
TIER 3  desktop clients     never mint; talk to tier 1, else tier 2,
                             else read-only from a local cache
```

`config.json → ledger.mode` is `"server"` (tier 1) · `"local_server"` (tier 2,
today's default) · `"client"` (tier 3). See `core/tier.py` for the resolver
and `core/db.py`'s `connect()` docstring for exactly which modes it covers
directly.

## Start it

Double-click **`run.bat`**, or:

```bash
python server.py
```

```
  this computer   http://localhost:8756
  same Wi-Fi      http://<this-pc-ip>:8756
```

One desktop (or the tier-2 server) hosts it; everyone else just opens the
address in a browser — nothing to install on their machines for that. For a
real desktop install with its own shortcuts, see **Local install** below.

## First run

`run.bat` builds the database automatically. To rebuild from the source
workbooks at any time:

```bash
python seed.py --rebuild
```

Source paths are in `config.json` → `sources`. On first run, if no accounts
exist yet, a one-time `admin` account is created and its password printed
once to the console — hand it to whoever will provision the rest
(`python manage.py adduser <username> --admin`).

## The API

Everything real lives under `/api/v1` — one response envelope, one set of
error codes, documented in `agents/CONTRACTS.md` §6 and enforced by
`tests/smoke.py`. Human-readable reference, generated live from the actual
route table (never hand-maintained, so it can't go stale):

```
GET /api/docs               (HTML)
GET /api/docs?format=json   (machine-readable)
GET /api/v1/health          (liveness + current tier)
```

Older, un-versioned `/api/...` paths still work exactly as before — the
current UI (`web/app.js`) still calls some of them directly — but nothing
new should be built against them; see `agents/done/AGENT_0.md`.

## Accounts

No sign-up, no self-service reset (agents/CONTRACTS.md decision 9):

```bash
python manage.py adduser <username> --name "Full Name" [--admin]
python manage.py resetpw <username>
python manage.py disable <username>
python manage.py listusers
```

## Testing

```bash
python tests/smoke.py
```

Standard library only, runs in a few seconds, starts its own server if one
isn't already up (and shuts it down again afterwards). Covers: every
`/api/v1` endpoint answers; every mutating/session-gated one refuses with
401 when called with no cookie (checked endpoint by endpoint); login and
the session cookie; a known-code decode; a full resolve → commit → decode
round trip; the public page has no creator function reachable; the error
envelope holds on a deliberately bad request.

## Local install (Windows, no admin rights, no secrets)

```powershell
powershell -ExecutionPolicy Bypass -File install\install.ps1 -ServerUrl "https://items.example.com"
```

Copies the app to `%LOCALAPPDATA%\ItemCodeStudio`, writes a `config.json`
pointed at that server address (`ledger.mode: "client"`) with **no API key,
no ERP credential, ever** — see `install/install.ps1`'s header for exactly
what "client mode" does and does not yet do end-to-end. Creates Desktop and
Start Menu shortcuts. Warns, but doesn't fail, if Python or Tesseract is
missing.

## VPS deployment (TLS mandatory)

`install/vps/` — a systemd unit, a Caddyfile (automatic HTTPS, HTTP→HTTPS
redirect, HSTS), and a provisioning script for a plain Debian/Ubuntu box:
non-root service user, firewall everything but 443, key-only SSH. Not yet
run against a real box — host and domain need confirming with Anuraag first
(agents/CONTRACTS.md task 5). See `HANDOVER.md` for exact status.

## Backup

```bash
python install/backup.py run              # daily DB snapshot + weekly Excel export
python install/backup.py restore-verify <path-to-a-daily-backup.db>
```

Writes into `config.json → backup.drive_folder` — a plain folder that
Google Drive for Desktop already syncs on the tier-2 machine. No Google API
integration, no OAuth, no credential in this codebase (agents/CONTRACTS.md
house rule 3). Keeps 14 dailies / 8 weeklies, prunes older. Uses SQLite's
own online-backup API, which is safe against a live WAL-mode database being
written to concurrently — not a raw file copy.

## Configuration — `config.json`

| Key | What it does |
|---|---|
| `match_threshold` | Fuzzy decides at or above this; the LLM is consulted below it. Default 60. |
| `llm.provider` | `none` · `anthropic` · `gemini` · `openai` · `ollama` · `grok` — set from the Settings screen, not here, once a real key exists |
| `erpnext.enabled` | Master switch for talking to ERPNext at all |
| `erpnext.dry_run` | `true` shows the exact payload it would post and writes nothing |
| `port` | Default 8756 |
| `ledger.mode` | `server` (tier 1) · `local_server` (tier 2, default today) · `client` (tier 3) |
| `ledger.server_url` / `.local_url` | tier 1 / tier 2 addresses |
| `backup.drive_folder` | a Google-Drive-for-Desktop-synced folder; empty until set |

**No secret belongs in this file.** The LLM key and every ERPNext credential
live only in the VPS's `settings` table (agents/CONTRACTS.md §5), set from
the Settings screen once a real key/credential exists — never hardcoded,
never committed.

## Layout

```
server.py            process entry point                (standard library only)
manage.py            admin CLI — users, tier status, cache refresh
seed.py               builds the DB from the source workbooks
config.json          settings — no secrets
core/
  db.py              SQLite schema, connect(), settings store
  dispatch.py        HTTP plumbing, request/response, routing
  api.py             ok()/err() envelopes, ApiError
  context.py         the shared runtime singleton (ctx)
  auth.py            passwords, sessions, login rate limiting
  codes.py           code grammar, prefix minting, numbering, vacancies, leases
  resolve.py         the three-phase LLM-first / rules-guardrail engine
  matcher.py         normaliser + fuzzy blend
  llm.py             LLM client
  ingest.py          text / PDF / OCR / Excel invoice reading
  restructure.py     move, merge, retire, rename - with impact preview
  versions.py        item revision history, revert
  erp.py             ERPNext REST client (Item-only, guardrailed)
  sync.py            twice-daily ERPNext sync, tier-1/tier-2 reconciliation
  exporter.py        Excel export
  tier.py            three-tier resolver + local dictionary cache
routes/              one module per API area (agents/CONTRACTS.md §6)
web/                 the interface (public page, login, settings, creator UI)
install/
  install.ps1        Windows desktop installer
  backup.py           Google-Drive-folder backup + restore-verify
  vps/               systemd unit, Caddyfile, provisioning script
tests/
  smoke.py           the whole system, end to end, under a minute
data/itemcode.db     single source of truth (safe to copy while running — WAL mode)
exports/             generated workbooks
```

## Dependencies

```bash
pip install -r requirements.txt
```

Standard library plus `rapidfuzz`, `openpyxl`, `requests`, and — as of
7 August 2026 — `pymupdf` (digital PDF text/tables) and `paddleocr` +
`paddlepaddle` (OCR for scanned PDFs and photographed invoices), replacing
the old `pdfplumber`/`pytesseract`/external-Tesseract stack. This assumes
internet + admin rights on the machine, a deliberate change from the
project's original "no pip install" constraint — see
`agents/CONTRACTS.md`'s house rule 1. PaddleOCR's models download once on
first real OCR use and are cached under the user's profile afterwards; if
`pymupdf`/`paddleocr` aren't installed, invoice text/table extraction and
OCR degrade to a plain-English note per file rather than crashing the
upload — everything else keeps working.
