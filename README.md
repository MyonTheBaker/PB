# Park Bäckerei Operations and Payroll

Internal Windows desktop tools for employee records, attendance, payroll review, Singapore statutory calculations, leave management, payslips, and controlled bank-file generation.

> This repository contains application code only. Employee records, payroll databases, credentials, bank configuration, source spreadsheets, and generated documents must remain local and are excluded from Git.

## Documentation

Start with the [documentation sitemap](docs/index.md).

| Area | Guide |
|---|---|
| System structure | [Architecture](docs/architecture.md) |
| Repository navigation | [Repository map](docs/repository-map.md) |
| Payroll workflow and calculations | [Payroll](docs/payroll.md) |
| Singapore leave processing | [Leave management](docs/leave-management.md) |
| Installation and daily commands | [Operations](docs/operations.md) |
| Data protection and release controls | [Security](docs/security.md) |
| Development and database migrations | [Development](docs/development.md) |

## Quick start

```powershell
cd C:\path\to\park-payroll
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item payroll_entities.ini.example payroll_entities.ini
Copy-Item secrets.ini.example secrets.ini
.\run-payroll.cmd -ICV -JUN26
```

The local database is expected at `data\employees.db`. It is intentionally not supplied by Git.

## Applications

- `run-payroll.cmd` — payroll review, calculation and approval workflow.
- `run_attendance.cmd` — attendance capture and review.
- `run_compliance_review.cmd` — residency, CPF and work-pass review.
- `run_staff_hours.cmd` — staff-hours workflow.
- `run_sales_upload.cmd` — controlled sales-file upload.

## Payroll command examples

```powershell
.\run-payroll.cmd -ICV -JUN26
.\run-payroll.cmd -MBL -JUN26
.\.venv\Scripts\python.exe payroll_admin.py validate 1
.\.venv\Scripts\python.exe payroll_admin.py statutory-reconcile 1
```

DBS files and final payslips are restricted to validated, approved payroll runs. Draft payslips are visibly marked for review.

## Status

The June 2026 payroll is the controlled test case. Production use still requires HR approval of employee master data, entity banking configuration, and payroll status transitions.
