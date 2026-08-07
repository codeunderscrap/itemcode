# Item Code Studio — Sprint Retrospective
**6–7 August 2026 · Agent 0 (Foundation) + Agents A–H, run in parallel**

Companion to `Sprint_Plan_v3.xlsx` (now carries this same retro, added
per-story, in five new columns on the Delivery Plan tab) and to each agent's
own handover in `agents/done/AGENT_*.md`. This document is the narrative
version: what happened, in what order, what worked, what didn't, and what
changed from the plan along the way.

---

## 1. Executive summary

| | Stories | Points |
|---|---:|---:|
| **Built and verified this session** | 52 | 240 |
| Pre-existing capability, touched/re-verified this session | 22 | 104 |
| Partially built (real, tested groundwork; not fully wired) | 2 | 13 |
| Still blocked on Anuraag (correctly not attempted) | 19 | 87 |
| Gap — in scope, flagged, deliberately not built | 2 | 7 |
| Deferred (backlog, correctly out of scope) | 2 | 8 |
| **Total tracked** | **99** | **459** |

Nine work packets (Foundation + A–H) ran across roughly nine hours of
elapsed session time, spanning two calendar days, two systemic
interruptions, and one security incident that was caught and fixed inside
the same session. Every packet reached a written handover
(`agents/done/AGENT_*.md`); the final integration run (86/86 smoke tests,
a live login→issue→edit→revert round trip, tier failover proven with real
processes) passed clean.

**The single most consequential open item**: `core/resolve.py`'s ERPNext
push path currently sends our own 889-group taxonomy name as ERPNext's
`item_group`. ERPNext only has ~23 groups. Every real push will be
correctly refused until an operator-facing sub-head→Item-Group mapping
exists on the create screen. Agent G found this, declined to invent the
mapping itself (per CONTRACTS.md rule 4 — never push our taxonomy), and
flagged it plainly. Nothing is broken; nothing has silently mismapped
anything; it simply isn't wired yet. See §6.

---

## 2. How "time taken" is reported, and why not more precisely

Sub-story timing was never tracked — each agent worked its whole packet as
one continuous task, not story-by-story with checkpoints, so a
story-level duration would be invented, not measured. What follows is
honestly measurable instead:

* **Tool calls and tokens per completed segment** — a real, granular
  measure of effort, reported per agent in §4.
* **Number of resumes** — how many times a packet had to be restarted
  after an interruption. This is the most informative "friction" number in
  this retro, and it wasn't a small one: **every one of the eight parallel
  agents needed at least one resume**, and four needed two or three.
* **Wall-clock span** is reported at the session level (§3), not
  per-agent, because it is dominated by two interruptions that had nothing
  to do with any agent's actual work — see §5.

---

## 3. Timeline

1. **Foundation (Agent 0), ~45 min, uninterrupted.** Schema migration
   (six new tables), the `server.py` → `routes/*.py` split, settings/ledger
   config, theme file. Landed clean; `agents/done/AGENT_0.md` written.
2. **All eight agents (A–H) launched simultaneously.** Per the original
   plan — genuinely parallel, since CONTRACTS.md's file ownership is
   disjoint by design.
3. **All eight stalled within ~600 seconds, producing zero file output.**
   A systemic failure — very likely eight concurrent heavy agents exceeding
   some shared capacity limit, not a fault in any individual brief. Every
   file on disk was still exactly what Agent 0 had left.
4. **Recovery: rebatched into groups of 3.** Resumed A, B, C first; once
   they were producing real, healthy output, brought back D, E, F; then G
   and H. This worked — no further systemic stalls.
5. **Two of the resumed agents (A, C) hit a second, unrelated failure**
   ("connection closed mid-response") right after landing real progress —
   a transient API disconnect, not a stall. Both resumed and completed
   normally.
6. **Security incident, caught mid-session:** Agent H's `HANDOVER.md`
   included a real, live admin bootstrap password in plaintext. Caught
   immediately on review, the password was rotated
   (`manage.py resetpw admin`) and invalidated, the document was redacted
   and rewritten to explain what happened rather than repeat the mistake,
   and the repo was swept for any other copy before anything was pushed
   anywhere. See §5.2.
7. **Hard stop: account-level monthly spend limit hit**, killing four
   agents simultaneously (E, G, H, and a resumed B) mid-task. Not a
   transient error — retrying immediately would have failed identically,
   so work paused rather than burning further attempts against it.
8. **Resumed once the limit was raised.** B finished its fix and verified
   it with a real Chrome round-trip; G and H finished their remaining work
   and each wrote a full handover. E's work was already complete on disk
   before its connection dropped — verified, not re-run.
9. **Final integration (Agent H):** clean `seed.py --rebuild` → server
   starts clean → 86/86 smoke tests → 401 sweep confirmed → live
   login→issue→edit→revert → group-move queue-claim proven → ERPNext
   confirmed off → tier failover/reconciliation proven with two real
   server processes → `HANDOVER.md` written.
10. **Retrospective + GitHub.** This document, the per-story columns in
    `Sprint_Plan_v3.xlsx`, and a private GitHub repository
    (`Saisam13/item-code-studio`) with real business data (`data/*.db`,
    `data/dict_cache.json`, `exports/*.xlsx`) excluded via `.gitignore` and
    a pre-push secret sweep.

---

## 4. Per-agent summary

| Agent | Stories built | Points | Resumes | Headline deliverable |
|---|---:|---:|---:|---|
| 0 Foundation | 5 | 26 | 0 | Schema, route split, settings/ledger config, theme |
| A Public | 5 | 20 | 2 | Split landing page, decoder, directory, dictionary |
| B Auth | 8 | 29 | 2 | Login/sessions, Settings+API key screen, closed the `X-User` hole |
| C Matching | 6 (+5 pre-existing extended) | ~26 | 2 | LLM-first inversion — rules shortlist + veto, deterministic fallback |
| D Engine | 5 (+6 pre-existing extended) | ~29 | 1 | Queue-claim numbering, `preview_code`, concurrency proof, leases |
| E Create | 6 (+6 pre-existing) | ~25 | 1 | Three-phase create screen, cascades, idempotent commit |
| F Master | 9 (+3 pre-existing) | ~50 | 1 | Editable master, versions, revert-respects-frozen-codes |
| G ERPNext | 6 built despite plan marking blocked (+3 pre-existing, 2 gaps) | ~34 | 2 | Closed-list guardrail, drift sync, failsafe reconciliation |
| H Deploy | 8 (+2 partial) | ~34 | 2 | 86/86 smoke suite, tier failover, installer, verified backup/restore |

*(Points above are approximate per-agent sums from the story table; several
stories split across two agents' files — e.g. K11/C4 — are counted once.)*

---

## 5. The two systemic incidents, in more detail

### 5.1 The 600-second stall

Launching eight heavy concurrent agents at once produced zero output from
any of them after ten minutes — not a slow start, a complete stall,
uniformly. Rebatching into groups of three resolved it immediately with no
further systemic failures for the rest of the session. **Lesson for next
time:** default to smaller concurrent batches (3–4) for heavy multi-tool
agent work in this environment, rather than assuming maximum parallelism is
free.

### 5.2 The exposed credential

Agent H's `HANDOVER.md` — a document explicitly meant to be read and kept —
included the real first-run admin password in plaintext, with a comment
telling the reader to change it "since it has been displayed in this
document." That reasoning is backwards: a document is exactly where a live
credential should never go, precisely because unlike a console it persists.

**What was done, in order:** the password was rotated immediately
(`manage.py resetpw admin`, old one invalidated); the document was rewritten
to explain the mistake and point at generating a fresh credential yourself
rather than repeat one in text; the whole repository was swept for any
other copy before anything was pushed to GitHub. No external exposure
occurred — this was caught before the document left the local machine.

**Lesson for next time:** an explicit instruction to any agent writing a
setup/handover document should state, up front, that a live credential is
handed to a person once, on a console, never written into a persistent
file — the general house rule ("secrets live in `settings`, never
`config.json`, never git") should have been read as covering *every*
document an agent writes, not only source files.

### 5.3 The spend-limit hard stop

A hard account-level cap, not a rate limit — four agents failed
simultaneously mid-task with an identical, unambiguous error. The
correct response was to stop immediately rather than retry (retrying
would have failed the same way against a still-exhausted limit) and wait
for the limit to be raised before resuming. Work resumed cleanly from each
agent's own transcript once it was — nothing had to be redone, including
work in a segment that failed mid-write, because most agents' actual file
writes had already landed on disk before the connection died.

---

## 6. What worked

* **Disjoint file ownership genuinely enabled real parallelism.** Once
  past the two interruptions above, nine agents editing one codebase
  concurrently produced no file-content conflicts — the CONTRACTS.md
  design held.
* **Agents caught each other's real bugs, unprompted.** This is the
  strongest signal in the whole session that the handover discipline
  worked as intended:
  - Agent F found the `Secure`-cookie-over-HTTP bug in Agent B's code
    while trying to test its own screen in a real browser, and reported it
    precisely enough that Agent B could fix it without re-deriving the
    problem.
  - Agent D's concurrency test caught a real race in its own
    `mint_from_lease` before anyone else could hit it.
  - Agent F's own revert testing caught a bug where `updated_at` was
    miscounted as a "restored field" — over-claiming a revert, the mirror
    image of the under-restoring failure the brief specifically warned
    against.
  - Agent G caught that its own new guardrail/validation code would have
    crashed two other agents' live call sites (`core/resolve.py`,
    `core/restructure.py`) by raising instead of returning a dict, and
    fixed its own code to match the existing contract rather than expect
    the other files to change.
  - Agent B found and partially mitigated a real `D.log()` commit gap in
    Agent 0's `core/db.py` (audit-log writes could hold SQLite's write
    lock indefinitely); Agent 0 then fixed it at the source once flagged,
    closing it for every caller at once instead of leaving each route
    module to remember its own workaround.
* **Verification was real, not asserted.** Nearly every handover cites a
  specific command, a specific curl call, or a specific browser
  interaction, not "should work." The frozen-code revert case, the lease
  concurrency race, and the tier failover were each proven with an actual
  running process, not reasoned about.
* **The deliberate design reversals (queue-claim numbering, LLM-first
  matching) landed coherently across three separate files owned by three
  different agents** (`core/codes.py`/D, `core/restructure.py`/D,
  `core/resolve.py`/C) with no drift between them — each one independently
  cross-referenced CONTRACTS.md rather than the older `Sprint_Plan_v3.xlsx`
  wording, which in places (see §7) still describes the pre-reversal design.

---

## 7. Challenges & deviations from the plan

* **`Sprint_Plan_v3.xlsx` itself is stale relative to `agents/CONTRACTS.md`
  on at least two rows.** `C4`/`K11` describe the old "vacated number held
  for a ≥88% name match" reservation rule as the target; CONTRACTS.md
  decision #5, dated the same day, explicitly reverses this to
  queue-claim/lowest-first. `H13` is marked `NEEDS INPUT`/Blocked in the
  plan, but the actual brief handed to Agent G instructed building it
  anyway, gated behind a setting default-off — which is what happened.
  Worth noting for future planning: when a plan and a later "frozen"
  contract disagree, the agents correctly followed the contract, but the
  plan document should be regenerated from it rather than read side by
  side.
* **CONTRACTS.md left several routes' ownership genuinely ambiguous**
  (`/api/group/move*`, `/api/groups`, `/settings/*` vs `/api/v1/settings`).
  Agent 0 made judgement calls and documented every one explicitly in its
  handover so later agents could override them; none did, but each said
  so plainly rather than silently accepting or silently changing them.
* **Several agents needed to touch a file outside their own ownership** to
  keep the whole system working — Agent B editing `core/dispatch.py`
  (Agent 0's) to close the `X-User` hole everywhere at once, Agent D
  fixing call sites in `routes/master.py` and `core/resolve.py` after
  changing a frozen function's signature. In every case this was flagged
  explicitly in the handover rather than done silently, per the house rule
  ("never delete or rewrite another agent's file… write it in your
  handover note instead") — the one partial exception being narrowly
  scoped, unavoidable call-site fixes that a signature change made
  strictly necessary, which is a different thing from a design change.
* **The ERPNext item-group mapping gap (§1) is a real, unresolved
  deviation from a working end-to-end system**, not a bug — it is the one
  place where "correctly refuses to guess" and "not yet buildable by
  anyone in this session" collide, since it needs a decision that isn't
  Agent G's, E's, or F's alone to make.

---

## 8. Open items — for Anuraag

In rough priority order:

1. **ERPNext `item_group` mapping.** Needed before any real ERPNext push
   will succeed. Needs an operator-facing field (sub-head → real ERPNext
   Item Group) on the create screen — not something any agent should
   invent unilaterally.
2. **`seed.py` non-determinism.** Agent 0 found that identical reruns of
   `seed.py --rebuild` from the same source files can land on either
   889/1,947 or 891/1,949 groups/items. Not caused by this session's
   changes; looks like Python's per-run string hash randomization
   affecting a `set`/`dict`-ordered dedup step. Worth a fix before it's
   mistaken for real data loss.
3. **`core/tier.py`'s continuous wiring into `server.py`, and the
   tier-to-tier shared secret**, are both built and tested standalone
   (two real server processes, real failover, real reconciliation) but
   not yet connected — a documented small patch to a file only Agent 0
   owns.
4. **Legacy un-versioned mutating routes remain unauthenticated**
   (`/api/commit`, `/api/resolve`, `/api/ingest`, `/api/erp/pull`, etc.) —
   deliberate, so the pre-existing UI didn't break mid-build, but a real
   exposure the moment this runs on a public VPS.
5. **The `Item Code Specification` slot-field name is an unconfirmed
   guess** (`item_specification_{slot}`) — check against live ERPNext
   doctype metadata before `erp.populate_specs` is ever turned on.
6. **No VPS exists yet.** `install/vps/` is written and vendor-neutral, not
   run against a real host — needs a host/domain decision first.
7. Everything already listed as blocked in `Sprint_Plan_v3.xlsx`'s
   `NEEDS INPUT` rows (19 stories, 87 points) is unchanged and correctly
   untouched — an API key, real invoices, ERP admin rights, and several
   judgement calls that are explicitly yours.

---

## 9. Where everything is

* Per-story retro: `Sprint_Plan_v3.xlsx` → Delivery Plan tab, five new
  columns after `Notes`.
* Full technical handover: `HANDOVER.md` (restart/restore/adduser/rotate-key
  runbook).
* Each agent's own account: `agents/done/AGENT_0.md` through `AGENT_H.md`.
* Source, private: `https://github.com/Saisam13/item-code-studio` (`main`).
* Your new admin password was sent to you directly in chat when it was
  rotated — not repeated here, per the lesson in §5.2.
