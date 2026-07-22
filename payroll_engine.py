"""Core payroll validation, lifecycle and statutory helper services."""

from __future__ import annotations

import calendar
import hashlib
import json
import sqlite3
from decimal import ROUND_FLOOR
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


DATABASE = Path(__file__).resolve().parent / "data" / "employees.db"
MOM_OT_MULTIPLIER = Decimal("1.5")
CPF_OW_CEILING_CENTS_2026 = 800_000


def cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), ROUND_HALF_UP))


def age_at(birth_date: date, at_date: date) -> int:
    return at_date.year - birth_date.year - ((at_date.month, at_date.day) < (birth_date.month, birth_date.day))


def cpf_rates_2026(age: int) -> tuple[Decimal, Decimal]:
    """Return full-rate employer and employee percentages for wages above $750."""
    if age <= 55:
        return Decimal("0.17"), Decimal("0.20")
    if age <= 60:
        return Decimal("0.16"), Decimal("0.18")
    if age <= 65:
        return Decimal("0.125"), Decimal("0.125")
    if age <= 70:
        return Decimal("0.09"), Decimal("0.075")
    return Decimal("0.075"), Decimal("0.05")


def _nearest_dollar(value_cents: Decimal) -> int:
    return int((value_cents / 100).quantize(Decimal("1"), ROUND_HALF_UP)) * 100


def _floor_dollar(value_cents: Decimal) -> int:
    return int((value_cents / 100).quantize(Decimal("1"), ROUND_FLOOR)) * 100


def _cpf_parameters(age: int, scheme: str, pr_year: int | None) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Low-wage total rate, transition coefficient, high total and employee rates."""
    if scheme == "GRADUATED" and pr_year == 1:
        if age <= 60:
            return Decimal(".04"), Decimal(".15"), Decimal(".09"), Decimal(".05")
        return Decimal(".035"), Decimal(".15"), Decimal(".085"), Decimal(".05")
    if scheme == "GRADUATED" and pr_year == 2:
        if age <= 55:
            return Decimal(".09"), Decimal(".45"), Decimal(".24"), Decimal(".15")
        if age <= 60:
            return Decimal(".06"), Decimal(".375"), Decimal(".185"), Decimal(".125")
        if age <= 65:
            return Decimal(".035"), Decimal(".225"), Decimal(".11"), Decimal(".075")
        return Decimal(".035"), Decimal(".15"), Decimal(".085"), Decimal(".05")
    employer, employee = cpf_rates_2026(age)
    transition = (Decimal(".60") if age <= 55 else Decimal(".54") if age <= 60 else
                  Decimal(".375") if age <= 65 else Decimal(".225") if age <= 70 else Decimal(".15"))
    return employer, transition, employer + employee, employee


def calculate_cpf_2026(ordinary_wage_cents: int, employee_age: int,
                       scheme: str = "FULL", pr_year: int | None = None) -> tuple[int, int]:
    """Calculate employer/employee CPF using official 2026 OW tables and rounding."""
    wage = min(max(ordinary_wage_cents, 0), CPF_OW_CEILING_CENTS_2026)
    low_total_rate, transition, high_total_rate, employee_rate = _cpf_parameters(
        employee_age, scheme, pr_year
    )
    if wage <= 5_000:
        return 0, 0
    if wage <= 50_000:
        total = _nearest_dollar(Decimal(wage) * low_total_rate)
        return total, 0
    if wage <= 75_000:
        total = _nearest_dollar(Decimal(wage) * low_total_rate + Decimal(wage - 50_000) * transition)
        employee = _floor_dollar(Decimal(wage - 50_000) * transition)
        return total - employee, employee
    total = _nearest_dollar(Decimal(wage) * high_total_rate)
    employee = _floor_dollar(Decimal(wage) * employee_rate)
    return total - employee, employee


def calculate_sdl(wages_cents: int) -> int:
    """0.25% of wages, minimum $2 and maximum $11.25 per employee."""
    return max(200, min(1125, cents(Decimal(max(wages_cents, 0)) * Decimal("0.0025"))))


def calculate_shg(fund: str | None, total_wages_cents: int) -> int:
    wage = max(total_wages_cents, 0)
    bands = {
        "CDAC": ((200_000,50),(350_000,100),(500_000,150),(750_000,200),(10**18,300)),
        "ECF": ((100_000,200),(150_000,400),(250_000,600),(400_000,900),(700_000,1200),
                (1_000_000,1600),(10**18,2000)),
        "MBMF": ((100_000,300),(200_000,450),(300_000,650),(400_000,1500),(600_000,1950),
                 (800_000,2200),(1_000_000,2400),(10**18,2600)),
        "SINDA": ((100_000,100),(150_000,300),(250_000,500),(450_000,700),(750_000,900),
                  (1_000_000,1200),(1_500_000,1800),(10**18,3000)),
    }
    if fund not in bands:
        return 0
    return next(amount for ceiling, amount in bands[fund] if wage <= ceiling)


def spr_year(spr_start: date, payroll_month: date) -> int:
    difference = (payroll_month.year - spr_start.year) * 12 + payroll_month.month - spr_start.month
    return 1 if difference <= 12 else 2 if difference <= 24 else 3


def month_dates(period: str) -> list[date]:
    year, month = map(int, period.split("-"))
    return [date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1)]


def scheduled_minutes(day: date) -> int:
    return 450 if day.weekday() < 5 else 390 if day.weekday() == 5 else 0


def build_work_entries(connection: sqlite3.Connection, payroll_run_id: int) -> int:
    run = connection.execute("SELECT * FROM payroll_runs WHERE payroll_run_id=?", (payroll_run_id,)).fetchone()
    if not run:
        raise ValueError("Payroll run does not exist")
    period = run["period_start"][:7]
    created = 0
    employees = connection.execute(
        """SELECT p.employee_id,p.pay_basis FROM payroll_run_items p
            WHERE p.payroll_run_id=?""", (payroll_run_id,)
    ).fetchall()
    for employee in employees:
        attendance = {row[0]: row[1] for row in connection.execute(
            """SELECT work_date,SUM(worked_minutes) FROM attendance_entries
                WHERE employee_id=? AND substr(work_date,1,7)=? GROUP BY work_date""",
            (employee["employee_id"], period),
        )}
        for day in month_dates(period):
            day_text = day.isoformat()
            worked = attendance.get(day_text, 0)
            planned = scheduled_minutes(day) if employee["pay_basis"] == "MONTHLY" else 0
            entry_type = "NORMAL" if planned else "REST_DAY_WORK" if worked else "NORMAL"
            connection.execute(
                """INSERT INTO work_entries(employee_id,work_date,entry_type,scheduled_minutes,
                   worked_minutes,break_minutes,source) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(employee_id,work_date,entry_type) DO UPDATE SET
                   scheduled_minutes=excluded.scheduled_minutes,worked_minutes=excluded.worked_minutes""",
                (employee["employee_id"], day_text, entry_type, planned, worked,
                 60 if planned or worked else 0, "attendance_entries + contractual schedule"),
            )
            created += 1
    return created


def sync_item_components(connection: sqlite3.Connection, item_id: int,
                         source: str = "payroll engine") -> None:
    row = connection.execute("SELECT * FROM payroll_run_items WHERE payroll_run_item_id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("Payroll item does not exist")
    amounts = {
        "BASIC": row["base_pay_cents"] if row["pay_basis"] == "MONTHLY" else 0,
        "HOURLY": row["gross_pay_cents"] if row["pay_basis"] == "HOURLY" else 0,
        "ALLOWANCE_FIXED": row["allowance_cents"], "OVERTIME": row["overtime_pay_cents"],
        "NS_MAKEUP": row["ns_makeup_pay_cents"],
        "PAYROLL_ADJUSTMENT": row["pay_adjustment_cents"],
        "EXTERNAL_FUNDED": row["external_funded_cents"],
        "EMPLOYEE_CPF": row["employee_cpf_cents"],
        "OTHER_DEDUCTION": row["other_deductions_cents"], "EMPLOYER_CPF": row["employer_cpf_cents"],
        "SHG": row["shg_contribution_cents"], "SDL": row["sdl_cents"],
    }
    for component, amount in amounts.items():
        if amount:
            connection.execute(
                """INSERT INTO payroll_item_components
                   (payroll_run_item_id,component_code,amount_cents,source)
                   VALUES(?,?,?,?) ON CONFLICT(payroll_run_item_id,component_code) DO UPDATE SET
                   amount_cents=excluded.amount_cents,source=excluded.source""",
                (item_id, component, amount, source),
            )
        else:
            connection.execute(
                "DELETE FROM payroll_item_components WHERE payroll_run_item_id=? AND component_code=?",
                (item_id, component),
            )


def calculate_verified_statutory(connection: sqlite3.Connection, item_id: int) -> dict[str, int]:
    """Calculate CPF/SDL only when the employee's statutory identity is verified."""
    row = connection.execute(
        """SELECT p.*,e.date_of_birth,c.residency_status,c.cpf_applicable,c.source compliance_source,
                  c.pr_contribution_scheme,c.pr_effective_date
             FROM payroll_run_items p JOIN employees e USING(employee_id)
             LEFT JOIN compliance_profiles c ON c.employee_id=e.employee_id
            WHERE p.payroll_run_item_id=? ORDER BY c.effective_from DESC LIMIT 1""", (item_id,),
    ).fetchone()
    if not row:
        raise ValueError("Payroll item does not exist")
    if not row["cpf_applicable"]:
        return {"employer_cpf_cents": 0, "employee_cpf_cents": 0,
                "sdl_cents": calculate_sdl(row["gross_pay_cents"])}
    if not row["date_of_birth"] or not row["residency_status"]:
        raise ValueError("Verified date of birth and residency are required for CPF")
    if row["compliance_source"] and "unverified" in row["compliance_source"].lower():
        raise ValueError("Statutory profile is explicitly marked unverified")
    period_end = date.fromisoformat(connection.execute(
        "SELECT period_end FROM payroll_runs WHERE payroll_run_id=?", (row["payroll_run_id"],)
    ).fetchone()[0])
    employee_age = age_at(date.fromisoformat(row["date_of_birth"]), period_end.replace(day=1) - timedelta(days=1))
    scheme = "FULL"
    pr_year = None
    if row["residency_status"] == "PR":
        if not row["pr_effective_date"] or row["pr_contribution_scheme"] not in {"FULL", "GRADUATED"}:
            raise ValueError("PR effective date and CPF contribution scheme are required")
        scheme = row["pr_contribution_scheme"]
        pr_year = spr_year(date.fromisoformat(row["pr_effective_date"]), period_end.replace(day=1))
    employer, employee = calculate_cpf_2026(
        row["cpf_wage_base_cents"] or row["gross_pay_cents"], employee_age, scheme, pr_year
    )
    return {"employer_cpf_cents": employer, "employee_cpf_cents": employee,
            "sdl_cents": calculate_sdl(row["gross_pay_cents"])}


def reconcile_statutory_run(connection: sqlite3.Connection, payroll_run_id: int) -> list[sqlite3.Row]:
    """Calculate without overwriting payroll; persist a legacy-versus-2026 reconciliation."""
    rows = connection.execute(
        """SELECT p.payroll_run_item_id,p.gross_pay_cents,p.cpf_wage_base_cents,
                  p.employer_cpf_cents,p.employee_cpf_cents,p.other_deductions_cents,
                  e.legal_name,e.employee_id,
                  COALESCE((SELECT t.engagement_type FROM employment_terms t
                    WHERE t.employee_id=e.employee_id AND t.effective_from<=r.period_end
                    ORDER BY t.effective_from DESC LIMIT 1),'EMPLOYEE') engagement_type,
                  c.cpf_applicable,c.shg_fund
             FROM payroll_run_items p JOIN payroll_runs r USING(payroll_run_id)
             JOIN employees e USING(employee_id)
             LEFT JOIN compliance_profiles c ON c.employee_id=e.employee_id
              AND c.effective_from=(SELECT MAX(c2.effective_from) FROM compliance_profiles c2
                WHERE c2.employee_id=e.employee_id AND c2.effective_from<=r.period_end)
            WHERE p.payroll_run_id=? ORDER BY e.legal_name""", (payroll_run_id,),
    ).fetchall()
    for row in rows:
        if row["engagement_type"] == "CONTRACT_FREELANCER":
            result = {"employer_cpf_cents": 0, "employee_cpf_cents": 0, "sdl_cents": 0}
            status, notes = "EXEMPT", "Contract freelancer; invoice payment outside CPF/SDL payroll"
        else:
            result = calculate_verified_statutory(connection, row["payroll_run_item_id"])
            status, notes = "REVIEW", None
        shg = calculate_shg(row["shg_fund"] if row["cpf_applicable"] else None, row["gross_pay_cents"])
        if status != "EXEMPT":
            status = "MATCH" if (
                result["employer_cpf_cents"] == (row["employer_cpf_cents"] or 0) and
                result["employee_cpf_cents"] == (row["employee_cpf_cents"] or 0) and
                shg == (row["other_deductions_cents"] or 0)
            ) else "DIFFERENCE"
        connection.execute(
            """INSERT INTO statutory_calculation_results
               (payroll_run_item_id,rule_version,cpf_wage_cents,calculated_employer_cpf_cents,
                calculated_employee_cpf_cents,calculated_shg_cents,calculated_sdl_cents,
                legacy_employer_cpf_cents,legacy_employee_cpf_cents,legacy_other_deductions_cents,
                status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(payroll_run_item_id,rule_version) DO UPDATE SET
                cpf_wage_cents=excluded.cpf_wage_cents,
                calculated_employer_cpf_cents=excluded.calculated_employer_cpf_cents,
                calculated_employee_cpf_cents=excluded.calculated_employee_cpf_cents,
                calculated_shg_cents=excluded.calculated_shg_cents,
                calculated_sdl_cents=excluded.calculated_sdl_cents,
                legacy_employer_cpf_cents=excluded.legacy_employer_cpf_cents,
                legacy_employee_cpf_cents=excluded.legacy_employee_cpf_cents,
                legacy_other_deductions_cents=excluded.legacy_other_deductions_cents,
                status=excluded.status,notes=excluded.notes,calculated_at=CURRENT_TIMESTAMP""",
            (row["payroll_run_item_id"], "SG-2026-v1", row["cpf_wage_base_cents"] or row["gross_pay_cents"],
             result["employer_cpf_cents"], result["employee_cpf_cents"], shg, result["sdl_cents"],
             row["employer_cpf_cents"] or 0, row["employee_cpf_cents"] or 0,
             row["other_deductions_cents"] or 0, status, notes),
        )
    return connection.execute(
        """SELECT s.*,e.legal_name FROM statutory_calculation_results s
            JOIN payroll_run_items p USING(payroll_run_item_id) JOIN employees e USING(employee_id)
            WHERE p.payroll_run_id=? AND s.rule_version='SG-2026-v1' ORDER BY e.legal_name""",
        (payroll_run_id,),
    ).fetchall()


def apply_statutory_run(connection: sqlite3.Connection, payroll_run_id: int, actor: str,
                        reason: str) -> int:
    """Apply reconciled statutory values to a draft run after all validation blockers clear."""
    status = connection.execute(
        "SELECT status FROM payroll_runs WHERE payroll_run_id=?", (payroll_run_id,)
    ).fetchone()
    if not status or status[0] != "DRAFT":
        raise ValueError("Statutory results can only be applied to a DRAFT payroll")
    blockers = [issue for issue in validate_run(connection, payroll_run_id) if issue.severity == "BLOCKER"]
    if blockers:
        raise ValueError(f"Cannot apply statutory results with {len(blockers)} blocker(s)")
    results = reconcile_statutory_run(connection, payroll_run_id)
    for result in results:
        item = connection.execute(
            "SELECT * FROM payroll_run_items WHERE payroll_run_item_id=?",
            (result["payroll_run_item_id"],),
        ).fetchone()
        net = (item["gross_pay_cents"] - result["calculated_employee_cpf_cents"] -
               result["calculated_shg_cents"])
        connection.execute(
            """UPDATE payroll_run_items SET employer_cpf_cents=?,employee_cpf_cents=?,
               shg_contribution_cents=?,sdl_cents=?,other_deductions_cents=0,net_pay_cents=?,
               cpf_wage_base_cents=COALESCE(cpf_wage_base_cents,gross_pay_cents),
               source=source||' | SG statutory engine 2026 v1'
               WHERE payroll_run_item_id=?""",
            (result["calculated_employer_cpf_cents"], result["calculated_employee_cpf_cents"],
             result["calculated_shg_cents"], result["calculated_sdl_cents"], net,
             result["payroll_run_item_id"]),
        )
        component_amounts = {
            "EMPLOYER_CPF": result["calculated_employer_cpf_cents"],
            "EMPLOYEE_CPF": result["calculated_employee_cpf_cents"],
            "SHG": result["calculated_shg_cents"], "SDL": result["calculated_sdl_cents"],
        }
        for component, amount in component_amounts.items():
            if amount:
                connection.execute(
                    """INSERT INTO payroll_item_components
                       (payroll_run_item_id,component_code,amount_cents,source)
                       VALUES(?,?,?,'SG statutory engine 2026 v1')
                       ON CONFLICT(payroll_run_item_id,component_code) DO UPDATE SET
                       amount_cents=excluded.amount_cents,source=excluded.source""",
                    (result["payroll_run_item_id"], component, amount),
                )
            else:
                connection.execute(
                    "DELETE FROM payroll_item_components WHERE payroll_run_item_id=? AND component_code=?",
                    (result["payroll_run_item_id"], component),
                )
        connection.execute(
            "DELETE FROM payroll_item_components WHERE payroll_run_item_id=? AND component_code='OTHER_DEDUCTION'",
            (result["payroll_run_item_id"],),
        )
    connection.execute(
        """INSERT INTO payroll_audit_events(payroll_run_id,event_type,reason,actor,new_value)
           VALUES(?,?,?,?,?)""",
        (payroll_run_id, "STATUTORY_APPLY", reason, actor,
         json.dumps({"rule_version": "SG-2026-v1", "items": len(results)})),
    )
    return len(results)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    item_id: int | None = None


def validate_run(connection: sqlite3.Connection, payroll_run_id: int, persist: bool = True) -> list[ValidationIssue]:
    run = connection.execute("SELECT * FROM payroll_runs WHERE payroll_run_id=?", (payroll_run_id,)).fetchone()
    if not run:
        raise ValueError("Payroll run does not exist")
    issues: list[ValidationIssue] = []
    rows = connection.execute(
        """SELECT p.*,e.legal_name,e.nric,e.date_of_birth,e.bank_account,e.bank_beneficiary_name,
                  e.bank_swift,e.main_branch,c.residency_status,c.cpf_applicable,c.source compliance_source,
                  c.pr_effective_date,c.pr_contribution_scheme,
                  COALESCE((SELECT t.engagement_type FROM employment_terms t
                    WHERE t.employee_id=e.employee_id AND t.effective_from<=?
                    ORDER BY t.effective_from DESC LIMIT 1),'EMPLOYEE') engagement_type,
                  s.self_help_group
             FROM payroll_run_items p JOIN employees e USING(employee_id)
             LEFT JOIN compliance_profiles c ON c.employee_id=e.employee_id
               AND c.effective_from=(SELECT MAX(c2.effective_from) FROM compliance_profiles c2
                   WHERE c2.employee_id=e.employee_id AND c2.effective_from<=?)
             LEFT JOIN payroll_statutory_profiles s ON s.employee_id=e.employee_id
            WHERE p.payroll_run_id=? ORDER BY e.legal_name""",
        (run["period_end"], run["period_end"], payroll_run_id),
    ).fetchall()
    seen_codes: set[str] = set()
    for row in rows:
        item, name = row["payroll_run_item_id"], row["legal_name"]
        def add(severity: str, code: str, text: str) -> None:
            issues.append(ValidationIssue(severity, code, f"{name}: {text}", item))
        if not row["employee_code"] or row["employee_code"] in seen_codes:
            add("BLOCKER", "EMPLOYEE_CODE", "missing or duplicate payroll employee code")
        seen_codes.add(row["employee_code"] or "")
        if row["gross_pay_cents"] < 0 or row["net_pay_cents"] < 0:
            add("BLOCKER", "NEGATIVE_PAY", "gross and net pay must not be negative")
        expected_net = (row["gross_pay_cents"] - (row["employee_cpf_cents"] or 0) -
                        (row["shg_contribution_cents"] or 0) - (row["other_deductions_cents"] or 0))
        if expected_net != row["net_pay_cents"]:
            add("BLOCKER", "NET_RECONCILIATION", "net pay does not reconcile to gross less deductions")
        if row["pay_basis"] == "MONTHLY":
            expected_gross = ((row["base_pay_cents"] or 0) + (row["allowance_cents"] or 0) +
                              (row["overtime_pay_cents"] or 0) + (row["pay_adjustment_cents"] or 0))
            if expected_gross != row["gross_pay_cents"]:
                add("BLOCKER", "GROSS_RECONCILIATION",
                    "gross pay does not reconcile to nominal base, allowance, overtime and adjustment")
            payable = min(max((row["worked_minutes"] or 0) - (row["expected_minutes"] or 0), 0),
                          row["approved_overtime_minutes"] or 0)
            if payable > 72 * 60:
                add("BLOCKER", "OT_72_HOUR_LIMIT", "payable overtime exceeds 72 hours")
        if row["cpf_applicable"]:
            if not row["nric"]:
                add("BLOCKER", "CPF_NRIC", "NRIC is required for CPF submission")
            if not row["date_of_birth"]:
                add("BLOCKER", "CPF_DOB", "date of birth is required for age-based CPF rates")
            if row["compliance_source"] and "unverified" in row["compliance_source"].lower():
                add("BLOCKER", "CPF_CLASSIFICATION_UNVERIFIED", "citizenship/CPF classification is unverified")
            if row["residency_status"] == "PR":
                if not row["pr_effective_date"]:
                    add("BLOCKER", "PR_EFFECTIVE_DATE", "PR effective date is required")
                if row["pr_contribution_scheme"] not in {"FULL", "GRADUATED"}:
                    add("BLOCKER", "PR_RATE_SCHEME", "confirm FULL or GRADUATED PR CPF rates")
            if row["self_help_group"] in {None, "REVIEW"}:
                add("BLOCKER", "SHG_CLASSIFICATION", "confirm CDAC, MBMF, SINDA, ECF or NONE")
        if row["engagement_type"] == "CONTRACT_FREELANCER":
            if row["employee_cpf_cents"] or row["employer_cpf_cents"]:
                add("BLOCKER", "FREELANCER_CPF", "contract freelancer must not have CPF contributions")
        if not row["bank_account"] or not row["bank_beneficiary_name"] or not row["bank_swift"]:
            add("BLOCKER", "BANK_DETAILS", "complete beneficiary name, account and SWIFT are required")
        if not connection.execute("SELECT 1 FROM payroll_item_components WHERE payroll_run_item_id=?", (item,)).fetchone():
            add("BLOCKER", "NO_COMPONENTS", "no earnings or deduction components exist")
    entities = {row[0] for row in connection.execute(
        """SELECT DISTINCT e.main_branch FROM payroll_run_items p JOIN employees e USING(employee_id)
            WHERE p.payroll_run_id=?""", (payroll_run_id,)
    )}
    if len(entities) != 1:
        issues.append(ValidationIssue("BLOCKER", "MULTI_ENTITY_RUN", "Payroll run contains multiple legal entities"))
    if persist:
        connection.execute("DELETE FROM payroll_validation_issues WHERE payroll_run_id=? AND resolved_at IS NULL", (payroll_run_id,))
        connection.executemany(
            """INSERT INTO payroll_validation_issues
               (payroll_run_id,payroll_run_item_id,severity,issue_code,message) VALUES(?,?,?,?,?)""",
            [(payroll_run_id, issue.item_id, issue.severity, issue.code, issue.message) for issue in issues],
        )
    return issues


TRANSITIONS = {"DRAFT": "REVIEW", "REVIEW": "APPROVED", "APPROVED": "LOCKED"}


def transition_run(connection: sqlite3.Connection, payroll_run_id: int, target: str,
                   actor: str, reason: str) -> None:
    row = connection.execute("SELECT status FROM payroll_runs WHERE payroll_run_id=?", (payroll_run_id,)).fetchone()
    if not row or TRANSITIONS.get(row["status"]) != target:
        raise ValueError(f"Invalid payroll transition: {row['status'] if row else 'missing'} -> {target}")
    blockers = [issue for issue in validate_run(connection, payroll_run_id) if issue.severity == "BLOCKER"]
    if blockers and target in {"REVIEW", "APPROVED", "LOCKED"}:
        raise ValueError(f"Payroll has {len(blockers)} unresolved blocker(s)")
    timestamp_field = "approved_at" if target == "APPROVED" else "locked_at" if target == "LOCKED" else None
    if timestamp_field:
        connection.execute(f"UPDATE payroll_runs SET status=?,{timestamp_field}=CURRENT_TIMESTAMP WHERE payroll_run_id=?",
                           (target, payroll_run_id))
    else:
        connection.execute("UPDATE payroll_runs SET status=? WHERE payroll_run_id=?", (target, payroll_run_id))
    connection.execute(
        """INSERT INTO payroll_audit_events(payroll_run_id,event_type,old_value,new_value,reason,actor)
           VALUES(?,?,?,?,?,?)""", (payroll_run_id, "STATUS_CHANGE", row["status"], target, reason, actor),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validation_summary(database: Path = DATABASE) -> dict[str, object]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    with connection:
        runs = connection.execute("SELECT payroll_run_id FROM payroll_runs").fetchall()
        all_issues = {row[0]: validate_run(connection, row[0]) for row in runs}
    return {str(run): {level: sum(i.severity == level for i in issues)
                       for level in ("BLOCKER", "WARNING", "INFO")}
            for run, issues in all_issues.items()}


if __name__ == "__main__":
    print(json.dumps(validation_summary(), indent=2))
