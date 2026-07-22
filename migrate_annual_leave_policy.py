"""Store the effective-dated company annual-leave tenure policy."""

import sqlite3
from pathlib import Path

database = Path(__file__).parent / "data" / "employees.db"
connection = sqlite3.connect(database)

try:
    connection.execute("BEGIN")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS leave_policies (
               leave_policy_id INTEGER PRIMARY KEY,
               leave_type TEXT NOT NULL,
               effective_from TEXT NOT NULL,
               effective_to TEXT,
               eligibility_months INTEGER NOT NULL,
               first_service_year_days TEXT NOT NULL,
               days_per_completed_year TEXT NOT NULL,
               maximum_days TEXT NOT NULL,
               allocation_basis TEXT NOT NULL,
               source TEXT NOT NULL,
               created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
               UNIQUE(leave_type, effective_from)
           )"""
    )
    connection.execute(
        """INSERT INTO leave_policies
           (leave_type, effective_from, eligibility_months,
            first_service_year_days, days_per_completed_year, maximum_days,
            allocation_basis, source)
           VALUES ('ANNUAL_LEAVE', '2026-01-01', 3, '11', '1', '14',
                   'SERVICE_ANNIVERSARY',
                   'Company policy: 11-day minimum plus one per completed year, capped at 14')
           ON CONFLICT(leave_type, effective_from) DO UPDATE SET
             eligibility_months=excluded.eligibility_months,
             first_service_year_days=excluded.first_service_year_days,
             days_per_completed_year=excluded.days_per_completed_year,
             maximum_days=excluded.maximum_days,
             allocation_basis=excluded.allocation_basis,
             source=excluded.source"""
    )
    connection.commit()
    total = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    with_join_date = connection.execute(
        "SELECT COUNT(*) FROM employees WHERE join_date IS NOT NULL AND trim(join_date) != ''"
    ).fetchone()[0]
    print("ANNUAL_LEAVE_POLICY=11_PLUS_1_PER_COMPLETED_YEAR_CAP_14")
    print(f"EMPLOYEES_WITH_JOIN_DATE={with_join_date}")
    print(f"EMPLOYEES_MISSING_JOIN_DATE={total - with_join_date}")
finally:
    connection.close()
