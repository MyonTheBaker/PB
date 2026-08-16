from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach externally supplied evidence to an order-control run.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--sent-at")
    parser.add_argument("--sender", default="External evidence supplied by operator")
    parser.add_argument("--body", required=True)
    args = parser.parse_args()

    database = args.root / "order-control.sqlite3"
    digest = sha256(args.input)
    message_id = f"external-{digest[:24]}"
    media_dir = args.root / "media" / args.run_id
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / f"{message_id}-{args.input.name}"
    if not target.exists():
        shutil.copy2(args.input, target)
    if sha256(target) != digest:
        raise RuntimeError("Copied evidence failed its SHA-256 integrity check.")

    with sqlite3.connect(database) as db:
        if not db.execute("SELECT 1 FROM ingest_runs WHERE id=?", (args.run_id,)).fetchone():
            raise SystemExit(f"Run does not exist: {args.run_id}")
        if not db.execute("SELECT 1 FROM messages WHERE id=?", (message_id,)).fetchone():
            ordinal = db.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM messages WHERE run_id=?", (args.run_id,)
            ).fetchone()[0]
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)", (
                message_id, args.run_id, args.sent_at, args.sender, args.body, ordinal))
        if not db.execute(
            "SELECT 1 FROM media WHERE run_id=? AND message_id=? AND sha256=?",
            (args.run_id, message_id, digest),
        ).fetchone():
            db.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)", (
                str(uuid.uuid4()), args.run_id, message_id, args.input.name,
                str(target), digest, args.input.suffix.lower()))
    print(json.dumps({"message_id": message_id, "sha256": digest, "stored_path": str(target)}, indent=2))


if __name__ == "__main__":
    main()
