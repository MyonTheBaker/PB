# ICON_SALES_UPLOAD.py

import os
import paramiko
import sys
from configparser import ConfigParser
from datetime import datetime
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QFrame,
    QLabel, QPushButton, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QMessageBox, QCheckBox
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QFont, QColor, QDesktopServices
import pandas as pd

# --- Style Configuration (match the toolkit) ---
WINDOW_BG_COLOR      = "#F2F2F7"   # iOS system gray
HEADER_BG_COLOR      = "#FFFFFF"
HEADER_BORDER_COLOR  = "#D0D0D7"
CARD_BG_COLOR        = "#FFFFFF"
CARD_BORDER_COLOR    = "#E5E5EA"
BUTTON_BLUE          = "#007AFF"
BUTTON_BLUE_HOVER    = "#0060D6"
BUTTON_BLUE_PRESSED  = "#004BB5"
TEXT_COLOR_PRIMARY   = "#1C1C1E"
FONT_TITLE           = QFont("Segoe UI", 20, QFont.DemiBold)
FONT_TEXT            = QFont("Segoe UI", 13)
FONT_SMALL           = QFont("Segoe UI", 11)

# --- Default SFTP Credentials ---
SECRETS_FILE = Path(__file__).with_name("secrets.ini")
_secrets = ConfigParser()
_secrets.read(SECRETS_FILE, encoding="utf-8")


def _setting(environment_name: str, ini_name: str, default: str = "") -> str:
    """Prefer cloud-friendly environment variables, then local configuration."""
    return os.getenv(environment_name) or _secrets.get(
        "icon_sftp", ini_name, fallback=default
    )


DEFAULT_SFTP_HOST = _setting("ICON_SFTP_HOST", "host")
DEFAULT_SFTP_USER = _setting("ICON_SFTP_USER", "username")
MERCHANT_ID = _setting("ICON_MERCHANT_ID", "merchant_id")


def load_sftp_password() -> str:
    """Load a cloud environment secret or the ignored local secrets file."""
    return _setting("ICON_SFTP_PASSWORD", "password")


class IconSalesUploadApp(QMainWindow):
    def __init__(self):
        super().__init__()

        # ——— Window Setup ——— #
        self.setWindowTitle("ICON SALES2MALL Upload")
        self.setGeometry(250, 150, 600, 650)
        self.setStyleSheet(f"background-color: {WINDOW_BG_COLOR};")

        # Central container and layout
        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        self.setCentralWidget(container)

        # ——— Header ——— #
        header = QFrame(container)
        header.setFixedHeight(60)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {HEADER_BG_COLOR};
                border-bottom: 1px solid {HEADER_BORDER_COLOR};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        header_layout.setSpacing(0)

        title_lbl = QLabel("ICON SALES2MALL Upload", header)
        title_lbl.setFont(FONT_TITLE)
        title_lbl.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY};")
        header_layout.addWidget(title_lbl, alignment=Qt.AlignVCenter | Qt.AlignLeft)

        container_layout.addWidget(header)

        # ——— Content Area ——— #
        content = QWidget(container)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)
        container_layout.addWidget(content, stretch=1)

        # Instruction Label
        instr_lbl = QLabel(
            "Select the Sales_Summary CSV to generate daily sales files,\n"
            "preview them in a table, then upload via SFTP or simulate."
        )
        instr_lbl.setFont(FONT_TEXT)
        instr_lbl.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY};")
        instr_lbl.setAlignment(Qt.AlignLeft)
        content_layout.addWidget(instr_lbl)

        # — Load & Preview Button ——
        self.load_button = QPushButton("Load & Preview Sales_Summary CSV")
        self.load_button.setFixedHeight(44)
        self.load_button.setFont(FONT_TEXT)
        self.load_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {BUTTON_BLUE};
                color: white;
                border-radius: 10px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_BLUE_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_BLUE_PRESSED};
            }}
        """)
        self.load_button.clicked.connect(self._on_load_clicked)
        content_layout.addWidget(self.load_button, alignment=Qt.AlignLeft)

        # “Card” container for table and SFTP form
        self.card = QFrame(content)
        self.card.setStyleSheet(f"""
            QFrame {{
                background-color: {CARD_BG_COLOR};
                border-radius: 12px;
                border: 1px solid {CARD_BORDER_COLOR};
            }}
        """)
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(16, 16, 16, 16)
        self.card_layout.setSpacing(12)
        content_layout.addWidget(self.card, stretch=1)

        # Placeholder: no data yet
        self._clear_card_contents()

        # In-memory storage: list of (datetime_obj, sales_val, file_name, file_content)
        self.sales_records = []

    def _clear_card_contents(self):
        while self.card_layout.count():
            child = self.card_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        placeholder = QLabel("No data loaded.")
        placeholder.setFont(FONT_SMALL)
        placeholder.setStyleSheet("color: #6C6C70;")
        placeholder.setAlignment(Qt.AlignCenter)
        self.card_layout.addWidget(placeholder, alignment=Qt.AlignCenter)

    def _on_load_clicked(self):
        # 1) Select the Sales_Summary CSV
        csv_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Sales_Summary CSV",
            "",
            "CSV Files (*.csv);;All Files (*)"
        )
        if not csv_path:
            return

        try:
            # Read and validate the two columns used by the upload format.
            df = pd.read_csv(csv_path, usecols=["Time From", "Total Payments"])
            df["Time From"] = pd.to_datetime(df["Time From"], errors="coerce")
            df["Total Payments"] = pd.to_numeric(
                df["Total Payments"].astype(str).str.replace(r"[$,]", "", regex=True),
                errors="coerce",
            )
            df = df.dropna(subset=["Time From", "Total Payments"])

            # A source export can contain more than one row for a day. The
            # destination accepts one file per date, so combine those rows.
            daily_sales = (
                df.assign(SalesDate=df["Time From"].dt.normalize())
                .groupby("SalesDate", as_index=False)["Total Payments"]
                .sum()
            )

            self.sales_records = []
            for _, row in daily_sales.iterrows():
                dt = row["SalesDate"].to_pydatetime()
                total_pay = float(row["Total Payments"])

                # file_name base = first 11 chars of line: “TD_YYYYMMDD”
                date_str = dt.strftime("%Y%m%d")     # e.g. “20250502”
                prefix = f"TD_{date_str}"            # exactly 11 characters
                file_name = f"{prefix}.txt"

                if not MERCHANT_ID:
                    raise ValueError("ICON merchant ID is not configured")
                line_string = f"{prefix}|{MERCHANT_ID}|{date_str}|1|0|{total_pay}|0.00"

                # file_content = everything from character index 12 onward
                # index 0..10 = “TD_YYYYMMDD”, index 11 = “|”
                file_content = line_string[12:] + "\n"

                self.sales_records.append((dt, total_pay, file_name, file_content))

            if not self.sales_records:
                QMessageBox.information(
                    self,
                    "No Valid Data",
                    "No rows could be parsed from the CSV."
                )
                self._clear_card_contents()
                return

            # Now build the preview table and SFTP form
            self._build_table_and_sftp_interface()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to process CSV:\n{e}")

    def _build_table_and_sftp_interface(self):
        # Clear whatever was in the card before
        while self.card_layout.count():
            child = self.card_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Sort records by datetime
        self.sales_records.sort(key=lambda rec: rec[0])

        # ─── Table: Date (weekday) | Sales ─── #
        row_count = len(self.sales_records) + 1  # +1 for “Total” row
        table = QTableWidget()
        table.setColumnCount(2)
        table.setRowCount(row_count)

        # Left‐align both headers
        headers = ["Date", "Sales"]
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setFixedHeight(32)
        table.horizontalHeader().setStyleSheet("""
            QHeaderView::section {
                background-color: #F7F7F8;
                color: #1C1C1E;
                font-weight: 600;
                font-size: 14px;
                padding-left: 8px;
                border: none;
            }
        """)

        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.setStyleSheet("""
            QTableWidget {
                background-color: transparent;
                font-size: 14px;
            }
            QTableWidget::item {
                padding: 8px 4px;
                border-bottom: 1px solid #E5E5EA;
            }
            QTableWidget::item:alternate {
                background-color: #FAFAFB;
            }
        """)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)

        total_sum = 0.0
        for row_idx, (dt, sales_val, _, _) in enumerate(self.sales_records):
            # Format date as “WEEKDAY, DD MON YYYY”
            weekday = dt.strftime("%A").upper()
            day     = dt.day
            month   = dt.strftime("%b").upper()
            year    = dt.year
            date_display = f"{weekday}, {day:02d} {month} {year}"

            item_date = QTableWidgetItem(date_display)
            item_date.setFlags(item_date.flags() ^ Qt.ItemIsEditable)
            item_date.setFont(FONT_SMALL)

            # Format sales as currency: “$ X,XXX.XX”
            sales_display = f"${sales_val:,.2f}"
            item_sales = QTableWidgetItem(sales_display)
            item_sales.setFlags(item_sales.flags() ^ Qt.ItemIsEditable)
            item_sales.setFont(FONT_SMALL)

            table.setItem(row_idx, 0, item_date)
            table.setItem(row_idx, 1, item_sales)
            total_sum += sales_val

        # ─── Total Row ─── #
        total_days = len(self.sales_records)
        bold_font = QFont(FONT_SMALL)
        bold_font.setBold(True)

        total_label = QTableWidgetItem(f"Total ({total_days} days)")
        total_label.setFlags(total_label.flags() ^ Qt.ItemIsEditable)
        total_label.setFont(bold_font)
        total_label.setBackground(QColor("#FFF9E7"))
        total_label.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        total_value_str = f"${total_sum:,.2f}"
        total_value = QTableWidgetItem(total_value_str)
        total_value.setFlags(total_value.flags() ^ Qt.ItemIsEditable)
        total_value.setFont(bold_font)
        total_value.setBackground(QColor("#FFF9E7"))
        total_value.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        table.setItem(row_count - 1, 0, total_label)
        table.setItem(row_count - 1, 1, total_value)

        self.card_layout.addWidget(table)

        # ─── SFTP Fields ─── #
        sftp_header = QLabel("SFTP Settings")
        sftp_header.setFont(FONT_TEXT)
        sftp_header.setStyleSheet(f"color: {TEXT_COLOR_PRIMARY};")
        self.card_layout.addWidget(sftp_header)

        # Host
        host_layout = QHBoxLayout()
        host_lbl = QLabel("Host:")
        host_lbl.setFont(FONT_SMALL)
        host_lbl.setStyleSheet("color: #3C3C43;")
        self.host_edit = QLineEdit()
        self.host_edit.setFont(FONT_SMALL)
        self.host_edit.setText(DEFAULT_SFTP_HOST)
        host_layout.addWidget(host_lbl)
        host_layout.addWidget(self.host_edit, stretch=1)
        self.card_layout.addLayout(host_layout)

        # Username
        user_layout = QHBoxLayout()
        user_lbl = QLabel("User:")
        user_lbl.setFont(FONT_SMALL)
        user_lbl.setStyleSheet("color: #3C3C43;")
        self.user_edit = QLineEdit()
        self.user_edit.setFont(FONT_SMALL)
        self.user_edit.setText(DEFAULT_SFTP_USER)
        user_layout.addWidget(user_lbl)
        user_layout.addWidget(self.user_edit, stretch=1)
        self.card_layout.addLayout(user_layout)

        # Password
        pwd_layout = QHBoxLayout()
        pwd_lbl = QLabel("Password:")
        pwd_lbl.setFont(FONT_SMALL)
        pwd_lbl.setStyleSheet("color: #3C3C43;")
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setFont(FONT_SMALL)
        self.pwd_edit.setText(load_sftp_password())
        pwd_layout.addWidget(pwd_lbl)
        pwd_layout.addWidget(self.pwd_edit, stretch=1)
        self.card_layout.addLayout(pwd_layout)

        # ─── Test Upload Checkbox + Upload Button ─── #
        control_layout = QHBoxLayout()
        control_layout.setSpacing(20)

        self.test_checkbox = QCheckBox("TEST UPLOAD")
        self.test_checkbox.setFont(FONT_SMALL)
        self.test_checkbox.setChecked(True)
        control_layout.addWidget(self.test_checkbox, alignment=Qt.AlignLeft)

        self.upload_btn = QPushButton("Upload/Simulate")
        self.upload_btn.setFixedHeight(44)
        self.upload_btn.setFont(FONT_TEXT)
        self.upload_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BUTTON_BLUE};
                color: white;
                border-radius: 10px;
                padding: 0 18px;
            }}
            QPushButton:hover {{
                background-color: {BUTTON_BLUE_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {BUTTON_BLUE_PRESSED};
            }}
        """)
        self.upload_btn.clicked.connect(self._on_upload_clicked)
        control_layout.addWidget(self.upload_btn, alignment=Qt.AlignLeft)

        control_layout.addStretch(1)
        self.card_layout.addLayout(control_layout)

    def _on_upload_clicked(self):
        if self.test_checkbox.isChecked():
            self._simulate_upload()
        else:
            self._perform_sftp_upload()

    def _simulate_upload(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        test_dir = os.path.join(base_dir, "TEST")
        os.makedirs(test_dir, exist_ok=True)

        try:
            for (_, _, file_name, file_content) in self.sales_records:
                out_path = os.path.join(test_dir, file_name)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(file_content)

            dlg = QMessageBox(self)
            dlg.setWindowTitle("Simulated Upload")
            dlg.setText("SIMULATED UPLOAD COMPLETED")
            open_btn = dlg.addButton("OPEN FOLDER", QMessageBox.ActionRole)
            dlg.addButton(QMessageBox.Ok)
            dlg.exec_()

            if dlg.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(test_dir))

            # Clear in-memory files & reset UI
            self.sales_records = []
            self._clear_card_contents()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Simulation failed:\n{e}")

    def _perform_sftp_upload(self):
        host = self.host_edit.text().strip()
        user = self.user_edit.text().strip()
        pwd  = self.pwd_edit.text()

        if not host or not user or not pwd:
            QMessageBox.warning(self, "Missing Info", "Please fill in all SFTP fields.")
            return

        transport = None
        sftp = None
        try:
            transport = paramiko.Transport((host, 22))
            transport.banner_timeout = 30
            transport.auth_timeout = 30
            transport.connect(username=user, password=pwd)
            sftp = paramiko.SFTPClient.from_transport(transport)

            # Upload all in-memory files into SFTP root
            for (_, _, file_name, file_content) in self.sales_records:
                with sftp.open(file_name, "w") as remote_file:
                    remote_file.write(file_content)

            QMessageBox.information(self, "Upload Complete", "Sales files uploaded successfully.")

            # Clear in-memory files & reset UI
            self.sales_records = []
            self._clear_card_contents()

        except Exception as e:
            QMessageBox.critical(self, "Upload Failed", f"{e}")
        finally:
            if sftp is not None:
                sftp.close()
            if transport is not None:
                transport.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = IconSalesUploadApp()
    win.show()
    sys.exit(app.exec_())
