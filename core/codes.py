"""Code grammar: assembly, parsing, prefix minting, next-free numbering.

    HEAD(2) SUB(2) GROUP(3) [S1(2)][S2(2)][S3(2)][S4(2)][VENDOR(2)]

Rules locked by the business:
  * a missing spec that sits BETWEEN two present segments is written as "00"
  * missing segments at the TAIL are dropped, the code just ends
  * vendor occupies the last position, so any code carrying a vendor is
    necessarily the full 17 characters (all four spec slots materialised)
  * therefore valid lengths are exactly 7, 9, 11, 13, 15, 17

Numbering is QUEUE-claim, lowest-first (CONTRACTS.md §4, reversing the
earlier semantic-matching design): a number freed by a move is a free slot,
not a reservation. The next arrival - of any name - takes the lowest free
number. `grp_vacancy.former_name` / `item_vacancy.former_item` are kept for
DISPLAY only ("041, freed by Tissue") and never influence who gets a number.
"""
import json
import re
import sqlite3
import time

from . import db as D

VALID_LENGTHS = (7, 9, 11, 13, 15, 17)
CODE_RE = re.compile(r"^([A-Z]{2})([A-Z]{2})(\d{3})((?:\d{2}){0,5})$")

STOPWORDS = {"AND", "OF", "THE", "FOR", "A", "AN", "&"}


# ---------------------------------------------------------------- assembly
def assemble(head2, sub2, grp3, slots, vendor=None):
    """slots = list of up to 4 two-digit strings (or None); vendor = '07' or None."""
    slots = list(slots or [])
    slots += [None] * (4 - len(slots))
    tail = slots[:4] + [vendor]
    last = -1
    for i, v in enumerate(tail):
        if v not in (None, ""):
            last = i
    out = f"{head2}{sub2}{grp3}"
    for i in range(last + 1):
        v = tail[i]
        out += "00" if v in (None, "") else str(v).zfill(2)
    return out


def structural_parse(code):
    """Split a code into segments without consulting the dictionary."""
    code = (code or "").strip().upper()
    m = CODE_RE.match(code)
    if not m or len(code) not in VALID_LENGTHS:
        return None
    head2, sub2, grp3, rest = m.groups()
    segs = [rest[i:i + 2] for i in range(0, len(rest), 2)]
    segs += [None] * (5 - len(segs))
    return {
        "head": head2, "sub": sub2, "group": grp3,
        "s1": segs[0], "s2": segs[1], "s3": segs[2], "s4": segs[3],
        "vendor": segs[4], "length": len(code),
    }


def is_wellformed(code):
    return structural_parse(code) is not None


# ------------------------------------------------------------ prefix minting
def _words(name):
    return [w for w in re.split(r"[^A-Za-z0-9]+", (name or "").upper())
            if w and w not in STOPWORDS]


def prefix_candidates(name):
    """Ordered two-letter candidates for a head or sub-head name.

    'Admin & Office'        -> AO, AD, AF, AM, AI, ...
    'Equipment & Machinery' -> EM, EQ, EU, EP, ...
    'Consumables'           -> CO, CN, CS, CM, ...
    """
    w = _words(name)
    if not w:
        return []
    out = []

    def push(c):
        if len(c) == 2 and c.isalpha() and c not in out:
            out.append(c)

    if len(w) >= 2:
        push(w[0][0] + w[1][0])
    push(w[0][:2])
    if len(w) >= 3:
        push(w[0][0] + w[2][0])
    # first letter + each later letter of word 1  (EQ -> EU -> EP ...)
    for ch in w[0][1:]:
        push(w[0][0] + ch)
    # first letter + each letter of word 2
    if len(w) >= 2:
        for ch in w[1]:
            push(w[0][0] + ch)
    # last resort: first letter + A..Z
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        push(w[0][0] + ch)
    return out


def mint_head_code(con, name):
    taken = {r["code2"] for r in con.execute("SELECT code2 FROM head")}
    for c in prefix_candidates(name):
        if c not in taken:
            return c
    raise ValueError("no free 2-letter head code")


def mint_subhead_code(con, head_id, name):
    """Sub-head codes only need to be unique *within* the head, but the
    resulting 4-letter prefix must be globally unique - that is what the
    collision check actually enforces."""
    hd = con.execute("SELECT code2 FROM head WHERE id=?", (head_id,)).fetchone()
    if not hd:
        raise ValueError("unknown head")
    h2 = hd["code2"]
    taken = {h2 + r["code2"] for r in
             con.execute("SELECT code2 FROM subhead WHERE head_id=?", (head_id,))}
    global_taken = {r["p"] for r in con.execute(
        "SELECT h.code2||s.code2 AS p FROM subhead s JOIN head h ON h.id=s.head_id")}
    global_taken |= {r["code"][:4] for r in
                     con.execute("SELECT code FROM erp_item WHERE length(code)>=7")}
    for c in prefix_candidates(name):
        if (h2 + c) not in taken and (h2 + c) not in global_taken:
            return c
    raise ValueError("no free 2-letter sub-head code")


# ------------------------------------------------------- numbering / vacancy
def next_group_code(con, subhead_id):
    """Lowest free 3-digit group number inside a sub-head, vacancies
    included - no semantic test anywhere. Returns (code3, freed_from_name)
    where freed_from_name is display-only (None if this number was never
    vacated, i.e. it is simply the next fresh number)."""
    used = {r["code3"] for r in con.execute(
        "SELECT code3 FROM grp WHERE subhead_id=? AND status='active'", (subhead_id,))}
    leased = _lease_blocked(con, ("group", subhead_id))
    blocked = used | leased
    n = 1
    while f"{n:03d}" in blocked:
        n += 1
    if n > 999:
        raise ValueError("sub-head is full (999 groups)")
    code3 = f"{n:03d}"
    vac = con.execute(
        "SELECT former_name FROM grp_vacancy WHERE subhead_id=? AND code3=? AND released=0",
        (subhead_id, code3)).fetchone()
    return code3, (vac["former_name"] if vac else None)


def release_group_vacancy(con, subhead_id, code3):
    """Mark a group vacancy consumed once something has actually claimed the
    number. Purely bookkeeping for display - never called to decide who gets
    a number."""
    con.execute("UPDATE grp_vacancy SET released=1 WHERE subhead_id=? AND code3=?",
                (subhead_id, code3))


def _item_position_of(con, item_row):
    """The 8-digit position string (s1..s4, '00' for any unused/undetermined
    slot) an item currently occupies inside its group - the part of the code
    that sits between the group number and the vendor digit."""
    codes = []
    for col in ("s1", "s2", "s3", "s4"):
        sid = item_row[col]
        if sid:
            r = con.execute("SELECT code2 FROM specval WHERE id=?", (sid,)).fetchone()
            codes.append(r["code2"] if r else "00")
        else:
            codes.append("00")
    return "".join(codes)


def _item_position_and_values(con, item_row):
    """Same as _item_position_of, but also returns the human-readable value
    of each slot (or None), for the vacancy's display-only spec_tuple."""
    codes, vals = [], []
    for col in ("s1", "s2", "s3", "s4"):
        sid = item_row[col]
        if sid:
            r = con.execute("SELECT code2,value FROM specval WHERE id=?", (sid,)).fetchone()
            codes.append(r["code2"] if r else "00")
            vals.append(r["value"] if r else None)
        else:
            codes.append("00")
            vals.append(None)
    return "".join(codes), vals


def next_item_position(con, grp_id, spec_tuple=None):
    """Lowest free position for an item inside a group, vacancies included -
    the same rule as next_group_code, one level down.

    spec_tuple: the (s1, s2, s3, s4) 2-digit codes already pinned down by
    spec-value matching, `None` for any slot the group leaves undetermined.
    Most items are fully identified by their spec values, and for those this
    just returns that combination - the position IS the spec identity, and
    two items can never collide here because each distinct value already has
    its own code2 (see next_spec_code).

    The only case this actually allocates a number is a slot the matcher
    could not pin down (most commonly a group with no distinguishing spec at
    all, where every item would otherwise land on the same all-'00' tail): a
    running serial is minted into the first undetermined slot, lowest free
    value first, so otherwise-identical items still get distinct codes.
    """
    codes = list(spec_tuple or [])
    codes += [None] * (4 - len(codes))
    codes = codes[:4]

    def _fmt(cs):
        return "".join("00" if c in (None, "") else str(c).zfill(2) for c in cs)

    if all(c not in (None, "") for c in codes):
        return _fmt(codes)

    idx = next(i for i, c in enumerate(codes) if c in (None, ""))
    used = {_item_position_of(con, dict(r)) for r in
            con.execute("SELECT s1,s2,s3,s4 FROM item WHERE grp_id=?", (grp_id,))}
    leased = _lease_blocked(con, ("item", grp_id))

    n = 1
    while True:
        trial = codes[:]
        trial[idx] = f"{n:02d}"
        cand = _fmt(trial)
        if cand not in used and cand not in leased:
            return cand
        n += 1
        if n > 99:
            raise ValueError("group position space exhausted (99 values)")


def release_item_vacancy(con, grp_id, position):
    """Mark an item vacancy consumed once an item actually claims that
    position. Display-only bookkeeping, same spirit as release_group_vacancy."""
    con.execute("UPDATE item_vacancy SET released=1 WHERE grp_id=? AND position=?",
                (grp_id, position))


def free_item_position(con, grp_id, position, spec_values, former_item_name):
    """Record that an item has left `position` inside `grp_id` - called only
    when the item's code was never live in ERPNext (freeze-on-first-use: a
    frozen item frees nothing, it did not leave, only its classification
    did). The freed position becomes immediately available to the next item
    there, queue-claim, via next_item_position."""
    con.execute("""INSERT OR IGNORE INTO item_vacancy(grp_id,position,spec_tuple,former_item,ts)
                   VALUES(?,?,?,?,?)""",
                (grp_id, position, json.dumps(spec_values), former_item_name, D.now()))


def next_spec_code(con, grp_id, slot):
    used = {r["code2"] for r in con.execute(
        "SELECT code2 FROM specval WHERE grp_id=? AND slot=?", (grp_id, slot))}
    n = 1
    while f"{n:02d}" in used:
        n += 1
    if n > 99:
        raise ValueError("spec slot is full (99 values)")
    return f"{n:02d}"


def code_is_free(con, code):
    if con.execute("SELECT 1 FROM item WHERE code=?", (code,)).fetchone():
        return False
    if con.execute("SELECT 1 FROM erp_item WHERE code=?", (code,)).fetchone():
        return False
    if con.execute("SELECT 1 FROM code_ledger WHERE code=?", (code,)).fetchone():
        return False
    return True


def list_vacancies(con):
    """Group-level and item-level vacancies, each with what freed it and
    when - Agent F displays this. Presented as 'next free number', not
    'reserved for', because the queue-claim rule means it is not held for
    anyone in particular."""
    out = []
    for r in con.execute("""
            SELECT v.subhead_id, v.code3, v.former_name, v.ts,
                   s.name AS sub_name, h.name AS head_name,
                   h.code2 AS head_code, s.code2 AS sub_code
            FROM grp_vacancy v JOIN subhead s ON s.id=v.subhead_id
                               JOIN head h ON h.id=s.head_id
            WHERE v.released=0 ORDER BY h.code2, s.code2, v.code3"""):
        out.append({
            "level": "group",
            "scope": f"{r['head_name']} / {r['sub_name']}",
            "prefix": f"{r['head_code']}{r['sub_code']}",
            "number": r["code3"],
            "freed_by": r["former_name"],
            "freed_at": r["ts"],
        })
    for r in con.execute("""
            SELECT v.grp_id, v.position, v.former_item, v.ts,
                   g.name AS grp_name, g.code3, s.name AS sub_name,
                   h.name AS head_name, h.code2 AS head_code, s.code2 AS sub_code
            FROM item_vacancy v JOIN grp g ON g.id=v.grp_id
                                JOIN subhead s ON s.id=g.subhead_id
                                JOIN head h ON h.id=s.head_id
            WHERE v.released=0 ORDER BY h.code2, s.code2, g.code3, v.position"""):
        out.append({
            "level": "item",
            "scope": r["grp_name"],
            "prefix": f"{r['head_code']}{r['sub_code']}{r['code3']}",
            "number": r["position"],
            "freed_by": r["former_item"],
            "freed_at": r["ts"],
        })
    return out


# -------------------------------------------------------------- preview_code
def _resolve_slot(con, grp_id, slot, entry):
    """entry may be: None (not chosen); an int (an existing specval.id);
    or a str (a value - matched exactly within grp+slot if it already
    exists, otherwise previewed as the code next_spec_code WOULD mint, with
    no write). Returns (value_text or None, code2 or None, is_new)."""
    if entry in (None, ""):
        return None, None, False
    if isinstance(entry, int) or (isinstance(entry, str) and entry.isdigit() and len(entry) > 2):
        r = con.execute("SELECT value,code2 FROM specval WHERE id=?", (int(entry),)).fetchone()
        if r:
            return r["value"], r["code2"], False
        return None, None, False
    text = str(entry).strip()
    r = con.execute("SELECT value,code2 FROM specval WHERE grp_id=? AND slot=? AND value=?",
                    (grp_id, slot, text)).fetchone()
    if r:
        return r["value"], r["code2"], False
    return text, next_spec_code(con, grp_id, slot), True


def preview_code(con, head_id, subhead_id, grp_id, slots, vendor):
    """Pure - no writes, no side effects, safe on every keystroke. Builds
    the code exactly as assemble() would and, for the create screen and the
    master, explains each position: why it holds what it holds, or why it is
    '00' (the slot is not applicable to this group) or omitted (past the
    last defined position - trailing gaps are dropped, not zero-filled)."""
    explain = []
    head = D.one(con, "SELECT * FROM head WHERE id=?", (head_id,)) if head_id else None
    sub = D.one(con, "SELECT * FROM subhead WHERE id=?", (subhead_id,)) if subhead_id else None
    grp = D.one(con, "SELECT * FROM grp WHERE id=?", (grp_id,)) if grp_id else None

    explain.append({"segment": "head", "value": head["name"] if head else None,
                    "code": head["code2"] if head else None})
    explain.append({"segment": "sub-head", "value": sub["name"] if sub else None,
                    "code": sub["code2"] if sub else None})
    explain.append({"segment": "group", "value": grp["name"] if grp else None,
                    "code": grp["code3"] if grp else None})

    if not (head and sub and grp):
        missing = [n for n, v in (("head", head), ("sub-head", sub), ("group", grp)) if not v]
        return {"code": "", "length": 0, "valid": False,
                "explain": explain + [{"segment": "blocked",
                                       "reason": f"no {', '.join(missing)} chosen yet"}]}

    labels = json.loads(grp["labels"] or "{}")
    slots = list(slots or [])
    slots += [None] * (4 - len(slots))
    slot_codes = []
    for i in range(4):
        slot_no = i + 1
        label = labels.get(str(slot_no))
        if not label:
            slot_codes.append(None)
            explain.append({"segment": f"slot {slot_no}", "label": None, "value": None,
                            "code": "00", "reason": f"slot {slot_no} is not applicable for "
                                                     f"{grp['name']} - written as 00 if a later "
                                                     "position is used, dropped otherwise"})
            continue
        value, code2, is_new = _resolve_slot(con, grp["id"], slot_no, slots[i])
        slot_codes.append(code2)
        if value is None:
            explain.append({"segment": f"slot {slot_no}", "label": label, "value": None,
                            "code": None, "reason": f"'{label}' not chosen yet"})
        else:
            explain.append({"segment": f"slot {slot_no}", "label": label, "value": value,
                            "code": code2, "reason": "new value - code previewed, not yet minted"
                            if is_new else "matched existing value"})

    vend_code = None
    if labels.get("vendor"):
        if vendor not in (None, ""):
            value, vend_code, is_new = _resolve_slot(con, grp["id"], 5, vendor)
            explain.append({"segment": "vendor", "label": labels["vendor"], "value": value,
                            "code": vend_code, "reason": "new value - code previewed, not yet "
                            "minted" if is_new else "matched existing value"})
        else:
            explain.append({"segment": "vendor", "label": labels["vendor"], "value": None,
                            "code": None, "reason": "not named on the invoice line"})
    elif vendor not in (None, ""):
        explain.append({"segment": "vendor", "label": None, "value": None, "code": None,
                        "reason": f"{grp['name']} does not declare a vendor position - ignored"})

    code = assemble(head["code2"], sub["code2"], grp["code3"], slot_codes, vend_code)
    return {"code": code, "length": len(code), "valid": len(code) in VALID_LENGTHS,
            "explain": explain}


# ---------------------------------------------------------------- concurrency
def _busy(con):
    con.execute("PRAGMA busy_timeout=5000")


def claim_group_code(con, subhead_id, name, uom=None, labels=None, retries=1):
    """The concurrency-safe way to create a group: allocation and insert in
    one BEGIN IMMEDIATE transaction, so two creators submitting in the same
    second cannot receive the same number. BEGIN IMMEDIATE takes SQLite's
    write lock up front, forcing a second concurrent caller to wait for this
    transaction to finish before it re-reads 'lowest free' - so it always
    sees this number as taken and moves on to the next one. Retries once on
    SQLITE_BUSY with a fresh busy timeout. Returns {id, code3, freed_from}."""
    _busy(con)
    last_exc = None
    for attempt in range(retries + 1):
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                code3, freed_from = next_group_code(con, subhead_id)
                cur = con.execute(
                    "INSERT INTO grp(subhead_id,name,code3,uom,labels) VALUES(?,?,?,?,?)",
                    (subhead_id, name, code3, uom, json.dumps(labels or {})))
                if freed_from:
                    release_group_vacancy(con, subhead_id, code3)
                con.commit()
                return {"id": cur.lastrowid, "code3": code3, "freed_from": freed_from}
            except Exception:
                con.rollback()
                raise
        except sqlite3.OperationalError as e:
            last_exc = e
            msg = str(e).lower()
            if attempt < retries and ("locked" in msg or "busy" in msg):
                time.sleep(0.05 * (attempt + 1))
                continue
            raise
    raise last_exc


# ------------------------------------------------------- leases (tier 2/3)
def _lease_key(scope):
    kind, ident = scope
    if kind not in ("group", "item"):
        raise ValueError("lease scope kind must be 'group' or 'item'")
    return f"lease.{kind}.{ident}"


def _lease_width(scope):
    return 3 if scope[0] == "group" else 2


def _lease_blocked(con, scope):
    """Every position inside a currently-active lease, whether minted yet or
    not. Leased numbers are never issued by the VPS to anyone else - the two
    number spaces are disjoint by construction, not by timing, so the WHOLE
    block is blocked from next_group_code / next_item_position for as long
    as the lease exists, not just the part already minted."""
    lease = D.get_setting(con, _lease_key(scope), None)
    if not lease:
        return set()
    width = _lease_width(scope)
    return {str(n).zfill(width) for n in range(lease["lo"], lease["hi"] + 1)}


def grant_lease(con, scope, size=10):
    """VPS side: reserve a disjoint block of positions for the local server.
    scope = ("group", subhead_id) for group numbers, or ("item", grp_id) for
    item positions. The block starts one past the highest number already in
    use or already leased for that scope, so it can never overlap normal
    queue-claim allocation. Leases renew (replace) on every call - the local
    server is expected to call this again on every sync."""
    _busy(con)
    kind, ident = scope
    width = _lease_width(scope)
    con.execute("BEGIN IMMEDIATE")
    try:
        if kind == "group":
            used = [int(r["code3"]) for r in con.execute(
                "SELECT code3 FROM grp WHERE subhead_id=? AND status='active'", (ident,))]
            cap = 999
        else:
            used = [int(_item_position_of(con, dict(r))[:2] or 0) for r in
                    con.execute("SELECT s1,s2,s3,s4 FROM item WHERE grp_id=?", (ident,))]
            cap = 99

        existing = D.get_setting(con, _lease_key(scope), None)
        floor = max([existing["hi"]] if existing else [0])
        top_used = max(used) if used else 0
        start = max(floor, top_used) + 1
        end = min(start + size - 1, cap)
        if start > cap:
            raise ValueError("no room left to grant a lease in this scope")
        lease = {"lo": start, "hi": end, "next": start, "size": size, "granted_at": D.now()}
        D.set_setting(con, _lease_key(scope), lease)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {"scope": list(scope), "lo": start, "hi": end, "size": end - start + 1,
            "range": f"{str(start).zfill(width)}-{str(end).zfill(width)}"}


def mint_from_lease(con, scope, retries=1):
    """Local-server side: next number from our own leased block, lowest
    first. None when the lease does not exist or is exhausted - the caller
    then falls back to a provisional code.

    The tier-2 box can itself be handling more than one creator at once, so
    this is read-modify-write on the settings blob under the same BEGIN
    IMMEDIATE + retry-once-on-busy discipline as claim_group_code - two
    local threads minting in the same second must not receive the same
    leased number either."""
    _busy(con)
    key = _lease_key(scope)
    width = _lease_width(scope)
    last_exc = None
    for attempt in range(retries + 1):
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                lease = D.get_setting(con, key, None)
                if not lease or lease["next"] > lease["hi"]:
                    con.commit()
                    return None
                n = lease["next"]
                lease["next"] = n + 1
                D.set_setting(con, key, lease)
                con.commit()
                return str(n).zfill(width)
            except Exception:
                con.rollback()
                raise
        except sqlite3.OperationalError as e:
            last_exc = e
            msg = str(e).lower()
            if attempt < retries and ("locked" in msg or "busy" in msg):
                time.sleep(0.05 * (attempt + 1))
                continue
            raise
    raise last_exc


def return_lease(con, scope):
    """On sync: give back what we did not use. Returned numbers become
    vacancies (group or item, matching scope) and the queue-claim rule then
    fills them - so leasing leaves no permanent holes. Returns the list of
    numbers handed back."""
    key = _lease_key(scope)
    lease = D.get_setting(con, key, None)
    if not lease:
        return []
    width = _lease_width(scope)
    kind, ident = scope
    unused = [str(n).zfill(width) for n in range(lease["next"], lease["hi"] + 1)]
    ts = D.now()
    if kind == "group":
        for code3 in unused:
            con.execute("""INSERT OR IGNORE INTO grp_vacancy(subhead_id,code3,former_name,ts)
                           VALUES(?,?,?,?)""", (ident, code3, None, ts))
    else:
        for pos in unused:
            con.execute("""INSERT OR IGNORE INTO item_vacancy(grp_id,position,spec_tuple,former_item,ts)
                           VALUES(?,?,?,?,?)""", (ident, pos, None, None, ts))
    con.execute("DELETE FROM settings WHERE k=?", (key,))
    con.commit()
    return unused


def _ensure_provisional_column(con):
    """item.provisional isn't in CONTRACTS.md §5's schema list - Agent 0 had
    already landed before Task 5 (offline leases/provisional codes) was
    written into this brief. core/db.py is Agent 0's exclusively, so rather
    than edit their file this adds the single column defensively (idempotent,
    additive, never touches existing data) the first time it's needed. Flagged
    in agents/done/AGENT_D.md for Agent 0/H to fold into SCHEMA properly."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(item)")}
    if "provisional" not in cols:
        con.execute("ALTER TABLE item ADD COLUMN provisional INT DEFAULT 0")
        con.commit()


def mark_provisional(con, item_id):
    """Flag an item as provisional: minted offline with no lease number left
    to give it. Must never be pushed to ERPNext (Agent G enforces that half);
    this only sets and exposes the flag."""
    _ensure_provisional_column(con)
    con.execute("UPDATE item SET provisional=1 WHERE id=?", (item_id,))
