# Operations

## Environment setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item payroll_entities.ini.example payroll_entities.ini
Copy-Item secrets.ini.example secrets.ini
```

Populate only the local configuration copies. Never place credentials or complete bank account numbers in example files.

## Launchers

```powershell
.\run-payroll.cmd -ICV -JUN26
.\run_attendance.cmd
.\run_compliance_review.cmd
.\run_staff_hours.cmd
```

## Administrative commands

```powershell
.\.venv\Scripts\python.exe payroll_admin.py validate 1
.\.venv\Scripts\python.exe payroll_admin.py work-entries 1
.\.venv\Scripts\python.exe payroll_admin.py statutory-reconcile 1
.\.venv\Scripts\python.exe payroll_admin.py transition 1 REVIEW --reason "Calculation reviewed"
```

## Pre-release checklist

1. Synchronize attendance and HR leave sources.
2. Review employee identity, residency, CPF, bank beneficiary, and entity assignment.
3. Resolve every validation blocker.
4. Compare payroll totals with the UI and accounting output.
5. Visually inspect representative full-time, part-time, adjustment, overtime, and freelancer documents.
6. Advance the run only after reviewer approval.
7. Test the first DBS file through the bank's test workflow before live submission.

## Recovery

Recovery snapshots are opt-in and encrypted. They require `PAYROLL_BACKUP_KEY`; no unencrypted database backup should be retained by the application.
