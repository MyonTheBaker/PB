"""Orchestrate refreshable Order Control Tower evidence sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from order_email_ingest import sync as sync_email
from email_order_processor import process_pending


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
            results.append({
                "source": source, "status": "manual_capture_required",
                "message": "WhatsApp: use the supervised capture extension, then refresh",
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
