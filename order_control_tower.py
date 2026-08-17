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

from PyQt5.QtCore import QProcess, Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)


PROJECT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT / "data" / "whatsapp-order-control"
REPORTS = DATA_ROOT / "reports"
NAVIGATION_REQUEST = PROJECT / "tmp" / "control_tower_navigation.json"
CAPTURE_RECEIVER = PROJECT / "whatsapp_order_exporter" / "receiver.py"
HR_CONTROL_TOWER = PROJECT / "hr_control_tower.py"
PYTHONW = PROJECT / ".venv" / "Scripts" / "pythonw.exe"
SOURCE_LABELS = {"whatsapp": "WhatsApp", "email": "Email", "web": "Web Crawler"}


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


class SourceSelector(QToolButton):
    def __init__(self) -> None:
        super().__init__()
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.menu = QMenu(self)
        self.setMenu(self.menu)
        self.actions: dict[str, QAction] = {}
        all_action = self.menu.addAction("All")
        all_action.setCheckable(True)
        all_action.setChecked(True)
        all_action.triggered.connect(self._all_toggled)
        self.actions["all"] = all_action
        self.menu.addSeparator()
        for key, label in SOURCE_LABELS.items():
            action = self.menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(True)
            action.triggered.connect(self._source_toggled)
            self.actions[key] = action
        self._update_text()

    def selected_sources(self) -> list[str]:
        return [key for key in SOURCE_LABELS if self.actions[key].isChecked()]

    def _all_toggled(self, checked: bool) -> None:
        for key in SOURCE_LABELS:
            self.actions[key].setChecked(checked)
        self._update_text()

    def _source_toggled(self) -> None:
        selected = self.selected_sources()
        self.actions["all"].blockSignals(True)
        self.actions["all"].setChecked(len(selected) == len(SOURCE_LABELS))
        self.actions["all"].blockSignals(False)
        self._update_text()

    def _update_text(self) -> None:
        selected = self.selected_sources()
        if len(selected) == len(SOURCE_LABELS):
            label = "All sources"
        elif not selected:
            label = "Select sources"
        else:
            label = ", ".join(SOURCE_LABELS[key] for key in selected)
        self.setText(f"Sources: {label}  ▾")


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
        self.source_selector.setStyleSheet("padding: 8px 12px; background: white; border: 1px solid #d5d1ca; border-radius: 7px;")
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
        outer.addWidget(self.status)
        self.setCentralWidget(root)

        self.load_week()
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
