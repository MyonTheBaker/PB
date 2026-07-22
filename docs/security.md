# Security and privacy

## Data classification

The local system contains restricted employee and financial data, including NRIC/FIN, dates of birth, salaries, bank beneficiary details, leave reasons, medical evidence links, CPF classifications, and payroll outputs.

## Never commit

- `data\` and SQLite database/WAL files.
- `exports\`, payslips, bank files, accounting exports, and previews.
- Source spreadsheets, attendance files, and leave exports.
- `.env`, `secrets.ini`, and `payroll_entities.ini`.
- Employee-specific repair or investigation scripts.
- Complete bank details, passwords, tokens, or shared private document identifiers.

The repository `.gitignore` enforces these boundaries, but operators must still inspect staged files before every commit.

## Payroll release controls

- Maintain separate legal-entity runs and bank funding accounts.
- Do not release output from a DRAFT or invalid run.
- Require audit reasons for manual payroll edits and status transitions.
- Store banking configuration only in local ignored files or environment variables.
- Use encrypted recovery only when explicitly required.

## GitHub

Use a private repository. Repository access does not grant access to the local payroll database or source documents. Review collaborators periodically and enable multi-factor authentication on GitHub accounts.
