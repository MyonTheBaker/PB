"""Administrative commands for controlled payroll lifecycle operations."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

from payroll_engine import DATABASE, build_work_entries, reconcile_statutory_run, transition_run, validate_run
from payroll_outputs import create_accounting_lines, export_iras_year_csv, generate_payslips


PROJECT = Path(__file__).resolve().parent


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def encrypted_snapshot(output: Path, retention_days: int) -> Path:
    key = os.environ.get("PAYROLL_BACKUP_KEY")
    if not key:
        raise ValueError("PAYROLL_BACKUP_KEY must contain a Fernet encryption key")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="payroll-snapshot-") as folder:
        plain = Path(folder) / "employees.sqlite"
        source = sqlite3.connect(DATABASE)
        target = sqlite3.connect(plain)
        source.backup(target)
        target.close()
        source.close()
        output.write_bytes(Fernet(key.encode("ascii")).encrypt(plain.read_bytes()))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with connect() as connection:
        connection.execute(
            """INSERT INTO payroll_recovery_snapshots
               (file_path,sha256,encrypted,retention_until) VALUES(?,?,1,?)""",
            (str(output), digest, (date.today() + timedelta(days=retention_days)).isoformat()),
        )
    return output


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Payroll administration")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("validate", "work-entries", "accounting", "statutory-reconcile"):
        command = commands.add_parser(name)
        command.add_argument("run_id", type=int)
    transition = commands.add_parser("transition")
    transition.add_argument("run_id", type=int)
    transition.add_argument("target")
    transition.add_argument("--reason", required=True)
    payslips = commands.add_parser("payslips")
    payslips.add_argument("run_id", type=int)
    payslips.add_argument("--payment-date", type=date.fromisoformat, required=True)
    iras = commands.add_parser("iras-review")
    iras.add_argument("entity", choices=("ICV", "MBL"))
    iras.add_argument("year", type=int)
    iras.add_argument("path", type=Path)
    backup = commands.add_parser("encrypted-snapshot")
    backup.add_argument("path", type=Path)
    backup.add_argument("--retention-days", type=int, default=90)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "validate":
        with connect() as connection:
            issues = validate_run(connection, args.run_id)
        for issue in issues:
            print(f"{issue.severity}\t{issue.code}\t{issue.message}")
        print(f"{sum(i.severity == 'BLOCKER' for i in issues)} blocker(s); {len(issues)} total issue(s)")
    elif args.command == "work-entries":
        with connect() as connection:
            print(build_work_entries(connection, args.run_id), "work entries processed")
    elif args.command == "transition":
        with connect() as connection:
            transition_run(connection, args.run_id, args.target, getpass.getuser(), args.reason)
        print("Payroll status advanced to", args.target)
    elif args.command == "accounting":
        with connect() as connection:
            print(create_accounting_lines(connection, args.run_id), "accounting lines created")
    elif args.command == "statutory-reconcile":
        with connect() as connection:
            rows = reconcile_statutory_run(connection, args.run_id)
        for row in rows:
            print(f"{row['status']}\t{row['legal_name']}\tCPF employer "
                  f"{row['legacy_employer_cpf_cents']}->{row['calculated_employer_cpf_cents']}\t"
                  f"employee {row['legacy_employee_cpf_cents']}->{row['calculated_employee_cpf_cents']}\t"
                  f"SHG {row['legacy_other_deductions_cents']}->{row['calculated_shg_cents']}\t"
                  f"SDL {row['calculated_sdl_cents']}")
    elif args.command == "payslips":
        paths = generate_payslips(args.run_id, args.payment_date)
        print(len(paths), "payslips generated")
    elif args.command == "iras-review":
        export_iras_year_csv(args.entity, args.year, args.path)
        print("IRAS review dataset created:", args.path)
    elif args.command == "encrypted-snapshot":
        print("Encrypted recovery snapshot created:", encrypted_snapshot(args.path, args.retention_days))


if __name__ == "__main__":
    main()
