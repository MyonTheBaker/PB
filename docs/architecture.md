# Architecture

## System boundary

The application is a local Windows desktop workflow. SQLite is the system of record; source spreadsheets and the HR leave sheet are controlled inputs. HTML is the authoritative payslip format and Chrome or Edge renders the PDF.

```text
Employee master + attendance + HR leave sheet
                    │
                    ▼
             SQLite employees.db
                    │
       ┌────────────┼────────────┐
       ▼            ▼            ▼
 Payroll review  Statutory    Leave engine
       │         calculation      │
       └────────────┬─────────────┘
                    ▼
            Validation lifecycle
          DRAFT → REVIEW → APPROVED → LOCKED
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
   Branded payslips       DBS UFF export
```

## Core components

| Component | Responsibility |
|---|---|
| `employee_database.py` | Employee master records and normalization. |
| `payroll_app.py` | Editable draft payroll UI with locked calculated fields. |
| `payroll_engine.py` | Scheduling, statutory calculations, validation and lifecycle. |
| `leave_engine.py` | Leave source synchronization, entitlements and balances. |
| `payroll_outputs.py` | Branded HTML/PDF payslips and accounting/annual outputs. |
| `uff_export.py` | DBS UFF v2.1 payroll file validation and generation. |
| `payroll_admin.py` | Controlled command-line administration. |

## Design principles

- Separate legal entities and funding accounts.
- Preserve nominal contractual salary and represent reductions as signed adjustments.
- Keep statutory and calculated fields locked in the UI.
- Treat source rows as auditable records rather than overwriting history invisibly.
- Prevent draft or invalid payroll from producing release-ready banking output.
- Keep all employee and banking data outside Git.
