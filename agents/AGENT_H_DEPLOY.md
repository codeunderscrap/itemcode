# AGENT H — API, PACKAGING & DEPLOYMENT

**Start after Agent 0 reports done. Runs in parallel with A, B, C, D, G.**
Working directory: `C:\Users\Anura\ItemCodeStudio`

---

Read `agents/CONTRACTS.md` first — it is frozen.

You make this a product rather than a folder of scripts, and you run the final
integration. Two things in your packet are load-bearing for everyone else: the
API contract that the larger system will build against, and the local-install
model that Anuraag has just specified.

## What you own

`routes/meta.py` · `run.bat` · `manage.py` (except user commands — Agent B) ·
`install/*` · `tests/*` · `README.md`

## The deployment model — read this carefully

**Decided 6 August 2026: a cloud VPS owns the database. Nothing lives on a
desktop but the app itself.**

```
CREATOR'S MACHINE — installed desktop app
  OCR (local) · normaliser + fuzzy matching (local, against a cached
  read-only dictionary) · the login
  HOLDS NO SECRETS — no LLM key, no ERPNext credential
                        │
                        │ HTTPS  (mandatory)
                        ▼
CLOUD VPS — the single authority
  SQLite: ledger, master, dictionary
  the only place a code is minted
  proxies the LLM and ERPNext calls so keys live only here
  serves the public read-only page: decoder + directory
```

**SQLite stays.** One server process is one writer, which is what SQLite is good
at. Do not introduce Postgres. Never place the file on a network share — its
locking is unreliable over SMB and concurrent writers corrupt it.

**There is a third tier, and it is the failsafe.** The VPS is not provisioned
yet and may not always be up, so work must not stop when it is down:

```
TIER 1  CLOUD VPS           authority whenever reachable (not yet provisioned)
   ▲                        build vendor-neutral: any plain Linux box
   │ syncs on return
TIER 2  LOCAL LAN SERVER    ONE nominated office machine. Full replica.
   ▲                        Authority only while tier 1 is unreachable.
   │
TIER 3  DESKTOP CLIENTS     never mint. Talk to tier 1, else tier 2.
                            Neither reachable -> read-only from cache.
```

**Build tier 2 first.** It works today with no VPS at all, so nothing waits on
infrastructure that does not exist. Point `server_url` at the VPS later and the
same code takes over.

Offline minting is made safe by **number leases** and **provisional codes** —
Agent D owns both, Agent G owns the reconciliation. Your job is the failover and
the plumbing, not the numbering.

`config.json` → `"ledger": {"mode": "server"|"local_server"|"client",
"server_url": "...", "local_url": "...", "lease_size": 10}`.

## Tasks

**1. Freeze the API at `/api/v1` (5 pts).** Everything under it, exactly as
`CONTRACTS.md` §6 documents. Both envelopes, the stable error codes, no stack
trace to a client, ever. Consistent pagination — `q`, `limit`, `offset`, and
`total` alongside `rows`.

The larger system will build against this, so what ships must match what is
written. If an agent's route deviates, fix the route, not the document.

**2. `/api/docs` (3 pts).** Generated from the route table itself, not
hand-maintained — a hand-written API doc is wrong within a week. Endpoint,
method, auth requirement, and an example request and response.

**3. Smoke suite (5 pts).** `tests/smoke.py`, standard library, under a minute.

Must cover: every endpoint answers; **every mutating endpoint returns 401 without
a session**; login works and the cookie is set; decode of
`RMBS0010206100007` is correct; a full resolve → commit → decode round trip; the
public page exposes no creator function; the error envelope holds on a
deliberately bad request.

That 401 sweep is the single most valuable test here. Assert it endpoint by
endpoint, not once.

**4. Three-tier failover + dictionary cache (10 pts).** `db.connect()` resolves
the tier: try the VPS, fall back to the local server, and if neither answers go
read-only from the local cache.

Failover must be **quick and quiet** — a short timeout, not a thirty-second hang.
Show the current tier permanently in the interface, because an operator must
always know whether the code they are about to mint is final or provisional:

```
● connected            minting final codes
● local failsafe       VPS unreachable — codes are provisional and
                       will be confirmed when it reconnects
● offline              read-only. decode and search only.
```

The client needs a **cached read-only copy of the dictionary** — groups, spec
values, aliases, and enough of the item index to match against — so OCR and fuzzy
matching keep working locally. Refresh on launch and on demand; it never writes
back.

Detect the VPS returning and hand authority back automatically, triggering Agent
G's reconciliation. Do not require anyone to restart anything.

**5. VPS deployment with TLS (8 pts).** This replaces the old LAN-host story and
is now the riskiest thing in the packet, because the service is on the public
internet.

* HTTPS with a valid certificate. HTTP redirects to HTTPS. HSTS on.
* **Rate-limit `/auth/login`** and back off after repeated failures from one IP.
  It is now reachable by anyone.
* Run as a non-root user, firewall everything except 443, disable password SSH.
* systemd unit: start on boot, restart on failure.
* Secrets only in the `settings` table on the VPS.

Confirm with Anuraag which host and domain before you provision anything.

**6. Local installer (8 pts).** `install/` with a Windows install that copies the
app, writes `config.json` with `mode: "client"` and the server URL, creates
Start-menu and desktop shortcuts, and checks Python is present with a clear
message if not.

**The installer must never contain a secret** — no API key, no ERP credential,
no admin password. It ships a server address and nothing else. Assume no admin
rights. **No pip install.** If Tesseract is absent, say so — OCR degrades,
nothing else breaks.

**7. Backup to Google Drive (5 pts).** Anuraag has chosen Google Drive.

Do **not** build a Google API integration — it needs OAuth credentials, a consent
screen and a refresh-token dance, all to move one file. Instead:

* the **local LAN server** (tier 2) pulls a nightly copy of the database from the
  VPS — it already syncs with it, so this is one more call;
* it writes that copy, plus a weekly Excel export, into a folder that **Google
  Drive for Desktop** already syncs on that machine;
* Drive uploads it. No credentials in our code, nothing to rotate, and it keeps
  working when someone changes a password.

The Drive folder path is a config value. Keep 14 dailies and 8 weeklies, prune
older. SQLite is in WAL mode so it copies safely while running.

**Verify a restore actually works** — take a backup, restore it into a scratch
folder, start the app against it and confirm the counts. An unverified backup is
a rumour, and once the VPS exists this is the only copy of the master.

If tier 2 does not exist yet, the VPS writes backups locally and the same folder
is picked up when tier 2 is set up. Say so in the handover rather than leaving it
silently unbacked.

**8. Integration and handover (4 pts).** When the other agents report done, run
the checklist in `agents/README.md`. Then write `HANDOVER.md`: what was built,
what is thin, what is untested, what Anuraag still owes, and how to restart,
restore, add a user and rotate a key.

Be straight about weak spots. A handover that oversells is worse than none,
because it removes the reader's chance to check.

## Watch for

* Standard library only, plus what is already installed. No new dependency.
* Windows first. Paths with spaces, `OneDrive` in the path, no admin rights.
* Do not weaken another agent's auth check to make a test pass. If a test fails
  on 401, the test is wrong.

## Done when

Everything answers under `/api/v1` in the documented shape; `/api/docs` is
generated; `tests/smoke.py` passes including the full 401 sweep; the local server
(tier 2) runs the whole app today with no VPS at all; killing tier 1 fails over
to tier 2 within a couple of seconds and the interface says so; killing both
leaves the app read-only with a clear message and no way to mint; bringing tier 1
back hands authority over and triggers reconciliation without a restart; the
installer produces a working shortcut and **contains no secret**; a backup lands
in the Drive-synced folder and a **restore has actually been tried**;
`HANDOVER.md` exists.

## Then

Write `agents/done/AGENT_H.md` plus `HANDOVER.md`. You are last out — make sure
the next person can run this without asking anyone.
