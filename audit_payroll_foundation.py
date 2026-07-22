import sqlite3
from pathlib import Path

database = Path(__file__).parent / "data" / "employees.db"
connection = sqlite3.connect(database)
try:
    checks = {
        "INTEGRITY": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "EMPLOYEES": connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
        "TERMS": connection.execute("SELECT COUNT(*) FROM employment_terms").fetchone()[0],
        "COMPLIANCE": connection.execute("SELECT COUNT(*) FROM compliance_profiles").fetchone()[0],
        "ATTENDANCE": connection.execute("SELECT COUNT(*) FROM attendance_entries").fetchone()[0],
        "PENDING_ADJUSTMENTS": connection.execute(
            "SELECT COUNT(*) FROM attendance_adjustments WHERE approved_at IS NULL"
        ).fetchone()[0],
        "UNMATCHED_PEOPLE": connection.execute(
            """SELECT COUNT(DISTINCT source_employee_name)
               FROM attendance_entries
               WHERE employee_id IS NULL
                 AND source_employee_name != 'Park Backerei, General'"""
        ).fetchone()[0],
        "LOCAL_STATUS_REVIEW": connection.execute(
            """SELECT COUNT(*) FROM compliance_profiles
               WHERE residency_status='CITIZEN_OR_PR_REVIEW'"""
        ).fetchone()[0],
        "CONFIRMED_WP": connection.execute(
            "SELECT COUNT(*) FROM compliance_profiles WHERE work_pass_type='WP'"
        ).fetchone()[0],
    }
    for name, value in checks.items():
        print(f"{name}={value}")
finally:
    connection.close()
