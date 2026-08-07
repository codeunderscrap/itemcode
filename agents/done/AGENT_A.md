# AGENT A — done

## What I built

`web/public.html` + `web/public.js` + `web/public.css` — a login-free split
landing page: MiniMines logo/header with a **Log in** link to `/login.html`
(Agent B's page — I only link to it, I don't touch it further), then two
equal-weight panels: **Decode a code** on the left, **Find an item /
Dictionary** (tabbed) on the right. Stacks to one column under 860px —
verified via `getComputedStyle(...).gridTemplateColumns` at innerWidth 767,
not just eyeballed.

`routes/public.py` — added five real `/api/v1` routes alongside the
pre-existing un-versioned ones (left untouched, byte-for-byte, so
`web/app.js` keeps working):

* `GET /api/v1/decode?code=`
* `GET /api/v1/directory?q=&limit=&offset=`
* `GET /api/v1/directory/<code>`
* `GET /api/v1/dictionary/groups?q=&limit=&offset=`
* `GET /api/v1/dictionary/group/<id>`

All five go through `core.api.ok()`/`ApiError` — proper envelope, proper
error codes. Two shared helpers, `_group_by_prefix()` and `_decode_specs()`,
back both the old `decode()` handler and the new `decode_v1()` so there is
exactly one place that turns a parsed code + its group row into labelled
specs — no drift between old and new. Every handler calls
`core.codes.structural_parse`; nothing reimplements code grammar.

## Verified against the real seeded DB (889 groups, 1,947 items)

A server from another agent's session was already live on :8756 (picked up
my file changes live — static files and route table both current), so I
verified end-to-end over real HTTP, not just unit-style calls, plus a full
browser pass:

* `RMBS0010206100007` → `Raw Materials · Battery Scrap · Battery Pack ·
  02 Form Factor Cylindrical · 06 Chemistry NMC · 10 Size Gen 3 Battery Pack
  without (...) · 00 Capacity (not applicable) · 07 Maker LG` — matches the
  brief's verification line exactly, screenshotted in-browser.
* Malformed length: `RMBS00102061000070` (18 chars) →
  `{"code":"BAD_CODE","message":"18 characters expected 7, 9, 11, 13, 15 or 17"}`
* Malformed shape (digits where head letters go) → plain-English BAD_CODE,
  not a bare code.
* Well-formed + known group + no item → `issued:false` with a note; verified
  against a real empty prefix (`AOHK001`).
* Well-formed but unknown group (`ZZZZ999`) → `known:false` + note, doesn't
  error.
* Directory search: `odonil` → 7 hits, `battery` → 47 hits, **`mseal` → 1
  hit** (`RMCH0520201 "Solvent - M-seal CPVC"`) — the last one only works
  because the search squashes non-alphanumerics on both sides
  (`core.matcher.normalize` keeps the hyphen in "M-SEAL", so a plain
  substring or that normalizer wouldn't have found it; see "thin" below).
* Item detail modal for `AOHK0010603` shows slot 1 labelled **"Type"**;
  group detail modal for Battery Pack (id 807) shows slot 1 labelled **"Form
  Factor"** — same slot number, different label, read from that group's own
  `labels` JSON in both cases. This is the thing the brief calls out as
  easy to get subtly wrong, so I checked it explicitly, in-browser, not just
  in the API response.
* Dictionary group modal renders all four slots with codes and values, `00`
  shown as `(not applicable)` there too, not just in the decoder.
* Pagination: default 20/page, prev/next disable correctly at the edges,
  "1–1 of 1" etc.
* Directory and dictionary search both debounce at 200ms; live decode also
  debounces (250ms) as a UX nicety, plus Enter/click still work immediately.

**Stats block removed.** The three `.stat` divs (`cItems`/`cGroups`/`cErp`)
are gone from `web/index.html`'s `.railfoot`, and their population lines
from `web/app.js`'s `boot()`. `.railfoot` still has `margin-top:auto` inside
a flex column, so `whoBtn`/`erpChip` stay pinned to the bottom of the rail
exactly as before — nothing shifted, no hole. `#seedInfo` (the "N codes
reserved" line under the app name, unrelated to the removed stats) was left
alone.

**Read-only enforced.** `grep -in "INSERT\|UPDATE \|DELETE FROM\|require_session"
routes/public.py` matches only the doc-comment sentence describing the rule
— zero writes, zero session checks, anywhere in the file.

## Design choices worth flagging

* **Search normalisation.** I did *not* reuse `core.matcher.normalize()` for
  the directory/dictionary search box. That function is tuned for the
  fuzzy-matching pipeline (tokenises, applies domain synonyms, keeps
  internal hyphens) and `normalize("M-Seal")` stays `"M-SEAL"` — a plain
  substring test against `normalize("mseal")` (`"M SEAL"`, via the MSEAL
  synonym) still fails on the hyphen-vs-space mismatch. Since the
  done-when criterion is literally "mseal must find M-Seal", I wrote a
  much smaller local `_norm()`: squash both sides to bare
  `[A-Z0-9]` before comparing. This is a search-box concern, not code
  decoding, so it doesn't touch the "don't reimplement decoding" rule —
  but it does mean the public search box and the internal matcher use two
  different normalisation ideas of "the same text." Worth a look if anyone
  later wants one canonical normaliser for both.
* **Directory/dictionary search is a full Python table scan** per request
  (1,947 items / 889 groups), not a SQL `LIKE`. Trivial at this size and
  the only way to get punctuation-insensitive matching without a custom
  SQLite function; would need revisiting if the catalogue grows by orders
  of magnitude.
* **`next_group_code` in `core/codes.py` (Agent D's file) still does the
  old ≥88%-similarity vacancy reuse**, not the queue/lowest-first rule
  CONTRACTS §4 now mandates. I didn't touch it — not mine — but flagging
  it since I read that file closely while wiring the decoder and it's a
  live contradiction between the code and the frozen contract.
* Old `/api/decode` output is unchanged byte-for-byte (same keys, same
  values) — I refactored its internals to share helpers with the new v1
  route but did not change what it returns.

## Nothing needed from Anuraag

Everything here builds and runs against the current seed with no open
questions on my side.
