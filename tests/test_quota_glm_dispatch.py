# tests/test_quota_glm_dispatch.py
"""Dispatch regression: quota_glm (single mode + rotation candidate) must
build the dual quota card, never the calendar-sensor catch-all card."""

import asyncio
import json
from unittest.mock import patch

from epd_status import main

CODEX = [
    {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
    {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
]
GLM = [
    {"label": "5 HOURS", "used": 0, "reset_at": 1_800_100_000},
    {"label": "7 DAYS", "used": 17, "reset_at": 1_800_200_000},
]


def _run_main(tmp_path, config):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    state_path = tmp_path / ".state.json"
    output_path = tmp_path / "preview.png"
    argv = ["epd_status.py", "--mode", config["display_mode"],
            "--config", str(config_path), "--state-file", str(state_path),
            "--output", str(output_path), "--dry-run"]
    with patch("epd_status.fetch_codex_quota", return_value=CODEX), \
         patch("glm_data.fetch_glm_quota",
               return_value={"level": "pro", "windows": GLM}), \
         patch("sys.argv", argv):
        asyncio.run(main())
    return output_path


def _base_config(display_mode):
    return {
        "display_mode": display_mode,
        "rotation": {"pages": ["quota_glm"]},
        "glm": {"api_key": "test-key"},
    }


class TestQuotaGlmModeDispatch:
    def test_single_mode_renders_dual_card(self, tmp_path):
        output = _run_main(tmp_path, _base_config("quota_glm"))
        assert output.exists(), "quota_glm mode must render a preview"

    def test_rotation_candidate_renders_dual_card_not_sensor(self, tmp_path):
        # The rotation candidate must resolve to quota_glm and render the
        # dual card. If dispatch fell into the calendar-sensor catch-all
        # else, fetch_sensor_reading would raise (no sensor source in the
        # tmp config) and nothing would render — so output existing proves
        # the quota_glm branch was taken.
        output = _run_main(tmp_path, _base_config("rotation"))
        assert output.exists(), (
            "rotation candidate quota_glm must render a preview; falling "
            "into the sensor catch-all would raise on missing sensor config"
        )
