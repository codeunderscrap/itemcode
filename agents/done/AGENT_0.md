# AGENT 0 — done

## Schema

Six tables added to `core/db.py`, all `CREATE TABLE IF NOT EXISTS`, verified
non-destructive against the real seeded data:

`app_user` · `session` · `item_version` · `item_vacancy` · `llm_cache` ·
`sync_log` — exact column lists as frozen in `CONTRACTS.md` §5. All six
confirmed empty after migration.

`db.get_setting(con, key, default=None)` added (the old name `setting` is
kept as an alias, only `server.py`/`seed.py` used it and both are fine).
`db.set_setting(con, key, value)` unchanged. `db.rows(con, sql, args)` and
`db.one(con, sql, args)` also added — small dict-row helpers every route
module needs; there was no shared home for them before.

`python seed.py --rebuild` completes clean. **Heads up for whoever owns
matching/dedup (Agent C, `core/matcher.py`/`core/resolve.py`, or Agent D's
group numbering):** across five consecutive `--rebuild` runs I saw the group
count land on either 889 or 891, and items on 1947 or 1949, with everything
else identical (46 sub-heads, 2,490 spec values, 2,677 ERP codes always
stable). This is pre-existing — I did not touch `seed.py`'s loading logic —
and looks like something in the fuzzy-dedup path is order-sensitive (likely
a `set`/`dict` iteration relying on Python's per-run string hash
randomization). Worth a look since it means two rebuilds from the *same*
source files can silently produce a different taxonomy. The DB is currently
left freshly rebuilt at the documented baseline: **889 groups, 1,947 items,
2,677 ERP codes, 2,946 reserved codes.**

## Route split

`server.py` is now a 91-line process-entry-point: load config, open the one
shared DB connection, build `Matcher`/`ERP`, populate `core/context.py`'s
`ctx`, import the six route modules, build the router, start listening.
Nothing else. HTTP protocol mechanics (the request/response plumbing,
multipart parsing, `<param>` path matching, the `BaseHTTPRequestHandler`
itself) live in **`core/dispatch.py`** — new, not in CONTRACTS.md's file
list, but it's plumbing, not a route, so I judged it fair game rather than
bloat `server.py` past the ~120-line target. `core/api.py` (new) holds
`ok()`/`err()`/`ApiError` — the two response envelopes plus the
`ERROR_STATUS` map for the ten error codes in §6. `core/context.py` (new)
is the shared `ctx` singleton (`con`, `lock`, `cfg`, `matcher`, `erp`,
`root`, `web`) every route module reads instead of building its own DB
connection.

Every route handler now has the signature `handler(req) -> result`, where
`req` (defined in `core/dispatch.py`) carries `.query`, `.params` (path
`<name>` captures), `.body` (parsed JSON), `.fields`/`.files` (multipart),
`.user`, `.headers`. A handler returns a plain dict/list for a `200` JSON
response, or a tuple `(status, body[, content_type[, extra_headers]])` for
anything else (used by `/api/download/<file>` for the XLSX headers). Raise
`core.api.ApiError` for a typed error; anything else raised is caught
centrally, logged server-side with `traceback.print_exc()`, and the client
gets `{"ok": false, "error": {"code": "INTERNAL", ...}}` — verified with a
deliberately malformed request, no traceback text reaches the client.

**Old un-versioned `/api/...` paths were kept exactly as they were** — same
URL, same response shape (unwrapped, not the new envelope) — because
`web/app.js` still consumes them directly and none of this was worth
redesigning yet. Only the *unhandled-exception* path changed shape, which is
what the brief's done-when criterion asks for. I did not add `/api/v1/...`
equivalents; each of you defines your own real v1 routes per CONTRACTS §6 and
this is where you land your actual work.

### Where each old handler went

| Module | Owner | Paths moved in |
|---|---|---|
| `routes/public.py` | A | `GET /api/bootstrap`, `/api/groups`, `/api/group/<id>`, `/api/decode` |
| `routes/auth.py` | B | none — empty `ROUTES`, clean start |
| `routes/create.py` | E | `POST /api/resolve`, `/api/resolve_batch`, `/api/commit`, `/api/ingest`, `/api/alias/add` |
| `routes/master.py` | F | `GET /api/master`, `/api/audit`, `/api/mappings`, `/api/vacancies`, `/api/export`, `/api/download/<file>`; `POST /api/item/update`, `/api/group/move/preview`, `/api/group/move`, `/api/group/merge`, `/api/group/delete`, `/api/rename`, `/api/group/labels`, `/api/specval/add`, `/api/head/add`, `/api/subhead/add`, `/api/group/add` |
| `routes/erp.py` | G | `GET /api/erp/ping`, `POST /api/erp/pull` |
| `routes/meta.py` | H | none — empty `ROUTES`, clean start |

**Judgement calls you may want to revisit:** CONTRACTS §6 doesn't name an
owner for `/api/groups`/`/api/group/<id>` (put with A, since they're
read-only directory/dictionary browsing) or for the taxonomy-mutation
endpoints — `group/move*`, `merge`, `delete`, `rename`, `labels`,
`specval/add`, `head/add`, `subhead/add`, `group/add` (put with F, closest
to "editable master"). I didn't touch the logic inside them
(`core/restructure.py` calls are untouched — that's Agent D's file), only
which module's `ROUTES` list registers the route. If you disagree, move the
registration — the handler functions themselves are trivial wrappers around
your own core module and cost nothing to relocate.

## Settings + ledger config

`config.json` gained:

```json
"ledger": {"mode": "local_server", "server_url": "", "local_url": "", "lease_size": 10}
```

Defaulted to `local_server` per the brief — the VPS doesn't exist yet.
`core/db.py`'s `connect()` docstring now says plainly what it promises (opens
SQLite directly, valid for `server`/`local_server` modes only) and explicitly
hands the three-tier resolution + `client`-mode HTTPS proxying to Agent H —
I did not build that, only left the config keys in place for it.

## Theme + assets

`web/theme.css` — the nine MiniMines CSS variables (`--mm-navy` etc.) plus a
`[data-theme='light']` override, Barlow → Segoe UI → system-ui fallback
stack. No rules, only variables, so it can't visually change anything by
itself; linked into `web/index.html` ahead of `style.css` so it's live but
inert until Agent A (or anyone) starts consuming the variables.
`web/assets/minimines-logo.svg` copied verbatim from
`C:\Users\Anura\ATT_Platform\frontend\public\minimines-logo.svg`.

## Verified

* `python seed.py --rebuild` — completes, DB left at 889/1,947/2,677 (see
  the non-determinism note above).
* `python server.py` starts clean, banner prints correctly, static `/` and
  `/theme.css` serve, and every relocated endpoint answers identically to
  before: `/api/bootstrap`, `/api/decode?code=RMBS0010206100007` (still
  decodes correctly including the interior `00`), `/api/group/1`,
  `/api/master`, `/api/vacancies`, `/api/export` (writes a real workbook),
  `/api/download/<file>` (correct XLSX content-type + attachment header),
  and a mutating write (`/api/group/add`) — tested, then the DB was
  rebuilt again to discard that test row.
* A malformed JSON POST and an unknown path both return the new
  `{"ok":false,"error":{"code":...}}` envelope, no traceback.
* All 20 touched `.py` files byte-compile cleanly.

## Nothing needed from Anuraag yet

Everything on this packet builds without him. The one open question is the
seed non-determinism above — not blocking, just worth someone's attention
before it's mistaken for a real data problem later.
