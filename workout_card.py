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

CANVAS_MARGIN = 20
TITLE_FONT_SIZE = 34
RING_FONT_SIZE = 30
STAT_FONT_SIZE = 24
STAT_LABEL_FONT_SIZE = 11
WEEKDAY_FONT_SIZE = 12
UPD_FONT_SIZE = 10

WEEKDAY_LABELS = ("日", "一", "二", "三", "四", "五", "六")

RING_STEPS = 240
RING_WIDTH = 4


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


def _draw_ring(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int,
               overall_ratio: float, *, from_ratio: float = 0.0,
               to_ratio: float | None = None, width: int = RING_WIDTH):
    """Draw a ring arc on the given plane.

    Default draws the full circle up to overall_ratio (background track);
    pass from_ratio/to_ratio to draw a highlighted arc segment.
    """
    start = from_ratio if to_ratio is not None else 0.0
    end = overall_ratio if to_ratio is None else to_ratio
    points = ring_progress_points(center=center, radius=radius, ratio=end)
    skip = int(len(points) * start) if end > 0 else len(points)
    for x, y in points[skip:]:
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
    left_cx = 100
    black_draw.text((CANVAS_MARGIN, 34), _fmt_month(year, month),
                    font=title_font, fill=0)

    # ---- Left column: progress ring ------------------------------------
    ring_center = (left_cx, 145)
    ring_radius = 52
    ratio = (workout_count / goal) if goal > 0 else 0.0
    _draw_ring(black_draw, ring_center, ring_radius, ratio)
    # Completed portion of the ring is red for at-a-glance progress reading
    _draw_ring(red_draw, ring_center, ring_radius, ratio,
               from_ratio=0.0, to_ratio=ratio)

    ring_text = f"{workout_count}/{goal}"
    _centered_text(black_draw, ring_center[0], ring_center[1] - 20,
                   ring_text, ring_font)

    # ---- Left column: divider + stats ----------------------------------
    divider_y = 248
    black_draw.line((CANVAS_MARGIN, divider_y, 180, divider_y), fill=0, width=1)
    _centered_text(black_draw, 65, divider_y + 12, str(streak), stat_font)
    _centered_text(black_draw, 65, divider_y + 44, "连续训练", stat_label_font)
    _centered_text(black_draw, 145, divider_y + 12, str(workout_count), stat_font)
    _centered_text(black_draw, 145, divider_y + 44, "训练次数", stat_label_font)

    # ---- Right side: weekday header + calendar grid --------------------
    grid_left = 218
    grid_right = width - CANVAS_MARGIN
    col_count = 7
    col_span = (grid_right - grid_left) / col_count
    col_centers = [round(grid_left + col_span * (i + 0.5)) for i in range(col_count)]

    header_y = 40
    for i, label in enumerate(WEEKDAY_LABELS):
        _centered_text(black_draw, col_centers[i], header_y, label, weekday_font)

    first_weekday, last_day = calendar.monthrange(year, month)  # Monday=0
    dot_radius = 11
    row_height = 38
    grid_top = 78
    today = now.date()

    def cell(day: int) -> tuple[int, int]:
        index = first_weekday + day - 1  # Sunday-based offset
        row, col = divmod(index, col_count)
        return col_centers[col], grid_top + row * row_height + dot_radius

    # Outline circles for all days of the month (black plane)
    for day in range(1, days + 1):
        cx, cy = cell(day)
        black_draw.ellipse(
            (cx - dot_radius, cy - dot_radius, cx + dot_radius, cy + dot_radius),
            outline=0, width=1,
        )

    # Trained days: filled red dots drawn over the outline (red plane)
    for day in sorted(trained_days):
        if day > days:
            continue
        cx, cy = cell(day)
        red_draw.ellipse(
            (cx - dot_radius + 1, cy - dot_radius + 1,
             cx + dot_radius - 1, cy + dot_radius - 1),
            fill=0,
        )

    # Today marker: slightly thicker outline
    if today.year == year and today.month == month and 1 <= today.day <= days:
        cx, cy = cell(today.day)
        black_draw.ellipse(
            (cx - dot_radius - 2, cy - dot_radius - 2,
             cx + dot_radius + 2, cy + dot_radius + 2),
            outline=0, width=1,
        )

    text_right(black_draw, width - CANVAS_MARGIN, 12,
               "UPD " + now.strftime("%H:%M"), upd_font)

    preview = Image.new("RGB", (width, height), (251, 250, 246))
    preview.paste((23, 21, 19), mask=ImageOps.invert(black.convert("L")))
    preview.paste((188, 46, 46), mask=ImageOps.invert(red.convert("L")))
    return black, red, preview
