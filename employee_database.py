"""Create and incrementally populate the employee payroll database."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


DATABASE_FIELDS = {
    "employee": "legal_name",
    "role": "role",
    "nric": "nric",
    "d.o.b": "date_of_birth",
    "dob": "date_of_birth",
    "association": "association",
    "asc. fee": "association_fee",
    "email": "email",
    "phone": "phone",
    "phone number": "phone",
    "bank": "bank_name",
    "account. number": "bank_account",
    "pt rate": "hourly_rate",
    "hourly rate": "hourly_rate",
    "otrb": "overtime_or_bonus",
    "leave tag": "leave_tag",
    "connection": "connection",
    "join date": "join_date",
    "status": "employment_status",
    "ranking": "ranking",
    "cpf contribute": "cpf_contribute",
}

EMPLOYEE_COLUMNS = tuple(dict.fromkeys(DATABASE_FIELDS.values()))


def clean(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime().date().isoformat()
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).replace("\xa0", " ").strip()
    return None if not text or text.lower() in {"nan", "none", "-"} else text


def normal_text(value: Any) -> str | None:
    text = clean(value)
    if text is None:
        return None
    return re.sub(r"\s+", " ", str(text)).casefold()


def normal_id(value: Any) -> str | None:
    text = clean(value)
    return re.sub(r"[^A-Z0-9]", "", str(text).upper()) if text else None


def normal_email(value: Any) -> str | None:
    text = normal_text(value)
    return text if text and "@" in text else None


def normal_phone(value: Any) -> str | None:
    text = clean(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", str(text))
    return digits[-8:] if len(digits) >= 8 else None


def detect_header_row(path: Path, sheet: str) -> int | None:
    preview = pd.read_excel(path, sheet_name=sheet, header=None, nrows=20)
    for index, row in preview.iterrows():
        values = {str(value).strip().casefold() for value in row if clean(value)}
        if "employee" in values:
            return int(index)
    return None


def connect(database: Path) -> sqlite3.Connection:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees (
            employee_id INTEGER PRIMARY KEY,
            legal_name TEXT NOT NULL,
            role TEXT,
            nric TEXT,
            date_of_birth TEXT,
            association TEXT,
            association_fee TEXT,
            email TEXT,
            phone TEXT,
            bank_name TEXT,
            bank_account TEXT,
            hourly_rate TEXT,
            overtime_or_bonus TEXT,
            leave_tag TEXT,
            connection TEXT,
            join_date TEXT,
            employment_status TEXT,
            ranking TEXT,
            cpf_contribute TEXT,
            main_branch TEXT NOT NULL DEFAULT 'ICV',
            visa_status TEXT,
            name_norm TEXT NOT NULL,
            nric_norm TEXT,
            email_norm TEXT,
            phone_norm TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_employees_nric ON employees(nric_norm);
        CREATE INDEX IF NOT EXISTS idx_employees_email ON employees(email_norm);
        CREATE INDEX IF NOT EXISTS idx_employees_phone ON employees(phone_norm);
        CREATE INDEX IF NOT EXISTS idx_employees_name_dob ON employees(name_norm, date_of_birth);

        CREATE TABLE IF NOT EXISTS import_runs (
            import_id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_directory TEXT NOT NULL,
            completed_at TEXT,
            rows_seen INTEGER NOT NULL DEFAULT 0,
            employees_created INTEGER NOT NULL DEFAULT 0,
            employees_matched INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS source_records (
            source_record_id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES import_runs(import_id),
            employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
            source_file TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            record_hash TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(record_hash)
        );
        CREATE TABLE IF NOT EXISTS field_conflicts (
            conflict_id INTEGER PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
            field_name TEXT NOT NULL,
            retained_value TEXT,
            incoming_value TEXT,
            source_file TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT
        );
        """
    )
    return connection


def find_employee(connection: sqlite3.Connection, values: dict[str, Any]) -> int | None:
    checks = (
        ("nric_norm", normal_id(values.get("nric"))),
        ("email_norm", normal_email(values.get("email"))),
        ("phone_norm", normal_phone(values.get("phone"))),
    )
    for column, value in checks:
        if value:
            row = connection.execute(
                f"SELECT employee_id FROM employees WHERE {column} = ? ORDER BY employee_id LIMIT 1",
                (value,),
            ).fetchone()
            if row:
                return int(row[0])
    dob = clean(values.get("date_of_birth"))
    if dob:
        row = connection.execute(
            "SELECT employee_id FROM employees WHERE name_norm = ? AND date_of_birth = ? ORDER BY employee_id LIMIT 1",
            (normal_text(values["legal_name"]), dob),
        ).fetchone()
        if row:
            return int(row[0])
    return None


def insert_employee(connection: sqlite3.Connection, values: dict[str, Any]) -> int:
    data = {column: clean(values.get(column)) for column in EMPLOYEE_COLUMNS}
    data.update(
        name_norm=normal_text(data["legal_name"]),
        nric_norm=normal_id(data["nric"]),
        email_norm=normal_email(data["email"]),
        phone_norm=normal_phone(data["phone"]),
    )
    columns = list(data)
    cursor = connection.execute(
        f"INSERT INTO employees ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        [data[column] for column in columns],
    )
    return int(cursor.lastrowid)


def merge_employee(
    connection: sqlite3.Connection,
    employee_id: int,
    values: dict[str, Any],
    source_file: str,
    sheet_name: str,
) -> None:
    current = connection.execute(
        "SELECT * FROM employees WHERE employee_id = ?", (employee_id,)
    ).fetchone()
    updates: dict[str, Any] = {}
    for field in EMPLOYEE_COLUMNS:
        incoming = clean(values.get(field))
        retained = clean(current[field])
        if incoming is None:
            continue
        if retained is None:
            updates[field] = incoming
        elif normal_text(retained) != normal_text(incoming):
            connection.execute(
                """INSERT INTO field_conflicts
                   (employee_id, field_name, retained_value, incoming_value, source_file, sheet_name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (employee_id, field, retained, incoming, source_file, sheet_name),
            )
    if updates:
        if "legal_name" in updates:
            updates["name_norm"] = normal_text(updates["legal_name"])
        if "nric" in updates:
            updates["nric_norm"] = normal_id(updates["nric"])
        if "email" in updates:
            updates["email_norm"] = normal_email(updates["email"])
        if "phone" in updates:
            updates["phone_norm"] = normal_phone(updates["phone"])
        assignments = ", ".join(f"{column} = ?" for column in updates)
        connection.execute(
            f"UPDATE employees SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE employee_id = ?",
            [*updates.values(), employee_id],
        )


def import_workbooks(source: Path, database: Path) -> dict[str, int]:
    workbooks = sorted(source.glob("*.xls*"))
    if len(workbooks) != 2:
        raise ValueError(f"Expected exactly 2 Excel workbooks; found {len(workbooks)}")
    connection = connect(database)
    cursor = connection.execute(
        "INSERT INTO import_runs (source_directory) VALUES (?)", (str(source),)
    )
    import_id = int(cursor.lastrowid)
    totals = {"rows_seen": 0, "employees_created": 0, "employees_matched": 0, "records_skipped": 0}
    try:
        for workbook in workbooks:
            for sheet in pd.ExcelFile(workbook).sheet_names:
                header = detect_header_row(workbook, sheet)
                if header is None:
                    continue
                frame = pd.read_excel(workbook, sheet_name=sheet, header=header)
                frame = frame.dropna(how="all")
                for offset, row in frame.iterrows():
                    raw = {str(column): clean(value) for column, value in row.items() if clean(value) is not None}
                    name = clean(row.get("Employee"))
                    if not name or normal_text(name) in {"total", "summary"}:
                        continue
                    totals["rows_seen"] += 1
                    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
                    digest = hashlib.sha256(
                        f"{workbook.name}|{sheet}|{payload}".encode("utf-8")
                    ).hexdigest()
                    if connection.execute(
                        "SELECT 1 FROM source_records WHERE record_hash = ?", (digest,)
                    ).fetchone():
                        totals["records_skipped"] += 1
                        continue
                    mapped = {DATABASE_FIELDS[str(column).strip().casefold()]: value for column, value in row.items() if str(column).strip().casefold() in DATABASE_FIELDS}
                    employee_id = find_employee(connection, mapped)
                    if employee_id is None:
                        employee_id = insert_employee(connection, mapped)
                        totals["employees_created"] += 1
                    else:
                        merge_employee(connection, employee_id, mapped, workbook.name, sheet)
                        totals["employees_matched"] += 1
                    connection.execute(
                        """INSERT INTO source_records
                           (import_id, employee_id, source_file, sheet_name, source_row, record_hash, raw_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (import_id, employee_id, workbook.name, sheet, int(offset) + header + 2, digest, payload),
                    )
        connection.execute(
            """UPDATE import_runs SET completed_at=CURRENT_TIMESTAMP, rows_seen=?,
               employees_created=?, employees_matched=? WHERE import_id=?""",
            (totals["rows_seen"], totals["employees_created"], totals["employees_matched"], import_id),
        )
        connection.commit()
        return totals
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/employees.db"))
    args = parser.parse_args()
    result = import_workbooks(args.source, args.database)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
