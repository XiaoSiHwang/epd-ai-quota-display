"""Byte-exact regression: the quota_glm page (bold-large style) must never change."""

import json
from datetime import datetime as real_datetime
from pathlib import Path
from unittest.mock import patch

from epd_status import (
    build_quota_glm_card,
    pack_monochrome,
    quota_glm_display_state,
)

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
FROZEN_NOW = real_datetime(2026, 8, 31, 16, 0, 0)
CODEX_WINDOWS = [
    {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
    {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
]
GLM_WINDOWS = [
    {"label": "5 HOURS", "used": 0, "reset_at": 1_800_100_000},
    {"label": "7 DAYS", "used": 17, "reset_at": 1_800_200_000},
]
GLM_LEVEL = "pro"


class FrozenDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


def test_quota_glm_black_plane_matches_golden():
    with patch("epd_status.datetime", FrozenDatetime):
        black, _, _ = build_quota_glm_card(
            400, 300, CODEX_WINDOWS, GLM_WINDOWS, glm_level=GLM_LEVEL)
    assert pack_monochrome(black) == (GOLDEN_DIR / "quota_glm_black.bin").read_bytes()


def test_quota_glm_red_plane_matches_golden():
    with patch("epd_status.datetime", FrozenDatetime):
        _, red, _ = build_quota_glm_card(
            400, 300, CODEX_WINDOWS, GLM_WINDOWS, glm_level=GLM_LEVEL)
    assert pack_monochrome(red) == (GOLDEN_DIR / "quota_glm_red.bin").read_bytes()


def test_quota_glm_display_state_matches_golden():
    state = quota_glm_display_state(CODEX_WINDOWS, GLM_WINDOWS, GLM_LEVEL)
    assert state == json.loads((GOLDEN_DIR / "quota_glm_state.json").read_text())
