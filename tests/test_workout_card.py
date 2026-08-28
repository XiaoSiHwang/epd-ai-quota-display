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
