"""Orchestrate refreshable Order Control Tower evidence sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from order_email_ingest import sync as sync_email
from email_order_processor import process_pending
from whatsapp_order_exporter.import_browser_capture import import_capture


VALID_SOURCES = {"whatsapp", "email", "web"}


def refresh(root: Path, sources: list[str]) -> dict:
    results = []
    for source in sources:
        if source == "email":
            try:
                outcome = sync_email(root, root / "config.json")
                if outcome["status"] == "ok":
                    processed = process_pending(root)
                    message = (f"Email: {outcome['imported']} new message(s), "
                               f"{processed['orders']} order(s), {processed['review']} for review")
                else:
                    message = "Email: mailbox not connected"
                results.append({"source": source, "status": outcome["status"], "message": message})
            except Exception as exc:
                results.append({"source": source, "status": "error", "message": f"Email: {exc}"})
        elif source == "whatsapp":
            try:
                imported = import_capture(root / "browser", root, None, None)
                results.append({
                    "source": source, "status": "ok",
                    "message": (f"WhatsApp: imported {imported['messages']} message(s) and "
                                f"{imported['media']} media file(s) from the latest capture"),
                })
            except SystemExit as exc:
                detail = str(exc)
                status = "up_to_date" if "already imported" in detail else "capture_required"
                message = ("WhatsApp: latest capture is already imported" if status == "up_to_date"
                           else "WhatsApp: select WhatsApp, press Refresh, and complete the extension capture")
                results.append({"source": source, "status": status, "message": message})
            except Exception:
                results.append({
                    "source": source, "status": "capture_required",
                    "message": "WhatsApp: select WhatsApp, press Refresh, and complete the extension capture",
                })
        elif source == "web":
            results.append({
                "source": source, "status": "not_configured",
                "message": "Web Crawler: CaterSpot/Oddle adapter not connected",
            })
    return {"results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh selected order evidence sources")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources", required=True)
    args = parser.parse_args()
    sources = [value.strip().casefold() for value in args.sources.split(",") if value.strip()]
    unknown = sorted(set(sources) - VALID_SOURCES)
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")
    print(json.dumps(refresh(args.root, sources), ensure_ascii=False))


if __name__ == "__main__":
    main()
