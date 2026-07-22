"""Controlled payslip, accounting and annual payroll outputs."""

from __future__ import annotations

import csv
import hashlib
import html
import sqlite3
import subprocess
import tempfile
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from PyQt5.QtGui import QFontDatabase, QTextDocument
from PyQt5.QtPrintSupport import QPrinter
from PyQt5.QtWidgets import QApplication

from payroll_engine import DATABASE
from leave_engine import leave_summary


PROJECT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT / "exports"


def _load_pdf_font() -> None:
    for path in (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\calibri.ttf")):
        if path.exists() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            return


def money(cents: int | None) -> str:
    return f"{(cents or 0) / 100:,.2f}"


def accounting_money(cents: int | None) -> str:
    amount = cents or 0
    return f"({money(abs(amount))})" if amount < 0 else money(amount)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approved_status(status: str) -> bool:
    return status in {"APPROVED", "LOCKED"}


def payslip_html(connection: sqlite3.Connection, item_id: int, payment_date: date,
                 draft: bool = False) -> str:
    row = connection.execute(
        """SELECT p.*,e.legal_name,e.employee_id,e.role,e.nric,e.main_branch,
                  r.period_start,r.period_end,r.status,
                  le.legal_name employer_name,le.uen,
                  COALESCE((SELECT t.engagement_type FROM employment_terms t
                    WHERE t.employee_id=e.employee_id AND t.effective_from<=r.period_end
                    ORDER BY t.effective_from DESC LIMIT 1),'EMPLOYEE') engagement_type
             FROM payroll_run_items p JOIN employees e USING(employee_id)
             JOIN payroll_runs r USING(payroll_run_id)
             JOIN legal_entities le ON le.entity_code=e.main_branch
            WHERE p.payroll_run_item_id=?""", (item_id,),
    ).fetchone()
    if not row or (not draft and not _approved_status(row["status"])):
        raise ValueError("Payslips can only be generated from an approved payroll")
    components = connection.execute(
        """SELECT c.name,c.kind,pc.amount_cents,pc.quantity,pc.rate_cents
             FROM payroll_item_components pc JOIN payroll_components c USING(component_code)
            WHERE pc.payroll_run_item_id=? ORDER BY c.kind,c.name""", (item_id,),
    ).fetchall()
    earning_rows = "".join(
        f"<tr><td>{html.escape(c['name'])}</td><td class='num'>{accounting_money(c['amount_cents'])}</td></tr>"
        for c in components if c["kind"] == "EARNING"
    ) or "<tr><td>Gross earnings</td><td class='num'>{}</td></tr>".format(money(row["gross_pay_cents"]))
    deduction_rows = "".join(
        f"<tr><td>{html.escape(c['name'])}</td><td class='num'>({money(abs(c['amount_cents']))})</td></tr>"
        for c in components if c["kind"] == "DEDUCTION"
    )
    memo_rows = "".join(
        f"<tr><td>{html.escape(c['name'])}</td><td class='num'>{money(c['amount_cents'])}</td></tr>"
        for c in components if c["kind"] == "MEMO"
    )
    freelancer = row["engagement_type"] == "CONTRACT_FREELANCER"
    document_title = "INVOICE PAYMENT ADVICE" if freelancer else "PAYROLL ADVICE"
    freelancer_note = (f"<p><b>Engagement:</b> Contract freelancer (contract for service)<br>"
                       f"<b>Invoice reference:</b> {html.escape(row['invoice_reference'] or 'Managed externally')}<br>"
                       "CPF does not apply to this invoice payment.</p>" if freelancer else "")
    period_label = date.fromisoformat(row["period_start"]).strftime("%B %Y").upper()
    year = date.fromisoformat(row["period_end"]).year
    leave_as_of = date.fromisoformat(row["period_end"])
    leave_items = leave_summary(connection, row["employee_id"], leave_as_of)
    leave_names = {"ANNUAL_LEAVE": "Annual leave", "SICK_LEAVE": "Medical leave",
                   "CHILDCARE_LEAVE": "Childcare leave", "OTHER_LEAVE": "Other leave"}
    def leave_number(value: Decimal) -> str:
        return f"{value:.1f}".rstrip("0").rstrip(".")
    leave_rows = "".join(
        f"<tr><td>{leave_names.get(str(item['leave_type']), str(item['leave_type']).replace('_', ' ').title())}</td>"
        f"<td>{leave_number(item['entitlement'])}</td><td>{leave_number(item['used'])}</td>"
        f"<td>{leave_number(item['remaining'])} {str(item['units']).lower()}</td></tr>"
        for item in leave_items if item["entitlement"] or item["used"]
    )
    pending_leave = connection.execute(
        """SELECT COUNT(*) FROM leave_import_records WHERE employee_id=? AND status='PENDING'
             AND substr(start_date,1,4)=? AND start_date<=?""",
        (row["employee_id"], str(year), row["period_end"]),
    ).fetchone()[0]
    draft_mark = "<div class='draft'>DRAFT — FOR REVIEW</div>" if draft else ""
    deductions = ((row["employee_cpf_cents"] or 0) + (row["shg_contribution_cents"] or 0) +
                  (row["other_deductions_cents"] or 0))
    logo_uri = (PROJECT / "assets" / "park-baeckerei-logo.png").as_uri()
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
      @page{{size:A4;margin:15mm 17mm}} *{{box-sizing:border-box}}
      body{{margin:0;font-family:Arial,Helvetica,sans-serif;font-size:10pt;color:#111;line-height:1.35}}
      .page{{display:block}} header{{display:flex;justify-content:space-between;align-items:flex-start;margin:2mm 0 25mm}}
      h1{{font-size:28pt;line-height:1;font-weight:400;letter-spacing:.02em;margin:0}} .logo{{width:52mm;height:auto}}
      .draft{{position:absolute;top:-8mm;left:50%;transform:translateX(-50%);color:#a60000;font-size:9pt;font-weight:700;letter-spacing:.16em}}
      .intro{{display:grid;grid-template-columns:1fr 1fr;gap:18mm;margin-bottom:16mm}} .period{{font-size:13pt;margin-top:4mm}}
      .details{{display:grid;grid-template-columns:34mm 1fr;gap:2mm 3mm}} .label{{font-weight:700}} .muted{{color:#666}}
      .section{{margin-bottom:9mm}} .section-title{{font-size:9pt;font-weight:700;letter-spacing:.12em;text-transform:uppercase;border-bottom:1.5px solid #111;padding-bottom:2mm;margin-bottom:1mm}}
      .columns{{display:grid;grid-template-columns:1fr 1fr;gap:14mm}} table{{width:100%;border-collapse:collapse;table-layout:fixed}}
      td{{padding:2.2mm 0;vertical-align:top}} td:last-child{{text-align:right;white-space:nowrap;width:31mm}}
      .total td{{border-top:1px solid #aaa;font-weight:700;padding-top:3mm}} .net{{display:flex;justify-content:space-between;align-items:center;background:#111;color:#fff;padding:4mm 5mm;font-size:15pt;margin:2mm 0 9mm}}
      .memo{{border-left:3px solid #e62027;background:#f5f5f5;padding:3mm 4mm}} .memo table td{{padding:1mm 0}}
      .leave{{color:#555}} .leave-table th{{font-size:8pt;text-align:left;color:#666;font-weight:400;padding-bottom:2mm}}
      .leave-table th:not(:first-child),.leave-table td:not(:first-child){{text-align:right}}
      .pending{{font-size:8pt;color:#a60000;margin-top:2mm}} footer{{position:fixed;left:0;right:0;bottom:0;border-top:1px solid #aaa;padding-top:3mm;font-size:8pt;color:#666;display:flex;justify-content:space-between}}
      </style></head><body><div class='page'>{draft_mark}
      <header><div><h1>{document_title}</h1><div class='period'>{period_label}</div></div><img class='logo' src='{logo_uri}'></header>
      <section class='intro'><div class='details'>
      <div class='label'>Employee</div><div>{html.escape(row['legal_name'])}</div><div class='label'>Employee ID</div><div>{html.escape(row['employee_code'] or '')}</div>
      <div class='label'>Designation</div><div>{html.escape(row['role'] or '')}</div><div class='label'>Department</div><div>Park Bäckerei</div></div>
      <div class='details'><div class='label'>Payment period</div><div>{date.fromisoformat(row['period_start']).strftime('%d %B %Y').lstrip('0')} – {date.fromisoformat(row['period_end']).strftime('%d %B %Y').lstrip('0')}</div>
      <div class='label'>Payment date</div><div>{payment_date.strftime('%d %B %Y').lstrip('0')}</div><div class='label'>NRIC / FIN</div><div>{html.escape(row['nric'] or '')}</div>
      <div class='label'>Employer</div><div>{html.escape(row['employer_name'])}</div></div></section>{freelancer_note}
      <section class='section'><div class='columns'><div><div class='section-title'>Earnings · SGD</div><table>{earning_rows}<tr class='total'><td>Total earnings</td><td>{money(row['gross_pay_cents'])}</td></tr></table></div>
      <div><div class='section-title'>Deductions · SGD</div><table>{deduction_rows}<tr class='total'><td>Total deductions</td><td>({money(deductions)})</td></tr></table></div></div></section>
      <div class='net'><span>NET PAYABLE SALARY</span><strong>SGD {money(row['net_pay_cents'])}</strong></div>
      <section class='section'><div class='columns'><div><div class='section-title'>CPF contributions · SGD</div><table>
      <tr><td>Employer CPF contribution</td><td class='num'>{money(row['employer_cpf_cents'])}</td></tr>
      <tr><td>YTD Employer CPF contribution</td><td class='num'>{money(row['cumulative_employer_cpf_cents'])}</td></tr>
      <tr><td>YTD Employee CPF contribution</td><td class='num'>{money(row['cumulative_employee_cpf_cents'])}</td></tr></table></div>
      <div><div class='section-title'>Leave · {year}</div>
      {f"<table class='leave-table'><tr><th>Type</th><th>Entitled</th><th>Used</th><th>Remaining</th></tr>{leave_rows}</table>" if leave_rows else "<p class='leave'>No applicable leave entitlement is configured.</p>"}
      {f"<p class='pending'>{pending_leave} application(s) pending approval are not included.</p>" if pending_leave else ""}</div></div></section>
      {f"<section class='section memo'><div class='section-title'>Information only · not added to net pay</div><table>{memo_rows}</table></section>" if memo_rows else ""}
      <footer><span>{html.escape(row['employer_name'])} · UEN {html.escape(row['uen'])}</span><span>Payroll record {row['payroll_run_item_id']}</span></footer>
      </div></body></html>"""


def generate_draft_payslip(item_id: int, payment_date: date, path: Path,
                           database: Path = DATABASE) -> Path:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    path.parent.mkdir(parents=True, exist_ok=True)
    html_path = path.with_suffix(".html")
    html_path.write_text(payslip_html(connection, item_id, payment_date, draft=True), encoding="utf-8")
    connection.close()
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.exists():
        chrome = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    profile = Path(tempfile.gettempdir()) / "park-payroll-chrome-profile"
    profile.mkdir(exist_ok=True)
    subprocess.run((str(chrome), "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--user-data-dir={profile}", f"--print-to-pdf={path}", html_path.as_uri()),
                   check=True, capture_output=True, text=True)
    for _ in range(50):
        if path.exists() and path.stat().st_size:
            break
        time.sleep(0.1)
    if not path.exists():
        raise RuntimeError("Browser completed without creating the payslip PDF")
    return path


def generate_payslips(payroll_run_id: int, payment_date: date,
                      database: Path = DATABASE, output_root: Path = OUTPUT_ROOT) -> list[Path]:
    app = QApplication.instance() or QApplication([])
    _load_pdf_font()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    paths: list[Path] = []
    with connection:
        items = connection.execute(
            """SELECT p.payroll_run_item_id,p.employee_code,e.main_branch,r.period_start,r.status
                 FROM payroll_run_items p JOIN employees e USING(employee_id)
                 JOIN payroll_runs r USING(payroll_run_id) WHERE p.payroll_run_id=?""",
            (payroll_run_id,),
        ).fetchall()
        if not items or not _approved_status(items[0]["status"]):
            raise ValueError("Payslips require an approved payroll run")
        folder = output_root / "payslips" / items[0]["main_branch"] / items[0]["period_start"][:7]
        folder.mkdir(parents=True, exist_ok=True)
        for item in items:
            path = folder / f"{item['employee_code']}.pdf"
            document = QTextDocument()
            document.setHtml(payslip_html(connection, item["payroll_run_item_id"], payment_date))
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(path))
            document.setPageSize(printer.pageRect(QPrinter.Point).size())
            document.print_(printer)
            digest = _hash(path)
            connection.execute(
                """INSERT INTO payslips(payroll_run_item_id,file_path,sha256,payment_date)
                   VALUES(?,?,?,?) ON CONFLICT(payroll_run_item_id) DO UPDATE SET
                   file_path=excluded.file_path,sha256=excluded.sha256,
                   generated_at=CURRENT_TIMESTAMP,payment_date=excluded.payment_date""",
                (item["payroll_run_item_id"], str(path), digest, payment_date.isoformat()),
            )
            paths.append(path)
    connection.close()
    return paths


def create_accounting_lines(connection: sqlite3.Connection, payroll_run_id: int) -> int:
    status = connection.execute("SELECT status FROM payroll_runs WHERE payroll_run_id=?", (payroll_run_id,)).fetchone()
    if not status or status[0] not in {"REVIEW", "APPROVED", "LOCKED"}:
        raise ValueError("Accounting output requires a reviewed payroll")
    connection.execute("DELETE FROM payroll_accounting_lines WHERE payroll_run_id=?", (payroll_run_id,))
    rows = connection.execute(
        """SELECT e.main_branch entity_code,c.component_code,c.kind,c.gl_account_code,
                  SUM(pc.amount_cents) amount
             FROM payroll_item_components pc JOIN payroll_components c USING(component_code)
             JOIN payroll_run_items p USING(payroll_run_item_id) JOIN employees e USING(employee_id)
            WHERE p.payroll_run_id=? GROUP BY e.main_branch,c.component_code,c.kind,c.gl_account_code""",
        (payroll_run_id,),
    ).fetchall()
    count = 0
    for row in rows:
        account = row["gl_account_code"] or f"PAYROLL_{row['component_code']}"
        if row["kind"] in {"EARNING", "EMPLOYER_COST"}:
            debit, credit = max(row["amount"], 0), max(-row["amount"], 0)
        elif row["kind"] == "DEDUCTION":
            debit, credit = 0, row["amount"]
        else:
            continue
        connection.execute(
            """INSERT INTO payroll_accounting_lines
               (payroll_run_id,entity_code,account_code,debit_cents,credit_cents,description)
               VALUES(?,?,?,?,?,?)""",
            (payroll_run_id, row["entity_code"], account, debit, credit, row["component_code"]),
        )
        count += 1
    # Balance the journal through payroll payable.
    for entity in {row["entity_code"] for row in rows}:
        debit, credit = connection.execute(
            """SELECT COALESCE(SUM(debit_cents),0),COALESCE(SUM(credit_cents),0)
                 FROM payroll_accounting_lines WHERE payroll_run_id=? AND entity_code=?""",
            (payroll_run_id, entity),
        ).fetchone()
        difference = debit - credit
        connection.execute(
            """INSERT INTO payroll_accounting_lines
               (payroll_run_id,entity_code,account_code,debit_cents,credit_cents,description)
               VALUES(?,?,?,?,?,?)""",
            (payroll_run_id, entity, "PAYROLL_PAYABLE", max(-difference, 0), max(difference, 0),
             "Payroll payable balancing line"),
        )
        count += 1
    return count


def export_iras_year_csv(entity: str, year: int, path: Path, database: Path = DATABASE) -> None:
    """IRAS-ready review dataset; not an AIS submission file."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """SELECT e.employee_id,e.legal_name,e.nric,
                  SUM(CASE WHEN c.kind='EARNING' THEN pc.amount_cents ELSE 0 END) taxable_review_cents,
                  SUM(CASE WHEN c.component_code='EMPLOYEE_CPF' THEN pc.amount_cents ELSE 0 END) employee_cpf_cents,
                  SUM(CASE WHEN c.component_code='EMPLOYER_CPF' THEN pc.amount_cents ELSE 0 END) employer_cpf_cents
             FROM payroll_item_components pc JOIN payroll_components c USING(component_code)
             JOIN payroll_run_items p USING(payroll_run_item_id) JOIN payroll_runs r USING(payroll_run_id)
             JOIN employees e USING(employee_id)
            WHERE e.main_branch=? AND substr(r.period_start,1,4)=?
            GROUP BY e.employee_id,e.legal_name,e.nric ORDER BY e.legal_name""",
        (entity, str(year)),
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(("employee_id", "legal_name", "nric", "taxable_income_review",
                         "employee_cpf", "employer_cpf", "status"))
        for row in rows:
            writer.writerow((row["employee_id"], row["legal_name"], row["nric"] or "",
                             money(row["taxable_review_cents"]), money(row["employee_cpf_cents"]),
                             money(row["employer_cpf_cents"]), "REVIEW_REQUIRED"))
    connection.close()
