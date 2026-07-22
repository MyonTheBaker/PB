# Leave management

## Source-of-truth rule

At payroll run time, every row present in the HR-managed leave sheet is treated as confirmed and approved. HR must edit or remove unapproved leave before payroll. During synchronization, a previously imported row that no longer exists in the sheet is marked cancelled.

## Singapore rules

- The company annual-leave policy starts at 11 days and increases by one per completed service year, capped at 14; it must never fall below the Employment Act minimum.
- Paid annual and medical leave begins after three months of service.
- Outpatient and hospitalisation leave follow the applicable MOM service tiers.
- Part-time employee entitlements are prorated in hours against a comparable 44-hour full-time schedule.
- Contract freelancers are excluded because they are not employees under a contract of service.
- Childcare entitlement requires explicit child age, citizenship, and employee eligibility information.
- Rest days, public holidays, and non-working days are not counted as medical leave.

## Synchronization

`leave_engine.py` imports an exported `.xlsx` file. It records source row identifiers, employee mapping, leave type, date, quantity, evidence reference, approval state, and source payload.

```powershell
.\.venv\Scripts\python.exe leave_engine.py C:\path\to\leave-export.xlsx
```

## Payslip summary

The payslip reports entitlement, approved usage through the payroll period end, and remaining balance. Future-dated leave is excluded from an earlier payslip.

```text
remaining = entitlement + carry-forward + adjustments - approved used leave
```

## Review exceptions

Investigate before payroll when the import reports:

- An unknown or ambiguous employee.
- Duplicate or overlapping leave.
- A half-day or hourly request with unclear quantity.
- A negative balance.
- Missing child eligibility information for childcare leave.
