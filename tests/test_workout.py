# tests/test_workout.py
import json
import tempfile
import unittest
from datetime import datetime as dt
from pathlib import Path
from unittest.mock import patch

from workout_data import (
    WorkoutActivity,
    WorkoutCacheError,
    WorkoutDataError,
    load_month_cache,
    merge_activities_into_cache,
    month_days,
    parse_activities,
    save_month_cache,
    summarize_month,
)


def _activity(day, atype="Run", moving_time=1800, distance=5000.0, name="Morning Run"):
    return {
        "id": f"i{day:08d}",
        "start_date_local": f"2026-08-{day:02d}T07:30:00",
        "type": atype,
        "name": name,
        "moving_time": moving_time,
        "distance": distance,
    }


class ParseActivitiesTests(unittest.TestCase):
    def test_parses_core_fields(self):
        acts = parse_activities([_activity(5)])
        self.assertEqual(len(acts), 1)
        act = acts[0]
        self.assertEqual(act.day, dt(2026, 8, 5).date())
        self.assertEqual(act.type, "Run")
        self.assertEqual(act.moving_time, 1800)
        self.assertEqual(act.distance, 5000.0)

    def test_missing_start_date_is_skipped(self):
        bad = {"id": "i1", "type": "Run"}
        self.assertEqual(parse_activities([bad, _activity(6)]), [None][:0] or parse_activities([_activity(6)]))
        self.assertEqual(len(parse_activities([bad, _activity(6)])), 1)

    def test_missing_optional_fields_default(self):
        acts = parse_activities([{"id": "i1", "start_date_local": "2026-08-07T10:00:00"}])
        self.assertEqual(acts[0].type, "")
        self.assertEqual(acts[0].moving_time, 0)
        self.assertEqual(acts[0].distance, 0.0)


class MonthDaysTests(unittest.TestCase):
    def test_august_2026_has_31_days(self):
        self.assertEqual(month_days(2026, 8), 31)

    def test_february_2026_has_28_days(self):
        self.assertEqual(month_days(2026, 2), 28)

    def test_leap_year_february(self):
        self.assertEqual(month_days(2028, 2), 29)


class SummarizeMonthTests(unittest.TestCase):
    def test_counts_and_streak(self):
        activities = parse_activities([
            _activity(1), _activity(2), _activity(4),
        ])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 4).date(),
        )
        self.assertEqual(summary["workout_count"], 3)
        self.assertEqual(summary["days"], 31)
        # trained {1,2,4}; today=4 but yesterday(3) untrained -> streak resets
        self.assertEqual(summary["streak"], 1)
        self.assertEqual(summary["trained_days"], {1, 2, 4})

    def test_today_without_workout_keeps_streak(self):
        activities = parse_activities([_activity(2), _activity(3)])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 4).date(),
        )
        self.assertEqual(summary["streak"], 2)

    def test_gap_yesterday_breaks_streak(self):
        activities = parse_activities([_activity(1)])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 3).date(),
        )
        self.assertEqual(summary["streak"], 0)

    def test_streak_carries_across_month_boundary(self):
        """Streak longer than current-month trained days extends into prior month
        via carry_in_streak (yesterday belongs to previous month)."""
        activities = parse_activities([_activity(1), _activity(2)])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 2).date(),
            carry_in_streak=5,
        )
        self.assertEqual(summary["streak"], 7)

    def test_type_filter(self):
        activities = parse_activities([
            _activity(1, atype="Run"), _activity(2, atype="WeightTraining"),
        ])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 2).date(),
            allowed_types=("Run",),
        )
        self.assertEqual(summary["workout_count"], 1)
        self.assertEqual(summary["trained_days"], {1})

    def test_future_days_beyond_today_not_trained(self):
        activities = parse_activities([_activity(20)])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 4).date(),
        )
        # Activity dated in the future should not count (defensive)
        self.assertEqual(summary["workout_count"], 0)
        self.assertEqual(summary["trained_days"], set())

    def test_streak_anchored_to_today_not_month_end(self):
        """Days 28,29,30 trained, today is 30th: streak = 3."""
        activities = parse_activities([
            _activity(28), _activity(29), _activity(30),
        ])
        summary = summarize_month(
            activities, year=2026, month=8,
            today=dt(2026, 8, 30).date(),
        )
        self.assertEqual(summary["streak"], 3)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmpdir.name) / ".workout-cache.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_cache_returns_empty(self):
        self.assertEqual(load_month_cache(self.cache_path, 2026, 8), [])

    def test_corrupt_cache_returns_empty(self):
        self.cache_path.write_text("not json at all")
        self.assertEqual(load_month_cache(self.cache_path, 2026, 8), [])

    def test_wrong_version_cache_returns_empty(self):
        self.cache_path.write_text(json.dumps({"version": 999, "months": {}}))
        self.assertEqual(load_month_cache(self.cache_path, 2026, 8), [])

    def test_round_trip(self):
        activities = parse_activities([_activity(3), _activity(4)])
        save_month_cache(self.cache_path, 2026, 8, activities)
        loaded = load_month_cache(self.cache_path, 2026, 8)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].day, dt(2026, 8, 3).date())
        self.assertEqual(loaded[0].type, "Run")

    def test_save_preserves_other_months(self):
        save_month_cache(self.cache_path, 2026, 7, parse_activities([_activity(1)]))
        save_month_cache(self.cache_path, 2026, 8, parse_activities([_activity(2)]))
        self.assertEqual(len(load_month_cache(self.cache_path, 2026, 7)), 1)
        self.assertEqual(len(load_month_cache(self.cache_path, 2026, 8)), 1)

    def test_atomic_write(self):
        save_month_cache(self.cache_path, 2026, 8, parse_activities([_activity(1)]))
        self.assertFalse(self.cache_path.with_suffix(".json.tmp").exists())
        self.assertTrue(self.cache_path.exists())


class MergeCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.tmpdir.name) / ".workout-cache.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_merge_dedupes_by_id(self):
        existing = parse_activities([_activity(1), _activity(2)])
        fresh = parse_activities([_activity(2), _activity(3)])
        merged = merge_activities_into_cache(
            self.cache_path, 2026, 8, existing, fresh)
        ids = [a.id for a in merged]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(ids), len(set(ids)))

    def test_merge_updates_existing_entry(self):
        existing = parse_activities([_activity(1, name="Old Name")])
        fresh = parse_activities([_activity(1, name="New Name")])
        merged = merge_activities_into_cache(
            self.cache_path, 2026, 8, existing, fresh)
        self.assertEqual(merged[0].name, "New Name")


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_builds_request_and_parses(self):
        from workout_data import fetch_activities_async

        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            body = json.dumps([_activity(5), _activity(6)]).encode()
            return _FakeResponse(body)

        with patch("workout_data._https_urlopen", side_effect=fake_urlopen):
            acts = await fetch_activities_async(
                athlete_id="i113469",
                api_key="k",
                oldest=dt(2026, 8, 1).date(),
                newest=dt(2026, 8, 28).date(),
            )
        self.assertIn("oldest=2026-08-01", captured["url"])
        self.assertIn("newest=2026-08-28", captured["url"])
        self.assertIn("/athlete/i113469/activities", captured["url"])
        self.assertTrue(captured["auth"].startswith("Basic "))
        self.assertEqual(len(acts), 2)

    async def test_fetch_failure_raises(self):
        from workout_data import fetch_activities_async

        def fake_urlopen(request, timeout):
            raise OSError("network down")

        with patch("workout_data._https_urlopen", side_effect=fake_urlopen):
            with self.assertRaises(WorkoutDataError):
                await fetch_activities_async(
                    athlete_id="i1", api_key="k",
                    oldest=dt(2026, 8, 1).date(),
                    newest=dt(2026, 8, 28).date(),
                )

    async def test_fetch_athletes_discovers_id(self):
        from workout_data import fetch_athlete_id_async

        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            body = json.dumps([{"id": "i113469", "name": "Eddie"}]).encode()
            return _FakeResponse(body)

        with patch("workout_data._https_urlopen", side_effect=fake_urlopen):
            athlete_id = await fetch_athlete_id_async(api_key="k")
        self.assertEqual(athlete_id, "i113469")
        self.assertIn("/api/v1/athletes", captured["url"])

    async def test_fetch_athletes_empty_raises(self):
        from workout_data import fetch_athlete_id_async

        def fake_urlopen(request, timeout):
            return _FakeResponse(b"[]")

        with patch("workout_data._https_urlopen", side_effect=fake_urlopen):
            with self.assertRaises(WorkoutDataError):
                await fetch_athlete_id_async(api_key="k")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class CacheErrorTests(unittest.TestCase):
    def test_cache_error_is_runtime_error(self):
        self.assertTrue(issubclass(WorkoutCacheError, RuntimeError))
        self.assertTrue(issubclass(WorkoutDataError, RuntimeError))
