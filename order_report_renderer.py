"""Render seven-day preorder overview images without external application dependencies."""

from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PLATTER_DICTIONARY_PATH = Path(__file__).with_name("caterspot_platter_dictionary.json")

ORDER_CARD_COLORS = {
    "platform_delivery": "#FFF4DD",
    "pb_delivery": "#FFE6E6",
    "full_setup": "#EEF0FF",
    "marketplace": "#EEF4E9",
    "unknown": "#F1F1F1",
}


def order_card_fill(customer: str | None, notes: str | None, source_ids_json: str | None = None) -> str:
    """Choose a production card color from platform and fulfilment semantics."""
    evidence = " ".join((customer or "", notes or "", source_ids_json or "")).casefold()
    if any(marker in evidence for marker in ("full setup", "setup by pb", "pb staff setup")):
        return ORDER_CARD_COLORS["full_setup"]
    if any(platform in evidence for platform in ("caterspot", "whyq", "feeds", "smartbites", "eatfirst")):
        return ORDER_CARD_COLORS["platform_delivery"]
    if any(platform in evidence for platform in ("oddle", "foodpanda", "food panda", "grab")):
        return ORDER_CARD_COLORS["marketplace"]
    if "lalamove" in evidence:
        return ORDER_CARD_COLORS["pb_delivery"]
    return ORDER_CARD_COLORS["unknown"]


def _platter_dictionary() -> dict:
    if not PLATTER_DICTIONARY_PATH.exists():
        return {"platters": {}}
    return json.loads(PLATTER_DICTIONARY_PATH.read_text(encoding="utf-8"))


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _number(value: float) -> str:
    return f"{value:g}"


def _ordered_quantity(heading: str) -> tuple[float, str]:
    match = re.match(r"^(?P<quantity>\d+(?:\.\d+)?)\s*(?:x\s*)?(?P<name>.+)$", heading.strip(), re.I)
    if not match:
        return 1, heading.strip()
    return float(match.group("quantity")), match.group("name").strip()


def _match_platter(heading: str) -> tuple[str, dict] | None:
    _, name = _ordered_quantity(heading)
    normalized = _normalized(name)
    for key, definition in _platter_dictionary().get("platters", {}).items():
        names = [definition.get("canonical_name", ""), *definition.get("aliases", [])]
        if any(_normalized(candidate) == normalized for candidate in names):
            return key, definition
    return None


def _canonical_filling(value: str, definition: dict) -> str:
    normalized = _normalized(re.sub(r"^\d+(?:\.\d+)?\s*x?\s*", "", value.strip(), flags=re.I))
    aliases = {
        "beat": "B.E.A.T",
        "b e a t": "B.E.A.T",
        "brie and caramelised pecans": "Brie",
        "brie and caramelized pecans": "Brie",
        "portobello pesto": "Portobello Pesto",
        "portobello mushroom pesto": "Portobello Pesto",
        "ham cheese": "Ham and Cheese",
        "egg": "Egg Salad",
    }
    if normalized in aliases:
        return aliases[normalized]
    for choice in definition.get("choices", {}).get("fillings", []):
        if _normalized(choice) == normalized:
            return choice
    return value.strip()


def _filling_with_quantity(value: str, definition: dict) -> tuple[str, float | None]:
    stripped = value.strip()
    leading = re.match(r"^(?P<quantity>\d+(?:\.\d+)?)\s*(?:x\s*)?(?P<name>.+)$", stripped, re.I)
    trailing = re.match(r"^(?P<name>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)$", stripped)
    match = leading or trailing
    if not match:
        return _canonical_filling(stripped, definition), None
    return _canonical_filling(match.group("name"), definition), float(match.group("quantity"))


def _expanded_platter(heading: str, details: str) -> tuple[str, list[str]] | None:
    matched = _match_platter(heading)
    if not matched:
        return None
    key, definition = matched
    quantity, _ = _ordered_quantity(heading)
    display = definition.get("overview_display", {})
    display_heading = display.get("heading", definition["canonical_name"])
    if quantity != 1:
        display_heading = f"{_number(quantity)} {display_heading}"
    else:
        display_heading = f"1 {display_heading}"

    if display.get("show_selected_fillings_only") or key == "pretzel_croissant_canape_platter":
        fillings = [_filling_with_quantity(value, definition) for value in details.split(",") if value.strip()]
        if not fillings:
            return display_heading, []
        pieces_per_platter = 24 if key == "mini_sandwiches_platter_c" else 16
        inferred_pieces = quantity * pieces_per_platter / len(fillings)
        return display_heading, [
            f"{filling} {_number(explicit_pieces if explicit_pieces is not None else inferred_pieces)}"
            for filling, explicit_pieces in fillings
        ]

    components = []
    for component in definition.get("components", []):
        component_quantity = float(component["quantity"]) * quantity
        unit = component.get("unit", "")
        quantity_label = _number(component_quantity)
        if unit in {"g", "kg"}:
            quantity_label += unit
        unit_label = "" if unit in {"pcs", "portions", "packs", "g", "kg"} else unit
        components.append(" ".join(value for value in (
            quantity_label, unit_label, component["item"]
        ) if value))
    return display_heading, components


def week_bounds(as_of: date, offset: int) -> tuple[date, date]:
    start = as_of - timedelta(days=as_of.weekday()) + timedelta(days=7 * offset)
    return start, start + timedelta(days=6)


OPERATION_TIME_RE = re.compile(
    r"(?P<label>Food ready|Self-collection|Collection|Pickup|Delivery)\s+"
    r"(?P<time>\d{1,2}(?::\d{2})?(?:\s*[-–]\s*\d{1,2}(?::\d{2})?)?\s*(?:AM|PM))",
    re.IGNORECASE,
)


def operation_time(notes: str | None) -> tuple[int, str, str]:
    text = notes or ""
    match = OPERATION_TIME_RE.search(text)
    if not match:
        return 24 * 60, "TIME TBC", text
    raw = match.group("time").upper().replace(" ", "")
    first = re.split(r"[-–]", raw)[0]
    meridiem = "PM" if raw.endswith("PM") else "AM"
    first = re.sub(r"(?:AM|PM)$", "", first)
    hour, _, minute = first.partition(":")
    h, m = int(hour), int(minute or 0)
    if meridiem == "PM" and h != 12:
        h += 12
    if meridiem == "AM" and h == 12:
        h = 0
    remaining = (text[:match.start()] + text[match.end():]).strip(" ;,-")
    return h * 60 + m, match.group("time").upper(), remaining


def formatted_product_lines(product: str, width: int = 23) -> list[tuple[str, bool]]:
    """Return (text, bold) lines with package headings above regular details."""
    output: list[tuple[str, bool]] = []
    for entry in (entry.strip() for entry in product.split(";") if entry.strip()):
        heading, separator, details = entry.partition(":")
        expanded = _expanded_platter(heading.strip(), details.strip())
        if expanded:
            display_heading, component_lines = expanded
            output.extend((line, True) for line in textwrap.wrap(display_heading, width))
            for component in component_lines:
                output.extend((line, False) for line in textwrap.wrap(component, width))
            continue
        output.extend((line, True) for line in (textwrap.wrap(heading.strip(), width) or [heading.strip()]))
        if separator:
            for detail in (part.strip() for part in details.split(",") if part.strip()):
                output.extend((line, False) for line in textwrap.wrap(detail, width))
    return output


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_png(path: Path, title: str, subtitle: str, rows: list[sqlite3.Row], start: date,
               source_gap: bool = False) -> None:
    width, height, margin, gutter = 1920, 1800, 34, 12
    title_font, day_font = _font("arialbd.ttf", 38), _font("arialbd.ttf", 23)
    body_bold, small = _font("arialbd.ttf", 17), _font("arial.ttf", 15)
    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.text((margin, 20), title, font=title_font, fill="#2A2A2A")
    draw.text((margin, 68), subtitle, font=small, fill="#777777")
    top = 118
    col_w = (width - 2 * margin - 6 * gutter) // 7
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["fulfillment_date"] or "", []).append(row)
    for day_index in range(7):
        day = start + timedelta(days=day_index)
        x0, x1 = margin + day_index * (col_w + gutter), margin + day_index * (col_w + gutter) + col_w
        draw.text((x0 + 6, top), f"{day.day} {day:%b}", font=day_font, fill="#222222")
        draw.text((x0 + 6, top + 31), day.strftime("%A"), font=small, fill="#333333")
        y = top + 70
        day_rows = grouped.get(day.isoformat(), [])
        if not day_rows:
            draw.line((x0, y, x1, y), fill="#EFEFEF", width=2)
            continue
        cards: dict[tuple, list[sqlite3.Row]] = {}
        for row in day_rows:
            cards.setdefault((row["customer"], row["status"], row["notes"]), []).append(row)
        prepared = []
        for (customer, status, notes), card_rows in sorted(cards.items(), key=lambda item: (operation_time(item[0][2])[0], item[0][0] or "")):
            _, time_label, remaining = operation_time(notes)
            lines: list[tuple[str, bool]] = []
            for row in card_rows:
                qty = f"{row['quantity']:g} {row['unit'] or ''}".strip() if row["quantity"] is not None else ""
                lines.extend(formatted_product_lines(f"{qty} {row['product']}".strip()))
            lines.extend((line, False) for line in textwrap.wrap(remaining, 25)[:3])
            source_ids = " ".join(str(row["source_ids_json"]) for row in card_rows if "source_ids_json" in row.keys())
            prepared.append((customer, status, card_rows, time_label, lines, source_ids))
        natural_height = sum(88 + 22 * len(lines) for *_, lines, _source_ids in prepared)
        scale = max(0.5, min(1.0, (height - 38 - y) / max(natural_height, 1)))
        font_size, small_size = max(9, round(17 * scale)), max(8, round(15 * scale))
        regular, bold, card_small = _font("arial.ttf", font_size), _font("arialbd.ttf", font_size), _font("arial.ttf", small_size)
        line_height, header_height, footer_height, card_gap = max(11, round(22 * scale)), max(22, round(40 * scale)), max(20, round(36 * scale)), max(6, round(12 * scale))
        for index, (customer, status, card_rows, time_label, lines, source_ids) in enumerate(prepared):
            card_h = header_height + line_height * len(lines) + footer_height
            if y + card_h > height - 38:
                draw.text((x0 + 8, height - 34), f"+{len(cards) - index} more", font=body_bold, fill="#E53935")
                break
            confidence = min(float(row["confidence"]) for row in card_rows)
            fill = "#FFE6E6" if status.lower() == "cancelled" else order_card_fill(customer, notes, source_ids)
            border = "#E53935" if confidence < 0.7 else "#F2A23A"
            draw.rounded_rectangle((x0, y, x1, y + card_h), radius=4, fill=fill, outline=border, width=3)
            bbox = draw.textbbox((0, 0), time_label, font=bold)
            draw.text((x1 - (bbox[2] - bbox[0]) - 10, y + 9), time_label, font=bold, fill="#E00000")
            draw.line((x0 + 8, y + header_height - 5, x1 - 8, y + header_height - 5), fill="#F2A23A")
            cy = y + header_height + 4
            for line, is_bold in lines:
                draw.text((x0 + 10, cy), line, font=bold if is_bold else regular, fill="#222222")
                cy += line_height
            footer_y = y + card_h - footer_height + max(2, round(5 * scale))
            footer = textwrap.shorten(customer or "Customer not identified", width=25, placeholder="…")
            draw.text((x0 + 10, footer_y), footer, font=card_small, fill="#E53935")
            confidence_text = f"{confidence:.0%}"
            bbox = draw.textbbox((0, 0), confidence_text, font=card_small)
            draw.text((x1 - (bbox[2] - bbox[0]) - 10, footer_y), confidence_text, font=card_small, fill="#777777")
            if status.lower() == "cancelled":
                draw.line((x0 + 6, y + 6, x1 - 6, y + card_h - 6), fill="#E00000", width=7)
                draw.line((x1 - 6, y + 6, x0 + 6, y + card_h - 6), fill="#E00000", width=7)
            y += card_h + card_gap
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
