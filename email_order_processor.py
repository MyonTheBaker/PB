"""Classify, extract, reconcile, and render orders archived from email."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from order_report_renderer import render_png, week_bounds


NON_ORDER_SUBJECTS = (
    "security alert", "verification", "confirmation instructions", "password reset",
    "welcome", "confirmation code", "official gmail app", "tips for using",
)
ORDER_HINTS = ("order", "preorder", "pre-order", "food to be ready", "new order for")
PARSER_VERSION = 2
DATE_PATTERNS = (
    r"food\s+to\s+be\s+ready\s+by\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)",
    r"new\s+order\s+for\s+(?:pick\s*up|delivery).*?(\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)\s*@\s*(\d{1,2}:\d{2}\s*[AP]M)",
    r"(?:collection|delivery|pickup|pick\s*up)\s*(?:date|time)?\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})[^\d]{0,20}(\d{1,2}(?::\d{2})?\s*[AP]M)",
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


class TableReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(clean(" ".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.rows.append(self.row)
            self.row = None


@dataclass
class ExtractedItem:
    product: str
    quantity: float
    unit: str = "pcs"


@dataclass
class ExtractedOrder:
    classification: str
    source_type: str
    external_order_id: str | None
    status: str
    customer: str | None
    fulfillment_date: str | None
    fulfillment_time: str | None
    method: str | None
    notes: str
    confidence: float
    items: list[ExtractedItem]
    reason: str


def message_content(raw: bytes) -> tuple[str, list[list[str]]]:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    chunks: list[str] = [str(message.get("Subject") or "")]
    tables: list[list[str]] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            payload = part.get_payload(decode=True) or b""
            if part.get_content_type() == "application/pdf":
                try:
                    chunks.extend(page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages)
                except Exception:
                    pass
            elif part.get_content_maintype() == "text":
                chunks.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            value = str(part.get_content())
        except Exception:
            value = (part.get_payload(decode=True) or b"").decode(
                part.get_content_charset() or "utf-8", "replace"
            )
        if part.get_content_type() == "text/html":
            reader = TableReader()
            reader.feed(value)
            tables.extend(reader.rows)
            value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
            value = re.sub(r"(?s)<[^>]+>", "\n", value)
        chunks.append(value)
    return "\n".join(chunks), tables


def parse_datetime(text: str, reference: datetime) -> tuple[str | None, str | None]:
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text, re.I | re.S)
        if not match:
            continue
        date_text, time_text = clean(match.group(1)), clean(match.group(2)).upper()
        if not re.search(r"\b\d{4}\b", date_text):
            date_text += f" {reference.year}"
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                parsed_date = datetime.strptime(date_text, fmt).date().isoformat()
                parsed_time = datetime.strptime(time_text.replace(" ", ""), "%I:%M%p").strftime("%H:%M")
                return parsed_date, parsed_time
            except ValueError:
                continue
    return None, None


def parse_items(text: str, tables: list[list[str]]) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    seen: set[tuple[str, float]] = set()

    def add(product: str, quantity: float, unit: str = "pcs") -> None:
        product = clean(product).strip("-–—:|$")
        if len(product) < 2 or product.casefold() in {
            "quantity", "item name", "total", "subtotal", "delivery cost", "service fee", "tax",
        }:
            return
        key = (product.casefold(), quantity)
        if key not in seen:
            items.append(ExtractedItem(product, quantity, unit))
            seen.add(key)

    for row in tables:
        cells = [clean(cell) for cell in row if clean(cell)]
        if len(cells) < 2:
            continue
        quantity = None
        for cell in cells[1:3]:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:x|pcs?|pax|sets?)?", cell, re.I)
            if match:
                quantity = float(match.group(1))
                break
        if quantity is not None and not re.search(r"item|product|description", cells[0], re.I):
            add(cells[0], quantity)

    for line in ([] if items else text.splitlines()):
        match = re.match(r"^\s*[-•]?\s*(\d+(?:\.\d+)?)\s*x\s+(.{2,100}?)\s*$", line, re.I)
        if match:
            add(match.group(2), float(match.group(1)))
            continue
        match = re.match(
            r"^\s*[-•]?\s*(\d+(?:\.\d+)?)\s+(?:pcs?|pieces?|sets?|pax)\s+(.{2,100}?)\s*$",
            line, re.I,
        )
        if match:
            add(match.group(2), float(match.group(1)))
    return items


def extract_order(raw: bytes, sender: str, sent_at: str | None) -> ExtractedOrder:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = clean(str(message.get("Subject") or ""))
    lower_subject = subject.casefold()
    source_type = "email"
    sender_lower = sender.casefold()
    if "caterspot" in sender_lower or "caterspot" in lower_subject:
        source_type = "caterspot_email"
    elif "oddle" in sender_lower or "oddle" in lower_subject:
        source_type = "oddle_email"
    if any(value in lower_subject for value in NON_ORDER_SUBJECTS):
        return ExtractedOrder("not_order", source_type, None, "ignored", None, None, None,
                              None, "", 1.0, [], "Known non-order notification")

    text, tables = message_content(raw)
    reference = datetime.fromisoformat(sent_at) if sent_at else datetime.now().astimezone()
    fulfillment_date, fulfillment_time = parse_datetime(text, reference)
    items = parse_items(text, tables)
    external = None
    for pattern in (r"order\s*(?:[-:]\s*)?#\s*([A-Z0-9-]{4,})",
                    r"order\s*(?:number|no\.?|id)\s*[:#]?\s*([A-Z0-9-]{4,})"):
        match = re.search(pattern, text, re.I)
        if match:
            external = match.group(1).upper()
            break
    customer = None
    customer_text = text.replace("*", "")
    for pattern in (r"fulfilled\s+order.*?\bfor\s+([^\n.]{2,100})",
                    r"(?:pending|confirmed|new)\s+order.*?\bfor\s+([^\n,.]{2,80}(?:,\s*[^\n.]{2,50})?)",
                    r"customer(?:\s+name)?\s*[:\-]\s*([^\n]{2,100})"):
        match = re.search(pattern, customer_text, re.I)
        if match:
            customer = clean(match.group(1)).strip(" ,.").replace(",", "")
            break
    cancellation_evidence = bool(
        re.search(r"\bcancel(?:led|ed|ation)\b", subject, re.I)
        or re.search(r"\b(?:order\s+(?:has\s+been\s+)?cancelled|cancelled\s+order)\b", text, re.I)
    )
    status = "cancelled" if cancellation_evidence else "confirmed"
    method = "delivery" if re.search(r"\bdeliver(?:y| to)\b", text, re.I) else "collection"
    has_hint = any(value in (subject + " " + text).casefold() for value in ORDER_HINTS)
    if not has_hint:
        return ExtractedOrder("not_order", source_type, external, "ignored", customer,
                              fulfillment_date, fulfillment_time, method, "", 0.98, items,
                              "No order indicators")
    missing = []
    if not fulfillment_date:
        missing.append("fulfillment date")
    if not items and status != "cancelled":
        missing.append("items")
    if missing:
        return ExtractedOrder("needs_review", source_type, external, status, customer,
                              fulfillment_date, fulfillment_time, method, "", 0.55, items,
                              "Missing " + " and ".join(missing))
    confidence = 0.96 if external and customer else 0.88 if external or customer else 0.82
    return ExtractedOrder("order", source_type, external, status, customer,
                          fulfillment_date, fulfillment_time, method, "", confidence, items, "Parsed")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS email_order_processing(
          email_message_id TEXT PRIMARY KEY REFERENCES email_messages(id),
          classification TEXT NOT NULL, processing_status TEXT NOT NULL,
          reason TEXT NOT NULL, processed_at TEXT NOT NULL, parser_version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_orders(
          id TEXT PRIMARY KEY, source_type TEXT NOT NULL, external_order_id TEXT,
          revision INTEGER NOT NULL, status TEXT NOT NULL, customer TEXT,
          fulfillment_date TEXT, fulfillment_time TEXT, method TEXT, notes TEXT NOT NULL,
          confidence REAL NOT NULL, source_email_id TEXT NOT NULL REFERENCES email_messages(id),
          fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS canonical_external_revision
          ON canonical_orders(source_type, external_order_id, revision)
          WHERE external_order_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS canonical_order_items(
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES canonical_orders(id),
          product TEXT NOT NULL, quantity REAL NOT NULL, unit TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_reconciliation(
          canonical_order_id TEXT PRIMARY KEY REFERENCES canonical_orders(id),
          outcome TEXT NOT NULL, matched_order_item_ids_json TEXT NOT NULL,
          details TEXT NOT NULL, reconciled_at TEXT NOT NULL
        );
        """
    )


def fingerprint(order: ExtractedOrder) -> str:
    payload = {
        "source": order.source_type, "external": order.external_order_id,
        "status": order.status, "customer": order.customer,
        "date": order.fulfillment_date, "time": order.fulfillment_time,
        "items": [(item.product, item.quantity, item.unit) for item in order.items],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def latest_revision(connection: sqlite3.Connection, order: ExtractedOrder) -> int:
    if not order.external_order_id:
        return 1
    row = connection.execute(
        "SELECT MAX(revision) FROM canonical_orders WHERE source_type=? AND external_order_id=?",
        (order.source_type, order.external_order_id),
    ).fetchone()
    return int(row[0] or 0) + 1


def reconcile(connection: sqlite3.Connection, canonical_id: str, order: ExtractedOrder) -> None:
    candidates = connection.execute(
        """SELECT oi.id,oi.customer,oi.product,oi.notes FROM order_items oi
           JOIN syntheses s ON s.id=oi.synthesis_id
           WHERE oi.fulfillment_date=? AND s.rowid=(SELECT MAX(s2.rowid) FROM syntheses s2 WHERE s2.run_id=s.run_id)""",
        (order.fulfillment_date,),
    ).fetchall()
    matched = []
    external = (order.external_order_id or "").casefold()
    customer = re.sub(r"\W+", "", order.customer or "").casefold()
    for row in candidates:
        haystack = " ".join(str(value or "") for value in row[1:]).casefold()
        normalized = re.sub(r"\W+", "", str(row[1] or "")).casefold()
        if (external and external in haystack) or (customer and len(customer) >= 5 and customer in normalized):
            matched.append(row[0])
    outcome = "matched_existing" if matched else "new_order"
    connection.execute(
        "INSERT OR REPLACE INTO order_reconciliation VALUES(?,?,?,?,?)",
        (canonical_id, outcome, json.dumps(matched),
         "Matched by platform order ID/customer and fulfillment date" if matched else "No existing order matched",
         now()),
    )


def process_pending(root: Path) -> dict:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    rows = connection.execute(
        """SELECT em.* FROM email_messages em LEFT JOIN email_order_processing ep
           ON ep.email_message_id=em.id
           WHERE ep.email_message_id IS NULL OR ep.parser_version<? ORDER BY em.uid""",
        (PARSER_VERSION,),
    ).fetchall()
    counts = {"orders": 0, "review": 0, "ignored": 0, "duplicates": 0}
    affected_dates: set[str] = set()
    with connection:
        for row in rows:
            prior_orders = connection.execute(
                "SELECT id FROM canonical_orders WHERE source_email_id=?", (row["id"],)
            ).fetchall()
            for prior in prior_orders:
                connection.execute("DELETE FROM order_reconciliation WHERE canonical_order_id=?", (prior["id"],))
                connection.execute("DELETE FROM canonical_order_items WHERE order_id=?", (prior["id"],))
            connection.execute("DELETE FROM canonical_orders WHERE source_email_id=?", (row["id"],))
            connection.execute("DELETE FROM email_order_processing WHERE email_message_id=?", (row["id"],))
            raw = Path(row["raw_path"]).read_bytes()
            sender = " ".join(json.loads(row["from_json"]))
            order = extract_order(raw, sender, row["sent_at"])
            status = "ignored"
            if order.classification == "order":
                value = fingerprint(order)
                existing = connection.execute("SELECT id FROM canonical_orders WHERE fingerprint=?", (value,)).fetchone()
                if existing:
                    counts["duplicates"] += 1
                    status = "duplicate"
                else:
                    canonical_id = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO canonical_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (canonical_id, order.source_type, order.external_order_id,
                         latest_revision(connection, order), order.status, order.customer,
                         order.fulfillment_date, order.fulfillment_time, order.method, order.notes,
                         order.confidence, row["id"], value, now()),
                    )
                    for item in order.items:
                        connection.execute(
                            "INSERT INTO canonical_order_items VALUES(?,?,?,?,?)",
                            (str(uuid.uuid4()), canonical_id, item.product, item.quantity, item.unit),
                        )
                    reconcile(connection, canonical_id, order)
                    counts["orders"] += 1
                    affected_dates.add(order.fulfillment_date)
                    status = "accepted"
            elif order.classification == "needs_review":
                counts["review"] += 1
                status = "needs_review"
            else:
                counts["ignored"] += 1
            connection.execute(
                "INSERT INTO email_order_processing VALUES(?,?,?,?,?,?)",
                (row["id"], order.classification, status, order.reason, now(), PARSER_VERSION),
            )
    connection.close()
    if affected_dates:
        build_combined_overviews(root, affected_dates)
    return {**counts, "affected_dates": sorted(affected_dates)}


def build_combined_overviews(root: Path, affected_dates: set[str]) -> None:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    connection.row_factory = sqlite3.Row
    base = connection.execute(
        "SELECT id,run_id FROM syntheses WHERE run_id NOT LIKE 'email-combined-%' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not base:
        connection.close()
        return
    rows = [dict(row) for row in connection.execute("SELECT * FROM order_items WHERE synthesis_id=?", (base["id"],))]
    latest_orders = connection.execute(
        """SELECT co.* FROM canonical_orders co WHERE co.revision=(SELECT MAX(co2.revision)
           FROM canonical_orders co2 WHERE co2.source_type=co.source_type
           AND COALESCE(co2.external_order_id,co2.id)=COALESCE(co.external_order_id,co.id))"""
    ).fetchall()
    for order in latest_orders:
        rec = connection.execute("SELECT * FROM order_reconciliation WHERE canonical_order_id=?", (order["id"],)).fetchone()
        matched = set(json.loads(rec["matched_order_item_ids_json"])) if rec else set()
        rows = [row for row in rows if row["id"] not in matched]
        items = connection.execute("SELECT * FROM canonical_order_items WHERE order_id=? ORDER BY rowid", (order["id"],)).fetchall()
        product = "; ".join(f"{item['quantity']:g} {item['unit']} {item['product']}" for item in items)
        source_label = order["source_type"].replace("_email", "").title()
        external = f" {order['external_order_id']}" if order["external_order_id"] else ""
        time_label = None
        if order["fulfillment_time"]:
            parsed_time = datetime.strptime(order["fulfillment_time"], "%H:%M")
            hour = parsed_time.hour % 12 or 12
            time_label = f"{hour}:{parsed_time.minute:02d} {'PM' if parsed_time.hour >= 12 else 'AM'}"
        method = (order["method"] or "collection").title()
        notes = f"{method} {time_label}" if time_label else method
        rows.append({
            "id": str(uuid.uuid4()), "synthesis_id": "", "customer": order["customer"] or f"{source_label}{external}",
            "product": product, "quantity": None, "unit": None,
            "fulfillment_date": order["fulfillment_date"], "status": order["status"],
            "notes": notes, "confidence": order["confidence"],
            "source_ids_json": json.dumps([f"email:{order['source_email_id']}"]),
        })
    run_id = f"email-combined-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    synthesis_id = str(uuid.uuid4())
    manifest_dir = root / "email" / "combined"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / f"{run_id}.json"
    manifest.write_text(json.dumps({"base_synthesis": base["id"], "email_dates": sorted(affected_dates)}, indent=2), encoding="utf-8")
    with connection:
        connection.execute("INSERT INTO ingest_runs VALUES(?,?,?,?,?,?)", (run_id, manifest.name,
                           hashlib.sha256(manifest.read_bytes()).hexdigest(), now(), now(), str(manifest)))
        connection.execute("INSERT INTO syntheses VALUES(?,?,?,?,?)", (synthesis_id, run_id, now(),
                           "Combined latest WhatsApp and canonical email orders", "{}"))
        for row in rows:
            connection.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                str(uuid.uuid4()), synthesis_id, row["customer"], row["product"], row["quantity"], row["unit"],
                row["fulfillment_date"], row["status"], row["notes"], row["confidence"], row["source_ids_json"],
            ))
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())
    offsets = {0, 1}
    offsets.update((date.fromisoformat(value) - current_monday).days // 7 for value in affected_dates)
    for offset in sorted(offsets):
        start, end = week_bounds(today, offset)
        report_rows = list(connection.execute(
            "SELECT * FROM order_items WHERE synthesis_id=? AND fulfillment_date BETWEEN ? AND ? ORDER BY fulfillment_date,customer",
            (synthesis_id, start.isoformat(), end.isoformat()),
        ))
        label = {0: "current-week", 1: "next-week"}.get(offset, f"week-{offset:+d}")
        target = root / "reports" / f"orders-{label}-{start.isoformat()}.png"
        render_png(target, f"{cfg.get('business_name','Business')} Preorder Overview",
                   f"{start:%d %b %Y} - {end:%d %b %Y} | {label.replace('-', ' ').title()}",
                   report_rows, start, False)
    connection.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(process_pending(args.root), ensure_ascii=False))
