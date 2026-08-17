"""Read a dedicated order mailbox into the local order-control evidence store."""

from __future__ import annotations

import argparse
import hashlib
import imaplib
import json
import os
import re
import sqlite3
import ssl
import uuid
from datetime import datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_name(value: str | None, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._")
    return value[:120] or fallback


def credential_from_environment(name: str) -> str | None:
    """Read a process credential, falling back to the Windows user environment registry."""
    value = os.environ.get(name)
    if value:
        return value
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            stored, _kind = winreg.QueryValueEx(key, name)
        return str(stored).strip() or None
    except (FileNotFoundError, OSError):
        return None


def addresses(message: Message, header: str) -> list[str]:
    return [address.casefold() for _, address in getaddresses(message.get_all(header, [])) if address]


def text_body(message: Message) -> str:
    chunks: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = str(part.get_content())
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if part.get_content_type() == "text/html":
            content = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", content)
            content = re.sub(r"(?s)<[^>]+>", " ", content)
        chunks.append(re.sub(r"\s+", " ", content).strip())
    return "\n".join(filter(None, chunks))[:100_000]


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS email_sync_state(
          account TEXT NOT NULL, mailbox TEXT NOT NULL, last_uid INTEGER NOT NULL DEFAULT 0,
          last_checked_at TEXT, last_error TEXT, PRIMARY KEY(account, mailbox)
        );
        CREATE TABLE IF NOT EXISTS email_messages(
          id TEXT PRIMARY KEY, account TEXT NOT NULL, mailbox TEXT NOT NULL, uid INTEGER NOT NULL,
          sha256 TEXT NOT NULL, message_id TEXT, sent_at TEXT, from_json TEXT NOT NULL,
          to_json TEXT NOT NULL, subject TEXT NOT NULL, text_body TEXT NOT NULL,
          raw_path TEXT NOT NULL, ingested_at TEXT NOT NULL,
          UNIQUE(account, mailbox, uid), UNIQUE(account, sha256)
        );
        CREATE TABLE IF NOT EXISTS email_attachments(
          id TEXT PRIMARY KEY, email_message_id TEXT NOT NULL REFERENCES email_messages(id),
          original_name TEXT NOT NULL, stored_path TEXT NOT NULL, sha256 TEXT NOT NULL,
          mime_type TEXT, size_bytes INTEGER NOT NULL
        );
        """
    )


def parsed_sent_at(message: Message) -> str | None:
    try:
        parsed = parsedate_to_datetime(message.get("Date"))
        return parsed.astimezone().isoformat(timespec="seconds") if parsed else None
    except Exception:
        return None


def archive_message(connection: sqlite3.Connection, root: Path, account: str, mailbox: str,
                    uid: int, raw: bytes) -> bool:
    digest = hashlib.sha256(raw).hexdigest()
    if connection.execute(
        "SELECT 1 FROM email_messages WHERE account=? AND (uid=? OR sha256=?)",
        (account, uid, digest),
    ).fetchone():
        return False

    message = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = str(uuid.uuid4())
    raw_dir = root / "email" / "raw"
    attachment_dir = root / "email" / "attachments" / message_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{digest}.eml"
    raw_path.write_bytes(raw)
    connection.execute(
        "INSERT INTO email_messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (message_id, account, mailbox, uid, digest, message.get("Message-ID"),
         parsed_sent_at(message), json.dumps(addresses(message, "From")),
         json.dumps(addresses(message, "To")), str(message.get("Subject") or ""),
         text_body(message), str(raw_path), now()),
    )

    for index, part in enumerate(message.walk(), 1):
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        attachment_dir.mkdir(parents=True, exist_ok=True)
        stored = attachment_dir / f"{index:02d}_{safe_name(filename, 'attachment')}"
        stored.write_bytes(payload)
        connection.execute(
            "INSERT INTO email_attachments VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), message_id, filename, str(stored),
             hashlib.sha256(payload).hexdigest(), part.get_content_type(), len(payload)),
        )
    return True


def sync(root: Path, config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cfg = config.get("email_ingestion", {})
    if not cfg.get("enabled"):
        return {"status": "disabled", "imported": 0, "message": "Email ingestion is not configured"}

    account = cfg["username"]
    mailbox = cfg.get("mailbox", "INBOX")
    password_env = cfg.get("password_env", "ORDER_CONTROL_EMAIL_PASSWORD")
    password = credential_from_environment(password_env)
    if not password:
        raise RuntimeError(f"Required environment variable is not set: {password_env}")

    db_path = root / "order-control.sqlite3"
    connection = sqlite3.connect(db_path)
    ensure_schema(connection)
    row = connection.execute(
        "SELECT last_uid FROM email_sync_state WHERE account=? AND mailbox=?", (account, mailbox)
    ).fetchone()
    last_uid = int(row[0]) if row else 0
    imported = 0
    highest_uid = last_uid

    context = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port", 993)), ssl_context=context)
    try:
        client.login(account, password)
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Could not open mailbox: {mailbox}")
        status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Mailbox search failed")
        uids = [int(value) for value in (data[0] or b"").split() if int(value) > last_uid]
        with connection:
            for uid in uids:
                status, fetched = client.uid("fetch", str(uid), "(BODY.PEEK[])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                imported += int(archive_message(connection, root, account, mailbox, uid, fetched[0][1]))
                highest_uid = max(highest_uid, uid)
            connection.execute(
                "INSERT INTO email_sync_state(account,mailbox,last_uid,last_checked_at,last_error) VALUES(?,?,?,?,NULL) "
                "ON CONFLICT(account,mailbox) DO UPDATE SET last_uid=excluded.last_uid, "
                "last_checked_at=excluded.last_checked_at,last_error=NULL",
                (account, mailbox, highest_uid, now()),
            )
    finally:
        try:
            client.logout()
        except Exception:
            pass
        connection.close()
    return {"status": "ok", "imported": imported, "last_uid": highest_uid, "account": account}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import forwarded orders from an IMAP mailbox")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    config_path = args.config or args.root / "config.json"
    try:
        print(json.dumps(sync(args.root, config_path), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
