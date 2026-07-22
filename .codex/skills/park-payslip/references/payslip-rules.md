# Payslip rules

## Payroll

- Show nominal contractual monthly base salary.
- Represent reductions as signed payroll adjustments.
- Keep externally funded amounts informational and outside gross/net.
- Produce Invoice Payment Advice for freelancers and exclude CPF/SDL.
- Reconcile `net = gross - employee CPF - SHG - other deductions`.
- Follow official self-help-group wage bands unless a documented election exists.

## Leave

- Use the payroll period end to define the reporting cutoff and year.
- Apply the company 11-day annual-leave floor, increasing with service to 14, without falling below MOM requirements.
- Apply MOM service eligibility and medical-leave tiers.
- Prorate part-time employee leave in hours against the comparable 44-hour schedule.
- Exclude contract freelancers.
- Require explicit child eligibility facts for childcare leave.
- Treat rows present in the HR-managed source at payroll run time as approved; removed rows become cancelled on synchronization.
- Reconcile `remaining = entitlement + carry-forward + adjustments - approved used leave`.

## Verification

- Keep draft and approved output separate.
- Generate one A4 page unless content genuinely requires another.
- Confirm no overlap, clipping, browser headers/footers, or mid-word breaks.
- Never expose bank account details on a payslip.
