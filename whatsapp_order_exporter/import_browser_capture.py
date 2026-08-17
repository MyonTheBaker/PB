from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def parse_pre_plain(value: str) -> tuple[str | None, str | None]:
    # WhatsApp Web exposes values such as: [12:56 PM, 8/11/2026] Sender:
    import re
    match = re.match(r"^\[(.+?),\s*(\d{1,2}/\d{1,2}/\d{4})\]\s*(.*?):?$", value or "")
    if not match:
        return None, None
    try:
        stamp = dt.datetime.strptime(f"{match.group(2)} {match.group(1)}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        return None, match.group(3).strip() or None
    return stamp.isoformat(timespec="minutes"), match.group(3).strip() or None


def import_capture(browser_root: Path, order_root: Path, capture_id: str | None,
                   merge_run_id: str | None = None) -> dict:
    browser_db = browser_root / "browser-captures.sqlite3"
    order_db = order_root / "order-control.sqlite3"
    if not browser_db.exists() or not order_db.exists():
        raise SystemExit("Browser or order-control database is missing.")
    source = sqlite3.connect(browser_db)
    source.row_factory = sqlite3.Row
    if capture_id:
        capture = source.execute("SELECT * FROM capture_runs WHERE capture_id=?", (capture_id,)).fetchone()
    else:
        capture = source.execute("SELECT * FROM capture_runs ORDER BY received_at DESC LIMIT 1").fetchone()
    if not capture:
        raise SystemExit("Capture run was not found.")
    payload_path = Path(capture["payload_path"])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    run_id = merge_run_id or f"browser-{capture['capture_id']}"
    target = sqlite3.connect(order_db)
    exists = target.execute("SELECT 1 FROM ingest_runs WHERE id=?", (run_id,)).fetchone()
    if exists and not merge_run_id:
        raise SystemExit(f"Browser capture is already imported as {run_id}.")
    if merge_run_id and not exists:
        raise SystemExit(f"Merge target does not exist: {merge_run_id}.")
    raw_dir = order_root / "raw" / run_id
    media_dir = order_root / "media" / run_id
    packet_dir = order_root / "analysis-packets"
    raw_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)
    packet_dir.mkdir(parents=True, exist_ok=True)
    archived_payload = raw_dir / payload_path.name
    shutil.copy2(payload_path, archived_payload)
    message_rows = []
    for ordinal, item in enumerate(payload.get("messages", [])):
        sent_at, sender = parse_pre_plain(item.get("pre_plain_text", ""))
        message_rows.append({
            "id": str(item["message_id"]), "sent_at": sent_at, "sender": sender,
            "body": str(item.get("raw_text", "")), "ordinal": ordinal,
        })
    asset_rows = list(source.execute(
        "SELECT ma.* FROM media_assets ma JOIN capture_messages cm ON cm.message_id=ma.message_id "
        "WHERE cm.capture_id=? ORDER BY cm.ordinal, ma.media_index", (capture["capture_id"],)))
    packet_media = []
    with target:
        target.execute(
            """CREATE TABLE IF NOT EXISTS message_revisions(
               id TEXT PRIMARY KEY, message_id TEXT NOT NULL, run_id TEXT NOT NULL,
               captured_at TEXT, sent_at TEXT, sender TEXT, body TEXT NOT NULL,
               ordinal INTEGER NOT NULL, UNIQUE(message_id,run_id))"""
        )
        if not merge_run_id:
            target.execute("INSERT INTO ingest_runs VALUES(?,?,?,?,?,?)", (
                run_id, payload_path.name, digest(archived_payload), capture["captured_at"],
                dt.datetime.now().astimezone().isoformat(timespec="seconds"), str(archived_payload)))
        else:
            # Free the run's unique ordinal space before applying a reordered
            # virtualized-browser revision.
            target.execute("UPDATE messages SET ordinal=-ordinal-1 WHERE run_id=?", (run_id,))
        for row in message_rows:
            existing_message = target.execute(
                "SELECT run_id,sent_at,sender,body,ordinal FROM messages WHERE id=?", (row["id"],)
            ).fetchone()
            if existing_message:
                target.execute(
                    "INSERT OR IGNORE INTO message_revisions VALUES(?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), row["id"], existing_message[0], capture["captured_at"],
                     existing_message[1], existing_message[2], existing_message[3], existing_message[4]),
                )
                target.execute(
                    "UPDATE messages SET run_id=?,sent_at=?,sender=?,body=?,ordinal=? WHERE id=?",
                    (run_id, row["sent_at"], row["sender"], row["body"], row["ordinal"], row["id"]),
                )
            else:
                target.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (
                    row["id"], run_id, row["sent_at"], row["sender"], row["body"], row["ordinal"]))
        for asset in asset_rows:
            source_path = Path(asset["path"])
            if not source_path.exists() or digest(source_path) != asset["sha256"]:
                raise RuntimeError(f"Media integrity check failed: {source_path}")
            suffix = source_path.suffix or ".bin"
            target_path = media_dir / f"{asset['message_id']}_{asset['media_index']:03d}_{asset['sha256'][:12]}{suffix}"
            shutil.copy2(source_path, target_path)
            media_exists = target.execute(
                "SELECT 1 FROM media WHERE run_id=? AND message_id=? AND sha256=?",
                (run_id, asset["message_id"], asset["sha256"])).fetchone()
            if not media_exists:
                target.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)", (
                    str(uuid.uuid4()), run_id, asset["message_id"], target_path.name,
                    str(target_path), asset["sha256"], asset["mime_type"]))
            packet_media.append({"message_id": asset["message_id"], "name": target_path.name,
                                 "local_path": str(target_path), "sha256": asset["sha256"],
                                 "mime_type": asset["mime_type"]})
    packet = {"run_id": run_id, "captured_for": capture["captured_at"],
              "messages": message_rows, "media": packet_media}
    packet_name = f"{run_id}-revision-{capture['capture_id']}.json" if merge_run_id else f"{run_id}.json"
    packet_path = packet_dir / packet_name
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    source.close()
    target.close()
    return {"run_id": run_id, "messages": len(message_rows), "media": len(packet_media),
            "analysis_packet": str(packet_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import a validated browser capture into order control.")
    parser.add_argument("--browser-root", type=Path, required=True)
    parser.add_argument("--order-root", type=Path, required=True)
    parser.add_argument("--capture-id")
    parser.add_argument("--merge-run-id")
    args = parser.parse_args()
    print(json.dumps(import_capture(args.browser_root, args.order_root, args.capture_id, args.merge_run_id), indent=2))


if __name__ == "__main__":
    main()
