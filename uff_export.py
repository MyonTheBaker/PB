"""Generate validated DBS UFF v2.1 Singapore payroll instruction files."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path


DETAIL_FIELD_COUNT = 112
PURPOSE_CODE = "PAYR"
TRANSACTION_CODE = "22"


class UffValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EntityBanking:
    code: str
    organization_id: str
    sender_name: str
    source_account: str
    currency: str = "SGD"


def load_entity(config_path: Path, code: str) -> EntityBanking:
    parser = ConfigParser()
    if not parser.read(config_path, encoding="utf-8") or code not in parser:
        raise UffValidationError(f"Missing [{code}] banking configuration in {config_path.name}")
    section = parser[code]
    entity = EntityBanking(
        code=code,
        organization_id=section.get("organization_id", "").strip(),
        sender_name=section.get("sender_name", "").strip(),
        source_account=re.sub(r"[-\s]", "", section.get("source_account", "")),
        currency=section.get("currency", "SGD").strip().upper(),
    )
    missing = [name for name, value in (
        ("organization_id", entity.organization_id), ("sender_name", entity.sender_name),
        ("source_account", entity.source_account), ("currency", entity.currency),
    ) if not value]
    if missing:
        raise UffValidationError(f"Incomplete [{code}] banking configuration: {', '.join(missing)}")
    if len(entity.organization_id.encode("utf-8")) > 12:
        raise UffValidationError("DBS organization ID exceeds 12 bytes")
    return entity


def payroll_rows(database: Path, entity: str, period: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """SELECT e.employee_id, e.legal_name, e.bank_beneficiary_name,
                      e.bank_account, e.bank_swift, p.net_pay_cents, r.status
                 FROM payroll_run_items p
                 JOIN payroll_runs r ON r.payroll_run_id=p.payroll_run_id
                 JOIN employees e ON e.employee_id=p.employee_id
                WHERE substr(r.period_start,1,7)=? AND e.main_branch=?
                ORDER BY e.legal_name""",
            (period, entity),
        ).fetchall()
    finally:
        connection.close()


def _field(row: list[str], number: int, value: object) -> None:
    row[number - 1] = str(value)


def build_uff(entity: EntityBanking, rows: list[sqlite3.Row], payment_date: date) -> str:
    if not rows:
        raise UffValidationError("No payroll records found for this entity and period")
    statuses = {row["status"] for row in rows}
    if not statuses.issubset({"APPROVED", "LOCKED"}):
        raise UffValidationError("DBS export requires an approved and locked payroll run")
    problems: list[str] = []
    details: list[list[str]] = []
    total_cents = 0
    for item in rows:
        label = f"employee {item['employee_id']} ({item['legal_name']})"
        item_problems: list[str] = []
        beneficiary = (item["bank_beneficiary_name"] or "").strip()
        account = re.sub(r"[-\s]", "", item["bank_account"] or "")
        swift = re.sub(r"\s", "", item["bank_swift"] or "").upper()
        amount = item["net_pay_cents"]
        if not beneficiary: item_problems.append(f"{label}: missing DBS beneficiary name")
        if not account: item_problems.append(f"{label}: missing beneficiary account")
        if not re.fullmatch(r"[A-Z0-9]{8}([A-Z0-9]{3})?", swift):
            item_problems.append(f"{label}: missing or invalid SWIFT BIC")
        if not isinstance(amount, int) or amount <= 0: item_problems.append(f"{label}: net pay must be positive")
        if any(len(v.encode("utf-8")) > limit for v, limit in ((beneficiary, 140), (account, 34))):
            item_problems.append(f"{label}: bank field exceeds the UFF byte limit")
        if item_problems:
            problems.extend(item_problems)
            continue
        detail = [""] * DETAIL_FIELD_COUNT
        _field(detail, 1, "PAYMENT")
        _field(detail, 2, "SAL")
        _field(detail, 3, entity.source_account)
        _field(detail, 4, entity.currency)
        _field(detail, 6, entity.currency)
        _field(detail, 8, payment_date.strftime("%d%m%Y"))
        _field(detail, 11, beneficiary)
        _field(detail, 16, account)
        _field(detail, 21, swift)
        _field(detail, 28, f"{Decimal(amount) / 100:.2f}")
        _field(detail, 33, TRANSACTION_CODE)
        _field(detail, 34, f"SAL{payment_date.strftime('%m%y')}")
        _field(detail, 36, f"SALARY {payment_date.strftime('%b %Y').upper()}")
        _field(detail, 43, PURPOSE_CODE)
        details.append(detail)
        total_cents += amount
    if problems:
        raise UffValidationError("Bank export blocked:\n- " + "\n- ".join(problems))

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(["HEADER", datetime.now().strftime("%d%m%Y"), entity.organization_id,
                     entity.sender_name, "UFFv2"])
    writer.writerows(details)
    writer.writerow(["TRAILER", len(details), f"{Decimal(total_cents) / 100:.2f}"])
    return output.getvalue()


def write_uff(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
