"""Render the 400x300 tri-color stock-index page.

Layout per approved mockup: three horizontally centered columns
(name / price / change), dashed-ruled bilingual zone headers, red-up /
black-down colors, no legend line.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageOps

from epd_status import font, text_right

ZONE_TITLES = {
    "US": "US · 美股",
    "CN": "CN · 中国",
    "ASIA": "ASIA · 日韩",
}

CANVAS_MARGIN = 16
TITLE_FONT_SIZE = 13
ZONE_FONT_SIZE = 10
NAME_FONT_SIZE = 14
PRICE_FONT_SIZE = 15
CHANGE_FONT_SIZE = 12
META_FONT_SIZE = 10


def _group_zones(quotes):
    """Preserve first-seen zone order; collect rows per zone."""
    zones: list[str] = []
    grouped: dict[str, list] = {}
    labels: dict[str, str] = {}
    for quote in quotes:
        zone = quote.zone
        if zone not in grouped:
            grouped[zone] = []
            zones.append(zone)
            labels[zone] = quote.zone_label or ZONE_TITLES.get(zone, zone)
        grouped[zone].append(quote)
    return [(zone, labels[zone], grouped[zone]) for zone in zones]


def _draw_dashed_hline(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int,
                       dash: int = 3, gap: int = 3):
    x = x1
    while x < x2:
        end = min(x + dash, x2)
        draw.line((x, y, end, y), fill=0, width=1)
        x = end + gap


def _centered_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str,
                   text_font, fill=0):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    width = right - left
    draw.text((cx - width // 2 - left, y - top), text, font=text_font, fill=fill)


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"{price:,.2f}"


def _fmt_change(change_pct: float | None) -> str | None:
    if change_pct is None:
        return None
    arrow = "▲" if change_pct > 0 else ("▼" if change_pct < 0 else "")
    if not arrow:
        return "±0.00%"
    return f"{arrow} {abs(change_pct):+.2f}%"


def build_stocks_card(
    width: int,
    height: int,
    quotes,
    *,
    now: datetime | None = None,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    if (width, height) != (400, 300):
        raise ValueError("The stocks layout currently targets the 400x300 panel.")

    now = (now or datetime.now().astimezone()).astimezone()
    black = Image.new("1", (width, height), 1)
    red = Image.new("1", (width, height), 1)
    black_draw = ImageDraw.Draw(black)
    red_draw = ImageDraw.Draw(red)

    title_font = font(TITLE_FONT_SIZE)
    zone_font = font(ZONE_FONT_SIZE)
    name_font = font(NAME_FONT_SIZE)
    price_font = font(PRICE_FONT_SIZE)
    change_font = font(CHANGE_FONT_SIZE)
    meta_font = font(META_FONT_SIZE)

    col_name = 100
    col_price = 200
    col_change = 310

    black_draw.text((CANVAS_MARGIN, 12), "GLOBAL INDICES", font=title_font, fill=0)
    text_right(black_draw, width - CANVAS_MARGIN, 14,
               "UPD " + now.strftime("%H:%M"), meta_font)
    black_draw.line((CANVAS_MARGIN, 34, width - CANVAS_MARGIN, 34), fill=0, width=2)

    row_height = 27
    zone_band = 24
    max_y = height - 12
    y = 44
    for zone, label, rows in _group_zones(quotes):
        label_width = zone_font.getbbox(label)[2]
        dashed_y = y + zone_band // 2 - 4
        _draw_dashed_hline(black_draw, CANVAS_MARGIN, width // 2 - label_width // 2 - 8, dashed_y)
        _draw_dashed_hline(black_draw, width // 2 + label_width // 2 + 8, width - CANVAS_MARGIN, dashed_y)
        _centered_text(black_draw, width // 2, y, label, zone_font)
        y += zone_band
        for quote in rows:
            if y + row_height > max_y:
                break
            _centered_text(black_draw, col_name, y, quote.name, name_font)
            if quote.unavailable:
                _centered_text(black_draw, col_price, y - 1, "—", price_font)
                _centered_text(black_draw, col_change, y + 2, "unavailable", change_font)
            else:
                going_up = quote.change_pct is not None and quote.change_pct > 0
                price_plane = red_draw if going_up else black_draw
                _centered_text(price_plane, col_price, y - 1,
                               _fmt_price(quote.price), price_font)
                change_text = _fmt_change(quote.change_pct)
                if change_text:
                    _centered_text(price_plane, col_change, y + 2, change_text, change_font)
            y += row_height

    preview = Image.new("RGB", (width, height), (251, 250, 246))
    preview.paste((23, 21, 19), mask=ImageOps.invert(black.convert("L")))
    preview.paste((188, 46, 46), mask=ImageOps.invert(red.convert("L")))
    return black, red, preview
