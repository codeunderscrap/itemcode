"""Item version history — Agent F.

Nothing about an item is ever overwritten in place. Every save (a field edit,
a revert, or the very first snapshot we can get of a legacy row) appends a
new row to `item_version`. A snapshot is the WHOLE item row, not a diff —
diffs are computed on read, for display, because reconstructing a row from a
chain of diffs breaks completely the moment one link in that chain is wrong.
A snapshot never has that failure mode: version 7 is readable even if every
version before it vanished.

    snapshot(con, item_id, user, summary) -> version_no
    versions(con, item_id)                -> [ {version_no, snapshot, ...}, ... ]  desc
    timeline(con, item_id)                -> versions(), each with a `diff` vs the version before it
    revert(con, item_id, version_no, user) -> dict, see revert() docstring

FREEZE-ON-FIRST-USE interacts with revert here, and it is the subtle part of
this module (agents/AGENT_F_MASTER.md task 4): a code that is live in
ERPNext is a submitted, immutable Frappe document. `revert()` restores every
field it can, leaves `code` untouched when the item is frozen, and always
says so in plain words — a silent partial revert is worse than none, because
someone who believes they undid something that is still live will act on
that belief.
"""
import json

from . import db as D

# Columns that are provenance or pure bookkeeping, not restorable state —
# `id` is the row's identity, not a field; `created_at`/`created_by` record
# who first made the row and a revert never rewrites that; `updated_at` is
# always re-stamped by the revert itself (it just wrote the row), never
# copied backwards out of an old snapshot — otherwise it would inflate the
# restored-field count with a column nobody asked to restore.
_NEVER_RESTORE = {"id", "created_at", "created_by", "updated_at"}

# The one column that is permanent once the item has shipped to ERPNext.
# Restoring an old snapshot never rewrites this while the item is frozen —
# CONTRACTS.md decision 6, "freeze on first use".
_FROZEN_GUARD = "code"


def _item_row(con, item_id):
    return D.one(con, "SELECT * FROM item WHERE id=?", (item_id,))


def _is_frozen(row):
    return bool(row.get("frozen")) or row.get("status") == "in_erp"


def _next_version_no(con, item_id):
    r = con.execute("SELECT MAX(version_no) m FROM item_version WHERE item_id=?",
                     (item_id,)).fetchone()
    return (r["m"] or 0) + 1


def snapshot(con, item_id, user, summary=""):
    """Write the item's CURRENT row as a new version. Returns the version_no.

    Called after every write to `item` that this app makes — a field edit,
    a revert (which is itself just a write, and so is itself revertible),
    or a fresh baseline for a row that predates this feature.
    """
    it = _item_row(con, item_id)
    if not it:
        raise ValueError(f"no such item id={item_id}")
    vno = _next_version_no(con, item_id)
    con.execute(
        """INSERT INTO item_version(item_id, version_no, snapshot, changed_by, changed_at, summary)
           VALUES (?,?,?,?,?,?)""",
        (item_id, vno, json.dumps(it, default=str), user or "system", D.now(), summary or ""))
    return vno


def ensure_baseline(con, item_id, user="system"):
    """If this item has no history yet, snapshot its current row as version 1.

    1,947 items already existed before this feature shipped, so "version 1
    is the original" can only mean the earliest state we actually have a
    record of. This is called lazily, on the first edit or the first time
    the version panel is opened for a given item, so every item gets a
    version 1 without a slow one-off migration touching all 1,947 rows.
    Returns True if a baseline was just created, False if history already
    existed.
    """
    has = con.execute("SELECT 1 FROM item_version WHERE item_id=? LIMIT 1",
                       (item_id,)).fetchone()
    if has:
        return False
    snapshot(con, item_id, user, "baseline — earliest version this app has on record")
    return True


def versions(con, item_id):
    """All versions for an item, newest first, snapshot already decoded."""
    rows = D.rows(con,
        """SELECT id, item_id, version_no, snapshot, changed_by, changed_at, summary
           FROM item_version WHERE item_id=? ORDER BY version_no DESC""", (item_id,))
    for r in rows:
        r["snapshot"] = json.loads(r["snapshot"])
    return rows


def diff_fields(before, after):
    """Field-level before/after between two snapshot dicts. Skips identity
    and pure-bookkeeping columns — nobody needs a diff row telling them
    updated_at changed."""
    skip = {"id", "updated_at"}
    keys = sorted((set(before or {}) | set(after or {})) - skip)
    out = []
    for k in keys:
        b, a = (before or {}).get(k), (after or {}).get(k)
        if b != a:
            out.append({"field": k, "before": b, "after": a})
    return out


def timeline(con, item_id):
    """versions(), newest first, each annotated with its diff against the
    version immediately before it — what the Activity screen renders."""
    vs = versions(con, item_id)                      # newest first
    asc = list(reversed(vs))
    prev_snap = None
    for v in asc:
        v["diff"] = diff_fields(prev_snap, v["snapshot"]) if prev_snap is not None else []
        prev_snap = v["snapshot"]
    return list(reversed(asc))


def revert(con, item_id, version_no, user):
    """Restore item `item_id` to the contents of `version_no`.

    Does NOT delete or touch versions after version_no — it writes their
    contents as a brand-new version n+1, so a revert is itself revertible
    and history only ever grows.

    If the item is frozen (live in ERPNext, or flagged `frozen`), `code` is
    excluded from what gets restored — a submitted Frappe document cannot
    be rewritten — and the result says exactly that, plainly, rather than
    silently leaving the operator to assume everything came back.
    """
    cur = _item_row(con, item_id)
    if not cur:
        raise ValueError(f"no such item id={item_id}")
    ver = D.one(con, "SELECT * FROM item_version WHERE item_id=? AND version_no=?",
                (item_id, version_no))
    if not ver:
        raise ValueError(f"item {item_id} has no version {version_no}")
    target = json.loads(ver["snapshot"])

    frozen = _is_frozen(cur)
    skipped_frozen = []
    candidate = dict(target)
    for col in _NEVER_RESTORE:
        candidate.pop(col, None)
    if frozen and candidate.get(_FROZEN_GUARD) != cur.get(_FROZEN_GUARD):
        skipped_frozen.append(_FROZEN_GUARD)
    if frozen:
        candidate.pop(_FROZEN_GUARD, None)

    changed = {}
    sets, args = [], []
    for k, v in candidate.items():
        if cur.get(k) != v:
            sets.append(f"{k}=?")
            args.append(v)
            changed[k] = {"before": cur.get(k), "after": v}

    if sets:
        sets.append("updated_at=?")
        args.append(D.now())
        args.append(item_id)
        con.execute(f"UPDATE item SET {', '.join(sets)} WHERE id=?", args)

    n = len(changed)
    summary = f"reverted to version {version_no} — {n} field{'s' if n != 1 else ''} restored"
    if skipped_frozen:
        summary += f"; code {cur.get('code')} left unchanged (frozen, live in ERPNext)"
    new_vno = snapshot(con, item_id, user, summary)

    D.log(con, user, "revert-item", cur.get("code"),
          {"to_version": version_no, "new_version": new_vno,
           "restored_fields": sorted(changed.keys()), "skipped_frozen": skipped_frozen})
    con.commit()

    message = f"Restored {n} field{'s' if n != 1 else ''}."
    if skipped_frozen:
        message += (f" The code {cur.get('code')} was not changed — it is live in "
                     f"ERPNext and is permanent.")

    return {
        "ok": True,
        "item_id": item_id,
        "code": cur.get("code"),
        "reverted_to_version": version_no,
        "new_version": new_vno,
        "restored_fields": sorted(changed.keys()),
        "field_count": n,
        "skipped_frozen": skipped_frozen,
        "message": message,
    }
