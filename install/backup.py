#!/usr/bin/env python
"""Backup to Google Drive — Agent H (agents/AGENT_H_DEPLOY.md task 7).

No Google API integration here on purpose: that needs OAuth credentials, a
consent screen and a refresh-token dance, all to move one file. Instead this
script writes into a plain local folder that **Google Drive for Desktop**
(already installed on the nominated machine) syncs on its own. No
credentials in this codebase, nothing to rotate, and it keeps working when
someone changes a password.

    python install/backup.py daily              # WAL-safe DB snapshot
    python install/backup.py weekly              # Excel export
    python install/backup.py restore-verify <path-to-a-daily-backup.db>
    python install/backup.py run                 # both, prune, then verify
                                                   # the daily one it just made

Where things land — config.json:

    "backup": {
      "drive_folder": "",              <- Anuraag sets this: a path already
                                           synced by Google Drive for Desktop
      "daily_keep": 14,
      "weekly_keep": 8
    }

Today (no VPS, no separate tier-2 machine — see agents/CONTRACTS.md §2) THIS
process is already tier 1/2's authority, so it backs up its own
data/itemcode.db directly. Once a VPS and a dedicated tier-2 box exist,
`pull_from_vps()` below is the one function that needs filling in — tier 2
already syncs with tier 1 for reconciliation (Agent G), so pulling one more
file on the same schedule is additive, not new plumbing. Until then: **the
VPS writes its own backups into this same folder locally** and they are
picked up automatically the moment tier 2 is pointed at it — nothing else
changes. Said plainly in HANDOVER.md rather than left silent.

WAL-safe by construction: `sqlite3.Connection.backup()` (stdlib, Python
3.7+) uses SQLite's own online backup API, which is explicitly safe to run
against a live WAL-mode database being written to concurrently — unlike a
raw file copy, which can grab a torn snapshot mid-checkpoint.
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "itemcode.db")
CFG_PATH = os.path.join(ROOT, "config.json")


def _cfg():
    with open(CFG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    b = cfg.get("backup") or {}
    return {
        "drive_folder": b.get("drive_folder") or "",
        "daily_keep": int(b.get("daily_keep", 14)),
        "weekly_keep": int(b.get("weekly_keep", 8)),
    }


def _require_folder(cfg):
    folder = cfg["drive_folder"]
    if not folder:
        print("config.json: backup.drive_folder is not set yet — nothing to do.")
        print("Set it to a path Google Drive for Desktop already syncs on this "
              "machine, e.g. \"C:\\\\Users\\\\<you>\\\\Google Drive\\\\ItemCodeStudio-Backups\"")
        sys.exit(1)
    os.makedirs(os.path.join(folder, "daily"), exist_ok=True)
    os.makedirs(os.path.join(folder, "weekly"), exist_ok=True)
    return folder


def _prune(folder, pattern, keep):
    files = sorted(glob.glob(os.path.join(folder, pattern)), key=os.path.getmtime, reverse=True)
    removed = []
    for f in files[keep:]:
        os.remove(f)
        removed.append(os.path.basename(f))
    return removed


# --------------------------------------------------------------------- daily

def daily_backup(db_path=DB_PATH, drive_folder=None, keep=None):
    cfg = _cfg()
    folder = drive_folder or _require_folder(cfg)
    keep = cfg["daily_keep"] if keep is None else keep

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(folder, "daily", f"itemcode-{stamp}.db")

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)          # WAL-safe online backup, not a file copy
    finally:
        dst.close()
        src.close()

    removed = _prune(folder, "itemcode-*.db", keep)
    size_kb = os.path.getsize(dest) / 1024
    print(f"daily backup written: {dest} ({size_kb:.0f} KB)")
    if removed:
        print(f"pruned {len(removed)} older daily backup(s): {removed}")
    return dest


# -------------------------------------------------------------------- weekly

def weekly_export(db_path=DB_PATH, drive_folder=None, keep=None):
    """Reuses core.exporter.export() (Agent F's module) — this script only
    calls it, never reimplements the workbook."""
    from core import db as D
    from core import exporter as EX

    cfg = _cfg()
    folder = drive_folder or _require_folder(cfg)
    keep = cfg["weekly_keep"] if keep is None else keep

    con = D.connect(db_path)
    try:
        tmp_dir = os.path.join(ROOT, "exports")
        os.makedirs(tmp_dir, exist_ok=True)
        path = EX.export(con, tmp_dir)
    finally:
        con.close()

    iso = datetime.date.today().isocalendar()
    dest_name = f"itemcode-{iso[0]}-W{iso[1]:02d}.xlsx"
    dest = os.path.join(folder, "weekly", dest_name)
    shutil.copy2(path, dest)

    removed = _prune(folder, "itemcode-*.xlsx", keep)
    print(f"weekly export written: {dest}")
    if removed:
        print(f"pruned {len(removed)} older weekly export(s): {removed}")
    return dest


# --------------------------------------------------------------------- pull
# Future work, not wired: fills in once a VPS + dedicated tier-2 exist.

def pull_from_vps(server_url, dest_path, timeout=30):
    """Not called by anything yet — there is no VPS to pull from. Left as a
    named, documented stub rather than silently absent, per
    agents/AGENT_H_DEPLOY.md task 7's instruction to say so plainly rather
    than leave it silently unbacked. When tier 2 exists for real, this is
    the one function `run()`/a scheduled task needs: GET the VPS's own most
    recent daily backup (or a dedicated /admin/backup endpoint) and write it
    to dest_path before daily_backup() runs against the local replica."""
    raise NotImplementedError(
        "no VPS is provisioned yet (agents/README.md: 'A VPS and a domain — "
        "not urgent'). Once one exists, replace this body with an HTTPS GET "
        "against it and keep everything below (daily_backup/weekly_export/"
        "restore_verify) unchanged.")


# ------------------------------------------------------------- restore proof
# "An unverified backup is a rumour." Copies a backup file into a throwaway
# folder, opens it as a fresh, independent connection, and reports the same
# counts the running app's health endpoint reports — proof the file is a
# real, loadable database, not just proof a file exists.

def restore_verify(backup_path, scratch_dir=None):
    from core import db as D

    if not os.path.isfile(backup_path):
        print(f"no such backup file: {backup_path}")
        return None

    scratch_dir = scratch_dir or os.path.join(ROOT, "data", "_restore_scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    scratch_db = os.path.join(scratch_dir, "itemcode-restored.db")
    if os.path.exists(scratch_db):
        os.remove(scratch_db)
    shutil.copy2(backup_path, scratch_db)

    con = D.connect(scratch_db)
    try:
        counts = {
            "heads": con.execute("SELECT COUNT(*) c FROM head").fetchone()["c"],
            "subheads": con.execute("SELECT COUNT(*) c FROM subhead").fetchone()["c"],
            "groups": con.execute("SELECT COUNT(*) c FROM grp").fetchone()["c"],
            "items": con.execute("SELECT COUNT(*) c FROM item").fetchone()["c"],
            "erp_items": con.execute("SELECT COUNT(*) c FROM erp_item").fetchone()["c"],
        }
    finally:
        con.close()

    print(f"restored {backup_path} -> {scratch_db}")
    print(f"counts: {counts}")
    if all(v > 0 for v in (counts["groups"], counts["items"])):
        print("restore looks healthy: groups and items are both non-zero.")
    else:
        print("WARNING: restored database has zero groups or items — investigate before trusting this backup.")
    return counts


# ----------------------------------------------------------------------- cli

def cmd_run(args):
    daily_path = daily_backup()
    weekly_export()
    print("\nverifying the daily backup just written...")
    restore_verify(daily_path)


def main():
    ap = argparse.ArgumentParser(description="Item Code Studio backups (Google-Drive-synced folder)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("daily", help="write a WAL-safe DB snapshot, prune old ones").set_defaults(
        func=lambda a: daily_backup())
    sub.add_parser("weekly", help="write an Excel export, prune old ones").set_defaults(
        func=lambda a: weekly_export())
    sub.add_parser("run", help="daily + weekly + verify the daily one").set_defaults(func=cmd_run)

    p = sub.add_parser("restore-verify", help="prove a backup file actually restores")
    p.add_argument("backup_file")
    p.add_argument("--scratch", help="scratch folder (default: data/_restore_scratch)")
    p.set_defaults(func=lambda a: restore_verify(a.backup_file, a.scratch))

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
