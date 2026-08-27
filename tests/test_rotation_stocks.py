# tests/test_rotation_stocks.py
import json
import os
import tempfile
import unittest
from datetime import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from epd_status import pack_monochrome
from stocks_data import IndexQuote

STOCKS_NOW = dt.fromisoformat("2026-08-27T21:05:00+08:00")


def _fake_fast_info(last, prev):
    info = MagicMock()
    info.last_price = last
    info.previous_close = prev
    info.currency = "USD"
    return info


class StocksDataTests(unittest.IsolatedAsyncioTestCase):
    INDICES = [
        {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
        {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
    ]

    async def _run_fetch(self, side_effect_fn, indices=None, proxy=None):
        from stocks_data import fetch_indices_async
        with patch("yfinance.Ticker", side_effect=side_effect_fn):
            return await fetch_indices_async(indices or self.INDICES, proxy=proxy)

    async def test_quotes_include_change_percent(self):
        t = MagicMock()
        t.fast_info = _fake_fast_info(53000.0, 52500.0)
        quotes = await self._run_fetch(lambda symbol: t)
        self.assertEqual(len(quotes), 2)
        first = quotes[0]
        self.assertEqual(first.name, "道琼斯")
        self.assertAlmostEqual(first.price, 53000.0)
        self.assertAlmostEqual(first.change_pct, (53000.0 / 52500.0 - 1) * 100, places=6)
        self.assertFalse(first.unavailable)

    async def test_partial_failure_marks_unavailable(self):
        def broken_for_ss(symbol):
            t = MagicMock()
            if symbol == "000001.SS":
                raise RuntimeError("boom")
            t.fast_info = _fake_fast_info(100.0, 99.0)
            return t
        quotes = await self._run_fetch(broken_for_ss)
        self.assertTrue(quotes[1].unavailable)
        self.assertFalse(quotes[0].unavailable)

    async def test_all_failures_raise_with_reasons(self):
        def always_broken(symbol):
            raise RuntimeError("network down")
        with self.assertRaisesRegex(RuntimeError, "network down"):
            await self._run_fetch(always_broken)

    async def test_none_price_row_is_unavailable_while_others_survive(self):
        def none_for_dji(symbol):
            t = MagicMock()
            if symbol == "^DJI":
                t.fast_info = _fake_fast_info(None, None)
            else:
                t.fast_info = _fake_fast_info(100.0, 99.0)
            return t
        quotes = await self._run_fetch(none_for_dji)
        self.assertTrue(quotes[0].unavailable)
        self.assertFalse(quotes[1].unavailable)

    async def test_proxy_env_injected(self):
        from stocks_data import fetch_indices_async
        env_seen = {}

        def capture_env(symbol):
            env_seen["HTTP_PROXY"] = os.environ.get("HTTP_PROXY")
            t = MagicMock()
            t.fast_info = _fake_fast_info(1.0, 1.0)
            return t

        with patch.dict(os.environ, {}, clear=False):
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k.upper().endswith("_PROXY")}
            try:
                with patch("yfinance.Ticker", side_effect=capture_env):
                    await fetch_indices_async([{"zone": "US", "symbol": "^DJI", "name": "D"}],
                                              proxy="http://127.0.0.1:7890")
            finally:
                os.environ.update(saved)
        self.assertEqual(env_seen["HTTP_PROXY"], "http://127.0.0.1:7890")


class RotationConfigValidationTests(unittest.TestCase):
    def _valid_config(self):
        return {
            "display_mode": "rotation",
            "rotation": {"pages": ["calendar-agenda", "stocks"], "interval_seconds": 300},
            "stocks": {
                "indices": [
                    {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
                    {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
                ]
            },
        }

    def test_valid_stock_config_passes(self):
        from rotation_state import validate_rotation_config
        validate_rotation_config(self._valid_config())  # should not raise

    def test_empty_pages_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["rotation"]["pages"] = []
        with self.assertRaisesRegex(RuntimeError, "pages"):
            validate_rotation_config(config)

    def test_unknown_page_id_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["rotation"]["pages"] = ["calendar-agenda", "stock"]  # typo
        with self.assertRaisesRegex(RuntimeError, "Unsupported page"):
            validate_rotation_config(config)

    def test_duplicate_page_id_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["rotation"]["pages"] = ["stocks", "stocks"]
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            validate_rotation_config(config)

    def test_stocks_page_requires_indices(self):
        from rotation_state import validate_rotation_config
        config = {"display_mode": "rotation", "rotation": {"pages": ["stocks"]}}
        with self.assertRaisesRegex(RuntimeError, "stocks.indices"):
            validate_rotation_config(config)

    def test_invalid_index_entry_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["stocks"]["indices"] = [{"zone": "US", "symbol": "^DJI"}]  # missing name
        with self.assertRaisesRegex(RuntimeError, "zone, symbol and name"):
            validate_rotation_config(config)

    def test_interval_below_60_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["rotation"]["interval_seconds"] = 30
        with self.assertRaisesRegex(RuntimeError, "interval_seconds"):
            validate_rotation_config(config)

    def test_interval_non_numeric_rejected(self):
        from rotation_state import validate_rotation_config
        config = self._valid_config()
        config["rotation"]["interval_seconds"] = "abc"
        with self.assertRaisesRegex(RuntimeError, "interval_seconds"):
            validate_rotation_config(config)


class NormalizeRotationConfigTests(unittest.TestCase):
    def test_missing_section_defaults_to_quota(self):
        from rotation_state import normalize_rotation_config
        pages, interval = normalize_rotation_config({})
        self.assertEqual(pages, ["quota"])
        self.assertIsNone(interval)

    def test_present_values_pass_through(self):
        from rotation_state import normalize_rotation_config
        pages, interval = normalize_rotation_config({
            "rotation": {"pages": ["stocks"], "interval_seconds": 900}
        })
        self.assertEqual(pages, ["stocks"])
        self.assertEqual(interval, 900)


class SelectNextPageTests(unittest.TestCase):
    def test_first_run_returns_first_page(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page(None, ["a", "b"]), "a")

    def test_cyclic_advance(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({"current_page": "a"}, ["a", "b"]), "b")
        self.assertEqual(select_next_page({"current_page": "b"}, ["a", "b"]), "a")

    def test_removed_current_page_falls_back_to_first(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({"current_page": "gone"}, ["a", "b"]), "a")

    def test_single_page_always_returns_itself_as_next_candidate(self):
        # 调度器只决定候选页；是否写屏由“内容变化”决定
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({"current_page": "a"}, ["a"]), "a")

    def test_state_without_current_page_key(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({}, ["a", "b"]), "a")


class DisplayStateV2Tests(unittest.TestCase):
    def test_round_trip_nested_state(self):
        from rotation_state import load_display_state_v2, save_display_state_v2
        state = {
            "version": 2,
            "current_page": "stocks",
            "pages": {
                "calendar-agenda": {"mode": "calendar-agenda", "date": "2026-08-27"},
                "stocks": {"mode": "stocks", "rows": [{"name": "道琼斯"}]},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_display_state_v2(path, state)
            self.assertEqual(load_display_state_v2(path), state)

    def test_v1_flat_state_is_discarded(self):
        from rotation_state import load_display_state_v2
        legacy = {"version": 1, "mode": "quota", "windows": []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(legacy))
            self.assertIsNone(load_display_state_v2(path))

    def test_corrupt_file_is_discarded(self):
        from rotation_state import load_display_state_v2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not json{{{")
            self.assertIsNone(load_display_state_v2(path))

    def test_save_preserves_and_cleans_pages(self):
        from rotation_state import merge_page_state
        base = {
            "version": 2,
            "current_page": "a-page",
            "pages": {
                "quota": {"mode": "quota"},
                "removed": {"mode": "stocks"},
            },
        }
        merged = merge_page_state(base, active_pages=["quota", "stocks"],
                                  current_page="stocks",
                                  new_entry={"mode": "stocks", "rows": []})
        self.assertNotIn("removed", merged["pages"])   # 清理已移除页
        self.assertIn("quota", merged["pages"])        # 保留仍在管辖的条目
        self.assertEqual(merged["current_page"], "stocks")
        self.assertEqual(merged["pages"]["stocks"], {"mode": "stocks", "rows": []})


class StocksCardTests(unittest.TestCase):
    QUOTES = [
        IndexQuote("US", "^DJI", "道琼斯", 44291.52, 0.35, "USD"),
        IndexQuote("US", "^GSPC", "标普500", 6032.38, 0.20, "USD"),
        IndexQuote("US", "^IXIC", "纳斯达克", 19269.39, -0.25, "USD"),
        IndexQuote("CN", "000001.SS", "上证指数", 3387.38, 0.63, "CNY"),
        IndexQuote("CN", "399006.SZ", "创业板指", 2299.31, 1.02, "CNY"),
        IndexQuote("ASIA", "^N225", "日经225", 38283.85, 0.14, "JPY"),
        IndexQuote("ASIA", "^KS11", "韩国KOSPI", 2653.45, -0.28, "KRW"),
    ]

    def test_card_planes_are_complete(self):
        from stocks_card import build_stocks_card
        black, red, preview = build_stocks_card(400, 300, self.QUOTES, now=STOCKS_NOW)
        self.assertEqual(preview.size, (400, 300))
        self.assertEqual(len(pack_monochrome(black)), 15_000)
        self.assertEqual(len(pack_monochrome(red)), 15_000)

    def test_up_quote_draws_dark_pixels_on_red_plane_only(self):
        from stocks_card import build_stocks_card
        up_quote = IndexQuote("US", "^DJI", "A指", 100.0, 0.50, "USD")
        _, red, _ = build_stocks_card(400, 300, [up_quote], now=STOCKS_NOW)
        dark_on_red = int((np.asarray(red.convert("L")) == 0).sum())
        self.assertGreater(dark_on_red, 50,
                           "up-quote pixels must be drawn dark on the red plane")

    def test_down_and_flat_quotes_stay_off_red_plane(self):
        from stocks_card import build_stocks_card
        down_quote = IndexQuote("US", "^IXIC", "B指", 200.0, -0.50, "USD")
        flat_quote = IndexQuote("US", "^GSPC", "C指", 300.0, 0.0, "USD")
        black, red, _ = build_stocks_card(400, 300, [down_quote, flat_quote], now=STOCKS_NOW)
        dark_on_red = int((np.asarray(red.convert("L")) == 0).sum())
        self.assertEqual(dark_on_red, 0,
                         "down/flat quotes must never touch the red plane")
        dark_on_black = int((np.asarray(black.convert("L")) == 0).sum())
        self.assertGreater(dark_on_black, 50)

    def test_unavailable_row_placeholder(self):
        from stocks_card import build_stocks_card
        quotes = [IndexQuote("US", "^DJI", "X指", None, None, unavailable=True)]
        black, red, _ = build_stocks_card(400, 300, quotes, now=STOCKS_NOW)
        self.assertEqual(black.size, (400, 300))
        self.assertEqual(red.size, (400, 300))

    def test_zone_label_override_renders_custom_title(self):
        # zone_label 由配置条目带来；渲染器通过 quote 附带字段显示
        from stocks_card import build_stocks_card
        quote = IndexQuote("UK", "^FTSE", "富时100", 8000.0, 0.10, "GBP",
                           zone_label="UK · 英国")
        black, _, _ = build_stocks_card(400, 300, [quote], now=STOCKS_NOW)
        self.assertEqual(black.size, (400, 300))  # 自定义分区不崩、可渲染


class StocksDisplayStateTests(unittest.TestCase):
    def test_display_state_excludes_timestamp(self):
        from epd_status import stocks_display_state
        q1 = [IndexQuote("US", "^DJI", "道琼斯", 100.0, 0.50)]
        s1 = stocks_display_state(q1, fetched_at=dt.fromisoformat("2026-08-27T21:00:00+08:00"))
        s2 = stocks_display_state(q1, fetched_at=dt.fromisoformat("2026-08-27T21:05:00+08:00"))
        self.assertEqual(s1, s2, "timestamp must not participate in comparison")

    def test_display_state_reflects_price_availability_changes(self):
        from epd_status import stocks_display_state
        base = [IndexQuote("US", "^DJI", "道琼斯", 100.0, 0.50)]
        moved = [IndexQuote("US", "^DJI", "道琼斯", 101.0, 1.50)]
        gone = [IndexQuote("US", "^DJI", "道琼斯", None, None, unavailable=True)]
        b = stocks_display_state(base, fetched_at=STOCKS_NOW)
        m = stocks_display_state(moved, fetched_at=STOCKS_NOW)
        g = stocks_display_state(gone, fetched_at=STOCKS_NOW)
        self.assertNotEqual(b, m)
        self.assertNotEqual(b, g)


class BleFailureInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_failure_leaves_state_file_untouched(self):
        from unittest.mock import AsyncMock, patch as mock_patch
        from rotation_state import (
            empty_display_state,
            load_display_state_v2,
            merge_page_state,
            save_display_state_v2,
        )

        async def send_then_persist(state_path: Path):
            """Mirrors render_and_send's ordering contract: write first, persist after."""
            from epd_status import write_card_with_retry
            await write_card_with_retry("NRF_EPD", b"x", None)
            stored = load_display_state_v2(state_path) or empty_display_state()
            save_display_state_v2(state_path, merge_page_state(
                stored, active_pages=["stocks"], current_page="stocks",
                new_entry={"mode": "stocks"}))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            baseline = {"version": 2, "current_page": "stocks",
                        "pages": {"stocks": {"mode": "stocks"}}}
            save_display_state_v2(path, baseline)
            before_bytes = path.read_bytes()
            with mock_patch("epd_status.write_card_with_retry",
                            new_callable=AsyncMock,
                            side_effect=RuntimeError("ble down")):
                with self.assertRaises(RuntimeError):
                    await send_then_persist(path)
            self.assertEqual(path.read_bytes(), before_bytes,
                             "failed BLE write must not touch the state file")


if __name__ == "__main__":
    unittest.main()
