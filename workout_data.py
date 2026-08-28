"""Fetch and cache workout activities from intervals.icu for the EPD workout page.

Data layer mirrors stocks_data.py: the fetch function is the only entry point
the renderer depends on. A local SQLite cache (.workout-cache.db) keeps the
last successful months so a failed fetch still renders (project invariant:
keep the previous frame on failure). A legacy JSON cache is imported once and
renamed aside.
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.request import ProxyHandler, Request

from epd_status import _https_urlopen

API_BASE = "https://intervals.icu"
FETCH_TIMEOUT_SECONDS = 20.0
# intervals.icu sits behind Cloudflare, which blocks requests without a
# User-Agent (error 1010); urllib sends none by default.
USER_AGENT = "epd-workout-display/1.0"


@dataclass(frozen=True)
class WorkoutActivity:
    id: str
    day: date
    type: str
    name: str
    moving_time: int
    distance: float

    def to_cache_dict(self) -> dict:
        return {
            "id": self.id,
            "day": self.day.isoformat(),
            "type": self.type,
            "name": self.name,
            "moving_time": self.moving_time,
            "distance": self.distance,
        }

    @classmethod
    def from_cache_dict(cls, payload: dict) -> WorkoutActivity | None:
        try:
            return cls(
                id=str(payload["id"]),
                day=date.fromisoformat(payload["day"]),
                type=str(payload.get("type", "")),
                name=str(payload.get("name", "")),
                moving_time=int(payload.get("moving_time", 0)),
                distance=float(payload.get("distance", 0.0)),
            )
        except (KeyError, TypeError, ValueError):
            return None


class WorkoutDataError(RuntimeError):
    """Raised when the intervals.icu API request fails entirely."""


class WorkoutCacheError(RuntimeError):
    """Raised when the local workout cache is unusable for a required read."""


def month_days(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_activities(payload: list[dict]) -> list[WorkoutActivity]:
    """Normalize the API list; entries without a usable start date are skipped."""
    activities: list[WorkoutActivity] = []
    for entry in payload or []:
        if not isinstance(entry, dict):
            continue
        raw_start = entry.get("start_date_local") or entry.get("start_date")
        if not raw_start:
            continue
        try:
            day = datetime.fromisoformat(str(raw_start)).date()
        except ValueError:
            continue
        activities.append(WorkoutActivity(
            id=str(entry.get("id", "")),
            day=day,
            type=str(entry.get("type", "")),
            name=str(entry.get("name", "")),
            moving_time=int(entry.get("moving_time") or 0),
            distance=float(entry.get("distance") or 0.0),
        ))
    return activities


def summarize_month(
    activities: list[WorkoutActivity],
    *,
    year: int,
    month: int,
    today: date,
    carry_in_streak: int = 0,
    allowed_types: tuple[str, ...] | None = None,
) -> dict:
    """Compute calendar summary for rendering.

    streak: consecutive trained natural days ending today (today itself may be
    untrained without breaking the streak; a gap at yesterday does break it).
    carry_in_streak extends the count across the month boundary when the
    streak started in the previous month.
    """
    allowed = set(allowed_types) if allowed_types else None
    first_of_month = date(year, month, 1)
    last_day = month_days(year, month)

    workout_count = 0
    trained_days: set[int] = set()
    for act in activities:
        if allowed is not None and act.type not in allowed:
            continue
        if act.day < first_of_month or act.day > today:
            continue
        workout_count += 1
        trained_days.add(act.day.day)

    streak = 0
    probe = today
    # Today untrained does not break the streak; start counting from yesterday.
    if probe.day not in trained_days:
        probe = probe.fromordinal(probe.toordinal() - 1)
    while (probe >= first_of_month and probe.day in trained_days) or probe < first_of_month:
        if probe < first_of_month:
            streak += carry_in_streak
            break
        streak += 1
        probe = probe.fromordinal(probe.toordinal() - 1)

    return {
        "year": year,
        "month": month,
        "days": last_day,
        "workout_count": workout_count,
        "streak": streak,
        "trained_days": trained_days,
    }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id          TEXT PRIMARY KEY,
    day         TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    moving_time INTEGER NOT NULL DEFAULT 0,
    distance    REAL NOT NULL DEFAULT 0.0
)
"""


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = month_days(year, month)
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _migrate_legacy_json(db_path: Path):
    """One-time import of the pre-SQLite JSON cache, then rename it aside."""
    legacy = db_path.with_suffix(".json")
    marker = legacy.with_suffix(".json.migrated")
    if not legacy.exists() or marker.exists():
        return
    try:
        payload = json.loads(legacy.read_text())
        months = payload.get("months", {}) if isinstance(payload, dict) else {}
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(_SCHEMA)
            for entries in months.values():
                for entry in entries if isinstance(entries, list) else []:
                    act = WorkoutActivity.from_cache_dict(entry)
                    if act is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?)",
                            (act.id, act.day.isoformat(), act.type, act.name,
                             act.moving_time, act.distance))
            conn.commit()
        finally:
            conn.close()
        legacy.replace(marker)
    except (OSError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Legacy workout JSON cache discarded during migration: {exc}")
        try:
            legacy.replace(marker)
        except OSError:
            pass


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    _migrate_legacy_json(db_path)
    return conn


def load_month_cache(path: Path, year: int, month: int) -> list[WorkoutActivity]:
    """Return cached activities for one month; missing/corrupt cache degrades to empty.

    Opening the database also runs the one-time legacy JSON migration, so this
    must go through _open_db even when the .db file does not exist yet.
    """
    try:
        conn = _open_db(path)
        try:
            start, end = _month_bounds(year, month)
            rows = conn.execute(
                "SELECT id, day, type, name, moving_time, distance "
                "FROM activities WHERE day BETWEEN ? AND ? ORDER BY day, moving_time",
                (start, end),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"Ignoring unreadable workout cache {path}: {exc}")
        return []
    return [
        WorkoutActivity(id, date.fromisoformat(day), type_, name,
                        moving_time, distance)
        for id, day, type_, name, moving_time, distance in rows
    ]


def save_month_cache(path: Path, year: int, month: int, activities: list[WorkoutActivity]):
    """Replace one month's activities atomically, preserving other months."""
    try:
        conn = _open_db(path)
        try:
            start, end = _month_bounds(year, month)
            with conn:
                conn.execute("DELETE FROM activities WHERE day BETWEEN ? AND ?", (start, end))
                conn.executemany(
                    "INSERT OR REPLACE INTO activities VALUES (?,?,?,?,?,?)",
                    [(act.id, act.day.isoformat(), act.type, act.name,
                      act.moving_time, act.distance) for act in activities],
                )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"Failed to write workout cache {path}: {exc}")


def merge_activities_into_cache(
    path: Path,
    year: int,
    month: int,
    existing: list[WorkoutActivity],
    fresh: list[WorkoutActivity],
) -> list[WorkoutActivity]:
    """Dedupe by id (fresh wins), sort by day, persist the merged month."""
    by_id = {act.id: act for act in existing if act.id}
    for act in fresh:
        if act.id:
            by_id[act.id] = act
        else:
            by_id[f"{act.day.isoformat()}-{act.type}-{act.moving_time}"] = act
    merged = sorted(by_id.values(), key=lambda act: (act.day, act.moving_time))
    save_month_cache(path, year, month, merged)
    return merged


def _basic_auth_header(api_key: str) -> str:
    token = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
    return f"Basic {token}"


def _api_activities_url(athlete_id: str, oldest: date, newest: date) -> str:
    return (f"{API_BASE}/api/v1/athlete/{athlete_id}/activities"
            f"?oldest={oldest.isoformat()}&newest={newest.isoformat()}&limit=200")


def _certifi_ssl_context():
    try:
        import certifi
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


def _open_with_optional_proxy(request: Request, *, proxy: str | None, timeout: float):
    """urlopen honoring an explicit proxy, keeping the certifi CA bundle.

    launchd jobs do not inherit terminal env vars, so the proxy must be
    injected explicitly (same rationale as stocks.proxy). Without a proxy,
    fall back to epd_status._https_urlopen.
    """
    if not proxy:
        return _https_urlopen(request, timeout=timeout)
    from urllib.request import HTTPSHandler, build_opener
    handlers = [ProxyHandler({"http": proxy, "https": proxy})]
    context = _certifi_ssl_context()
    if context is not None:
        handlers.append(HTTPSHandler(context=context))
    return build_opener(*handlers).open(request, timeout=timeout)


async def fetch_activities_async(
    *,
    athlete_id: str,
    api_key: str,
    oldest: date,
    newest: date,
    proxy: str | None = None,
    timeout_seconds: float = FETCH_TIMEOUT_SECONDS,
) -> list[WorkoutActivity]:
    """Fetch activities for a date range; raises WorkoutDataError on total failure."""
    loop = asyncio.get_running_loop()
    url = _api_activities_url(athlete_id, oldest, newest)

    def work():
        request = Request(url, headers={
            "Authorization": _basic_auth_header(api_key),
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        with _open_with_optional_proxy(request, proxy=proxy,
                                       timeout=timeout_seconds) as response:
            return json.loads(response.read())

    try:
        payload = await asyncio.wait_for(loop.run_in_executor(None, work), timeout=timeout_seconds + 5)
    except Exception as exc:
        raise WorkoutDataError(f"intervals.icu activities fetch failed: {exc}") from exc
    if not isinstance(payload, list):
        raise WorkoutDataError("intervals.icu returned an unexpected payload shape.")
    return parse_activities(payload)
