# Parallel build — orchestration

**Item Code Studio · 6 August 2026 · 9 work packets · 234 points buildable now**

Each file in this folder is a **complete, self-contained prompt**. Open one, copy
the whole thing, paste it into a fresh Claude Code instance in
`C:\Users\Anura\ItemCodeStudio`. It needs nothing from this conversation.

---

## Run order

```
                    ┌──────────────────────────┐
   FIRST, ALONE     │  AGENT 0 — FOUNDATION    │   ~25 min
   nothing else     │  schema · route split ·  │
   may start        │  settings store · theme  │
                    └────────────┬─────────────┘
                                 │
        ┌─────────┬──────────┬───┴────┬──────────┬─────────┐
        ▼         ▼          ▼        ▼          ▼         ▼
      ┌───┐     ┌───┐      ┌───┐    ┌───┐      ┌───┐     ┌───┐
      │ A │     │ B │      │ C │    │ D │      │ G │     │ H │      ← all six
      │pub│     │aut│      │llm│    │eng│      │erp│     │api│        in parallel
      └─┬─┘     └─┬─┘      └─┬─┘    └─┬─┘      └───┘     └───┘
        │         │          │        │
        │         └────┬─────┴────┬───┘
        │              ▼          ▼
        │            ┌───┐      ┌───┐
        └───────────▶│ E │      │ F │                    ← start when C and D
                     │cre│      │mst│                      report their contracts done
                     └───┘      └───┘
```

**Agent 0 must finish before anything else starts.** It creates the tables, splits
`server.py` into per-agent route modules, and lands the theme. Everything after
it is genuinely parallel because file ownership is disjoint.

**A, B, C, D, G, H can all run at once** — six instances, no shared files.

**E and F** need the function signatures from C and D. Those signatures are
already frozen in `CONTRACTS.md`, so E and F can start immediately against them
and will compile once C and D land. If you want zero friction, start E and F
about twenty minutes after the others.

---

## The packets

| | Agent | Owns | Build now | Blocked | Already done |
|---|---|---|---:|---:|---:|
| **0** | [Foundation](AGENT_0_FOUNDATION.md) | schema, route split, settings store, theme | **26** | — | — |
| **A** | [Public face](AGENT_A_PUBLIC.md) | split landing page, decoder, directory, dictionary | **20** | — | — |
| **B** | [Auth & settings](AGENT_B_AUTH.md) | login, sessions, provisioning, **the API-key screen** | **29** | — | — |
| **C** | [Matching & LLM](AGENT_C_MATCHING.md) | LLM-first with fuzzy fallback, cache, batching | **20** | 11 | 21 |
| **D** | [Code engine](AGENT_D_ENGINE.md) | **queue-claim numbering**, live recompute, concurrency | **18** | — | 40 |
| **E** | [Create screen](AGENT_E_CREATE.md) | three-phase UI, invoice upload, cascading dropdowns | **24** | 10 | 21 |
| **F** | [Master & versions](AGENT_F_MASTER.md) | editable master, revision history, revert, activity | **34** | — | 23 |
| **G** | [ERPNext](AGENT_G_ERPNEXT.md) | Item-only client, guardrails, twice-daily sync | **16** | 36 | 9 |
| **H** | [API, packaging, deploy](AGENT_H_DEPLOY.md) | `/api/v1`, smoke suite, installer, three-tier failover | **47** | 13 | — |
| | | | **234** | **103** | **114** |

Full breakdown with acceptance criteria: `Sprint_Plan_v3.xlsx`, **Delivery Plan**
tab, filterable by the Agent column.

Some packets include stories that are **blocked on Anuraag** (an API key, real
invoices, ERP admin rights). Each brief says exactly what to build now and what
to leave stubbed — no agent should sit idle waiting.

---

## File ownership — the thing that makes this safe

Two agents editing one file is the only way this goes wrong. Ownership is
exclusive:

| Path | Owner |
|---|---|
| `core/db.py`, `server.py`, `seed.py` | **0** |
| `web/theme.css`, `web/assets/*` | **0** creates · **A** extends |
| `routes/public.py`, `web/public.html`, `web/public.js` | **A** |
| `routes/auth.py`, `core/auth.py`, `web/login.html`, `web/settings.js` | **B** |
| `core/matcher.py`, `core/llm.py`, `core/resolve.py` | **C** |
| `core/codes.py`, `core/restructure.py` | **D** |
| `routes/create.py`, `core/ingest.py`, `web/create.js` | **E** |
| `routes/master.py`, `core/versions.py`, `core/exporter.py`, `web/master.js` | **F** |
| `routes/erp.py`, `core/erp.py`, `core/sync.py` | **G** |
| `routes/meta.py`, `run.bat`, `manage.py`, `install/*`, `tests/*` | **H** |

If your work seems to need someone else's file, it almost certainly means the
contract is wrong. Stop and say so rather than reaching across.

---

## When a packet finishes

Each agent writes `agents/done/<AGENT>.md` — about fifteen lines:

* what was built, and what was left stubbed
* anything thin or untested, stated plainly
* any contract that turned out wrong
* anything needed from Anuraag

Then integration, in this order — **H runs it**:

1. `python seed.py --rebuild`
2. `python server.py` starts clean
3. `python tests/smoke.py` passes
4. Load the public page logged out; confirm no creator control is reachable
5. `curl -X POST /api/v1/commit` with no session → must be **401**
6. Log in, issue one code end to end, edit it, revert it
7. Move a group; confirm the freed number goes to the **next** arrival
8. ERPNext still `enabled: false` unless Anuraag has said otherwise
9. Stop tier 1; confirm failover to tier 2 and that new codes are marked
   **provisional**
10. Restart tier 1; confirm reconciliation finalises them and reports every
    old→new change

---

## What Anuraag still owes, and what happens meanwhile

| Needed | Blocks | Meanwhile |
|---|---|---|
| LLM API key | LLM-first matching | **Agent B builds the Settings field for it.** App runs fuzzy-only until pasted — no code change needed later |
| 10 real scanned invoices | OCR accuracy | OCR path is built and wired; accuracy simply unmeasured |
| ERP admin rights → service account | ERP writes | Agent G builds against UAT with `dry_run: true` |
| **A VPS and a domain** — *not urgent* | TLS, tier 1 | **Agent H builds the local LAN server (tier 2) first, so everything runs today with no VPS at all.** Point `server_url` at it later |
| Which office machine is the local server | Tier 2 | One nominated always-on machine, with Google Drive for Desktop on it for backups |
| Judgement on 889 duplicate groups | Clean-up | Merge tooling already exists and is untouched |

Nothing on this list stops a single agent from starting.
