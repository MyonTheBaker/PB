"""Operator UI for weekly preorder overviews and evidence-source refreshes."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from PyQt5.QtCore import QProcess, QSize, Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)


PROJECT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT / "data" / "whatsapp-order-control"
REPORTS = DATA_ROOT / "reports"
SOURCE_LABELS = {"whatsapp": "WhatsApp", "email": "Email", "web": "Web Crawler"}


def monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def next_business_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class OverviewLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._source = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(640, 420)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: white; border: 1px solid #e5e7eb; border-radius: 10px;")

    def set_overview(self, path: Path | None) -> None:
        self._source = QPixmap(str(path)) if path else QPixmap()
        if self._source.isNull():
            self.setPixmap(QPixmap())
            self.setText("No preorder overview has been generated for this week.")
        else:
            self._source = self._crop_blank_bottom(self._source)
            self.setText("")
            self._fit()

    @staticmethod
    def _crop_blank_bottom(source: QPixmap) -> QPixmap:
        image = source.toImage()
        bottom = 0
        for y in range(image.height() - 1, -1, -4):
            if any(
                min(image.pixelColor(x, y).red(), image.pixelColor(x, y).green(),
                    image.pixelColor(x, y).blue()) < 245
                for x in range(0, image.width(), 4)
            ):
                bottom = y
                break
        crop_height = min(image.height(), max(500, bottom + 55))
        return source.copy(0, 0, source.width(), crop_height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self._source.isNull():
            return
        available = self.size() - QSize(28, 28)
        self.setPixmap(self._source.scaled(available, Qt.KeepAspectRatio, Qt.SmoothTransformation))


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
        title = QLabel("ORDER CONTROL TOWER")
        title.setStyleSheet("font-size: 25px; font-weight: 800; letter-spacing: 1px;")
        heading.addWidget(title)
        heading.addStretch()
        self.source_selector = SourceSelector()
        self.source_selector.setStyleSheet("padding: 8px 12px; background: white; border: 1px solid #d5d1ca; border-radius: 7px;")
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet("padding: 9px 22px; color: white; background: #b7362d; border: 0; border-radius: 7px; font-weight: 700;")
        self.refresh_button.clicked.connect(self.refresh_orders)
        heading.addWidget(self.source_selector)
        heading.addWidget(self.refresh_button)
        outer.addLayout(heading)

        subheading = QHBoxLayout()
        self.week_label = QLabel()
        self.week_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.focus_label = QLabel()
        self.focus_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.focus_label.setStyleSheet("color: #6d655d;")
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
        self.overview = OverviewLabel()
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
        QTimer.singleShot(0, self.refresh_orders)

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

    def refresh_orders(self) -> None:
        sources = self.source_selector.selected_sources()
        if not sources:
            self.status.setText("Select at least one source to refresh.")
            return
        if self.process and self.process.state() != QProcess.NotRunning:
            return
        self.refresh_button.setEnabled(False)
        self.source_selector.setEnabled(False)
        self.status.setText("Refreshing " + ", ".join(SOURCE_LABELS[key] for key in sources) + "…")
        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        self.process.setArguments([
            str(PROJECT / "order_source_refresh.py"), "--root", str(DATA_ROOT),
            "--sources", ",".join(sources),
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
