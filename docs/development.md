# Development

## Local checks

```powershell
.\.venv\Scripts\python.exe -m py_compile employee_database.py payroll_app.py payroll_engine.py payroll_outputs.py payroll_admin.py leave_engine.py uff_export.py
.\.venv\Scripts\python.exe payroll_admin.py validate 1
```

Use the off-screen Qt platform for non-interactive UI smoke tests:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
```

## Database changes

- Make migrations idempotent.
- Preserve existing user data and unrelated local changes.
- Add effective dates to payroll, compliance, and leave policies.
- Keep source identifiers and audit reasons.
- Test migrations against a disposable database, never by publishing the live database.

`migrate_payroll_foundation.py` accepts the source workbook through `PAYROLL_SOURCE_WORKBOOK` rather than a hard-coded local path.

## Calculation changes

For payroll or statutory logic:

1. Document the rule and effective date.
2. Add or update reconciliation validation.
3. Verify edge cases and rounding.
4. Regenerate a draft payslip and inspect its text and page image.
5. Confirm net pay and statutory totals did not change unexpectedly.

## Configuration

Only sanitized templates are versioned. Copy examples to their local filenames and fill values outside Git.
