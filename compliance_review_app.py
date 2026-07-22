"""Review and safely update employee residency/work-pass classifications."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


PROJECT = Path(__file__).resolve().parent
DATABASE = PROJECT / "data" / "employees.db"
BACKUP_DIR = PROJECT / "data" / "backups"
RESIDENCY_LABELS = {
    "Needs review": "CITIZEN_OR_PR_REVIEW",
    "Singapore Citizen": "CITIZEN",
    "Permanent Resident": "PR",
    "Foreigner": "FOREIGNER",
}
PASS_TYPES = ["", "EP", "SP", "WP", "DP", "LTVP", "LOC", "OTHER"]


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def create_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"employees_before_compliance_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    source = sqlite3.connect(DATABASE, timeout=20)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def ensure_audit_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS compliance_review_audit (
               audit_id INTEGER PRIMARY KEY,
               employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
               effective_from TEXT NOT NULL,
               previous_values TEXT NOT NULL,
               new_values TEXT NOT NULL,
               reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )


class ComplianceReviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.employee_id: int | None = None
        self.rows: list[sqlite3.Row] = []
        self.setWindowTitle("Personnel Compliance Review")
        self.resize(1180, 720)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        heading = QLabel("Residency and work-pass review")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        outer.addWidget(heading)

        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search employee, role, or branch")
        self.search.textChanged.connect(self.load_rows)
        self.provisional_only = QCheckBox("Show provisional records only")
        self.provisional_only.setChecked(True)
        self.provisional_only.toggled.connect(self.load_rows)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load_rows)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.provisional_only)
        toolbar.addWidget(refresh)
        outer.addLayout(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Employee", "Role", "Branch", "Current status", "Pass", "Effective from"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self.select_row)
        self.table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.table)

        editor = QWidget()
        form = QFormLayout(editor)
        self.selected_name = QLabel("Select an employee")
        self.selected_name.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.residency = QComboBox()
        self.residency.addItems(RESIDENCY_LABELS)
        self.pass_type = QComboBox()
        self.pass_type.addItems(PASS_TYPES)
        self.effective_date = QDateEdit()
        self.effective_date.setCalendarPopup(True)
        self.effective_date.setDate(date.today())
        self.pass_expiry = QDateEdit()
        self.pass_expiry.setCalendarPopup(True)
        self.pass_expiry.setSpecialValueText("Not set")
        self.pass_expiry.setMinimumDate(date(1900, 1, 1))
        self.pass_expiry.setDate(date(1900, 1, 1))
        self.cpf = QCheckBox("CPF applicable")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional review note")
        self.save_button = QPushButton("Save reviewed status")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        self.residency.currentTextChanged.connect(self.sync_fields)
        form.addRow(self.selected_name)
        form.addRow("Residency status", self.residency)
        form.addRow("Work-pass type", self.pass_type)
        form.addRow("Effective date", self.effective_date)
        form.addRow("Pass expiry", self.pass_expiry)
        form.addRow("CPF", self.cpf)
        form.addRow("Notes", self.notes)
        form.addRow(self.save_button)
        splitter.addWidget(editor)
        splitter.setSizes([820, 340])

        self.status = QLabel()
        outer.addWidget(self.status)
        self.load_rows()

    def load_rows(self) -> None:
        query = """
            SELECT e.employee_id, e.legal_name, e.role, e.main_branch,
                   cp.residency_status, cp.work_pass_type, cp.effective_from,
                   cp.pass_expiry_date, cp.cpf_applicable
            FROM employees e
            JOIN compliance_profiles cp ON cp.compliance_profile_id = (
                SELECT cp2.compliance_profile_id FROM compliance_profiles cp2
                WHERE cp2.employee_id=e.employee_id
                ORDER BY cp2.effective_from DESC, cp2.compliance_profile_id DESC LIMIT 1
            )
            WHERE (?=0 OR cp.residency_status='CITIZEN_OR_PR_REVIEW')
              AND (e.legal_name LIKE ? OR coalesce(e.role,'') LIKE ? OR coalesce(e.main_branch,'') LIKE ?)
            ORDER BY e.legal_name
        """
        term = f"%{self.search.text().strip()}%"
        with connect() as connection:
            self.rows = connection.execute(
                query, (int(self.provisional_only.isChecked()), term, term, term)
            ).fetchall()
        self.table.setRowCount(len(self.rows))
        for row_index, row in enumerate(self.rows):
            values = [
                row["legal_name"], row["role"], row["main_branch"],
                row["residency_status"], row["work_pass_type"], row["effective_from"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value or "")
                item.setData(Qt.UserRole, row["employee_id"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self.status.setText(f"{len(self.rows)} record(s) shown")

    def select_row(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = self.rows[selected[0].row()]
        self.employee_id = int(row["employee_id"])
        self.selected_name.setText(row["legal_name"])
        label = next(
            (label for label, value in RESIDENCY_LABELS.items() if value == row["residency_status"]),
            "Needs review",
        )
        self.residency.setCurrentText(label)
        self.pass_type.setCurrentText(row["work_pass_type"] or "")
        self.effective_date.setDate(date.fromisoformat(row["effective_from"]))
        expiry = row["pass_expiry_date"]
        self.pass_expiry.setDate(date.fromisoformat(expiry) if expiry else date(1900, 1, 1))
        self.cpf.setChecked(bool(row["cpf_applicable"]))
        self.notes.clear()
        self.save_button.setEnabled(True)
        self.sync_fields()

    def sync_fields(self) -> None:
        foreigner = self.residency.currentText() == "Foreigner"
        self.pass_type.setEnabled(foreigner)
        self.pass_expiry.setEnabled(foreigner)
        if not foreigner:
            self.pass_type.setCurrentText("")
            self.pass_expiry.setDate(date(1900, 1, 1))
            self.cpf.setChecked(self.residency.currentText() in {"Singapore Citizen", "Permanent Resident"})

    def save(self) -> None:
        if self.employee_id is None:
            return
        residency = RESIDENCY_LABELS[self.residency.currentText()]
        pass_type = self.pass_type.currentText() or None
        effective = self.effective_date.date().toPyDate()
        expiry = self.pass_expiry.date().toPyDate()
        expiry_text = None if expiry == date(1900, 1, 1) else expiry.isoformat()
        if residency == "CITIZEN_OR_PR_REVIEW":
            QMessageBox.warning(self, "Not reviewed", "Choose a confirmed residency status.")
            return
        if residency == "FOREIGNER" and not pass_type:
            QMessageBox.warning(self, "Pass required", "Choose the employee's work-pass type.")
            return
        if residency != "FOREIGNER":
            pass_type = expiry_text = None
        backup = create_backup()
        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            ensure_audit_table(connection)
            previous = connection.execute(
                "SELECT * FROM compliance_profiles WHERE employee_id=? ORDER BY effective_from DESC LIMIT 1",
                (self.employee_id,),
            ).fetchone()
            previous_json = json.dumps(dict(previous) if previous else {}, sort_keys=True)
            previous_day = (effective - timedelta(days=1)).isoformat()
            connection.execute(
                """UPDATE compliance_profiles SET effective_to=?
                   WHERE employee_id=? AND effective_from<?
                     AND (effective_to IS NULL OR effective_to>=?)""",
                (previous_day, self.employee_id, effective.isoformat(), effective.isoformat()),
            )
            source = "Compliance review interface"
            if self.notes.text().strip():
                source += f": {self.notes.text().strip()}"
            connection.execute(
                """INSERT INTO compliance_profiles
                   (employee_id,effective_from,residency_status,work_pass_type,
                    pass_start_date,pass_expiry_date,cpf_applicable,
                    tax_clearance_required,source)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(employee_id,effective_from) DO UPDATE SET
                     effective_to=NULL,residency_status=excluded.residency_status,
                     work_pass_type=excluded.work_pass_type,
                     pass_start_date=excluded.pass_start_date,
                     pass_expiry_date=excluded.pass_expiry_date,
                     cpf_applicable=excluded.cpf_applicable,
                     tax_clearance_required=excluded.tax_clearance_required,
                     source=excluded.source""",
                (
                    self.employee_id, effective.isoformat(), residency, pass_type,
                    effective.isoformat() if residency == "FOREIGNER" else None,
                    expiry_text, int(self.cpf.isChecked()),
                    int(residency == "FOREIGNER"), source,
                ),
            )
            visa_status = {"CITIZEN": "CITIZEN", "PR": "PR"}.get(residency, pass_type)
            connection.execute(
                "UPDATE employees SET visa_status=?,updated_at=CURRENT_TIMESTAMP WHERE employee_id=?",
                (visa_status, self.employee_id),
            )
            new_values = {
                "residency_status": residency, "work_pass_type": pass_type,
                "effective_from": effective.isoformat(), "pass_expiry_date": expiry_text,
                "cpf_applicable": int(self.cpf.isChecked()), "source": source,
            }
            connection.execute(
                """INSERT INTO compliance_review_audit
                   (employee_id,effective_from,previous_values,new_values)
                   VALUES (?,?,?,?)""",
                (self.employee_id, effective.isoformat(), previous_json, json.dumps(new_values, sort_keys=True)),
            )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            QMessageBox.critical(self, "Save failed", f"No changes were committed.\n\n{exc}")
            return
        finally:
            connection.close()
        QMessageBox.information(
            self, "Saved", f"Compliance status saved.\nBackup: {backup.name}"
        )
        self.employee_id = None
        self.selected_name.setText("Select an employee")
        self.save_button.setEnabled(False)
        self.load_rows()


def main() -> None:
    app = QApplication(sys.argv)
    window = ComplianceReviewWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
