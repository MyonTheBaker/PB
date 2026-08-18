"""Deterministically turn clear WhatsApp availability notices into provisional orders."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from llm_order_extractor import audit, extract_orders


PLATFORMS = ("CaterSpot", "EatFirst", "WhyQ", "Feeds", "SmartBites", "Oddle", "Grab", "Foodpanda")
DATE_TIME_RE = re.compile(
    r"(?P<day>\d{1,2})\s*/\s*(?P<month>\d{1,2})"
    r"(?:\s*(?:-|–|:)\s*(?:pick\s*up|pickup|collection|collect(?:ion)?|delivery))?"
    r"\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProvisionalOrder:
    customer: str
    product: str
    fulfillment_date: str
    notes: str
    source_message_ids: tuple[str, ...]
    status: str = "provisional"
    confidence: float = 0.9


def extract_provisional_orders(messages: list[dict]) -> list[ProvisionalOrder]:
    text = "\n".join(str(message.get("body") or "") for message in messages)
    lowered = text.casefold()
    if "tbd" not in lowered and "availability" not in lowered:
        return []
    platform = next((value for value in PLATFORMS if value.casefold() in lowered), None)
    if not platform:
        return []
    product = "Lunch Set (details TBD)" if "lunch set" in lowered else "Order details TBD"
    source_ids = tuple(str(message["id"]) for message in messages if message.get("id"))
    sent_at = next((str(message.get("sent_at")) for message in messages if message.get("sent_at")), "")
    year = datetime.fromisoformat(sent_at).year if sent_at else datetime.now().year
    orders = []
    seen_slots: set[tuple[str, str]] = set()
    for match in DATE_TIME_RE.finditer(text):
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)
        meridiem = match.group("meridiem").upper()
        label = f"{hour}:{minute:02d} {meridiem}"
        fulfillment_date = f"{year:04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
        slot = (fulfillment_date, label)
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        orders.append(ProvisionalOrder(
            customer=platform,
            product=product,
            fulfillment_date=fulfillment_date,
            notes=f"Pickup {label}; exact order TBD",
            source_message_ids=source_ids,
        ))
    return orders


def process_whatsapp_run(root: Path, run_id: str) -> int:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    connection.row_factory = sqlite3.Row
    messages = [dict(row) for row in connection.execute(
        "SELECT id,sent_at,sender,body FROM messages WHERE run_id=? ORDER BY ordinal", (run_id,)
    )]
    try:
        model_result, metadata = extract_orders(messages)
        audit(root, run_id, model_result, metadata, None)
        candidates = [ProvisionalOrder(
            customer=order["customer"], product=order["product"],
            fulfillment_date=order["fulfillment_date"], notes=order["notes"],
            source_message_ids=tuple(order["source_message_ids"]), status=order["status"],
            confidence=order["confidence"],
        ) for order in model_result["orders"] if order["confidence"] >= 0.9]
    except Exception as exc:
        audit(root, run_id, None, None, str(exc))
        candidates = extract_provisional_orders(messages)
    base = connection.execute(
        "SELECT id FROM syntheses WHERE run_id NOT LIKE 'email-combined-%' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not candidates or not base:
        connection.close()
        return 0
    base_rows = [dict(row) for row in connection.execute(
        "SELECT * FROM order_items WHERE synthesis_id=?", (base["id"],)
    )]
    existing_sources = {source for row in base_rows for source in json.loads(row["source_ids_json"])}
    candidates = [order for order in candidates if not set(order.source_message_ids) <= existing_sources]
    if not candidates:
        connection.close()
        return 0
    synthesis_id = str(uuid.uuid4())
    with connection:
        connection.execute(
            "INSERT INTO syntheses VALUES(?,?,?,?,?)",
            (synthesis_id, run_id, datetime.now().astimezone().isoformat(timespec="seconds"),
             f"Added {len(candidates)} provisional WhatsApp order(s).",
             json.dumps({"base_synthesis_id": base["id"], "provisional_orders": len(candidates)})),
        )
        for row in base_rows:
            connection.execute(
                "INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), synthesis_id, row["customer"], row["product"], row["quantity"],
                 row["unit"], row["fulfillment_date"], row["status"], row["notes"],
                 row["confidence"], row["source_ids_json"]),
            )
        for order in candidates:
            connection.execute(
                "INSERT INTO order_items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), synthesis_id, order.customer, order.product, None, None,
                 order.fulfillment_date, order.status, order.notes, order.confidence,
                 json.dumps(order.source_message_ids)),
            )
    connection.close()
    return len(candidates)
