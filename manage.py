#!/usr/bin/env python
"""Item Code Studio — admin CLI.

Agent B owns the user-provisioning commands below (agents/AGENT_B_AUTH.md
task 5). agents/README.md's file-ownership table originally gave the whole
of manage.py to Agent H; the brief handed to this agent carves the user
commands out explicitly ("manage.py (user commands only — Agent H owns the
rest of it)") so this file is created here, structured so Agent H can add
further subcommands (e.g. packaging, tier-2 setup) via `sub.add_parser(...)`
without touching anything below.

Accounts are provisioned by an admin only — agents/CONTRACTS.md decision 9.
There is no sign-up page, no email reset, no OTP.

    python manage.py adduser <username> --name "Full Name" [--admin]
    python manage.py disable <username>
    python manage.py resetpw <username>
    python manage.py listusers

Every generated password is printed once, to the terminal, and never logged
or written anywhere else. It must be handed to the person directly.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import db as D                                        # noqa: E402
from core import auth as A                                      # noqa: E402


def _open():
    con = D.connect()
    D.init(con)
    return con


def _print_password_block(heading, username, password):
    line = "=" * 60
    print(line)
    print(f"  {heading}")
    print(f"  username: {username}")
    print(f"  password: {password}")
    print("  hand this to the person directly — it will not be shown again.")
    print("  they must change it at first login.")
    print(line)


def cmd_adduser(args):
    con = _open()
    username = args.username.strip().lower()
    if not username:
        print("username is required")
        return 1
    if D.one(con, "SELECT 1 FROM app_user WHERE username=?", (username,)):
        print(f"'{username}' already exists.")
        return 1
    password = A.generate_password()
    pw_hash, salt = A.hash_password(password)
    con.execute(
        "INSERT INTO app_user(username, display_name, pw_hash, salt, is_admin, active, created_at, created_by) "
        "VALUES (?,?,?,?,?,1,?,?)",
        (username, args.name or username, pw_hash, salt, 1 if args.admin else 0, D.now(), "manage.py"))
    con.commit()
    A.flag_must_change(con, username)
    D.log(con, "manage.py", "adduser", username, {"admin": bool(args.admin)})
    con.commit()  # D.log() does not commit itself
    _print_password_block(f"account created: {username}", username, password)
    return 0


def cmd_disable(args):
    con = _open()
    username = args.username.strip().lower()
    row = D.one(con, "SELECT * FROM app_user WHERE username=?", (username,))
    if not row:
        print(f"no such user: {username}")
        return 1
    if not row["active"]:
        print(f"'{username}' is already disabled.")
        return 0
    if row["is_admin"]:
        remaining = D.one(
            con, "SELECT COUNT(*) c FROM app_user WHERE is_admin=1 AND active=1 AND username<>?",
            (username,))["c"]
        if remaining == 0:
            print("refused: this is the last active admin account.")
            return 1
    con.execute("UPDATE app_user SET active=0 WHERE username=?", (username,))
    con.commit()
    A.delete_sessions_for(con, username)
    D.log(con, "manage.py", "disable", username)
    con.commit()  # D.log() does not commit itself
    print(f"disabled: {username}")
    return 0


def cmd_resetpw(args):
    con = _open()
    username = args.username.strip().lower()
    row = D.one(con, "SELECT * FROM app_user WHERE username=?", (username,))
    if not row:
        print(f"no such user: {username}")
        return 1
    password = A.generate_password()
    pw_hash, salt = A.hash_password(password)
    con.execute("UPDATE app_user SET pw_hash=?, salt=? WHERE username=?", (pw_hash, salt, username))
    con.commit()
    A.flag_must_change(con, username)
    A.delete_sessions_for(con, username)
    D.log(con, "manage.py", "resetpw", username)
    con.commit()  # D.log() does not commit itself
    _print_password_block(f"password reset for: {username}", username, password)
    return 0


def cmd_listusers(args):
    con = _open()
    rows = D.rows(con, "SELECT username, display_name, is_admin, active, created_at "
                        "FROM app_user ORDER BY username")
    if not rows:
        print("no users yet — run 'python manage.py adduser <username> --admin' to create the first one,")
        print("or start the server once: it creates a bootstrap admin automatically.")
        return 0
    for r in rows:
        flags = []
        if r["is_admin"]:
            flags.append("admin")
        if not r["active"]:
            flags.append("disabled")
        tag = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {r['username']:<20} {(r['display_name'] or ''):<26} {r['created_at']}{tag}")
    return 0


# ============================================================================
# Below this line: Agent H's subcommands (agents/AGENT_H_DEPLOY.md). Nothing
# above this line was touched — see the file docstring for why user
# provisioning is Agent B's even though this file is otherwise Agent H's.

def cmd_tier_status(args):
    """python manage.py tier-status — what core.tier.TierClient would
    resolve right now, using this machine's own config.json. Useful to
    check from a desktop before relying on it, and as a quick manual check
    of the three-tier failover without needing the running server's own
    /api/v1/health (which only reports its OWN process's view)."""
    import json
    from core.tier import TierClient

    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    tc = TierClient(cfg.get("ledger") or {})
    status = tc.resolve_once()
    print(f"mode:          {tc.mode}")
    print(f"server_url:    {tc.server_url or '(not set)'}")
    print(f"local_url:     {tc.local_url or '(not set)'}")
    print(f"status:        {status}")
    print(f"meaning:       {tc.status_text()}")
    print(f"active base:   {tc.active_base or '(none — this process is the authority itself, or offline)'}")
    return 0


def cmd_refresh_cache(args):
    """python manage.py refresh-cache — rebuild data/dict_cache.json from
    the live local database. Works today (this process IS tier 1/2), no
    network call involved. A future client-mode desktop would instead call
    DictCache.refresh_from_tier(tier_client) — see core/tier.py."""
    from core.tier import DictCache

    con = _open()
    dc = DictCache()
    snap = dc.refresh_from_db(con)
    print(f"cache written: {dc.path}")
    print(f"  groups:  {len(snap['groups'])}")
    print(f"  items:   {len(snap['items'])}")
    print(f"  specvals:{len(snap['specvals'])}")
    print(f"  aliases: {len(snap['aliases'])}")
    return 0


def cmd_backup(args):
    """python manage.py backup [daily|weekly|run] — thin wrapper around
    install/backup.py so the one CLI covers provisioning AND operations;
    install/backup.py itself is what a scheduled task should call directly."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "install"))
    import backup as B                                              # noqa: E402
    kind = args.kind or "run"
    if kind == "daily":
        B.daily_backup()
    elif kind == "weekly":
        B.weekly_export()
    else:
        B.daily_backup()
        B.weekly_export()
    return 0


def main():
    ap = argparse.ArgumentParser(description="Item Code Studio admin CLI")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("adduser", help="create an account")
    p.add_argument("username")
    p.add_argument("--name", help="full display name")
    p.add_argument("--admin", action="store_true", help="grant admin rights")
    p.set_defaults(func=cmd_adduser)

    p = sub.add_parser("disable", help="deactivate an account")
    p.add_argument("username")
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("resetpw", help="issue a new password for an account")
    p.add_argument("username")
    p.set_defaults(func=cmd_resetpw)

    p = sub.add_parser("listusers", help="list all accounts")
    p.set_defaults(func=cmd_listusers)

    p = sub.add_parser("tier-status", help="resolve which tier is authoritative right now")
    p.set_defaults(func=cmd_tier_status)

    p = sub.add_parser("refresh-cache", help="rebuild data/dict_cache.json from the live local DB")
    p.set_defaults(func=cmd_refresh_cache)

    p = sub.add_parser("backup", help="run a backup into the Drive-synced folder")
    p.add_argument("kind", nargs="?", choices=["daily", "weekly", "run"], default="run")
    p.set_defaults(func=cmd_backup)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
