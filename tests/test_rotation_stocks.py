# tests/test_rotation_stocks.py
import unittest


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


if __name__ == "__main__":
    unittest.main()
