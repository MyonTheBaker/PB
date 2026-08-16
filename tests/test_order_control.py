import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from email_order_processor import extract_order
from order_report_renderer import formatted_product_lines, operation_time, render_png, week_bounds


class OrderControlTests(unittest.TestCase):
    def test_week_bounds_are_monday_to_sunday(self):
        self.assertEqual(week_bounds(date(2026, 8, 16), 1), (date(2026, 8, 17), date(2026, 8, 23)))

    def test_package_lines_preserve_heading_emphasis(self):
        lines = formatted_product_lines("Breakfast Platter: 10 Croissant, 6 Eggs; 12 Mini Muesli")
        self.assertEqual(lines, [
            ("Breakfast Platter", True), ("10 Croissant", False), ("6 Eggs", False),
            ("12 Mini Muesli", True),
        ])

    def test_operation_time_sorts_and_removes_label(self):
        minutes, label, notes = operation_time("Delivery 7:45 AM; loading bay")
        self.assertEqual((minutes, label, notes), (465, "7:45 AM", "loading bay"))

    def test_plain_text_order_extraction(self):
        message = EmailMessage()
        message["Subject"] = "New Order #ODD-42"
        message.set_content(
            "New order for pick up 19 August 2026 @ 7:45 AM\n"
            "Customer: Example Bakery\n2 x Croissant Platter\n12 x Mini Muesli"
        )
        order = extract_order(message.as_bytes(), "orders@example.com", datetime(2026, 8, 16).isoformat())
        self.assertEqual(order.classification, "order")
        self.assertEqual(order.fulfillment_date, "2026-08-19")
        self.assertEqual(order.fulfillment_time, "07:45")
        self.assertGreaterEqual(len(order.items), 2)

    def test_renderer_creates_png(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE items(customer,status,notes,product,quantity,unit,fulfillment_date,confidence)")
            connection.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?)", (
                "Example", "new", "Collection 7:45 AM", "Breakfast: 10 Croissant, 6 Eggs",
                None, None, "2026-08-17", 0.95,
            ))
            rows = list(connection.execute("SELECT * FROM items"))
            target = Path(directory) / "overview.png"
            render_png(target, "Preorder Overview", "17-23 Aug", rows, date(2026, 8, 17))
            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
