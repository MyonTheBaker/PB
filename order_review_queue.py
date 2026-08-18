"""Persistent review queue and resolution actions for uncertain order candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path


def initialise(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS uncertain_order_reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL, created_at TEXT NOT NULL, customer TEXT NOT NULL,
        product TEXT NOT NULL, fulfillment_date TEXT, notes TEXT NOT NULL,
        confidence REAL NOT NULL, source_ids_json TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending', resolved_at TEXT, resolution_note TEXT)""")


def enqueue(root: Path, run_id: str, orders: list[dict]) -> int:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    initialise(connection)
    added = 0
    with connection:
        for order in orders:
            if float(order.get("confidence", 0)) >= 0.9:
                continue
            sources = json.dumps(sorted(order.get("source_message_ids", [])))
            identity = json.dumps([run_id, order.get("customer"), order.get("product"),
                                   order.get("fulfillment_date"), sources], ensure_ascii=False)
            fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            added += connection.execute(
                """INSERT OR IGNORE INTO uncertain_order_reviews
                   (fingerprint,run_id,created_at,customer,product,fulfillment_date,notes,confidence,source_ids_json)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (fingerprint, run_id, datetime.now().astimezone().isoformat(timespec="seconds"),
                 order.get("customer", "Unknown"), order.get("product", "Order details TBD"),
                 order.get("fulfillment_date"), order.get("notes", ""),
                 float(order.get("confidence", 0)), sources),
            ).rowcount
    connection.close()
    return added


def pending(root: Path) -> list[dict]:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    connection.row_factory = sqlite3.Row
    initialise(connection)
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM uncertain_order_reviews WHERE state='pending' ORDER BY fulfillment_date,created_at"
    )]
    connection.close()
    return rows


def pending_count(root: Path) -> int:
    return len(pending(root))


def dismiss(root: Path, review_id: int) -> None:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    initialise(connection)
    with connection:
        connection.execute("UPDATE uncertain_order_reviews SET state='dismissed',resolved_at=? WHERE id=? AND state='pending'",
                           (datetime.now().astimezone().isoformat(timespec="seconds"), review_id))
    connection.close()


def approve(root: Path, review_id: int, customer: str, product: str,
            fulfillment_date: str, notes: str) -> None:
    date.fromisoformat(fulfillment_date)
    if not customer.strip() or not product.strip():
        raise ValueError("Customer and product are required")
    connection = sqlite3.connect(root / "order-control.sqlite3")
    connection.row_factory = sqlite3.Row
    initialise(connection)
    review = connection.execute(
        "SELECT * FROM uncertain_order_reviews WHERE id=? AND state='pending'", (review_id,)
    ).fetchone()
    base = connection.execute(
        "SELECT id FROM syntheses WHERE run_id NOT LIKE 'email-combined-%' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not review or not base:
        connection.close()
        raise ValueError("Review item or base synthesis is unavailable")
    rows = connection.execute("SELECT * FROM order_items WHERE synthesis_id=?", (base["id"],)).fetchall()
    synthesis_id = str(uuid.uuid4())
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connection:
        connection.execute("INSERT INTO syntheses VALUES(?,?,?,?,?)", (
            synthesis_id, f"review-{review_id}-{uuid.uuid4().hex[:8]}", now,
            f"Approved uncertain order review {review_id}.",
            json.dumps({"base_synthesis_id": base["id"], "review_id": review_id}),
        ))
        for row in rows:
            connection.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                str(uuid.uuid4()), synthesis_id, row["customer"], row["product"], row["quantity"], row["unit"],
                row["fulfillment_date"], row["status"], row["notes"], row["confidence"], row["source_ids_json"],
            ))
        connection.execute("INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            str(uuid.uuid4()), synthesis_id, customer.strip(), product.strip(), None, None,
            fulfillment_date, "confirmed", notes.strip(), 1.0, review["source_ids_json"],
        ))
        connection.execute("""UPDATE uncertain_order_reviews SET state='approved',resolved_at=?,
                              resolution_note=? WHERE id=?""",
                           (now, "Approved with operator edits", review_id))
    connection.close()
    from email_order_processor import regenerate_combined_overviews
    regenerate_combined_overviews(root)
