"""Operator UI for weekly preorder overviews and evidence-source refreshes."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

from PyQt5.QtCore import QProcess, QSettings, Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from order_review_queue import approve as approve_review
from order_review_queue import dismiss as dismiss_review
from order_review_queue import pending as pending_reviews
from order_review_queue import pending_count


PROJECT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT / "data" / "whatsapp-order-control"
REPORTS = DATA_ROOT / "reports"
NAVIGATION_REQUEST = PROJECT / "tmp" / "control_tower_navigation.json"
CAPTURE_RECEIVER = PROJECT / "whatsapp_order_exporter" / "receiver.py"
HR_CONTROL_TOWER = PROJECT / "hr_control_tower.py"
PYTHONW = PROJECT / ".venv" / "Scripts" / "pythonw.exe"
SOURCE_LABELS = {"whatsapp": "WhatsApp", "email": "Email", "web": "Web Crawl"}


def saved_source_enabled(settings: QSettings, source: str) -> bool:
    return settings.value(f"refresh_sources/{source}", True, type=bool)


def save_source_enabled(settings: QSettings, source: str, checked: bool) -> None:
    settings.setValue(f"refresh_sources/{source}", checked)
    settings.sync()


def monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def next_business_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def write_navigation_request(path: Path, page: int) -> None:
    """Atomically ask the already-running HR Control Tower to show a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"page": page}), encoding="utf-8")
    temporary.replace(path)


def receiver_is_ready(timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def record_post_preparation(root: Path, report: Path, week_start: date,
                            target_chat: str | None) -> str:
    """Record the selected report in the approval outbox without claiming it was sent."""
    connection = sqlite3.connect(root / "order-control.sqlite3")
    artifact_id, outbox_id = str(uuid.uuid4()), str(uuid.uuid4())
    week_end = week_start + timedelta(days=6)
    with connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS artifacts(
             id TEXT PRIMARY KEY, created_at TEXT NOT NULL, period_start TEXT NOT NULL,
             period_end TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
             source_synthesis_ids_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outbox(
             id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
             target_chat TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, sent_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)",
            (artifact_id, date.today().isoformat(), week_start.isoformat(), week_end.isoformat(),
             "png", str(report.resolve()), "[]"),
        )
        connection.execute(
            "INSERT INTO outbox VALUES(?,?,?,?,?,NULL)",
            (outbox_id, artifact_id, target_chat, "prepared_for_operator", date.today().isoformat()),
        )
    connection.close()
    return outbox_id


def hr_control_tower_is_running() -> bool:
    if sys.platform != "win32":
        return False
    escaped = str(HR_CONTROL_TOWER.resolve()).replace("'", "''")
    command = (
        f"$target='{escaped}'; "
        "@(Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -in @('python.exe','pythonw.exe') -and $_.CommandLine -and "
        "$_.CommandLine.Contains($target) }).Count"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=str(PROJECT), capture_output=True, text=True, timeout=5, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(result.stdout.strip() or "0") > 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def start_hr_control_tower() -> bool:
    if hr_control_tower_is_running():
        return True
    if not HR_CONTROL_TOWER.exists():
        return False
    executable = PYTHONW if PYTHONW.exists() else Path(sys.executable)
    outcome = QProcess.startDetached(str(executable), [str(HR_CONTROL_TOWER)], str(PROJECT))
    return bool(outcome[0] if isinstance(outcome, tuple) else outcome)


def focus_whatsapp_window() -> bool:
    """Bring an existing WhatsApp Web/PWA window forward without opening a new tab."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def inspect(hwnd, _parameter):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if "whatsapp" in buffer.value.casefold():
                matches.append(int(hwnd))
            return True

        user32.EnumWindows(callback_type(inspect), 0)
        if not matches:
            return False
        hwnd = matches[0]
        user32.ShowWindow(hwnd, 9)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        user32.SetForegroundWindow(hwnd)
        return True
    except (AttributeError, OSError, ValueError):
        return False


class OverviewLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._source = QPixmap()
        self.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.setStyleSheet("background: white;")

    def set_overview(self, path: Path | None) -> None:
        self._source = QPixmap(str(path)) if path else QPixmap()
        if self._source.isNull():
            self.setPixmap(QPixmap())
            self.setText("No preorder overview has been generated for this week.")
        else:
            self.setText("")
            self.fit_width(self.parentWidget().width() if self.parentWidget() else self._source.width())

    def fit_width(self, width: int) -> None:
        if self._source.isNull():
            return
        scaled = self._source.scaledToWidth(max(640, width), Qt.SmoothTransformation)
        self.setPixmap(scaled)
        self.setFixedSize(scaled.size())


class OverviewScrollArea(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.overview = OverviewLabel()
        self.setWidget(self.overview)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.setMinimumSize(640, 420)
        self.setStyleSheet(
            "QScrollArea { background: white; border: 1px solid #e5e7eb; border-radius: 10px; }"
            "QScrollArea > QWidget > QWidget { background: white; }"
        )

    def set_overview(self, path: Path | None) -> None:
        self.overview.set_overview(path)
        self._fit_width()
        self.verticalScrollBar().setValue(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_width()

    def _fit_width(self) -> None:
        self.overview.fit_width(self.viewport().width())


class UncertainOrdersDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.tower = parent
        self.rows: dict[int, dict] = {}
        self.setWindowTitle("Validate uncertain orders")
        self.resize(920, 620)
        layout = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.load_selected)
        layout.addWidget(self.list, 2)
        editor = QVBoxLayout()
        form = QFormLayout()
        self.customer, self.fulfillment_date = QLineEdit(), QLineEdit()
        self.product, self.notes = QTextEdit(), QTextEdit()
        self.product.setMaximumHeight(120); self.notes.setMaximumHeight(110)
        self.evidence = QTextEdit(); self.evidence.setReadOnly(True); self.evidence.setMaximumHeight(150)
        form.addRow("Customer/platform", self.customer)
        form.addRow("Order", self.product)
        form.addRow("Date (YYYY-MM-DD)", self.fulfillment_date)
        form.addRow("Notes/time", self.notes)
        form.addRow("Evidence", self.evidence)
        self.open_evidence_button = QPushButton("Open attached evidence")
        self.open_evidence_button.clicked.connect(self.open_evidence)
        form.addRow("", self.open_evidence_button)
        editor.addLayout(form); editor.addStretch()
        actions = QHBoxLayout()
        dismiss_button, approve_button = QPushButton("Dismiss"), QPushButton("Approve order")
        dismiss_button.clicked.connect(self.dismiss_selected)
        approve_button.clicked.connect(self.approve_selected)
        approve_button.setStyleSheet("padding: 9px 16px; color: white; background: #207a5b; font-weight: 700;")
        actions.addWidget(dismiss_button); actions.addStretch(); actions.addWidget(approve_button)
        editor.addLayout(actions); layout.addLayout(editor, 3)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        self.rows = {row["id"]: row for row in pending_reviews(DATA_ROOT)}
        for row in self.rows.values():
            item = QListWidgetItem(f"{row['fulfillment_date'] or 'Date TBD'} · {row['customer']}\n{row['product']} · {row['confidence']:.0%}")
            item.setData(Qt.UserRole, row["id"]); self.list.addItem(item)
        if self.list.count(): self.list.setCurrentRow(0)
        else: self.evidence.setPlainText("No uncertain orders remain.")
        self.tower.update_review_count()

    def selected(self) -> dict | None:
        item = self.list.currentItem()
        return self.rows.get(item.data(Qt.UserRole)) if item else None

    def load_selected(self, current, _previous) -> None:
        row = self.rows.get(current.data(Qt.UserRole)) if current else None
        if not row: return
        self.customer.setText(row["customer"]); self.product.setPlainText(row["product"])
        self.fulfillment_date.setText(row["fulfillment_date"] or ""); self.notes.setPlainText(row["notes"])
        source_ids = json.loads(row["source_ids_json"])
        connection = sqlite3.connect(DATA_ROOT / "order-control.sqlite3")
        placeholders = ",".join("?" for _ in source_ids)
        try:
            messages = connection.execute(
                f"SELECT sent_at,sender,body FROM messages WHERE id IN ({placeholders}) ORDER BY sent_at", source_ids
            ).fetchall() if source_ids else []
            media = connection.execute(
                f"SELECT stored_path FROM media WHERE message_id IN ({placeholders})", source_ids
            ).fetchall() if source_ids else []
            if source_ids and not media:
                media = connection.execute(
                    f"""SELECT DISTINCT x.stored_path FROM messages current
                        JOIN messages previous ON previous.run_id=current.run_id
                         AND previous.ordinal=current.ordinal-1
                        JOIN media x ON x.message_id=previous.id
                        WHERE current.id IN ({placeholders})""", source_ids,
                ).fetchall()
        except sqlite3.Error as exc:
            messages, media = [], []
            self.evidence.setPlainText(f"Evidence could not be loaded: {exc}")
        finally:
            connection.close()
        row["media_paths"] = [value[0] for value in media]
        evidence = [f"Confidence {row['confidence']:.0%}"]
        evidence.extend(f"{sent_at or ''} · {sender or 'Unknown'}\n{body}" for sent_at, sender, body in messages)
        self.evidence.setPlainText("\n\n".join(evidence))
        self.open_evidence_button.setEnabled(bool(row["media_paths"]))

    def open_evidence(self) -> None:
        row = self.selected()
        if row and row.get("media_paths"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(row["media_paths"][0]))

    def dismiss_selected(self) -> None:
        row = self.selected()
        if row and QMessageBox.question(self, "Dismiss candidate", "Dismiss this candidate as not an order?") == QMessageBox.Yes:
            dismiss_review(DATA_ROOT, row["id"]); self.reload()

    def approve_selected(self) -> None:
        row = self.selected()
        if not row: return
        try:
            approve_review(DATA_ROOT, row["id"], self.customer.text(), self.product.toPlainText(),
                           self.fulfillment_date.text().strip(), self.notes.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot approve", str(exc)); return
        self.tower.load_week()
        self.tower.update_review_count()
        self.accept()


class SourceSelector(QWidget):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("Park Baeckerei", "Order Control Tower")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(10)
        self.checkboxes: dict[str, QCheckBox] = {}
        for key, label in SOURCE_LABELS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(saved_source_enabled(self.settings, key))
            checkbox.toggled.connect(lambda checked, source=key: self._save(source, checked))
            checkbox.setStyleSheet("QCheckBox { spacing: 4px; font-size: 13px; }")
            self.checkboxes[key] = checkbox
            layout.addWidget(checkbox)

    def selected_sources(self) -> list[str]:
        return [key for key in SOURCE_LABELS if self.checkboxes[key].isChecked()]

    def _save(self, source: str, checked: bool) -> None:
        save_source_enabled(self.settings, source, checked)

class OrderControlTower(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Park Bäckerei · Order Control Tower")
        self.resize(1460, 940)
        self.week_start = monday(next_business_day(date.today()))
        self.process: QProcess | None = None

        root = QWidget()
        root.setStyleSheet("background: #f5f3ef; color: #252525;")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(14)

        heading = QHBoxLayout()
        self.back_button = QPushButton("← Back to Control Tower")
        self.back_button.setToolTip("Return to the Pre-Orders page in the HR Control Tower")
        self.back_button.setStyleSheet(
            "padding: 9px 14px; background: white; border: 1px solid #d5d1ca; "
            "border-radius: 7px; font-weight: 600;"
        )
        self.back_button.clicked.connect(self.back_to_control_tower)
        title = QLabel("ORDER CONTROL TOWER")
        title.setStyleSheet("font-size: 25px; font-weight: 800; letter-spacing: 1px;")
        heading.addWidget(title)
        heading.addStretch()
        self.source_selector = SourceSelector()
        self.source_selector.setStyleSheet("background: white; border: 1px solid #d5d1ca; border-radius: 7px;")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet("padding: 9px 22px; color: white; background: #b7362d; border: 0; border-radius: 7px; font-weight: 700;")
        self.refresh_button.clicked.connect(self.refresh_orders)
        self.post_button = QPushButton("Post week to WhatsApp")
        self.post_button.setToolTip("Prepare the overview currently displayed for an approved WhatsApp post")
        self.post_button.setStyleSheet("padding: 9px 16px; color: white; background: #207a5b; border: 0; border-radius: 7px; font-weight: 700;")
        self.post_button.clicked.connect(self.prepare_whatsapp_post)
        heading.addWidget(self.source_selector)
        heading.addWidget(self.refresh_button)
        heading.addWidget(self.post_button)
        outer.addLayout(heading)

        subheading = QHBoxLayout()
        self.week_label = QLabel()
        self.week_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.focus_label = QLabel()
        self.focus_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.focus_label.setStyleSheet("color: #6d655d;")
        subheading.addWidget(self.back_button)
        subheading.addWidget(self.week_label)
        subheading.addStretch()
        subheading.addWidget(self.focus_label)
        outer.addLayout(subheading)

        overview_row = QHBoxLayout()
        overview_row.setSpacing(14)
        self.previous_button = self._arrow("‹", "Previous week")
        self.next_button = self._arrow("›", "Next week")
        self.previous_button.clicked.connect(lambda: self.change_week(-1))
        self.next_button.clicked.connect(lambda: self.change_week(1))
        self.overview = OverviewScrollArea()
        overview_row.addWidget(self.previous_button)
        overview_row.addWidget(self.overview, 1)
        overview_row.addWidget(self.next_button)
        outer.addLayout(overview_row, 1)

        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        self.status.setFrameShape(QFrame.NoFrame)
        self.status.setStyleSheet("padding: 8px 2px; color: #625b53;")
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        self.review_button = QPushButton()
        self.review_button.clicked.connect(self.open_review_queue)
        bottom.addWidget(self.review_button)
        outer.addLayout(bottom)
        self.setCentralWidget(root)

        self.load_week()
        self.update_review_count()
        QTimer.singleShot(0, lambda: self.refresh_orders(operator_initiated=False))

    def back_to_control_tower(self) -> None:
        write_navigation_request(NAVIGATION_REQUEST, 3)
        if start_hr_control_tower():
            self.close()
            return
        QMessageBox.critical(
            self, "Control Tower unavailable",
            "The main HR Control Tower could not be opened. This overview will remain open.",
        )

    def start_whatsapp_capture(self, show_instructions: bool = True) -> None:
        if not CAPTURE_RECEIVER.exists():
            QMessageBox.critical(self, "Capture unavailable", "The WhatsApp capture receiver is missing.")
            return
        if not receiver_is_ready():
            started, _pid = QProcess.startDetached(
                sys.executable, [str(CAPTURE_RECEIVER), "--data-root", str(DATA_ROOT / "browser")],
                str(PROJECT),
            )
            if not started:
                QMessageBox.critical(self, "Capture unavailable", "The local WhatsApp receiver could not be started.")
                return
        self.status.setText("WhatsApp capture is ready in the existing WhatsApp Web window.")
        if show_instructions:
            self.status.setText("WhatsApp automation started: opening the order chat, capturing media and history, then ingesting the result…")

    def prepare_whatsapp_post(self) -> None:
        report = self.report_for_week()
        if not report or not report.exists():
            QMessageBox.warning(self, "No overview", "No generated overview is available for the selected week.")
            return
        week_end = self.week_start + timedelta(days=6)
        answer = QMessageBox.question(
            self, "Prepare WhatsApp post",
            f"Prepare the overview for {self.week_start:%d %b} – {week_end:%d %b %Y}?\n\n"
            "The image will be copied to the clipboard and WhatsApp Web will open. "
            "Verify the destination chat and image before pressing Send.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        pixmap = QPixmap(str(report))
        if pixmap.isNull():
            QMessageBox.critical(self, "Cannot prepare post", "The selected overview image could not be loaded.")
            return
        config_path = DATA_ROOT / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        outbox_id = record_post_preparation(DATA_ROOT, report, self.week_start, config.get("chat_title"))
        QApplication.clipboard().setPixmap(pixmap)
        found = focus_whatsapp_window()
        self.status.setText(f"Selected week copied for WhatsApp (outbox {outbox_id[:8]}). Paste with Ctrl+V, verify, then send.")
        QMessageBox.information(
            self, "Selected week ready",
            "The selected overview is on the clipboard. "
            + ("The existing WhatsApp Web window has been brought forward. " if found else "Open your WhatsApp Web desktop shortcut. ")
            + "Open the approved order channel, press Ctrl+V, verify the preview and week, then press Send.",
        )

    def _arrow(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedWidth(52)
        button.setStyleSheet("font-size: 42px; background: white; border: 1px solid #d5d1ca; border-radius: 10px;")
        return button

    def report_for_week(self) -> Path | None:
        candidates = list(REPORTS.glob(f"orders-*-{self.week_start.isoformat()}.png"))
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    def load_week(self) -> None:
        week_end = self.week_start + timedelta(days=6)
        self.week_label.setText(f"{self.week_start:%d %b %Y} – {week_end:%d %b %Y}")
        target = next_business_day(date.today())
        if self.week_start <= target <= week_end:
            self.focus_label.setText(f"Next business day: {target:%A, %d %B}")
        else:
            self.focus_label.setText("Preorder week")
        self.overview.set_overview(self.report_for_week())

    def change_week(self, direction: int) -> None:
        self.week_start += timedelta(days=7 * direction)
        self.load_week()

    def update_review_count(self) -> None:
        count = pending_count(DATA_ROOT)
        self.review_button.setText(f"Uncertain orders ({count})")
        self.review_button.setStyleSheet(
            "padding: 9px 16px; border-radius: 7px; font-weight: 700; "
            + ("color: white; background: #b36b00;" if count else "background: white; border: 1px solid #d5d1ca;")
        )

    def open_review_queue(self) -> None:
        UncertainOrdersDialog(self).exec_()

    def refresh_orders(self, _checked: bool = False, operator_initiated: bool = True) -> None:
        sources = self.source_selector.selected_sources()
        if not sources:
            self.status.setText("Select at least one source to refresh.")
            return
        if self.process and self.process.state() != QProcess.NotRunning:
            return
        if operator_initiated and "whatsapp" in sources:
            self.start_whatsapp_capture(show_instructions=True)
        self.refresh_button.setEnabled(False)
        self.source_selector.setEnabled(False)
        self.status.setText("Refreshing " + ", ".join(SOURCE_LABELS[key] for key in sources) + "…")
        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments([
            str(PROJECT / "order_source_refresh.py"), "--root", str(DATA_ROOT),
            "--sources", ",".join(sources),
            *(["--automate-whatsapp"] if operator_initiated and "whatsapp" in sources else []),
        ])
        self.process.finished.connect(self.refresh_finished)
        self.process.errorOccurred.connect(self.refresh_error)
        self.process.start()

    def refresh_finished(self, _exit_code: int, _exit_status) -> None:
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        error = bytes(self.process.readAllStandardError()).decode("utf-8", "replace").strip()
        try:
            result = json.loads(output.splitlines()[-1])
            messages = [item["message"] for item in result["results"]]
            self.status.setText(" · ".join(messages))
        except Exception:
            self.status.setText(f"Refresh failed: {error or output or 'Unknown error'}")
        self.refresh_button.setEnabled(True)
        self.source_selector.setEnabled(True)
        self.load_week()
        self.update_review_count()

    def refresh_error(self, _error) -> None:
        self.status.setText("The source refresh process could not be started.")
        self.refresh_button.setEnabled(True)
        self.source_selector.setEnabled(True)


def main() -> None:
    app = QApplication(sys.argv)
    window = OrderControlTower()
    window.show()
    raise SystemExit(app.exec_())


if __name__ == "__main__":
    main()
