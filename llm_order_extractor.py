"""OpenAI-backed structured extraction for newly archived WhatsApp messages."""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from datetime import date, datetime
from pathlib import Path


MODEL = os.environ.get("ORDER_CONTROL_OPENAI_MODEL", "gpt-5.6-luna")
API_URL = "https://api.openai.com/v1/responses"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "orders": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "customer": {"type": "string"}, "product": {"type": "string"},
                "fulfillment_date": {"type": "string"}, "status": {"type": "string"},
                "notes": {"type": "string"}, "confidence": {"type": "number"},
                "source_message_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["customer", "product", "fulfillment_date", "status", "notes", "confidence", "source_message_ids"],
        }},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["orders", "unresolved"],
}


def api_key() -> str | None:
    value = os.environ.get("OPENAI_API_KEY")
    if value or os.name != "nt":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, "OPENAI_API_KEY")[0])
    except (FileNotFoundError, OSError):
        return None


def _output_text(response: dict) -> str:
    for item in response.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    return str(content.get("text", ""))
    raise ValueError("OpenAI response contained no structured output text")


def validate_result(result: dict, valid_message_ids: set[str]) -> dict:
    valid_orders = []
    for order in result.get("orders", []):
        sources = order.get("source_message_ids") or []
        try:
            date.fromisoformat(order["fulfillment_date"])
            confidence = float(order["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        if not sources or not set(sources) <= valid_message_ids or not 0 <= confidence <= 1:
            continue
        if not str(order.get("customer", "")).strip() or not str(order.get("product", "")).strip():
            continue
        valid_orders.append({**order, "confidence": confidence})
    return {"orders": valid_orders, "unresolved": [str(value) for value in result.get("unresolved", [])]}


def extract_orders(messages: list[dict], timeout: int = 60) -> tuple[dict, dict]:
    key = api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    compact = [{"id": m["id"], "sent_at": m.get("sent_at"), "sender": m.get("sender"), "body": m.get("body", "")} for m in messages]
    instructions = (
        "Extract operational bakery preorder facts only. Interpret forwarded/quoted context and consecutive messages together. "
        "Do not use staff summaries as authority when an original platform order is present. Never invent quantities. "
        "Use 'Order details TBD' for a real dated availability/order notice without menu details. Preserve pickup, delivery, "
        "Lalamove and full-setup facts in notes. Return no order for casual discussion. Every order must cite only supplied IDs. "
        "Use ISO dates and Asia/Hong_Kong context; today is " + date.today().isoformat() + "."
    )
    payload = {
        "model": MODEL, "reasoning": {"effort": "low"},
        "input": [{"role": "system", "content": instructions},
                  {"role": "user", "content": json.dumps(compact, ensure_ascii=False)}],
        "text": {"format": {"type": "json_schema", "name": "order_extraction", "strict": True, "schema": SCHEMA}},
        "store": False,
    }
    request = urllib.request.Request(API_URL, json.dumps(payload).encode("utf-8"), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    result = validate_result(json.loads(_output_text(raw)), {str(m["id"]) for m in messages})
    metadata = {"response_id": raw.get("id"), "model": raw.get("model", MODEL), "usage": raw.get("usage", {})}
    return result, metadata


def audit(root: Path, run_id: str, result: dict | None, metadata: dict | None, error: str | None) -> None:
    connection = sqlite3.connect(root / "order-control.sqlite3")
    with connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS llm_extraction_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, created_at TEXT NOT NULL,
            model TEXT, response_id TEXT, result_json TEXT, usage_json TEXT, error TEXT)""")
        connection.execute("INSERT INTO llm_extraction_audit(run_id,created_at,model,response_id,result_json,usage_json,error) VALUES(?,?,?,?,?,?,?)", (
            run_id, datetime.now().astimezone().isoformat(timespec="seconds"),
            (metadata or {}).get("model", MODEL), (metadata or {}).get("response_id"),
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            json.dumps((metadata or {}).get("usage", {})), error,
        ))
    connection.close()
    if result is not None:
        from order_review_queue import enqueue
        enqueue(root, run_id, result.get("orders", []))
