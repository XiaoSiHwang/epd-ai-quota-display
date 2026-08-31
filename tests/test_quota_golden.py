"""Byte-exact regression: the quota page must never change."""

import json
from datetime import datetime as real_datetime
from pathlib import Path
from unittest.mock import patch

from epd_status import (
    build_quota_card,
    pack_monochrome,
    quota_display_state,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
FROZEN_NOW = real_datetime(2026, 8, 31, 16, 0, 0)
WINDOWS = [
    {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
    {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
]


class FrozenDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


def test_quota_black_plane_matches_golden():
    with patch("epd_status.datetime", FrozenDatetime):
        black, _, _ = build_quota_card(400, 300, WINDOWS)
    assert pack_monochrome(black) == (GOLDEN_DIR / "quota_black.bin").read_bytes()


def test_quota_red_plane_matches_golden():
    with patch("epd_status.datetime", FrozenDatetime):
        _, red, _ = build_quota_card(400, 300, WINDOWS)
    assert pack_monochrome(red) == (GOLDEN_DIR / "quota_red.bin").read_bytes()


def test_quota_display_state_matches_golden():
    state = quota_display_state(WINDOWS)
    assert state == json.loads((GOLDEN_DIR / "quota_state.json").read_text())
