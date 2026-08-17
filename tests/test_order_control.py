import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path
from PyQt5.QtCore import QSettings

from email_order_processor import extract_order, is_reconciled_order_row
from order_email_ingest import credential_from_environment
from order_control_tower import (
    record_post_preparation, save_source_enabled, saved_source_enabled, write_navigation_request,
)
from order_report_renderer import formatted_product_lines, operation_time, order_card_fill, render_png, week_bounds
from order_source_refresh import refresh, run_whatsapp_automation
from whatsapp_order_exporter.receiver import incremental_cutoff, initialise_database


class OrderControlTests(unittest.TestCase):
    def test_order_card_colors_follow_platform_and_fulfilment_rules(self):
        self.assertEqual(order_card_fill("CaterSpot ABCD-EFGH", "Delivery 8:00 AM"), "#FFF4DD")
        self.assertEqual(order_card_fill("EatFirst", "Delivery 11:30 AM"), "#FFF4DD")
        self.assertEqual(order_card_fill("Client", "Delivery by Lalamove"), "#FFE6E6")
        self.assertEqual(order_card_fill("Client", "Full Setup by PB staff"), "#EEF0FF")
        self.assertEqual(order_card_fill("Chinny", "Delivery 9:00 AM", '["source:oddle_email"]'), "#EEF4E9")
        self.assertEqual(order_card_fill("Grab preorder", "Pickup 9:00 AM"), "#EEF4E9")
        self.assertEqual(order_card_fill("Direct customer", "Collection 9:00 AM"), "#F1F1F1")

    def test_email_credential_prefers_process_environment(self):
        with patch.dict("os.environ", {"ORDER_TEST_PASSWORD": "secret"}, clear=False):
            self.assertEqual(credential_from_environment("ORDER_TEST_PASSWORD"), "secret")

    def test_week_bounds_are_monday_to_sunday(self):
        self.assertEqual(week_bounds(date(2026, 8, 16), 1), (date(2026, 8, 17), date(2026, 8, 23)))

    def test_package_lines_preserve_heading_emphasis(self):
        lines = formatted_product_lines("Breakfast Platter: 10 Croissant, 6 Eggs; 12 Mini Muesli")
        self.assertEqual(lines, [
            ("Breakfast Platter", True), ("10 Croissant", False), ("6 Eggs", False),
            ("12 Mini Muesli", True),
        ])

    def test_dictionary_expands_fixed_platter_components(self):
        lines = formatted_product_lines("1 German Sausage Platter (D) (15 pax)", width=60)
        self.assertEqual(lines[0], ("1 German Sausage Platter (D) (15 pax)", True))
        self.assertIn(("5 Smoked Bratwurst", False), lines)
        self.assertIn(("100g Ketchup", False), lines)
        self.assertIn(("100g Spicy Mustard", False), lines)

    def test_sandwich_platter_drops_pax_and_displays_piece_counts(self):
        lines = formatted_product_lines(
            "1 Mini Sandwiches Platter (C) (20 pax): Brie and Caramelised Pecans, Sausage Kraut",
            width=60,
        )
        self.assertEqual(lines, [
            ("1 Mini Sandwiches Platter (C)", True),
            ("Brie 12", False),
            ("Sausage Kraut 12", False),
        ])

    def test_croissant_platter_displays_filling_piece_count(self):
        lines = formatted_product_lines(
            "1 Mini Pretzel-Croissant Canape Platter (16 pcs): Egg Salad", width=60
        )
        self.assertEqual(lines, [
            ("1 Mini Pretzel-Croissant Canape Platter", True),
            ("Egg Salad 16", False),
        ])

    def test_sandwich_platter_preserves_explicit_uneven_piece_counts(self):
        lines = formatted_product_lines(
            "1 Mini Sandwiches Platter (C): 8 Brie, Sausage Kraut 16", width=60
        )
        self.assertEqual(lines, [
            ("1 Mini Sandwiches Platter (C)", True),
            ("Brie 8", False),
            ("Sausage Kraut 16", False),
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

    def test_oddle_completed_order_is_not_cancelled_by_action_link(self):
        message = EmailMessage()
        message["Subject"] = "# 000846 - Completed Delivery Order for 17 Aug 2026"
        message.set_content(
            "Completed Order - #000846\n"
            "Fulfilled order on 12 Aug 2026 for *Chinny, Liew.*\n"
            "Food to be Ready By: 17 Aug 2026 09:00 AM\n"
            "2 x Assorted Flavoured Pretzel\n12 x Bircher Muesli\n"
            "View Order\nCancel This Order"
        )
        order = extract_order(message.as_bytes(), "courier+sg@oddle.me", "2026-08-17T09:24:00+08:00")
        self.assertEqual(order.external_order_id, "000846")
        self.assertEqual(order.customer, "Chinny Liew")
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(len(order.items), 2)

    def test_reconciliation_survives_regenerated_synthesis_row_ids(self):
        row = {"id": "new-row-id", "customer": "Chinny Liew", "fulfillment_date": "2026-08-17"}
        order = {"customer": "Chinny Liew", "fulfillment_date": "2026-08-17"}
        self.assertTrue(is_reconciled_order_row(row, order, {"old-row-id"}))

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

    def test_data_gap_flag_does_not_add_a_visual_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE items(customer,status,notes,product,quantity,unit,fulfillment_date,confidence)")
            rows = list(connection.execute("SELECT * FROM items"))
            plain = Path(directory) / "plain.png"
            gap = Path(directory) / "gap.png"
            args = ("Preorder Overview", "03-09 Aug", rows, date(2026, 8, 3))
            render_png(plain, *args, source_gap=False)
            render_png(gap, *args, source_gap=True)
            self.assertEqual(plain.read_bytes(), gap.read_bytes())

    def test_back_navigation_targets_preorders_page_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "control_tower_navigation.json"
            write_navigation_request(target, 3)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"page": 3}')
            self.assertFalse(target.with_suffix(".tmp").exists())

    def test_selected_week_is_recorded_without_claiming_it_was_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "reports" / "orders-next-week-2026-08-17.png"
            report.parent.mkdir()
            report.write_bytes(b"png")
            outbox_id = record_post_preparation(root, report, date(2026, 8, 17), "Orders")
            connection = sqlite3.connect(root / "order-control.sqlite3")
            row = connection.execute(
                "SELECT o.status,o.sent_at,a.period_start,a.period_end,a.path "
                "FROM outbox o JOIN artifacts a ON a.id=o.artifact_id WHERE o.id=?", (outbox_id,)
            ).fetchone()
            connection.close()
            self.assertEqual(row[:4], ("prepared_for_operator", None, "2026-08-17", "2026-08-23"))
            self.assertEqual(Path(row[4]), report.resolve())

    def test_refresh_source_selection_persists_between_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = str(Path(directory) / "order-control.ini")
            first = QSettings(settings_path, QSettings.IniFormat)
            save_source_enabled(first, "whatsapp", False)
            second = QSettings(settings_path, QSettings.IniFormat)
            self.assertFalse(saved_source_enabled(second, "whatsapp"))
            self.assertTrue(saved_source_enabled(second, "email"))

    def test_whatsapp_refresh_requests_capture_when_none_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            result = refresh(Path(directory), ["whatsapp"])
            self.assertEqual(result["results"][0]["status"], "capture_required")
            self.assertIn("press Refresh", result["results"][0]["message"])
            self.assertNotIn("Capture WhatsApp", result["results"][0]["message"])

    def test_whatsapp_refresh_regenerates_overview_after_import(self):
        imported = {"status": "imported", "messages": 15, "media": 5}
        with patch("order_source_refresh.import_capture", return_value=imported), \
             patch("order_source_refresh.regenerate_combined_overviews") as regenerate:
            result = refresh(Path("unused"), ["whatsapp"])
        regenerate.assert_called_once_with(Path("unused"))
        self.assertIn("processed 15 message(s)", result["results"][0]["message"])
        self.assertIn("overview regenerated", result["results"][0]["message"])

    def test_whatsapp_automation_waits_for_completed_extension_job(self):
        responses = [
            {"job_id": "job-1"},
            {"job": {"id": "job-1", "status": "completed", "result": {"capture": {"ok": True}}}},
        ]
        with patch("order_source_refresh.receiver_json", side_effect=responses):
            result = run_whatsapp_automation(timeout_seconds=1)
        self.assertEqual(result["status"], "completed")

    def test_whatsapp_automation_retries_chat_opening_race(self):
        responses = [
            {"job_id": "job-1"},
            {"job": {"id": "job-1", "status": "failed", "error": "Could not open PB Advance Orders"}},
            {"job_id": "job-2"},
            {"job": {"id": "job-2", "status": "completed", "result": {"capture": {"ok": True}}}},
        ]
        with patch("order_source_refresh.receiver_json", side_effect=responses):
            result = run_whatsapp_automation(timeout_seconds=1)
        self.assertEqual(result["id"], "job-2")

    def test_incremental_cutoff_persists_edit_window_and_margin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "browser-captures.sqlite3"
            initialise_database(database)
            connection = sqlite3.connect(database)
            connection.execute(
                "INSERT INTO capture_runs VALUES(?,?,?,?,?,?,?,?)",
                ("cap-1", "2026-08-17T01:00:00+00:00", "2026-08-17T01:01:00+00:00",
                 "PB Advance Orders", "https://web.whatsapp.com/", 2, "capture.json", "hash"),
            )
            connection.commit()
            connection.close()
            self.assertEqual(incremental_cutoff(root), "2026-08-17T00:36:00+00:00")

    def test_extension_waits_for_chat_and_recovers_missing_content_script(self):
        extension = Path(__file__).parents[1] / "whatsapp_order_exporter" / "extension"
        content = (extension / "content.js").read_text(encoding="utf-8")
        background = (extension / "background.js").read_text(encoding="utf-8")
        manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("cell-frame-container", content)
        self.assertIn("needs_trusted_click", content)
        self.assertIn("chrome.debugger.attach", background)
        self.assertIn('"Input.dispatchMouseEvent"', background)
        self.assertIn("chrome.debugger.detach", background)
        self.assertIn("debugger", manifest["permissions"])
        self.assertIn("await chrome.tabs.reload(tabId)", background)


if __name__ == "__main__":
    unittest.main()
