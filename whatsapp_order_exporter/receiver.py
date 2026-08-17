from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

EXPECTED_CHAT = "PB Advance Orders"
MAX_BODY_BYTES = 25 * 1024 * 1024
MAX_MEDIA_BYTES = 150 * 1024 * 1024


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def initialise_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS capture_runs (
                capture_id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                chat_title TEXT NOT NULL,
                page_url TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                payload_path TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS browser_messages (
                message_id TEXT PRIMARY KEY,
                raw_text TEXT NOT NULL,
                first_capture_id TEXT NOT NULL,
                last_capture_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_messages (
                capture_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                PRIMARY KEY (capture_id, message_id),
                FOREIGN KEY (capture_id) REFERENCES capture_runs(capture_id),
                FOREIGN KEY (message_id) REFERENCES browser_messages(message_id)
            );
            CREATE TABLE IF NOT EXISTS media_assets (
                asset_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                media_index INTEGER NOT NULL,
                received_at TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                path TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                width INTEGER,
                height INTEGER,
                UNIQUE(message_id, media_index, sha256)
            );
            CREATE TABLE IF NOT EXISTS media_passes (
                pass_id TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                chat_title TEXT NOT NULL,
                scanned_messages INTEGER NOT NULL,
                candidates INTEGER NOT NULL,
                downloaded INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                manifest_path TEXT NOT NULL
            );
            """
        )


def safe_header(value: str, limit: int = 500) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()[:limit]


def extension_for_mime(mime_type: str) -> str:
    known = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "video/mp4": ".mp4", "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3", "application/pdf": ".pdf",
    }
    return known.get(mime_type.split(";", 1)[0].lower(), ".bin")


def store_media(data_root: Path, headers: Any, raw: bytes) -> dict[str, Any]:
    message_id = safe_header(headers.get("X-Message-Id", ""), 200)
    media_index_text = safe_header(headers.get("X-Media-Index", "0"), 20)
    if not message_id:
        raise ValueError("Media upload lacks X-Message-Id.")
    try:
        media_index = int(media_index_text)
    except ValueError as error:
        raise ValueError("Invalid media index.") from error
    mime_type = safe_header(headers.get("Content-Type", "application/octet-stream"), 200)
    source_kind = safe_header(headers.get("X-Source-Kind", "blob"), 50)
    width = int(headers.get("X-Media-Width", "0") or 0) or None
    height = int(headers.get("X-Media-Height", "0") or 0) or None
    digest = hashlib.sha256(raw).hexdigest()
    asset_id = f"media_{digest[:20]}"
    folder = data_root / "media" / message_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{media_index:03d}_{digest[:16]}{extension_for_mime(mime_type)}"
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(raw)
        temporary.replace(path)
    database_path = data_root / "browser-captures.sqlite3"
    initialise_database(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(
            "INSERT OR IGNORE INTO media_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, message_id, media_index, utc_now(), mime_type, len(raw), digest,
             str(path), source_kind, width, height),
        )
    return {"ok": True, "asset_id": asset_id, "message_id": message_id,
            "byte_count": len(raw), "sha256": digest, "path": str(path)}


def store_media_manifest(data_root: Path, payload: dict[str, Any], raw: bytes) -> dict[str, Any]:
    if payload.get("chat_title") != EXPECTED_CHAT:
        raise ValueError(f"Chat must be exactly {EXPECTED_CHAT!r}.")
    pass_id = f"media_pass_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    folder = data_root / "media-manifests" / dt.date.today().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{pass_id}.json"
    path.write_bytes(raw)
    results = payload.get("results", [])
    downloaded = sum(1 for item in results if item.get("status") == "downloaded")
    failed = sum(1 for item in results if item.get("status") != "downloaded")
    database_path = data_root / "browser-captures.sqlite3"
    initialise_database(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute("INSERT INTO media_passes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            pass_id, str(payload.get("captured_at", utc_now())), utc_now(), EXPECTED_CHAT,
            int(payload.get("scanned_messages", 0)), len(results), downloaded, failed, str(path)))
    return {"ok": True, "pass_id": pass_id, "downloaded": downloaded,
            "failed": failed, "manifest_path": str(path)}


def validate_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")
    if payload.get("chat_title") != EXPECTED_CHAT:
        raise ValueError(f"Chat must be exactly {EXPECTED_CHAT!r}.")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Payload contains no messages.")
    valid: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, item in enumerate(messages):
        if not isinstance(item, dict):
            raise ValueError(f"Message {index} is not an object.")
        message_id = str(item.get("message_id", "")).strip()
        raw_text = str(item.get("raw_text", "")).strip()
        if not message_id or not raw_text:
            raise ValueError(f"Message {index} lacks message_id or raw_text.")
        if message_id in ids:
            continue
        ids.add(message_id)
        valid.append(item)
    return valid


def store_capture(data_root: Path, payload: dict[str, Any], raw: bytes) -> dict[str, Any]:
    messages = validate_payload(payload)
    received_at = utc_now()
    capture_id = f"cap_{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    capture_dir = data_root / "captures" / dt.date.today().isoformat()
    capture_dir.mkdir(parents=True, exist_ok=True)
    payload_path = capture_dir / f"{capture_id}.json"
    temporary_path = payload_path.with_suffix(".json.tmp")
    temporary_path.write_bytes(raw)
    temporary_path.replace(payload_path)
    digest = hashlib.sha256(raw).hexdigest()
    database_path = data_root / "browser-captures.sqlite3"
    initialise_database(database_path)
    new_messages = 0
    with sqlite3.connect(database_path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(
            "INSERT INTO capture_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                capture_id,
                str(payload.get("captured_at", received_at)),
                received_at,
                EXPECTED_CHAT,
                str(payload.get("page_url", "")),
                len(messages),
                str(payload_path),
                digest,
            ),
        )
        for ordinal, message in enumerate(messages):
            message_id = str(message["message_id"]).strip()
            exists = db.execute("SELECT 1 FROM browser_messages WHERE message_id=?", (message_id,)).fetchone()
            if not exists:
                new_messages += 1
                db.execute(
                    "INSERT INTO browser_messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (message_id, str(message["raw_text"]), capture_id, capture_id, received_at, received_at, json.dumps(message, ensure_ascii=False)),
                )
            else:
                db.execute(
                    "UPDATE browser_messages SET raw_text=?, last_capture_id=?, last_seen_at=?, source_json=? WHERE message_id=?",
                    (str(message["raw_text"]), capture_id, received_at, json.dumps(message, ensure_ascii=False), message_id),
                )
            db.execute(
                "INSERT INTO capture_messages VALUES (?, ?, ?)",
                (capture_id, message_id, int(message.get("ordinal", ordinal))),
            )
    return {
        "ok": True,
        "capture_id": capture_id,
        "message_count": len(messages),
        "new_messages": new_messages,
        "payload_path": str(payload_path),
        "database_path": str(database_path),
    }


class CaptureHandler(BaseHTTPRequestHandler):
    server_version = "PBOrderCapture/0.1"

    def _headers(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "https://web.whatsapp.com")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Message-Id, X-Media-Index, X-Source-Kind, X-Media-Width, X-Media-Height",
        )
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _json(self, status: int, value: dict[str, Any]) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "PB Advance Orders local receiver"})
            return
        if parsed.path == "/automation/next":
            job = next((item for item in self.server.automation_jobs.values()  # type: ignore[attr-defined]
                        if item["status"] == "pending"), None)
            if job:
                job["status"] = "running"
                job["started_at"] = utc_now()
            self._json(200, {"ok": True, "job": job})
            return
        if parsed.path == "/automation/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            job = self.server.automation_jobs.get(job_id)  # type: ignore[attr-defined]
            self._json(200 if job else 404, {"ok": bool(job), "job": job})
            return
        else:
            self._json(404, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/capture", "/media", "/media-manifest", "/automation/start", "/automation/result"}:
            self._json(404, {"ok": False, "error": "Not found."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = MAX_MEDIA_BYTES if parsed.path == "/media" else MAX_BODY_BYTES
            if length <= 0 or length > limit:
                raise ValueError("Invalid capture size.")
            raw = self.rfile.read(length)
            if parsed.path == "/media":
                result = store_media(self.server.data_root, self.headers, raw)  # type: ignore[attr-defined]
            else:
                payload = json.loads(raw.decode("utf-8"))
                if parsed.path == "/capture":
                    result = store_capture(self.server.data_root, payload, raw)  # type: ignore[attr-defined]
                elif parsed.path == "/media-manifest":
                    result = store_media_manifest(self.server.data_root, payload, raw)  # type: ignore[attr-defined]
                elif parsed.path == "/automation/start":
                    job_id = f"automation_{uuid.uuid4().hex}"
                    result = {"ok": True, "job_id": job_id, "status": "pending"}
                    self.server.automation_jobs[job_id] = {  # type: ignore[attr-defined]
                        "id": job_id, "status": "pending", "created_at": utc_now(),
                        "expected_chat": EXPECTED_CHAT, "result": None, "error": None,
                    }
                else:
                    job_id = str(payload.get("job_id", ""))
                    job = self.server.automation_jobs.get(job_id)  # type: ignore[attr-defined]
                    if not job:
                        raise ValueError("Unknown automation job.")
                    job["status"] = "completed" if payload.get("ok") else "failed"
                    job["completed_at"] = utc_now()
                    job["result"] = payload.get("result")
                    job["error"] = payload.get("error")
                    result = {"ok": True, "job_id": job_id, "status": job["status"]}
            self._json(201, result)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            self._json(400, {"ok": False, "error": str(error)})
        except Exception as error:
            self._json(500, {"ok": False, "error": f"Receiver error: {error}"})

    def log_message(self, format: str, *args: Any) -> None:
        if sys.stdout is not None:
            sys.stdout.write(f"{self.address_string()} - {format % args}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local receiver for read-only WhatsApp order captures.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "whatsapp-order-control" / "browser",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    initialise_database(args.data_root / "browser-captures.sqlite3")
    if args.self_test:
        print(json.dumps({"ok": True, "data_root": str(args.data_root.resolve())}))
        return 0
    server = ThreadingHTTPServer((args.host, args.port), CaptureHandler)
    server.data_root = args.data_root.resolve()  # type: ignore[attr-defined]
    server.automation_jobs = {}  # type: ignore[attr-defined]
    print(f"Listening on http://{args.host}:{args.port}; data root: {server.data_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
