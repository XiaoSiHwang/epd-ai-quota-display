"""Render the 400x300 tri-color workout calendar page.

Layout follows the approved Keep-style mockup: left column with month title,
progress ring (workouts / monthly goal), streak and total counters; right
side a 7-column dot calendar where each trained day is a red dot and every
other day an outlined circle.
"""

from __future__ import annotations

import calendar
from datetime import datetime

from PIL import Image, ImageDraw, ImageOps

from epd_status import font, text_right

CANVAS_MARGIN = 16
TITLE_FONT_SIZE = 34
RING_FONT_SIZE = 26
STAT_FONT_SIZE = 22
STAT_LABEL_FONT_SIZE = 10
WEEKDAY_FONT_SIZE = 11
UPD_FONT_SIZE = 10

WEEKDAY_LABELS = ("日", "一", "二", "三", "四", "五", "六")

RING_STEPS = 240
RING_WIDTH = 4
RING_CENTER = (92, 138)
RING_RADIUS = 48

DOT_RADIUS = 9
ROW_HEIGHT = 34
GRID_LEFT = 206
GRID_TOP = 76


def workout_display_state(
    *,
    year: int,
    month: int,
    workout_count: int,
    streak: int,
    trained_days: set[int],
    days: int,
    goal: int,
    fetched_at: datetime,
) -> dict:
    """Comparable visible state for the workout page.

    fetched_at is deliberately excluded so an unchanged month skips the BLE
    write; the summary fields fully determine on-screen content.
    """
    del fetched_at
    return {
        "mode": "workout",
        "month": f"{year:04d}-{month:02d}",
        "workout_count": workout_count,
        "streak": streak,
        "trained_days": sorted(trained_days),
        "days": days,
        "goal": goal,
    }


def weekday_header_labels() -> list[str]:
    return list(WEEKDAY_LABELS)


def ring_progress_points(
    *,
    center: tuple[int, int],
    radius: int,
    ratio: float,
    steps: int = RING_STEPS,
) -> list[tuple[int, int]]:
    """Sampled arc points starting at 12 o'clock, clockwise; ratio clamped to [0, 1]."""
    import math

    clamped = max(0.0, min(1.0, ratio))
    points = []
    for i in range(int(steps * clamped)):
        angle = -math.pi / 2 + (2 * math.pi * i) / steps
        x = round(center[0] + radius * math.cos(angle))
        y = round(center[1] + radius * math.sin(angle))
        points.append((x, y))
    return points


def _centered_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str,
                   text_font, fill=0):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    width = right - left
    draw.text((cx - width // 2 - left, y - top), text, font=text_font, fill=fill)


def _fmt_month(year: int, month: int) -> str:
    return f"{month}月"


def _draw_ring_arc(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int,
                   ratio: float, *, width: int = RING_WIDTH):
    """Draw the arc from 12 o'clock clockwise covering `ratio` of the circle."""
    for x, y in ring_progress_points(center=center, radius=radius, ratio=ratio):
        draw.ellipse((x - width // 2, y - width // 2,
                      x + width // 2, y + width // 2), fill=0)


def build_workout_card(
    width: int,
    height: int,
    summary: dict,
    *,
    goal: int,
    now: datetime | None = None,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    if (width, height) != (400, 300):
        raise ValueError("The workout layout currently targets the 400x300 panel.")

    now = (now or datetime.now().astimezone()).astimezone()
    black = Image.new("1", (width, height), 1)
    red = Image.new("1", (width, height), 1)
    black_draw = ImageDraw.Draw(black)
    red_draw = ImageDraw.Draw(red)

    title_font = font(TITLE_FONT_SIZE)
    ring_font = font(RING_FONT_SIZE)
    stat_font = font(STAT_FONT_SIZE)
    stat_label_font = font(STAT_LABEL_FONT_SIZE)
    weekday_font = font(WEEKDAY_FONT_SIZE)
    upd_font = font(UPD_FONT_SIZE)

    year: int = summary["year"]
    month: int = summary["month"]
    days: int = summary["days"]
    workout_count: int = summary["workout_count"]
    streak: int = summary["streak"]
    trained_days: set[int] = summary["trained_days"]

    # ---- Left column: month title --------------------------------------
    black_draw.text((CANVAS_MARGIN + 4, 30), _fmt_month(year, month),
                    font=title_font, fill=0)

    # ---- Left column: progress ring ------------------------------------
    ratio = (workout_count / goal) if goal > 0 else 0.0
    # Full track in black (visible even at zero progress) ...
    _draw_ring_arc(black_draw, RING_CENTER, RING_RADIUS, 1.0)
    # ... with the completed portion overdrawn in red.
    _draw_ring_arc(red_draw, RING_CENTER, RING_RADIUS, ratio)

    ring_text = f"{workout_count}/{goal}"
    _centered_text(black_draw, RING_CENTER[0], RING_CENTER[1] - 16,
                   ring_text, ring_font)

    # ---- Left column: divider + stats (kept inside the canvas) ----------
    divider_y = 238
    black_draw.line((CANVAS_MARGIN, divider_y, 180, divider_y), fill=0, width=1)
    _centered_text(black_draw, 62, divider_y + 8, str(streak), stat_font)
    _centered_text(black_draw, 62, divider_y + 36, "连续训练", stat_label_font)
    _centered_text(black_draw, 142, divider_y + 8, str(workout_count), stat_font)
    _centered_text(black_draw, 142, divider_y + 36, "训练次数", stat_label_font)

    # ---- Right side: weekday header + calendar grid --------------------
    grid_right = width - CANVAS_MARGIN
    col_count = 7
    col_span = (grid_right - GRID_LEFT) / col_count
    col_centers = [round(GRID_LEFT + col_span * (i + 0.5)) for i in range(col_count)]

    header_y = 40
    for i, label in enumerate(WEEKDAY_LABELS):
        _centered_text(black_draw, col_centers[i], header_y, label, weekday_font)

    first_weekday, last_day = calendar.monthrange(year, month)  # Monday=0
    first_col = (first_weekday + 1) % 7  # Sunday-first column of day 1
    today = now.date()

    def cell(day: int) -> tuple[int, int]:
        index = first_col + day - 1  # Sunday-based offset
        row, col = divmod(index, col_count)
        return col_centers[col], GRID_TOP + row * ROW_HEIGHT + DOT_RADIUS

    # Outline circles for all days of the month (black plane)
    for day in range(1, days + 1):
        cx, cy = cell(day)
        black_draw.ellipse(
            (cx - DOT_RADIUS, cy - DOT_RADIUS, cx + DOT_RADIUS, cy + DOT_RADIUS),
            outline=0, width=1,
        )

    # Trained days: filled red dots drawn over the outline (red plane)
    for day in sorted(trained_days):
        if day > days:
            continue
        cx, cy = cell(day)
        red_draw.ellipse(
            (cx - DOT_RADIUS + 1, cy - DOT_RADIUS + 1,
             cx + DOT_RADIUS - 1, cy + DOT_RADIUS - 1),
            fill=0,
        )

    # Today marker ring: red when today is trained, black otherwise
    if today.year == year and today.month == month and 1 <= today.day <= days:
        cx, cy = cell(today.day)
        plane = red_draw if today.day in trained_days else black_draw
        plane.ellipse(
            (cx - DOT_RADIUS - 3, cy - DOT_RADIUS - 3,
             cx + DOT_RADIUS + 3, cy + DOT_RADIUS + 3),
            outline=0, width=1,
        )

    text_right(black_draw, width - CANVAS_MARGIN, 12,
               "UPD " + now.strftime("%H:%M"), upd_font)

    preview = Image.new("RGB", (width, height), (251, 250, 246))
    preview.paste((23, 21, 19), mask=ImageOps.invert(black.convert("L")))
    preview.paste((188, 46, 46), mask=ImageOps.invert(red.convert("L")))
    return black, red, preview
