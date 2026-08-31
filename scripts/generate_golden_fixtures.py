"""Generate golden fixtures for build_quota_card / quota_display_state.

Run BEFORE refactoring build_quota_card. The clock is frozen because the
black plane bakes datetime.now() into the UPDATED line.
"""

import json
import sys
from datetime import datetime as real_datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FROZEN_NOW = real_datetime(2026, 8, 31, 16, 0, 0)
WINDOWS = [
    {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
    {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
]


class FrozenDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW


def main():
    from epd_status import pack_monochrome, quota_display_state

    golden_dir = Path(__file__).resolve().parent.parent / "tests" / "golden"
    golden_dir.mkdir(parents=True, exist_ok=True)

    with patch("epd_status.datetime", FrozenDatetime):
        from epd_status import build_quota_card

        black, red, _ = build_quota_card(400, 300, WINDOWS)
        state = quota_display_state(WINDOWS)

    (golden_dir / "quota_black.bin").write_bytes(pack_monochrome(black))
    (golden_dir / "quota_red.bin").write_bytes(pack_monochrome(red))
    (golden_dir / "quota_state.json").write_text(json.dumps(state, indent=2))
    print(f"Golden fixtures written to {golden_dir}")


if __name__ == "__main__":
    main()
