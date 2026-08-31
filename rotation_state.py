"""Rotation scheduling and per-page display state management.

Constants live here so every consumer imports one source of truth.
"""

import json
from pathlib import Path

VALID_PAGE_IDS = ("quota", "quota_glm", "calendar-agenda", "calendar-sensor", "stocks", "workout")
DISPLAY_STATE_VERSION = 2


def validate_rotation_config(config: dict):
    """Fail fast on invalid rotation configuration at startup."""
    rotation = config.get("rotation") or {}
    if not isinstance(rotation, dict):
        raise RuntimeError("The rotation configuration must be a JSON object.")
    stocks = config.get("stocks") or {}
    if not isinstance(stocks, dict):
        raise RuntimeError("The stocks configuration must be a JSON object.")

    pages = rotation.get("pages")
    if pages is None:
        pages = ["quota"]
    if not isinstance(pages, list) or not pages:
        raise RuntimeError("rotation.pages must be a non-empty JSON array of page ids.")
    for page in pages:
        if page not in VALID_PAGE_IDS:
            raise RuntimeError(f"Unsupported page id in rotation.pages: {page}")
    if len(set(pages)) != len(pages):
        raise RuntimeError("rotation.pages must not contain duplicate page ids.")
    if "stocks" in pages:
        indices = stocks.get("indices")
        if not isinstance(indices, list) or not indices:
            raise RuntimeError(
                "rotation.pages includes 'stocks' but stocks.indices is missing or empty."
            )
        for entry in indices:
            if (not isinstance(entry, dict)
                    or not entry.get("zone")
                    or not entry.get("symbol")
                    or not entry.get("name")):
                raise RuntimeError(
                    "Each stocks.indices entry requires zone, symbol and name fields."
                )

    if "workout" in pages:
        workout = config.get("workout") or {}
        if not isinstance(workout, dict):
            raise RuntimeError("The workout configuration must be a JSON object.")
        if not workout.get("api_key"):
            raise RuntimeError(
                "rotation.pages includes 'workout' but workout.api_key is missing."
            )
        if not workout.get("athlete_id"):
            # /api/v1/athletes lists friends too, so the id cannot be guessed
            # reliably; the owner must state it explicitly.
            raise RuntimeError(
                "rotation.pages includes 'workout' but workout.athlete_id is missing. "
                "Find it in the intervals.icu web UI (Settings or profile URL) or via "
                "GET /api/v1/athletes."
            )
        try:
            goal = workout.get("monthly_goal")
            if goal is not None:
                int(goal)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("workout.monthly_goal must be an integer.") from exc
        if goal is not None and int(goal) <= 0:
            raise RuntimeError("workout.monthly_goal must be a positive integer.")

    if "quota_glm" in pages:
        glm = config.get("glm") or {}
        if not isinstance(glm, dict):
            raise RuntimeError("The glm configuration must be a JSON object.")
        if not glm.get("api_key"):
            raise RuntimeError(
                "rotation.pages includes 'quota_glm' but glm.api_key is missing. "
                "Get it from the bigmodel.cn console (API Keys page)."
            )

    try:
        interval = rotation.get("interval_seconds")
        if interval is not None:
            int(interval)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("rotation.interval_seconds must be an integer of seconds.") from exc
    if interval is not None and int(interval) < 60:
        raise RuntimeError("rotation.interval_seconds must be at least 60 seconds.")


def normalize_rotation_config(config: dict) -> tuple[list[str], int | None]:
    """Return (pages, interval) with defaults applied for absent keys."""
    rotation = config.get("rotation") or {}
    if not isinstance(rotation, dict):
        raise RuntimeError("The rotation configuration must be a JSON object.")
    pages = rotation.get("pages")
    if pages is None or (isinstance(pages, list) and not pages):
        pages = ["quota"]
    raw_interval = rotation.get("interval_seconds")
    interval = int(raw_interval) if raw_interval is not None else None
    return list(pages), interval


def select_next_page(state: dict | None, pages: list[str]) -> str:
    """Return the next candidate page id.

    First run (no current_page) and removed pages both fall back to the
    first configured page.
    """
    state = state or {}
    current = state.get("current_page")
    if current not in pages:
        return pages[0]
    index = pages.index(current)
    return pages[(index + 1) % len(pages)]


def empty_display_state() -> dict:
    return {"version": DISPLAY_STATE_VERSION, "current_page": None, "pages": {}}


def load_display_state_v2(path: Path) -> dict | None:
    """Load nested display state; legacy/unknown layouts are discarded."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Ignoring unreadable display state {path}: {exc}")
        return None
    if not (isinstance(payload, dict)
            and payload.get("version") == DISPLAY_STATE_VERSION
            and isinstance(payload.get("pages"), dict)):
        print(f"Discarding legacy display-state layout at {path}; it will be rebuilt.")
        return None
    payload.setdefault("current_page", None)
    return payload


def save_display_state_v2(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)


def merge_page_state(
    state: dict,
    *,
    active_pages: list[str],
    current_page: str,
    new_entry: dict,
) -> dict:
    """Keep entries still managed by this flow, drop removed ones, set the pointer."""
    merged = {
        "version": DISPLAY_STATE_VERSION,
        "current_page": current_page,
        "pages": {
            page: entry
            for page, entry in state.get("pages", {}).items()
            if page in active_pages
        },
    }
    merged["pages"][current_page] = new_entry
    return merged
