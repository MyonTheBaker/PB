# Order Control Tower

## Purpose and status

The Order Control Tower is a Windows desktop application for consolidating preorder evidence into a weekly production overview. It is designed for traceability: raw source material is retained locally, normalized records link back to evidence, uncertain content is not silently guessed, and outbound posting remains approval-controlled.

Current integration status:

| Source | Status | Refresh behaviour |
|---|---|---|
| Email | Operational | Read-only Gmail IMAP sync, archive, deduplication, extraction, reconciliation, and report regeneration |
| WhatsApp | Automated, read-only | Refresh queues the extension to open the approved chat, capture media and loaded history, archive it, and ingest the completed capture |
| CaterSpot / Oddle | Planned | UI option is present, but no authenticated first-party adapter is connected |

The repository contains application code and sanitized configuration only. Mail, message history, attachments, databases, credentials, and generated report images are intentionally excluded from Git.

## Operator workflow

1. Start the application with `run_order_control_tower.cmd`.
2. The initial view opens the week containing the next business day and starts a refresh automatically.
3. Use the source selector to choose Email, WhatsApp, Web Crawler, or all sources.
4. Tick WhatsApp, Email, and/or Web Crawler in the source selector.
5. Select **Refresh** to run every selected source. When WhatsApp is selected, Refresh starts the receiver, opens the approved order chat, captures full media and loaded history, waits for completion, and ingests the result in one workflow.
6. Use the left and right arrows to inspect earlier or later preorder weeks.
7. Select **Post week to WhatsApp** to approve the week currently displayed, copy that exact PNG to the clipboard, and open WhatsApp Web. Paste with `Ctrl+V`, verify the chat and preview, and press Send.
8. Use **Back to Control Tower** to return directly to the Pre-Orders page.
9. Review any source conflicts or records held for operator review before relying on the overview.

The posting handoff is intentionally visible. The tower records the selected report as `prepared_for_operator`; opening WhatsApp does not mark it as sent.

Each day is shown as a report column. Every product or package starts on a new line; package headings use bold type, and package details appear below in regular type. When a column becomes tall, its fonts and spacing scale down, with a minimum scale of 50%.

## Architecture and data flow

```text
Gmail (read-only IMAP) ----> raw email + attachments ----> classifier/extractor --+
                                                                                |
WhatsApp browser capture --> raw chat + media ----------> synthesis ------------+--> reconciliation --> SQLite --> weekly PNG --> operator approval
                                                                                |
CaterSpot / Oddle ---------> future first-party adapters -----------------------+
```

The main modules are:

| Module | Responsibility |
|---|---|
| `order_control_tower.py` | PyQt5 desktop UI, source selection, weekly navigation, and refresh process management |
| `order_source_refresh.py` | Source orchestration and user-facing refresh results |
| `order_email_ingest.py` | Read-only IMAP synchronization, raw archival, attachment storage, UID tracking, and SHA-256 deduplication |
| `email_order_processor.py` | Classification, extraction, canonical order revisions, cross-source reconciliation, and combined synthesis |
| `order_report_renderer.py` | Self-contained seven-day PNG rendering and adaptive typography |
| `whatsapp_order_exporter/` | Local receiver, browser extension, and import tools for supervised WhatsApp capture |

## Installation

Requirements are Windows 10/11, Python 3.11 or later, Chrome for WhatsApp capture, and a Gmail account with IMAP access. From PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
New-Item -ItemType Directory -Force data\whatsapp-order-control | Out-Null
Copy-Item order_control_config.example.json data\whatsapp-order-control\config.json
```

Edit the local configuration with the business name, target chat label, and order mailbox. Do not add a password to JSON.

## Gmail authentication

The mailbox must use a Google App Password; the normal account password is not stored by the application. Two-step verification must be enabled on the Google account before Google exposes App Passwords.

Run:

```powershell
.\configure_order_email_auth.ps1
```

The script prompts without echo and stores the value in the current Windows user's `ORDER_CONTROL_EMAIL_PASSWORD` environment variable. Close and reopen the application after changing the credential so the new process inherits it.

Email access uses `IMAP4_SSL`, opens the configured mailbox read-only, and retrieves messages with `BODY.PEEK[]`. This avoids deliberately changing Gmail read/unread state. The importer advances its UID checkpoint only for retrieved messages and also stores a SHA-256 hash to prevent duplicate archival.

## Email processing and reconciliation

The processor reads plain text and HTML, HTML tables, text/CSV attachments, and text-bearing PDF attachments. Known account, security, verification, and onboarding notifications are ignored. An order-like message is accepted only when the extractor can establish the required fulfillment information and items at sufficient confidence; otherwise it is retained as `needs_review`.

Accepted email orders are written as revisions rather than overwriting history. Reconciliation compares source, external order identity, fulfillment date, customer, and item evidence. When an authoritative email order matches an existing synthesized item, the combined view removes the matched lower-level record before adding the canonical revision. Source IDs remain attached for audit.

Important operational rule: image-only PDFs or ambiguous quantities require human review. The application does not use OCR or invent missing data.

## Local data model

The local SQLite database includes:

- `ingest_runs`, `messages`, `media`, `syntheses`, and `order_items` for WhatsApp evidence and synthesized items;
- `email_sync_state`, `email_messages`, and `email_attachments` for immutable mailbox evidence and checkpoints;
- `email_order_processing` for classification outcomes;
- `canonical_orders` and `canonical_order_items` for normalized, revisioned orders;
- `order_reconciliation` for links between canonical and synthesized evidence;
- `artifacts` and `outbox` for generated reports and approval-controlled delivery.

The database and all raw archives live below `data\whatsapp-order-control` and are excluded from version control.

## WhatsApp capture

Start the local receiver with:

```powershell
.\whatsapp_order_exporter\start_receiver.cmd
```

Load the unpacked extension from `whatsapp_order_exporter\extension` in Chrome's extension developer mode. Open the authorized WhatsApp Business order channel, run the supervised history/media capture, and import the resulting evidence. Capture and import tools retain timestamps, sender/body data, media hashes, source linkage, and capture manifests.

WhatsApp capture is constrained to the exact configured order-chat title and remains read-only. The extension refuses a different chat, archives capture manifests and hashes, and reports unavailable media or unexpanded long-message markers for review. Sara's daily summary messages may be used for comparison, but canonical synthesis should be built from underlying order evidence rather than copying those summaries.

## Security and privacy

- Never commit the `data` directory, SQLite files, raw mail, WhatsApp exports, customer details, attachments, generated reports, `.env`, or `secrets.ini`.
- Keep Gmail credentials in the Windows user environment only. Rotate the App Password immediately if it is exposed.
- Restrict access to the workstation and repository because order evidence contains personal and commercial information.
- Treat all imported message content and attachments as untrusted data; do not execute attachments.
- Review `git status` and run a secret scan before every release.
- Keep posting approval-only until each source has passed a supervised end-to-end validation.

## Verification

Run the automated suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Before an operational release, additionally verify:

1. A refresh with no new mail imports zero records and creates no duplicates.
2. A representative Oddle plain-text order and CaterSpot HTML/table order extract the correct ID, date, time, method, and line quantities.
3. A malformed or image-only order-like message is held for review.
4. A canonical email revision replaces its reconciled synthesized duplicate in the combined view.
5. Current- and next-week PNGs render correctly, including a tall column at 50% scale.
6. Navigation begins on the week containing the next business day.
7. Refresh does not create or send an outbound WhatsApp post.

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `ORDER_CONTROL_EMAIL_PASSWORD` is not set | Run the credential script, then restart the application |
| Gmail rejects login | Confirm two-step verification, create a fresh App Password, and verify the configured account |
| No overview for a week | Confirm a synthesis exists and that fulfillment dates fall in that Monday-Sunday range |
| Email is archived but no order appears | Inspect its processing classification; ambiguous evidence is deliberately held for review |
| WhatsApp refresh requests manual capture | Start the receiver and run the supervised Chrome extension capture |
| Web Crawler says not configured | CaterSpot and Oddle adapters are not implemented yet |

## Roadmap

The next integration milestone is authorized first-party ingestion for CaterSpot and Oddle, preferring official API, export, or order-email evidence over browser scraping. Each adapter must preserve raw evidence, external IDs, revisions, amendments, cancellations, source hashes, and conflicts before it can be enabled for unattended refresh.
