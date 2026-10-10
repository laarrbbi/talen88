"""Operator account management (command line).

    python -m data.users list
    python -m data.users add EMAIL --name "Full Name" --role admin
    python -m data.users add EMAIL --name "Full Name" --role manager --division technology
    python -m data.users set-password EMAIL

Passwords are read from the PULSESCORE_NEW_PASSWORD environment variable when set
(for scripts), otherwise prompted for without echo. They are stored only as scrypt
hashes (data/auth.py). Every change writes an audit row.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import auth
from .audit import write_audit
from .db import get_connection, migrate


def _read_password() -> str:
    pw = os.environ.get("PULSESCORE_NEW_PASSWORD")
    if pw:
        return pw
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    return pw


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m data.users")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list operators")
    add = sub.add_parser("add", help="add an operator and set their password")
    add.add_argument("email")
    add.add_argument("--name", required=True)
    add.add_argument("--role", choices=("admin", "manager"), required=True)
    add.add_argument("--division", help="required for managers")
    setpw = sub.add_parser("set-password", help="set or reset an operator's password")
    setpw.add_argument("email")
    args = parser.parse_args(argv)

    conn = get_connection()
    try:
        migrate(conn)
        if args.cmd == "list":
            for r in conn.execute("SELECT email, role, division, password_hash IS NOT NULL "
                                  "AS has_pw FROM users ORDER BY role, email"):
                pw = "password set" if r["has_pw"] else "NO PASSWORD (cannot sign in)"
                print(f"{r['email']:<45} {r['role']:<8} {r['division'] or '-':<20} {pw}")
            return
        if args.cmd == "add":
            if args.role == "manager" and not args.division:
                sys.exit("--division is required for a manager")
            password = _read_password()
            conn.execute("INSERT INTO users (email, name, role, division, password_hash) "
                         "VALUES (?,?,?,?,?)",
                         (args.email.strip().lower(), args.name, args.role,
                          None if args.role == "admin" else args.division,
                          auth.hash_password(password)))
            conn.commit()
            write_audit(conn, actor_email="cli", action="user_add",
                        filters={"email": args.email.strip().lower(), "role": args.role},
                        result_count=1)
            print(f"added {args.email}")
            return
        if args.cmd == "set-password":
            auth.set_password(conn, args.email, _read_password())
            write_audit(conn, actor_email="cli", action="user_set_password",
                        filters={"email": args.email.strip().lower()}, result_count=1)
            print(f"password updated for {args.email}")
    except (ValueError, auth.AuthError) as exc:
        sys.exit(str(exc))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
