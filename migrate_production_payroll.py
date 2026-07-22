"""Create the normalized production payroll schema and seed the June baseline."""

from __future__ import annotations

import sqlite3
from pathlib import Path


DATABASE = Path(__file__).resolve().parent / "data" / "employees.db"

DDL = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS legal_entities (
    entity_code TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    uen TEXT NOT NULL UNIQUE,
    address TEXT,
    cpf_submission_number TEXT,
    dbs_organization_id TEXT,
    source_account_last4 TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS payroll_components (
    component_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('EARNING','DEDUCTION','EMPLOYER_COST','MEMO')),
    recurring_allowed INTEGER NOT NULL DEFAULT 1 CHECK(recurring_allowed IN (0,1)),
    include_in_gross INTEGER NOT NULL DEFAULT 0 CHECK(include_in_gross IN (0,1)),
    cpf_wage_type TEXT NOT NULL DEFAULT 'NONE' CHECK(cpf_wage_type IN ('OW','AW','NONE','REVIEW')),
    mom_rate_type TEXT NOT NULL DEFAULT 'NONE' CHECK(mom_rate_type IN ('BASIC','GROSS','NONE')),
    iras_category TEXT,
    gl_account_code TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);

CREATE TABLE IF NOT EXISTS employee_recurring_components (
    recurring_component_id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
    component_code TEXT NOT NULL REFERENCES payroll_components(component_code),
    amount_cents INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    notes TEXT,
    UNIQUE(employee_id, component_code, effective_from)
);

CREATE TABLE IF NOT EXISTS payroll_item_components (
    payroll_item_component_id INTEGER PRIMARY KEY,
    payroll_run_item_id INTEGER NOT NULL REFERENCES payroll_run_items(payroll_run_item_id) ON DELETE CASCADE,
    component_code TEXT NOT NULL REFERENCES payroll_components(component_code),
    amount_cents INTEGER NOT NULL,
    quantity TEXT,
    rate_cents INTEGER,
    source TEXT NOT NULL,
    reason TEXT,
    is_manual INTEGER NOT NULL DEFAULT 0 CHECK(is_manual IN (0,1)),
    UNIQUE(payroll_run_item_id, component_code)
);

CREATE TABLE IF NOT EXISTS work_entries (
    work_entry_id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
    work_date TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK(entry_type IN
      ('NORMAL','ANNUAL_LEAVE','SICK_LEAVE','HOSPITAL_LEAVE','UNPAID_LEAVE','NS_LEAVE',
       'PUBLIC_HOLIDAY','PUBLIC_HOLIDAY_WORK','REST_DAY_WORK','ABSENCE')),
    scheduled_minutes INTEGER NOT NULL DEFAULT 0,
    worked_minutes INTEGER NOT NULL DEFAULT 0,
    break_minutes INTEGER NOT NULL DEFAULT 0,
    approved_overtime_minutes INTEGER NOT NULL DEFAULT 0,
    employer_requested INTEGER CHECK(employer_requested IN (0,1)),
    approval_status TEXT NOT NULL DEFAULT 'PENDING' CHECK(approval_status IN ('PENDING','APPROVED','REJECTED')),
    source TEXT NOT NULL,
    notes TEXT,
    UNIQUE(employee_id, work_date, entry_type)
);

CREATE TABLE IF NOT EXISTS public_holidays (
    holiday_date TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_code TEXT REFERENCES legal_entities(entity_code),
    observed_date TEXT,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_applications (
    leave_application_id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(employee_id),
    leave_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    paid INTEGER NOT NULL CHECK(paid IN (0,1)),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
    requested_minutes INTEGER,
    reason TEXT,
    approved_by TEXT,
    approved_at TEXT,
    CHECK(end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS payroll_statutory_profiles (
    employee_id INTEGER PRIMARY KEY REFERENCES employees(employee_id),
    residency_status TEXT NOT NULL CHECK(residency_status IN ('SC','SPR','FOREIGN','UNKNOWN')),
    spr_start_date TEXT,
    cpf_rate_scheme TEXT NOT NULL DEFAULT 'FULL' CHECK(cpf_rate_scheme IN ('FULL','GRADUATED','EXEMPT','REVIEW')),
    self_help_group TEXT CHECK(self_help_group IN ('CDAC','MBMF','SINDA','ECF','NONE','REVIEW')),
    sdl_applicable INTEGER NOT NULL DEFAULT 1 CHECK(sdl_applicable IN (0,1)),
    fwl_applicable INTEGER NOT NULL DEFAULT 0 CHECK(fwl_applicable IN (0,1)),
    fwl_monthly_cents INTEGER NOT NULL DEFAULT 0,
    tax_clearance_required INTEGER NOT NULL DEFAULT 0 CHECK(tax_clearance_required IN (0,1)),
    reviewed_at TEXT,
    reviewed_by TEXT
);

CREATE TABLE IF NOT EXISTS payroll_validation_issues (
    validation_issue_id INTEGER PRIMARY KEY,
    payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(payroll_run_id) ON DELETE CASCADE,
    payroll_run_item_id INTEGER REFERENCES payroll_run_items(payroll_run_item_id) ON DELETE CASCADE,
    severity TEXT NOT NULL CHECK(severity IN ('BLOCKER','WARNING','INFO')),
    issue_code TEXT NOT NULL,
    message TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution_notes TEXT
);

CREATE TABLE IF NOT EXISTS payroll_audit_events (
    audit_event_id INTEGER PRIMARY KEY,
    payroll_run_id INTEGER REFERENCES payroll_runs(payroll_run_id),
    payroll_run_item_id INTEGER REFERENCES payroll_run_items(payroll_run_item_id),
    event_type TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    actor TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payslips (
    payslip_id INTEGER PRIMARY KEY,
    payroll_run_item_id INTEGER NOT NULL UNIQUE REFERENCES payroll_run_items(payroll_run_item_id),
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    issued_at TEXT,
    payment_date TEXT
);

CREATE TABLE IF NOT EXISTS payroll_exports (
    payroll_export_id INTEGER PRIMARY KEY,
    payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(payroll_run_id),
    entity_code TEXT NOT NULL REFERENCES legal_entities(entity_code),
    export_type TEXT NOT NULL CHECK(export_type IN ('DBS_UFF','CPF','ACCOUNTING','IRAS')),
    batch_reference TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    total_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED' CHECK(status IN
      ('CREATED','TEST_ACCEPTED','TEST_REJECTED','SUBMITTED','ACCEPTED','REJECTED','RECONCILED','VOID')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL,
    UNIQUE(entity_code, export_type, batch_reference)
);

CREATE TABLE IF NOT EXISTS payroll_reconciliations (
    reconciliation_id INTEGER PRIMARY KEY,
    payroll_export_id INTEGER NOT NULL REFERENCES payroll_exports(payroll_export_id),
    external_reference TEXT,
    external_total_cents INTEGER,
    matched_at TEXT,
    matched_by TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS payroll_accounting_lines (
    accounting_line_id INTEGER PRIMARY KEY,
    payroll_run_id INTEGER NOT NULL REFERENCES payroll_runs(payroll_run_id),
    entity_code TEXT NOT NULL REFERENCES legal_entities(entity_code),
    account_code TEXT NOT NULL,
    debit_cents INTEGER NOT NULL DEFAULT 0,
    credit_cents INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(employee_id),
    CHECK(debit_cents >= 0 AND credit_cents >= 0 AND NOT (debit_cents > 0 AND credit_cents > 0))
);

CREATE TABLE IF NOT EXISTS payroll_recovery_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    encrypted INTEGER NOT NULL CHECK(encrypted IN (0,1)),
    verified_at TEXT,
    retention_until TEXT
);

CREATE TABLE IF NOT EXISTS statutory_calculation_results (
    statutory_result_id INTEGER PRIMARY KEY,
    payroll_run_item_id INTEGER NOT NULL REFERENCES payroll_run_items(payroll_run_item_id) ON DELETE CASCADE,
    rule_version TEXT NOT NULL,
    cpf_wage_cents INTEGER NOT NULL,
    calculated_employer_cpf_cents INTEGER NOT NULL,
    calculated_employee_cpf_cents INTEGER NOT NULL,
    calculated_shg_cents INTEGER NOT NULL,
    calculated_sdl_cents INTEGER NOT NULL,
    legacy_employer_cpf_cents INTEGER NOT NULL,
    legacy_employee_cpf_cents INTEGER NOT NULL,
    legacy_other_deductions_cents INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('MATCH','DIFFERENCE','EXEMPT','REVIEW')),
    notes TEXT,
    calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(payroll_run_item_id,rule_version)
);

CREATE INDEX IF NOT EXISTS idx_work_entries_employee_date ON work_entries(employee_id, work_date);
CREATE INDEX IF NOT EXISTS idx_components_item ON payroll_item_components(payroll_run_item_id);
CREATE INDEX IF NOT EXISTS idx_validation_run ON payroll_validation_issues(payroll_run_id, severity);
CREATE INDEX IF NOT EXISTS idx_audit_run ON payroll_audit_events(payroll_run_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS lock_non_draft_payroll_items_update
BEFORE UPDATE ON payroll_run_items
WHEN (SELECT status FROM payroll_runs WHERE payroll_run_id=OLD.payroll_run_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'Payroll items are locked outside DRAFT status'); END;

CREATE TRIGGER IF NOT EXISTS lock_non_draft_payroll_items_delete
BEFORE DELETE ON payroll_run_items
WHEN (SELECT status FROM payroll_runs WHERE payroll_run_id=OLD.payroll_run_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'Payroll items are locked outside DRAFT status'); END;

CREATE TRIGGER IF NOT EXISTS lock_approved_component_changes
BEFORE UPDATE ON payroll_item_components
WHEN (SELECT r.status FROM payroll_runs r JOIN payroll_run_items p USING(payroll_run_id)
      WHERE p.payroll_run_item_id=OLD.payroll_run_item_id) != 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'Payroll components are locked outside DRAFT status'); END;
"""

COMPONENTS = (
    ('BASIC','Basic salary','EARNING',1,1,'OW','BASIC','SALARY'),
    ('HOURLY','Hourly wages','EARNING',1,1,'OW','BASIC','SALARY'),
    ('ALLOWANCE_FIXED','Fixed allowance','EARNING',1,1,'OW','GROSS','ALLOWANCE'),
    ('ALLOWANCE_ADHOC','Ad-hoc allowance','EARNING',0,1,'REVIEW','GROSS','ALLOWANCE'),
    ('OVERTIME','Overtime pay','EARNING',0,1,'OW','NONE','OVERTIME'),
    ('BONUS','Bonus','EARNING',0,1,'AW','NONE','BONUS'),
    ('PUBLIC_HOLIDAY','Public holiday pay','EARNING',0,1,'OW','NONE','OTHER'),
    ('REST_DAY','Rest day pay','EARNING',0,1,'OW','NONE','OTHER'),
    ('COMMISSION','Commission','EARNING',0,1,'OW','NONE','COMMISSION'),
    ('REIMBURSEMENT','Expense reimbursement','MEMO',0,0,'NONE','NONE','NON_TAXABLE'),
    ('NS_MAKEUP','NS make-up adjustment','MEMO',0,0,'REVIEW','NONE','OTHER'),
    ('PAYROLL_ADJUSTMENT','Signed payroll adjustment','EARNING',0,1,'REVIEW','NONE','OTHER'),
    ('EXTERNAL_FUNDED','Externally funded amount','MEMO',0,0,'NONE','NONE','NON_TAXABLE'),
    ('UNPAID_LEAVE','Unpaid leave deduction','DEDUCTION',0,0,'NONE','NONE','DEDUCTION'),
    ('EMPLOYEE_CPF','Employee CPF','DEDUCTION',0,0,'NONE','NONE','CPF'),
    ('ASSOCIATION_FEE','Association/self-help contribution','DEDUCTION',1,0,'NONE','NONE','DEDUCTION'),
    ('SHG','Self-help group contribution','DEDUCTION',0,0,'NONE','NONE','SHG'),
    ('LOAN_RECOVERY','Loan or salary advance recovery','DEDUCTION',0,0,'NONE','NONE','DEDUCTION'),
    ('OTHER_DEDUCTION','Other authorised deduction','DEDUCTION',0,0,'NONE','NONE','DEDUCTION'),
    ('EMPLOYER_CPF','Employer CPF','EMPLOYER_COST',0,0,'NONE','NONE','EMPLOYER_CPF'),
    ('SDL','Skills Development Levy','EMPLOYER_COST',0,0,'NONE','NONE','SDL'),
    ('FWL','Foreign Worker Levy','EMPLOYER_COST',0,0,'NONE','NONE','FWL'),
)


def main() -> None:
    with sqlite3.connect(DATABASE) as connection:
        connection.executescript(DDL)
        item_columns = {row[1] for row in connection.execute('PRAGMA table_info(payroll_run_items)')}
        for name in ('shg_contribution_cents', 'sdl_cents', 'pay_adjustment_cents',
                     'external_funded_cents'):
            if name not in item_columns:
                connection.execute(
                    f'ALTER TABLE payroll_run_items ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0'
                )
        if 'adjustment_reason' not in item_columns:
            connection.execute('ALTER TABLE payroll_run_items ADD COLUMN adjustment_reason TEXT')
        connection.executemany(
            """INSERT INTO legal_entities(entity_code,legal_name,uen,address,dbs_organization_id,source_account_last4)
               VALUES(?,?,?,?,?,?) ON CONFLICT(entity_code) DO UPDATE SET legal_name=excluded.legal_name,
               uen=excluded.uen,address=excluded.address""",
            (
                ('ICV','DVB CONSULTING PTE LTD','201913369C','12 Gopeng Street #01-41',None,None),
                ('MBL','PARK BAECKEREI MBLM PTE LTD','202408517R','12 Gopeng Street #01-42',None,None),
            ),
        )
        connection.executemany(
            """INSERT INTO payroll_components(component_code,name,kind,recurring_allowed,include_in_gross,
               cpf_wage_type,mom_rate_type,iras_category) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(component_code) DO UPDATE SET name=excluded.name,kind=excluded.kind,
               include_in_gross=excluded.include_in_gross,cpf_wage_type=excluded.cpf_wage_type,
               mom_rate_type=excluded.mom_rate_type,iras_category=excluded.iras_category""",
            COMPONENTS,
        )
        connection.execute(
            """INSERT OR IGNORE INTO employee_recurring_components
               (employee_id,component_code,amount_cents,effective_from,notes)
               SELECT employee_id,'ALLOWANCE_FIXED',monthly_allowance_cents,effective_from,
                      'Migrated from employment terms'
                 FROM employment_terms WHERE monthly_allowance_cents > 0"""
        )
        mappings = (
            ('BASIC','base_pay_cents'), ('HOURLY','gross_pay_cents'),
            ('ALLOWANCE_FIXED','allowance_cents'), ('OVERTIME','overtime_pay_cents'),
            ('NS_MAKEUP','ns_makeup_pay_cents'),
            ('PAYROLL_ADJUSTMENT','pay_adjustment_cents'),
            ('EXTERNAL_FUNDED','external_funded_cents'),
            ('EMPLOYEE_CPF','employee_cpf_cents'),
            ('OTHER_DEDUCTION','other_deductions_cents'), ('EMPLOYER_CPF','employer_cpf_cents'),
        )
        for component, column in mappings:
            where = "pay_basis='HOURLY'" if component == 'HOURLY' else "pay_basis='MONTHLY'" if component == 'BASIC' else '1=1'
            connection.execute(
                f"""INSERT OR IGNORE INTO payroll_item_components
                    (payroll_run_item_id,component_code,amount_cents,source)
                    SELECT payroll_run_item_id,?,COALESCE({column},0),'Legacy June migration'
                      FROM payroll_run_items WHERE {where} AND COALESCE({column},0) != 0""",
                (component,),
            )
        connection.execute(
            """INSERT OR IGNORE INTO payroll_statutory_profiles
               (employee_id,residency_status,spr_start_date,cpf_rate_scheme,self_help_group,
                sdl_applicable,fwl_applicable,fwl_monthly_cents,tax_clearance_required)
               SELECT c.employee_id,
                      CASE c.residency_status WHEN 'CITIZEN' THEN 'SC' WHEN 'PR' THEN 'SPR'
                           WHEN 'FOREIGNER' THEN 'FOREIGN' ELSE 'UNKNOWN' END,
                      c.pr_effective_date,
                      CASE WHEN COALESCE(c.cpf_applicable,0)=0 THEN 'EXEMPT'
                           WHEN c.pr_contribution_scheme LIKE '%GRAD%' THEN 'GRADUATED'
                           ELSE 'FULL' END,
                      CASE c.shg_fund WHEN 'MFBD' THEN 'MBMF'
                           WHEN 'CDAC' THEN 'CDAC' WHEN 'SINDA' THEN 'SINDA'
                           WHEN 'ECF' THEN 'ECF' ELSE 'REVIEW' END,
                      1,CASE WHEN c.residency_status='FOREIGNER' THEN 1 ELSE 0 END,
                      COALESCE(c.actual_monthly_levy_cents,0),COALESCE(c.tax_clearance_required,0)
                 FROM compliance_profiles c
                WHERE c.effective_from=(SELECT MAX(c2.effective_from) FROM compliance_profiles c2
                                         WHERE c2.employee_id=c.employee_id)"""
        )
        connection.execute(
            """INSERT INTO payroll_audit_events(payroll_run_id,event_type,reason,actor)
               SELECT payroll_run_id,'SCHEMA_MIGRATION','Production payroll schema installed','codex'
                 FROM payroll_runs
                WHERE NOT EXISTS (SELECT 1 FROM payroll_audit_events a
                                   WHERE a.payroll_run_id=payroll_runs.payroll_run_id
                                     AND a.event_type='SCHEMA_MIGRATION')"""
        )
    print('Production payroll schema installed and June components seeded.')


if __name__ == '__main__':
    main()
