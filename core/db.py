"""SQLite store for Item Code Studio.

One file on the host desktop is the single source of truth. Everyone on the
same Wi-Fi talks to that one file through the HTTP server, so two people can
never mint the same code.
"""
import os
import sqlite3
import json
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "itemcode.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS head(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, code2 TEXT UNIQUE, active INT DEFAULT 1);

CREATE TABLE IF NOT EXISTS subhead(
  id INTEGER PRIMARY KEY, head_id INT, name TEXT, code2 TEXT, active INT DEFAULT 1,
  UNIQUE(head_id, name));

CREATE TABLE IF NOT EXISTS grp(
  id INTEGER PRIMARY KEY, subhead_id INT, name TEXT, code3 TEXT, uom TEXT,
  labels TEXT DEFAULT '{}', status TEXT DEFAULT 'active',
  UNIQUE(subhead_id, name));

-- a group code freed by a move; held for a semantically-matching future group
CREATE TABLE IF NOT EXISTS grp_vacancy(
  id INTEGER PRIMARY KEY, subhead_id INT, code3 TEXT, former_name TEXT,
  ts TEXT, released INT DEFAULT 0, UNIQUE(subhead_id, code3));

CREATE TABLE IF NOT EXISTS specval(
  id INTEGER PRIMARY KEY, grp_id INT, slot INT, value TEXT, code2 TEXT,
  UNIQUE(grp_id, slot, value));

-- learned corrections: "the operator said THIS text means THAT entity"
CREATE TABLE IF NOT EXISTS alias(
  id INTEGER PRIMARY KEY, scope TEXT, ref_id INT, term TEXT, term_norm TEXT,
  user TEXT, ts TEXT, UNIQUE(scope, ref_id, term_norm));

CREATE TABLE IF NOT EXISTS item(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, name_norm TEXT,
  description TEXT, grp_id INT,
  s1 INT, s2 INT, s3 INT, s4 INT, vend INT,
  uom TEXT, alt_uom TEXT, hsn TEXT, tax TEXT,
  maintain_stock INT DEFAULT 1, allow_sales INT DEFAULT 0, has_batch INT DEFAULT 0,
  origin TEXT, frozen INT DEFAULT 0, decodable INT DEFAULT 1,
  status TEXT DEFAULT 'draft', erp_synced_at TEXT,
  created_by TEXT, created_at TEXT, updated_at TEXT);

CREATE INDEX IF NOT EXISTS ix_item_norm ON item(name_norm);
CREATE INDEX IF NOT EXISTS ix_item_grp  ON item(grp_id);

-- every code ever issued, so a retired code is never silently re-used
CREATE TABLE IF NOT EXISTS code_ledger(
  code TEXT PRIMARY KEY, item_id INT, state TEXT, ts TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS code_mapping(
  id INTEGER PRIMARY KEY, old_code TEXT, new_code TEXT, reason TEXT,
  user TEXT, ts TEXT, pushed_to_erp INT DEFAULT 0);

CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, user TEXT, action TEXT, target TEXT, detail TEXT);

-- read-only mirror of what is actually live in ERPNext (phase-1 existence check)
CREATE TABLE IF NOT EXISTS erp_item(
  code TEXT PRIMARY KEY, name TEXT, name_norm TEXT, item_group TEXT, uom TEXT,
  disabled INT, owner TEXT, pulled_at TEXT);

CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);

-- ---------------------------------------------------------------- Agent 0
-- The public face, login, editable master and packaging added 6 Aug 2026.
-- Every statement below is IF NOT EXISTS / non-destructive: there is real
-- seeded data (889 groups, 1,947 items, 2,677 ERP codes) and this migration
-- must never touch it. Agent 0 owns this file exclusively - CONTRACTS.md §5.

CREATE TABLE IF NOT EXISTS app_user(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE, display_name TEXT,
  pw_hash BLOB, salt BLOB, is_admin INT DEFAULT 0, active INT DEFAULT 1,
  created_at TEXT, created_by TEXT);

CREATE TABLE IF NOT EXISTS session(
  token_hash TEXT PRIMARY KEY, username TEXT, issued_at TEXT, expires_at TEXT);

CREATE TABLE IF NOT EXISTS item_version(
  id INTEGER PRIMARY KEY, item_id INT, version_no INT, snapshot TEXT,
  changed_by TEXT, changed_at TEXT, summary TEXT,
  UNIQUE(item_id, version_no));

CREATE TABLE IF NOT EXISTS item_vacancy(
  id INTEGER PRIMARY KEY, grp_id INT, position TEXT, spec_tuple TEXT,
  former_item TEXT, ts TEXT, released INT DEFAULT 0,
  UNIQUE(grp_id, position));

CREATE TABLE IF NOT EXISTS llm_cache(
  key TEXT PRIMARY KEY, question TEXT, answer TEXT, provider TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS sync_log(
  id INTEGER PRIMARY KEY, ts TEXT, direction TEXT, doctype TEXT,
  found INT, changed INT, conflicts TEXT, ok INT, detail TEXT);
"""


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    """Open the ledger database directly.

    Valid only for `ledger.mode` "server" or "local_server" (CONTRACTS.md
    §2) - both open SQLite in-process, which is what this does. "client"
    mode proxies over HTTPS instead of opening a file at all; Agent H owns
    that resolution and the three-tier failover on top of it. This function
    only promises: given a path (or the default), hand back a working
    connection. Never point it at a network share - SQLite locking over SMB
    is unreliable and concurrent writers corrupt the file.
    """
    con = sqlite3.connect(path or DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(con):
    con.executescript(SCHEMA)
    con.commit()


def get_setting(con, key, default=None):
    r = con.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return json.loads(r["v"]) if r else default


# back-compat name; prefer get_setting in new code
setting = get_setting


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (key, json.dumps(value)))
    con.commit()


def rows(con, sql, args=()):
    """List of plain dicts - the shape every route module wants back."""
    return [dict(r) for r in con.execute(sql, args)]


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def log(con, user, action, target, detail=None):
    """Writes and commits. A handler whose last write is an audit entry must
    not be left holding the connection's write lock just because it forgot a
    trailing commit() - Agent B hit this live (a separate manage.py process
    got `database is locked` against an otherwise-idle server) and worked
    around it at their own call sites. Fixing it here instead, since this is
    the one place every caller goes through. A caller that already commits
    afterward is unaffected - a second commit on a clean transaction is a
    no-op."""
    con.execute("INSERT INTO audit(ts,user,action,target,detail) VALUES(?,?,?,?,?)",
                (now(), user or "system", action, str(target),
                 json.dumps(detail) if not isinstance(detail, str) else detail))
    con.commit()
