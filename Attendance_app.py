# attendance_app.py

import pandas as pd
from datetime import datetime, time
import sys
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QLabel, QHeaderView, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont

FONT_FAMILY = "Segoe UI"

class AttendanceProcessor(QMainWindow):
    """
    A PyQt5 QMainWindow styled to look sleek, spacious, and iOS-like.
    """

    def __init__(self):
        super().__init__()

        # ——— Window (borderless content area to allow custom header) ——— #
        self.setWindowTitle("Attendance QC")
        self.setGeometry(200, 200, 900, 650)
        self.setStyleSheet("background-color: #F2F2F7;")  # iOS system gray background

        # Central widget & main vertical layout
        self.central_widget = QWidget(self)
        self.central_widget.setObjectName("central_widget")
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # ——— Custom header bar (simulated) ——— #
        header = QFrame(self.central_widget)
        header.setObjectName("header")
        header.setFixedHeight(60)
        header.setStyleSheet("""
            QFrame#header {
                background-color: #FFFFFF;
                border-bottom: 1px solid #D0D0D7;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)
        header_layout.setSpacing(0)

        title_lbl = QLabel("Attendance QC", header)
        title_lbl.setFont(QFont(FONT_FAMILY, 20, QFont.DemiBold))
        title_lbl.setStyleSheet("color: #000000;")
        header_layout.addWidget(title_lbl, alignment=Qt.AlignVCenter | Qt.AlignLeft)

        self.layout.addWidget(header)

        # ——— Content area (with padding) ——— #
        content = QWidget(self.central_widget)
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)
        self.layout.addWidget(content)

        # Instruction label
        self.info_label = QLabel("Load a timesheet and review/edit break times", content)
        self.info_label.setFont(QFont(FONT_FAMILY, 16))
        self.info_label.setStyleSheet("color: #3C3C43;")
        content_layout.addWidget(self.info_label)

        # Load button (styled)
        self.load_button = QPushButton("Load Timesheet File", content)
        self.load_button.setFixedHeight(44)
        self.load_button.setFont(QFont(FONT_FAMILY, 15, QFont.DemiBold))
        self.load_button.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: white;
                border-radius: 10px;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #0060D6;
            }
            QPushButton:pressed {
                background-color: #004BB5;
            }
        """)
        self.load_button.clicked.connect(self.load_file)
        content_layout.addWidget(self.load_button, alignment=Qt.AlignLeft)

        # “Card” frame behind table for iOS-style container
        self.card = QFrame(content)
        self.card.setObjectName("card")
        self.card.setStyleSheet("""
            QFrame#card {
                background-color: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #E5E5EA;
            }
        """)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        content_layout.addWidget(self.card, stretch=1)

        # Placeholder for table and update button
        self.table = None
        self.update_button = None

        # Keep a reference to summary_df
        self.summary_df = None

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Timesheet CSV", "", "CSV Files (*.csv);;All Files (*)"
        )
        if not path:
            return

        try:
            self.process_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to process file:\n{e}")

    def process_file(self, file_path):
        # ─── Read CSV and verify columns ─── #
        df = pd.read_csv(file_path)
        required_cols = ['Staff Name', 'Start', 'End']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Expected column '{col}' not found in CSV.")

        # ─── Parse Start & End ─── #
        df['In DT'] = pd.to_datetime(df['Start'], dayfirst=True, errors='coerce')
        df['Out DT'] = pd.to_datetime(df['End'], dayfirst=True, errors='coerce')

        # ─── Flag problem rows ─── #
        df['Problem'] = df.apply(
            lambda r: pd.isna(r['Out DT']) or
                      (pd.notna(r['In DT']) and pd.notna(r['Out DT']) and r['Out DT'] <= r['In DT']),
            axis=1
        )

        # ─── Compute average logout per staff ─── #
        avg_logout = {}
        for emp in df['Staff Name'].unique():
            emp_valid = df[(df['Staff Name'] == emp) & (~df['Problem']) & (df['Out DT'].notna())]
            if not emp_valid.empty:
                secs = (
                    emp_valid['Out DT'].dt.hour * 3600 +
                    emp_valid['Out DT'].dt.minute * 60 +
                    emp_valid['Out DT'].dt.second
                )
                avg_sec = int(secs.mean())
                avg_logout[emp] = time(hour=avg_sec // 3600,
                                      minute=(avg_sec % 3600) // 60,
                                      second=(avg_sec % 60))
            else:
                avg_logout[emp] = time(17, 0, 0)

        # ─── QC pop-ups ─── #
        for idx, row in df[df['Problem']].iterrows():
            emp = row['Staff Name']
            in_dt = row['In DT']
            out_dt = row['Out DT']
            if pd.isna(in_dt):
                continue
            date_only = in_dt.date()

            if pd.isna(out_dt):
                problem_desc = "Missing End time"
            else:
                problem_desc = (
                    f"End ({out_dt.strftime('%d %b %Y %I:%M %p')}) "
                    f"≤ Start ({in_dt.strftime('%d %b %Y %I:%M %p')})"
                )

            suggested_t = avg_logout.get(emp, time(17, 0, 0))
            suggested_dt = datetime.combine(date_only, suggested_t)

            msg = (
                f"Employee: {emp}\n"
                f"Original Start: {in_dt.strftime('%d %b %Y %I:%M %p')}\n"
                f"{problem_desc}\n\n"
                f"Suggested End: {suggested_t.strftime('%I:%M %p')} on {date_only}\n\n"
                "Replace this row’s End with the suggestion?"
            )
            resp = QMessageBox.question(
                self, "QC Alert", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if resp == QMessageBox.Yes:
                df.at[idx, 'Out DT'] = suggested_dt
                df.at[idx, 'End'] = suggested_dt.strftime("%d/%b/%Y %H:%M:%S")

        # ─── Compute hours worked ─── #
        def compute_hours(i, o):
            if pd.isna(i) or pd.isna(o) or (o <= i):
                return 0.0
            return (o - i).total_seconds() / 3600.0

        df['Hours Worked'] = df.apply(lambda r: compute_hours(r['In DT'], r['Out DT']), axis=1)
        df['Worked Flag']  = df['Hours Worked'].apply(lambda x: 1 if x > 0 else 0)

        # ─── Aggregate per staff ─── #
        days_worked = (
            df.groupby('Staff Name')['Worked Flag']
              .sum()
              .reset_index()
              .rename(columns={'Worked Flag': 'Days Worked'})
        )
        total_hours = (
            df.groupby('Staff Name')['Hours Worked']
              .sum()
              .reset_index()
              .rename(columns={'Hours Worked': 'Total Hours'})
        )

        summary = pd.merge(days_worked, total_hours, on='Staff Name')
        summary = summary.sort_values('Staff Name').reset_index(drop=True)

        # ─── Add Break & Net Hours ─── #
        summary['Break (min)'] = 60
        summary['Net Hours'] = summary.apply(
            lambda r: max(0.0, r['Total Hours'] - (r['Break (min)'] / 60.0) * r['Days Worked']),
            axis=1
        )

        self.summary_df = summary
        self._display_summary()

    def _display_summary(self):
        # Clear existing table & button
        if self.table:
            self.table.setParent(None)
            self.table.deleteLater()
            self.table = None
        if self.update_button:
            self.update_button.setParent(None)
            self.update_button.deleteLater()
            self.update_button = None

        # Create table inside a container with padding
        container = QFrame(self.card)
        container.setObjectName("table_container")
        container.setStyleSheet("""
            QFrame#table_container {
                background-color: #FFFFFF;
                border-radius: 12px;
                margin: 12px;
            }
        """)
        table_layout = QVBoxLayout(container)
        table_layout.setContentsMargins(16, 16, 16, 16)
        table_layout.setSpacing(0)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Staff Name", "Days Worked", "Total Hours", "Break (min)", "Net Hours"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setFixedHeight(40)
        self.table.horizontalHeader().setStyleSheet("""
            QHeaderView::section {
                background-color: #F7F7F8;
                color: #1C1C1E;
                font-weight: 600;
                font-size: 14px;
                padding: 0 8px;
                border: none;
            }
        """)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
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
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.table.setRowCount(len(self.summary_df))

        for row_idx, row in enumerate(self.summary_df.itertuples(index=False, name=None)):
            staff, days, tot, brk, net = row
            # Staff
            item_staff = QTableWidgetItem(str(staff))
            item_staff.setFlags(item_staff.flags() ^ Qt.ItemIsEditable)
            item_staff.setFont(QFont(FONT_FAMILY, 13))
            # Days
            item_days = QTableWidgetItem(str(int(days)))
            item_days.setFlags(item_days.flags() ^ Qt.ItemIsEditable)
            item_days.setFont(QFont(FONT_FAMILY, 13))
            # Total Hours
            item_tot = QTableWidgetItem(f"{tot:.2f}")
            item_tot.setFlags(item_tot.flags() ^ Qt.ItemIsEditable)
            item_tot.setFont(QFont(FONT_FAMILY, 13))
            # Break (editable)
            item_brk = QTableWidgetItem(str(int(brk)))
            item_brk.setFont(QFont(FONT_FAMILY, 13))
            item_brk.setBackground(QColor("#FFF9E7"))  # Slight yellow tint
            # Net Hours
            item_net = QTableWidgetItem(f"{net:.2f}")
            item_net.setFlags(item_net.flags() ^ Qt.ItemIsEditable)
            item_net.setFont(QFont(FONT_FAMILY, 13))

            self.table.setItem(row_idx, 0, item_staff)
            self.table.setItem(row_idx, 1, item_days)
            self.table.setItem(row_idx, 2, item_tot)
            self.table.setItem(row_idx, 3, item_brk)
            self.table.setItem(row_idx, 4, item_net)

        table_layout.addWidget(self.table)
        self.card.layout().addWidget(container)

        # Make column 3 (“Break”) editable on double click
        self.table.setEditTriggers(QTableWidget.DoubleClicked)

        # Update button at bottom
        self.update_button = QPushButton("Update")
        self.update_button.setFixedHeight(44)
        self.update_button.setFont(QFont(FONT_FAMILY, 15, QFont.DemiBold))
        self.update_button.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: white;
                border-radius: 10px;
                padding: 0 20px;
                margin: 12px;
            }
            QPushButton:hover {
                background-color: #28A745;
            }
            QPushButton:pressed {
                background-color: #1E7E34;
            }
        """)
        self.update_button.clicked.connect(self._on_update_clicked)
        self.card.layout().addWidget(self.update_button, alignment=Qt.AlignCenter)

        self.info_label.setText("Double-click any “Break” cell to edit, then tap Update.")

    def _on_update_clicked(self):
        # Commit any active edits
        self.table.clearFocus()
        QApplication.processEvents()

        # Recalculate net hours
        for row_idx in range(self.table.rowCount()):
            days = float(self.table.item(row_idx, 1).text())
            tot  = float(self.table.item(row_idx, 2).text())
            try:
                brk = float(self.table.item(row_idx, 3).text())
            except ValueError:
                brk = 60.0
                self.table.item(row_idx, 3).setText("60")

            new_net = tot - (brk / 60.0) * days
            if new_net < 0:
                new_net = 0.0
            self.table.item(row_idx, 4).setText(f"{new_net:.2f}")

        QMessageBox.information(self, "✓ Updated", "Net Hours have been recalculated.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = AttendanceProcessor()
    win.show()
    sys.exit(app.exec_())
