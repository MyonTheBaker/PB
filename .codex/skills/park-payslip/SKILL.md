---
name: park-payslip
description: Generate, review, and revise Park Bäckerei employee payslips from the payroll database, including branded HTML, browser-rendered PDF, earnings, deductions, CPF, adjustments, external-funding memos, and leave summaries. Use whenever creating, inspecting, fixing, or exporting a payslip or adding leave information.
---

# Park Bäckerei payslips

Use the repository root as the authoritative implementation and `data\employees.db` as the local payroll source.

## Workflow

1. Read `references/payslip-rules.md` before changing calculations, source mappings, or layout.
2. Generate standalone HTML/CSS first and treat it as the editable source.
3. Render PDF with installed Chrome or Edge. Do not use Qt `QTextDocument` for payslip layout.
4. Preserve the invoice-derived brand and use `assets/park-baeckerei-logo.png`.
5. Mark DRAFT output clearly as `DRAFT — FOR REVIEW`.
6. Reconcile gross, deductions, net pay, statutory contributions, and leave totals.
7. Inspect the rendered page and extracted PDF text for wrapping, clipping, missing glyphs, or extra pages.
8. Give the user plain copyable Windows paths for HTML and PDF.

## Leave integration

Treat every row present in the HR-managed leave sheet at payroll time as confirmed and approved. HR edits or removes unapproved leave before payroll; synchronization cancels source rows removed later. Map employees by stable identifier first and normalized name only when unambiguous.

Calculate entitlement, used, and remaining by leave type. Flag duplicates, overlaps, unknown employees, ambiguous quantities, and negative balances.

## Output standard

Use a spacious one-page A4 design: logo upper right, title upper left, minimal black rules, two-column metadata, aligned earnings and deductions, prominent net-pay bar, restrained red information accent, and no mid-word breaking.
