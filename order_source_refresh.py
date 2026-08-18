"""Orchestrate refreshable Order Control Tower evidence sources."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from order_email_ingest import sync as sync_email
from email_order_processor import process_pending, regenerate_combined_overviews
from whatsapp_order_exporter.import_browser_capture import import_capture
from whatsapp_order_processor import process_whatsapp_run


VALID_SOURCES = {"whatsapp", "email", "web"}


def receiver_json(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:8765{path}", data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _run_whatsapp_job(timeout_seconds: int) -> dict:
    started = receiver_json("/automation/start", {})
    job_id = started["job_id"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = receiver_json(f"/automation/status?id={job_id}").get("job") or {}
        if job.get("status") == "completed":
            return job
        if job.get("status") == "failed":
            raise RuntimeError(job.get("error") or "WhatsApp automation failed")
        time.sleep(1)
    raise TimeoutError("WhatsApp extension did not complete the capture within four minutes")


def run_whatsapp_automation(timeout_seconds: int = 240) -> dict:
    for attempt in range(2):
        try:
            return _run_whatsapp_job(timeout_seconds)
        except RuntimeError as exc:
            if attempt == 0 and "Could not open" in str(exc):
                # Older loaded extension workers can report failure just before
                # WhatsApp finishes opening the row they clicked. By the time a
                # second job is claimed, the intended chat is ready.
                continue
            raise
    raise RuntimeError("WhatsApp automation failed after opening the target chat")


def refresh(root: Path, sources: list[str], automate_whatsapp: bool = False) -> dict:
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
                automation_job = None
                if automate_whatsapp:
                    automation_job = run_whatsapp_automation()
                automatic_import = ((automation_job or {}).get("result") or {}).get("import")
                imported = automatic_import if automatic_import and automatic_import.get("status") == "imported" else import_capture(root / "browser", root, None, None)
                provisional = process_whatsapp_run(root, imported["run_id"]) if imported.get("run_id") else 0
                regenerate_combined_overviews(root)
                results.append({
                    "source": source, "status": "ok",
                    "message": (f"WhatsApp: processed {imported['messages']} message(s), "
                                f"{imported['media']} media file(s), and {provisional} provisional order(s); "
                                "overview regenerated"),
                })
            except SystemExit as exc:
                detail = str(exc)
                status = "up_to_date" if "already imported" in detail else "capture_required"
                message = ("WhatsApp: latest capture is already imported" if status == "up_to_date"
                           else "WhatsApp: select WhatsApp, press Refresh, and complete the extension capture")
                results.append({"source": source, "status": status, "message": message})
            except Exception as exc:
                results.append({
                    "source": source, "status": "error",
                    "message": f"WhatsApp automation failed: {exc}",
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
    parser.add_argument("--automate-whatsapp", action="store_true")
    args = parser.parse_args()
    sources = [value.strip().casefold() for value in args.sources.split(",") if value.strip()]
    unknown = sorted(set(sources) - VALID_SOURCES)
    if unknown:
        raise SystemExit(f"Unknown sources: {', '.join(unknown)}")
    print(json.dumps(refresh(args.root, sources, args.automate_whatsapp), ensure_ascii=False))


if __name__ == "__main__":
    main()
