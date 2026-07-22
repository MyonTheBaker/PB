"""Entity-scoped payroll editor and DBS UFF export command."""

from __future__ import annotations

import argparse
import calendar
import getpass
import json
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from uff_export import UffValidationError, build_uff, load_entity, payroll_rows, write_uff
from payroll_engine import TRANSITIONS, sync_item_components, transition_run, validate_run


PROJECT = Path(__file__).resolve().parent
DATABASE = PROJECT / "data" / "employees.db"
ENTITY_CONFIG = PROJECT / "payroll_entities.ini"
ENTITY_CODES = ("ICV", "MBL")
MOM_OT_MULTIPLIER = Decimal("1.5")
MONTH_CODES = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_period(value: str) -> str:
    value = value.strip().upper().lstrip("-")
    if len(value) == 5 and value[:3] in MONTH_CODES and value[3:].isdigit():
        return f"20{value[3:]}-{MONTH_CODES[value[:3]]:02d}"
    try:
        return datetime.strptime(value, "%Y-%m").strftime("%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use JUN26 or 2026-06") from exc


def parse_args(argv: list[str]) -> argparse.Namespace:
    entity = period = None
    remaining: list[str] = []
    for token in argv:
        upper = token.upper()
        if upper in {"-ICV", "-MBL"}:
            entity = upper[1:]
        elif upper.startswith("-") and len(upper) == 6 and upper[1:4] in MONTH_CODES:
            period = parse_period(upper)
        else:
            remaining.append(token)
    parser = argparse.ArgumentParser(description="Review or export an entity payroll")
    parser.add_argument("--entity", choices=ENTITY_CODES)
    parser.add_argument("--period", type=parse_period)
    parser.add_argument("--export-uff", type=Path, metavar="FILE")
    parser.add_argument("--payment-date", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parsed = parser.parse_args(remaining)
    parsed.entity = parsed.entity or entity or "ICV"
    parsed.period = parsed.period or period or "2026-06"
    return parsed


def money(cents: int | None) -> str:
    return "" if cents is None else f"{cents / 100:,.2f}"


def hours(minutes: int | None) -> str:
    return "" if minutes is None else f"{minutes / 60:,.2f}"


def full_time_contract_minutes(period: str) -> int:
    """Net scheduled minutes: Mon-Fri 7.5h, Sat 6.5h, Sun off."""
    year, month = (int(part) for part in period.split("-"))
    return sum(
        450 if date(year, month, day).weekday() < 5 else
        390 if date(year, month, day).weekday() == 5 else 0
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
    )


class NumericTableItem(QTableWidgetItem):
    """Sort formatted numeric cells by value rather than alphabetically."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return Decimal(self.text().replace(",", "") or "0") < Decimal(
                other.text().replace(",", "") or "0"
            )
        except InvalidOperation:
            return super().__lt__(other)


class PayrollWindow(QMainWindow):
    HEADERS = (
        "Employee", "Basis", "Base salary", "Allowance", "Hourly Rate",
        "Contract h (calc)", "Worked h", "Excess h (calc)",
        "Approved OT h", "OT pay (calc)", "Adjustments", "Externally funded (memo)", "Employer CPF",
        "Employee CPF", "SHG", "SDL", "Other deductions", "Gross (calculated)",
        "Net (calculated)", "Invoice Ref", "Default order",
    )
    EDITABLE_COLUMNS = frozenset((2, 3, 4, 6, 8, 10, 16, 19))

    def __init__(self, entity: str, period: str) -> None:
        super().__init__()
        self.setWindowTitle("Payroll Review and Edit")
        self.resize(1500, 760)
        self.loading = False
        self.rows_by_id: dict[int, dict[str, object]] = {}
        self.dirty_rows: set[int] = set()

        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("Payroll review and edit")
        title.setStyleSheet("font-size: 22px; font-weight: 600")
        layout.addWidget(title)
        warning = QLabel(
            "Draft editor — yellow cells are inputs; grey calculated fields are locked. "
            "CPF and statutory fields are calculated and locked. Adjustments are signed: "
            "use a negative amount to reduce nominal salary."
        )
        warning.setStyleSheet("color: #8a4b08; padding-bottom: 8px")
        layout.addWidget(warning)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Legal entity:"))
        self.entity = QComboBox()
        self.entity.addItems(ENTITY_CODES)
        self.entity.setCurrentText(entity)
        controls.addWidget(self.entity)
        controls.addWidget(QLabel("Pay period:"))
        self.period = QComboBox()
        self.period.addItems(self.available_periods())
        if period not in [self.period.itemText(i) for i in range(self.period.count())]:
            self.period.addItem(period)
        self.period.setCurrentText(period)
        controls.addWidget(self.period)
        reload_button = QPushButton("Discard edits / reload")
        reload_button.clicked.connect(self.reload_requested)
        controls.addWidget(reload_button)
        self.save_button = QPushButton("Save changes and recalculate")
        self.save_button.clicked.connect(self.save_changes)
        self.save_button.setEnabled(False)
        controls.addWidget(self.save_button)
        validate_button = QPushButton("Validate payroll")
        validate_button.clicked.connect(self.validate_current)
        controls.addWidget(validate_button)
        self.advance_button = QPushButton("Advance status")
        self.advance_button.clicked.connect(self.advance_status)
        controls.addWidget(self.advance_button)
        controls.addStretch()
        layout.addLayout(controls)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setColumnHidden(20, True)
        self.table.itemChanged.connect(self.input_changed)
        layout.addWidget(self.table)
        self.totals = QLabel()
        self.totals.setStyleSheet("font-size: 15px; font-weight: 600; padding: 8px")
        layout.addWidget(self.totals)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        self.entity.currentTextChanged.connect(self.selection_changed)
        self.period.currentTextChanged.connect(self.selection_changed)
        self.load_preview()

    def connect(self, writable: bool = False) -> sqlite3.Connection:
        if writable:
            connection = sqlite3.connect(DATABASE)
        else:
            connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def available_periods(self) -> list[str]:
        with self.connect() as connection:
            return [row[0][:7] for row in connection.execute(
                "SELECT period_start FROM payroll_runs ORDER BY period_start DESC"
            )] or ["2026-06"]

    def selection_changed(self) -> None:
        if self.dirty_rows:
            QMessageBox.information(self, "Unsaved changes", "Save or discard edits before changing payroll.")
            self.entity.blockSignals(True)
            self.period.blockSignals(True)
            first = next(iter(self.rows_by_id.values()), {})
            self.entity.setCurrentText(str(first.get("main_branch", self.entity.currentText())))
            self.period.setCurrentText(str(first.get("period", self.period.currentText())))
            self.entity.blockSignals(False)
            self.period.blockSignals(False)
            return
        self.load_preview()

    def reload_requested(self) -> None:
        if self.dirty_rows and QMessageBox.question(
            self, "Discard changes", "Discard all unsaved changes?",
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Discard:
            return
        self.load_preview()

    def load_preview(self) -> None:
        entity, period = self.entity.currentText(), self.period.currentText()
        if not entity or not period:
            return
        query = """SELECT p.*, e.legal_name, e.main_branch, r.status,
                           COALESCE((SELECT t.engagement_type FROM employment_terms t
                             WHERE t.employee_id=e.employee_id AND t.effective_from<=r.period_end
                             ORDER BY t.effective_from DESC LIMIT 1),'EMPLOYEE') engagement_type
                     FROM payroll_run_items p
                     JOIN payroll_runs r ON r.payroll_run_id=p.payroll_run_id
                     JOIN employees e ON e.employee_id=p.employee_id
                    WHERE substr(r.period_start,1,7)=? AND e.main_branch=?
                    ORDER BY CASE p.pay_basis WHEN 'MONTHLY' THEN 0 ELSE 1 END,
                             CASE WHEN p.pay_basis='MONTHLY' THEN p.base_pay_cents
                                  ELSE p.hourly_rate_cents END DESC,
                             e.legal_name"""
        try:
            with self.connect() as connection:
                rows = connection.execute(query, (period, entity)).fetchall()
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Cannot load payroll", str(exc))
            return

        self.loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        self.rows_by_id.clear()
        self.dirty_rows.clear()
        for index, row in enumerate(rows):
            stored = dict(row)
            stored["period"] = period
            item_id = int(row["payroll_run_item_id"])
            self.rows_by_id[item_id] = stored
            mom_hourly = None
            excess = None
            approved = row["approved_overtime_minutes"]
            calculated_ot = row["overtime_pay_cents"]
            if row["pay_basis"] == "MONTHLY" and row["base_pay_cents"] is not None:
                mom_hourly = int((Decimal(row["base_pay_cents"]) * 12 / (52 * 44)).quantize(
                    Decimal("1"), ROUND_HALF_UP))
                excess = max((row["worked_minutes"] or 0) - (row["expected_minutes"] or 0), 0)
                payable = min(excess, approved or 0)
                calculated_ot = int((Decimal(row["base_pay_cents"]) * 12 / (52 * 44) *
                                     MOM_OT_MULTIPLIER * payable / 60).quantize(
                    Decimal("1"), ROUND_HALF_UP))
            values = (
                row["legal_name"],
                ("FREELANCER" if row["engagement_type"] == "CONTRACT_FREELANCER" else row["pay_basis"]),
                money(row["base_pay_cents"]),
                money(row["allowance_cents"]),
                money(mom_hourly if row["pay_basis"] == "MONTHLY" else row["hourly_rate_cents"]),
                (hours(row["expected_minutes"]) if row["pay_basis"] == "MONTHLY" else ""),
                hours(row["worked_minutes"]), hours(excess),
                hours(approved), money(calculated_ot), money(row["pay_adjustment_cents"]),
                money(row["external_funded_cents"]),
                money(row["employer_cpf_cents"]),
                money(row["employee_cpf_cents"]), money(row["shg_contribution_cents"]), money(row["sdl_cents"]),
                money(row["other_deductions_cents"]), money(row["gross_pay_cents"]), money(row["net_pay_cents"]),
                row["invoice_reference"] or "",
                (1_000_000_000 + (row["base_pay_cents"] or 0)
                 if row["pay_basis"] == "MONTHLY" else (row["hourly_rate_cents"] or 0)),
            )
            for column, value in enumerate(values):
                item = NumericTableItem(str(value or "")) if column >= 2 else QTableWidgetItem(str(value or ""))
                item.setData(Qt.UserRole, item_id)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                editable = column in self.EDITABLE_COLUMNS and row["status"] == "DRAFT"
                if ((row["pay_basis"] == "MONTHLY" and column == 4) or
                        (row["pay_basis"] == "HOURLY" and column in (2, 3, 5, 8))):
                    editable = False
                if column == 19 and row["engagement_type"] != "CONTRACT_FREELANCER":
                    editable = False
                if row["engagement_type"] == "CONTRACT_FREELANCER" and column in (12, 13):
                    editable = False
                if editable:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                    item.setBackground(QColor("#fff4bf"))
                else:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if column >= 2:
                        item.setBackground(QColor("#e8e8e8"))
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        self.table.sortItems(20, Qt.DescendingOrder)
        self.loading = False
        self.save_button.setEnabled(False)
        self.update_totals()
        status = rows[0]["status"] if rows else "NO RUN"
        self.status.setText(f"{entity} · {period} · {len(rows)} employee(s) · {status}")

    @staticmethod
    def parse_decimal(text: str, label: str, allow_negative: bool = False) -> Decimal | None:
        text = text.replace(",", "").strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{label} must be a number") from exc
        if not value.is_finite() or (value < 0 and not allow_negative):
            raise ValueError(f"{label} cannot be negative")
        return value

    @classmethod
    def parse_money(cls, text: str, label: str) -> int | None:
        value = cls.parse_decimal(text, label)
        return None if value is None else int((value * 100).quantize(Decimal("1"), ROUND_HALF_UP))

    @classmethod
    def parse_money_signed(cls, text: str, label: str) -> int | None:
        value = cls.parse_decimal(text, label, allow_negative=True)
        return None if value is None else int((value * 100).quantize(Decimal("1"), ROUND_HALF_UP))

    @classmethod
    def parse_minutes(cls, text: str, label: str, allow_negative: bool = False) -> int | None:
        value = cls.parse_decimal(text, label, allow_negative)
        return None if value is None else int((value * 60).quantize(Decimal("1"), ROUND_HALF_UP))

    def values_for_row(self, row: int) -> dict[str, int | None]:
        cell = lambda column: self.table.item(row, column).text()
        return {
            "base_pay_cents": self.parse_money(cell(2), "Base pay"),
            "allowance_cents": self.parse_money(cell(3), "Allowance") or 0,
            "hourly_rate_cents": (self.parse_money(cell(4), "Hourly rate")
                                  if self.record_for_row(row)["pay_basis"] == "HOURLY" else None),
            "expected_minutes": (self.parse_minutes(cell(5), "Contract hours")
                                 if self.record_for_row(row)["pay_basis"] == "MONTHLY" else None),
            "worked_minutes": self.parse_minutes(cell(6), "Worked hours"),
            "approved_overtime_minutes": self.parse_minutes(cell(8), "Approved OT hours"),
            "pay_adjustment_cents": self.parse_money_signed(cell(10), "Adjustments") or 0,
            "employer_cpf_cents": self.parse_money(cell(12), "Employer CPF") or 0,
            "employee_cpf_cents": self.parse_money(cell(13), "Employee CPF") or 0,
            "other_deductions_cents": self.parse_money(cell(16), "Other deductions") or 0,
        }

    def record_for_row(self, row: int) -> dict[str, object]:
        return self.rows_by_id[int(self.table.item(row, 0).data(Qt.UserRole))]

    def calculate_row(self, row: int) -> tuple[dict[str, int | None], int, int]:
        values = self.values_for_row(row)
        if self.record_for_row(row)["pay_basis"] == "MONTHLY":
            if values["base_pay_cents"] is None:
                raise ValueError("Monthly employee requires base pay")
            contract = values["expected_minutes"] or 0
            worked = values["worked_minutes"] or 0
            approved = values["approved_overtime_minutes"] or 0
            excess = max(worked - contract, 0)
            payable = min(excess, approved)
            mom_hourly = Decimal(values["base_pay_cents"]) * 12 / (52 * 44)
            overtime_pay = int((mom_hourly * MOM_OT_MULTIPLIER * payable / 60).quantize(
                Decimal("1"), ROUND_HALF_UP))
            gross = (values["base_pay_cents"] + values["allowance_cents"] + overtime_pay +
                     values["pay_adjustment_cents"])
        else:
            if values["hourly_rate_cents"] is None or values["worked_minutes"] is None:
                raise ValueError("Hourly employee requires hourly rate and worked hours")
            gross = int((Decimal(values["hourly_rate_cents"]) * Decimal(values["worked_minutes"]) /
                         60).quantize(Decimal("1"), ROUND_HALF_UP)) + values["pay_adjustment_cents"]
            excess = overtime_pay = 0
        shg = self.parse_money(self.table.item(row, 14).text(), "SHG") or 0
        net = gross - values["employee_cpf_cents"] - shg - values["other_deductions_cents"]
        if gross < 0 or net < 0:
            raise ValueError("Calculated gross and net pay cannot be negative")
        values["overtime_minutes"] = excess
        values["overtime_pay_cents"] = overtime_pay
        return values, gross, net

    def input_changed(self, item: QTableWidgetItem) -> None:
        if self.loading or item.column() not in self.EDITABLE_COLUMNS:
            return
        row = item.row()
        try:
            _, gross, net = self.calculate_row(row)
        except ValueError:
            item.setBackground(QColor("#ffb3b3"))
            self.status.setText("Correct the red input before saving")
            self.save_button.setEnabled(False)
            return
        self.loading = True
        item.setBackground(QColor("#fff4bf"))
        values, gross, net = self.calculate_row(row)
        base = values["base_pay_cents"]
        mom_hourly = None if base is None or self.record_for_row(row)["pay_basis"] != "MONTHLY" else int(
            (Decimal(base) * 12 / (52 * 44)).quantize(Decimal("1"), ROUND_HALF_UP))
        if mom_hourly is not None:
            self.table.item(row, 4).setText(money(mom_hourly))
        self.table.item(row, 7).setText(hours(values["overtime_minutes"]))
        self.table.item(row, 9).setText(money(values["overtime_pay_cents"]))
        self.table.item(row, 17).setText(money(gross))
        self.table.item(row, 18).setText(money(net))
        self.loading = False
        self.dirty_rows.add(int(item.data(Qt.UserRole)))
        self.save_button.setEnabled(True)
        self.update_totals()
        self.status.setText(f"{len(self.dirty_rows)} changed employee(s) — not saved")

    def update_totals(self) -> None:
        def total(column: int) -> int:
            result = 0
            for row in range(self.table.rowCount()):
                try:
                    result += self.parse_money(self.table.item(row, column).text(), "total") or 0
                except ValueError:
                    pass
            return result
        self.totals.setText(
            f"Gross: ${money(total(17))}    Allowances: ${money(total(3))}    "
            f"Employer CPF: ${money(total(12))}    Employee CPF: ${money(total(13))}    "
            f"SHG: ${money(total(14))}    SDL: ${money(total(15))}    "
            f"Other deductions: ${money(total(16))}    Net pay: ${money(total(18))}"
        )

    def save_changes(self) -> None:
        if not self.dirty_rows:
            return
        try:
            row_for_id = {int(self.table.item(row, 0).data(Qt.UserRole)): row
                          for row in range(self.table.rowCount())}
            updates = [(self.calculate_row(row_for_id[item_id]), item_id)
                       for item_id in sorted(self.dirty_rows)]
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid payroll input", str(exc))
            return
        if QMessageBox.question(
            self, "Save payroll changes", f"Recalculate and save {len(updates)} employee record(s)?",
            QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Save:
            return
        reason, accepted = QInputDialog.getText(
            self, "Reason for payroll change", "Reason (required for the audit record):"
        )
        if not accepted or not reason.strip():
            return
        sql = """UPDATE payroll_run_items SET base_pay_cents=?, allowance_cents=?,
                 hourly_rate_cents=?, expected_minutes=?, worked_minutes=?, approved_overtime_minutes=?,
                 pay_adjustment_cents=?, employer_cpf_cents=?, employee_cpf_cents=?,
                 other_deductions_cents=?, overtime_minutes=?, overtime_pay_cents=?,
                 gross_pay_cents=?, net_pay_cents=?,
                 source=source || ' | manually edited in payroll UI'
                 WHERE payroll_run_item_id=? AND payroll_run_id IN
                   (SELECT payroll_run_id FROM payroll_runs WHERE status='DRAFT')"""
        try:
            with self.connect(writable=True) as connection:
                for (values, gross, net), item_id in updates:
                    old_row = connection.execute(
                        "SELECT * FROM payroll_run_items WHERE payroll_run_item_id=?", (item_id,)
                    ).fetchone()
                    cursor = connection.execute(sql, (*values.values(), gross, net, item_id))
                    if cursor.rowcount != 1:
                        raise sqlite3.IntegrityError("Payroll is no longer editable or record is missing")
                    visible_row = next(
                        row for row in range(self.table.rowCount())
                        if int(self.table.item(row, 0).data(Qt.UserRole)) == item_id
                    )
                    connection.execute(
                        "UPDATE payroll_run_items SET invoice_reference=? WHERE payroll_run_item_id=?",
                        (self.table.item(visible_row, 19).text().strip() or None, item_id),
                    )
                    connection.execute(
                        """INSERT INTO payroll_audit_events
                           (payroll_run_id,payroll_run_item_id,event_type,old_value,new_value,reason,actor)
                           VALUES(?,?,?,?,?,?,?)""",
                        (old_row["payroll_run_id"], item_id, "MANUAL_EDIT",
                         json.dumps(dict(old_row), default=str, sort_keys=True),
                         json.dumps({**values, "gross_pay_cents": gross, "net_pay_cents": net},
                                    default=str, sort_keys=True),
                         reason.strip(), getpass.getuser()),
                    )
                    sync_item_components(connection, item_id, "manual payroll UI edit")
        except sqlite3.Error as exc:
            QMessageBox.critical(self, "Could not save payroll", str(exc))
            return
        count = len(updates)
        self.load_preview()
        QMessageBox.information(self, "Payroll saved", f"Saved {count} employee record(s).")

    def current_run_id(self) -> int | None:
        first = next(iter(self.rows_by_id.values()), None)
        return int(first["payroll_run_id"]) if first else None

    def validate_current(self) -> None:
        run_id = self.current_run_id()
        if run_id is None:
            return
        with self.connect(writable=True) as connection:
            issues = validate_run(connection, run_id)
        blockers = [issue for issue in issues if issue.severity == "BLOCKER"]
        warnings = [issue for issue in issues if issue.severity == "WARNING"]
        details = "\n".join(f"[{issue.severity}] {issue.message}" for issue in issues[:20])
        if len(issues) > 20:
            details += f"\n…and {len(issues)-20} more issue(s)."
        QMessageBox.information(
            self, "Payroll validation",
            f"{len(blockers)} blocker(s), {len(warnings)} warning(s).\n\n{details or 'No issues found.'}",
        )

    def advance_status(self) -> None:
        run_id = self.current_run_id()
        if run_id is None:
            return
        current = str(next(iter(self.rows_by_id.values()))["status"])
        target = TRANSITIONS.get(current)
        if not target:
            QMessageBox.information(self, "Payroll status", f"No automatic transition follows {current}.")
            return
        reason, accepted = QInputDialog.getText(
            self, "Advance payroll", f"Reason for {current} → {target}:"
        )
        if not accepted or not reason.strip():
            return
        try:
            with self.connect(writable=True) as connection:
                transition_run(connection, run_id, target, getpass.getuser(), reason.strip())
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Cannot advance payroll", str(exc))
            return
        self.load_preview()


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.export_uff:
        if not args.payment_date:
            raise SystemExit("--payment-date YYYY-MM-DD is required with --export-uff")
        try:
            entity = load_entity(ENTITY_CONFIG, args.entity)
            rows = payroll_rows(DATABASE, args.entity, args.period)
            content = build_uff(entity, rows, args.payment_date)
            write_uff(args.export_uff, content)
        except UffValidationError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"DBS UFF v2.1 file created: {args.export_uff} ({len(rows)} payments)")
        return
    app = QApplication(sys.argv[:1])
    window = PayrollWindow(args.entity, args.period)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
