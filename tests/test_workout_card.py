# tests/test_workout_card.py
import unittest
from datetime import date, datetime as dt

from PIL import Image, ImageOps

from workout_card import (
    build_workout_card,
    ring_progress_points,
    weekday_header_labels,
    workout_display_state,
)

NOW = dt(2026, 8, 28, 21, 5)


def _pixels(image: Image.Image) -> set[tuple[int, int]]:
    """Coordinates of dark pixels in a 1-bit image."""
    return {
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y)) == 0
    }


class WeekdayHeaderTests(unittest.TestCase):
    def test_chinese_weekday_labels(self):
        self.assertEqual(weekday_header_labels(), ["日", "一", "二", "三", "四", "五", "六"])


class RingProgressTests(unittest.TestCase):
    def test_full_circle(self):
        pts = ring_progress_points(center=(50, 50), radius=30, ratio=1.0, steps=360)
        self.assertEqual(len(pts), 360)

    def test_zero_progress(self):
        pts = ring_progress_points(center=(50, 50), radius=30, ratio=0.0, steps=360)
        self.assertEqual(len(pts), 0)

    def test_ratio_clamped(self):
        pts = ring_progress_points(center=(50, 50), radius=30, ratio=1.5, steps=360)
        self.assertEqual(len(pts), 360)


class BuildWorkoutCardTests(unittest.TestCase):
    SUMMARY = {
        "year": 2026,
        "month": 8,
        "days": 31,
        "workout_count": 9,
        "streak": 3,
        "trained_days": {1, 2, 4, 6, 12, 13, 14, 20, 28},
    }

    def build(self, summary=None, goal=20, now=NOW):
        return build_workout_card(
            400, 300, summary or self.SUMMARY,
            goal=goal, now=now,
        )

    def test_planes_are_correct_size(self):
        black, red, preview = self.build()
        self.assertEqual(black.size, (400, 300))
        self.assertEqual(red.size, (400, 300))
        self.assertEqual(preview.size, (400, 300))

    def test_trained_day_dot_drawn_on_red_plane(self):
        black, red, _ = self.build()
        black_pixels = _pixels(black)
        red_pixels = _pixels(red)
        # The red plane must have content (trained-day dots)
        self.assertTrue(red_pixels, "red plane should contain trained-day dots")
        # And it should not be a strict subset overlap-free with black dots:
        # at minimum, dot areas exist only on red plane.
        self.assertFalse(red_pixels.issubset(black_pixels))

    def test_empty_month_has_title_and_zero_counts(self):
        summary = {"year": 2026, "month": 8, "days": 31,
                   "workout_count": 0, "streak": 0, "trained_days": set()}
        black, red, preview = self.build(summary=summary)
        self.assertTrue(_pixels(black), "black plane should render titles and empty rings")

    def test_goal_reached_ring_full(self):
        black, red, _ = self.build(goal=9)
        # Should not raise; ring at 100%
        black2, red2, _ = self.build(goal=5)
        self.assertTrue(_pixels(black2) or _pixels(red2))

    def test_non_400x300_rejected(self):
        with self.assertRaises(ValueError):
            build_workout_card(
                300, 300, self.SUMMARY, goal=20, now=NOW)

    def test_layout_reasonably_dense(self):
        """Both planes carry substantial content for a normal month."""
        black, red, _ = self.build()
        black_ratio = len(_pixels(black)) / (400 * 300)
        red_ratio = len(_pixels(red)) / (400 * 300)
        self.assertGreater(black_ratio, 0.01)
        self.assertGreater(red_ratio, 0.001)

    def test_calendar_circles_have_clear_gaps(self):
        """Dot outlines must not touch: horizontal spacing > diameter."""
        from workout_card import DOT_RADIUS, GRID_LEFT, GRID_TOP, ROW_HEIGHT
        black, _, _ = self.build()
        # Horizontal scan across the center line of calendar row 1 (holds a
        # full week for most months, incl. Aug 2026): each circle outline
        # contributes 2 crossings; touching circles would merge into fewer.
        row_y = GRID_TOP + ROW_HEIGHT + DOT_RADIUS
        pixels = sorted(x for x, y in _pixels(black) if y == row_y and x >= GRID_LEFT)
        crossings = 0
        for i in range(1, len(pixels)):
            if pixels[i] - pixels[i - 1] > 1:
                crossings += 1
        self.assertGreater(crossings, 7, "expected multiple separated circle outlines")

    def test_all_content_fits_inside_canvas(self):
        black, red, _ = self.build()
        self.assertTrue(all(x < 400 and y < 300 for x, y in _pixels(black)))
        self.assertTrue(all(x < 400 and y < 300 for x, y in _pixels(red)))

    def test_august_2026_first_day_lands_on_saturday_column(self):
        """Aug 1 2026 is a Saturday: its circle must sit under the last (六) column."""
        import calendar as cal
        from workout_card import CANVAS_MARGIN, DOT_RADIUS, GRID_LEFT, GRID_TOP

        black, red, _ = self.build()
        first_weekday, _ = cal.monthrange(2026, 8)  # Monday=0 → Saturday=5
        first_col = (first_weekday + 1) % 7
        self.assertEqual(first_col, 6, "precondition: Aug 1 2026 maps to Sunday-first col 6")
        col_span = (400 - CANVAS_MARGIN - GRID_LEFT) / 7
        cx = round(GRID_LEFT + col_span * (first_col + 0.5))
        cy = GRID_TOP + DOT_RADIUS
        # Some outline pixel of day 1 must exist near the expected center.
        found = any(
            black.getpixel((x, y)) == 0
            for x in range(cx - DOT_RADIUS - 2, cx + DOT_RADIUS + 2)
            for y in range(cy - DOT_RADIUS - 2, cy + DOT_RADIUS + 2)
        )
        self.assertTrue(found, "day-1 circle not found under the Saturday column")

    def test_today_is_solid_black_dot_when_untrained(self):
        """Today (untrained) renders as a filled black dot; other untrained
        days stay hollow."""
        import calendar as cal
        from workout_card import (
            CANVAS_MARGIN, DOT_RADIUS, GRID_LEFT, GRID_TOP, ROW_HEIGHT,
        )

        year, month = 2026, 8
        today_day = 28
        first_weekday, _ = cal.monthrange(year, month)  # Monday=0
        first_col = (first_weekday + 1) % 7
        index = first_col + today_day - 1
        row, col = divmod(index, 7)
        col_span = (400 - CANVAS_MARGIN - GRID_LEFT) / 7
        cx = round(GRID_LEFT + col_span * (col + 0.5))
        cy = GRID_TOP + row * ROW_HEIGHT + DOT_RADIUS

        summary = dict(self.SUMMARY, trained_days={27})  # today untrained
        black, _, _ = self.build(summary=summary)
        # Today center filled black (solid), unlike hollow untrained day 30.
        self.assertEqual(black.getpixel((cx, cy)), 0, "today dot must be solid black")
        first_weekday_30 = first_col + 30 - 1
        row30, col30 = divmod(first_weekday_30, 7)
        cx30 = round(GRID_LEFT + col_span * (col30 + 0.5))
        cy30 = GRID_TOP + row30 * ROW_HEIGHT + DOT_RADIUS
        self.assertEqual(black.getpixel((cx30, cy30)), 1, "other untrained days stay hollow")
        # No outer marker ring beyond the dot radius.
        self.assertEqual(black.getpixel((cx + DOT_RADIUS + 2, cy)), 1)

    def test_today_is_red_dot_when_trained(self):
        import calendar as cal
        from workout_card import (
            CANVAS_MARGIN, DOT_RADIUS, GRID_LEFT, GRID_TOP, ROW_HEIGHT,
        )

        year, month = 2026, 8
        today_day = 28
        first_weekday, _ = cal.monthrange(year, month)
        first_col = (first_weekday + 1) % 7
        index = first_col + today_day - 1
        row, col = divmod(index, 7)
        col_span = (400 - CANVAS_MARGIN - GRID_LEFT) / 7
        cx = round(GRID_LEFT + col_span * (col + 0.5))
        cy = GRID_TOP + row * ROW_HEIGHT + DOT_RADIUS

        summary = dict(self.SUMMARY, trained_days={today_day})
        _, red, _ = self.build(summary=summary)
        self.assertEqual(red.getpixel((cx, cy)), 0, "today must be a red dot when trained")

    def test_full_ring_track_drawn_even_at_zero_progress(self):
        """The progress ring shows a complete black circle even with 0 workouts."""
        summary = dict(self.SUMMARY, workout_count=0)
        black, _, _ = self.build(summary=summary)
        from workout_card import RING_CENTER, RING_RADIUS
        cx, cy = RING_CENTER
        # Sample points around the full circle; most must be dark on the black plane.
        import math
        hits = total = 0
        for i in range(36):
            ang = 2 * math.pi * i / 36
            x, y = round(cx + RING_RADIUS * math.cos(ang)), round(cy + RING_RADIUS * math.sin(ang))
            total += 1
            if 0 <= x < 400 and 0 <= y < 300 and black.getpixel((x, y)) == 0:
                hits += 1
        self.assertGreater(hits / total, 0.8, "full ring track missing at zero progress")


class WorkoutDisplayStateTests(unittest.TestCase):
    def test_state_excludes_timestamp(self):
        state = workout_display_state(
            year=2026, month=8,
            workout_count=9, streak=3,
            trained_days={1, 2, 3}, days=31, goal=20,
            fetched_at=NOW,
        )
        self.assertNotIn("fetched_at", state)
        self.assertEqual(state["mode"], "workout")
        self.assertEqual(state["month"], "2026-08")
        self.assertEqual(state["workout_count"], 9)
        self.assertEqual(state["streak"], 3)
        self.assertEqual(state["goal"], 20)

    def test_state_reflects_trained_day_change(self):
        before = workout_display_state(
            year=2026, month=8,
            workout_count=1, streak=1, trained_days={1}, days=31,
            goal=20, fetched_at=NOW)
        after = workout_display_state(
            year=2026, month=8,
            workout_count=2, streak=2, trained_days={1, 2}, days=31,
            goal=20, fetched_at=NOW)
        self.assertNotEqual(before, after)
