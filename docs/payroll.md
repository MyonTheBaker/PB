# Payroll workflow

## Entity and period selection

ICV and MBL are processed as separate legal entities because each has its own funding account. The launcher accepts compact or explicit arguments:

```powershell
.\run-payroll.cmd -ICV -JUN26
.\run-payroll.cmd --entity ICV --period 2026-06
```

## Pay bases

### Monthly employees

- Contractual base salary remains the full nominal amount.
- Gross pay is `base + allowance + overtime + signed adjustment`.
- A negative adjustment represents proration, unpaid leave, NS reduction, or another reviewed reduction.
- Externally funded amounts are memo-only and do not increase employer-paid gross or net.
- The MOM hourly basic rate is `(12 × monthly basic salary) ÷ (52 × 44)`.
- Payable overtime is the lower of approved overtime and actual time above contractual hours, multiplied by the configured overtime multiplier.

Monthly contractual time is calculated from 7.5 net hours Monday–Friday and 6.5 net hours Saturday, with a one-hour automatic break on working days.

### Hourly employees

- Gross pay is contractual hourly rate multiplied by recorded worked time.
- Contract-hour fields are not used in the payroll table.
- Part-time leave remains a separate prorated-hours entitlement under the leave engine.

### Contract freelancers

- CPF and SDL do not apply.
- The document is an Invoice Payment Advice rather than an employee payslip.
- Invoice approval remains outside this application.

## Statutory handling

The engine calculates 2026 CPF, SDL and self-help-group deductions from verified employee classifications. CDAC follows official wage bands unless a documented employee election is recorded.

## Lifecycle and controls

```text
DRAFT → REVIEW → APPROVED → LOCKED
```

- Only DRAFT rows are editable.
- Validation blockers prevent advancement.
- Banking files and final payslips require an approved state.
- Audit events record status transitions and manual edits.

## Payslips

The authoritative source is standalone HTML/CSS. Chrome or Edge generates a one-page A4 PDF using the Park Bäckerei brand. Drafts are clearly marked and generated files remain outside Git.
