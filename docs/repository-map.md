# Repository map

```text
park-payroll/
├── README.md                     Project entry point
├── docs/                         Operating and technical documentation
│   ├── index.md                  Documentation sitemap
│   ├── architecture.md           Components and data flow
│   ├── repository-map.md         This file
│   ├── payroll.md                Payroll rules and lifecycle
│   ├── leave-management.md       Leave rules and HR synchronization
│   ├── operations.md             Setup and daily operation
│   ├── security.md               Privacy and release controls
│   └── development.md            Engineering workflow
├── assets/                       Versioned non-sensitive brand assets
├── data/                         Local SQLite data; ignored by Git
├── exports/                      Generated files; ignored by Git
├── payroll_app.py                Payroll desktop interface
├── payroll_engine.py             Calculation and validation engine
├── payroll_outputs.py            Payslip and reporting outputs
├── payroll_admin.py              Administrative CLI
├── leave_engine.py               Singapore leave engine
├── uff_export.py                 DBS UFF exporter
├── employee_database.py          Employee master database
├── compliance_review_app.py      Compliance review interface
├── Attendance_app.py             Attendance interface
├── icon_staff_hours.py           Staff-hours workflow
├── ICON_SALES_UPLOAD.py          Sales upload workflow
├── migrate_payroll_foundation.py Foundation schema/import migration
├── migrate_production_payroll.py Production payroll schema migration
├── requirements.txt              Python dependencies
├── *.ini.example                 Sanitized configuration templates
└── run_*.cmd / run-payroll.cmd   Windows launchers
```

One-off investigation, data-repair, generated preview, local migration, and employee-specific scripts remain local and are excluded from Git.
