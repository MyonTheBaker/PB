"""Render seven-day preorder overview images without external application dependencies."""

from __future__ import annotations

import re
import sqlite3
import textwrap
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


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
    fills = ["#FFF4DD", "#F1F1F1", "#EEF4E9", "#EEF0FF"]
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
            prepared.append((customer, status, card_rows, time_label, lines))
        natural_height = sum(88 + 22 * len(lines) for *_, lines in prepared)
        scale = max(0.5, min(1.0, (height - 38 - y) / max(natural_height, 1)))
        font_size, small_size = max(9, round(17 * scale)), max(8, round(15 * scale))
        regular, bold, card_small = _font("arial.ttf", font_size), _font("arialbd.ttf", font_size), _font("arial.ttf", small_size)
        line_height, header_height, footer_height, card_gap = max(11, round(22 * scale)), max(22, round(40 * scale)), max(20, round(36 * scale)), max(6, round(12 * scale))
        for index, (customer, status, card_rows, time_label, lines) in enumerate(prepared):
            card_h = header_height + line_height * len(lines) + footer_height
            if y + card_h > height - 38:
                draw.text((x0 + 8, height - 34), f"+{len(cards) - index} more", font=body_bold, fill="#E53935")
                break
            confidence = min(float(row["confidence"]) for row in card_rows)
            fill = "#FFE6E6" if status.lower() == "cancelled" else fills[index % len(fills)]
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
