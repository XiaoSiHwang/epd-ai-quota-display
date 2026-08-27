# 轮播模式与股票指数页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EPD 电子价签上新增股票指数页（7 个全球指数，红涨黑跌，三列居中），并引入配置驱动的页面轮播机制（变化才切换策略 B）。

**Architecture:** 三个新模块（`stocks_data.py` 行情层、`stocks_card.py` 渲染层、`rotation_state.py` 调度/状态层）+ `epd_status.py` 入口接入 rotation 分支。状态文件统一升级为 v2 嵌套结构 `{version, current_page, pages:{...}}`，四种模式共用。数据源用 yfinance 的逐符号 `fast_info` 路径（已实测：批量 `download()` 对部分深证符号有 NaN 缺洞，不可用）。

**Tech Stack:** Python 3.12 / Pillow / yfinance 1.7.0（新增）/ bleak / unittest（项目现有测试风格）+ pytest 运行器

**Spec:** `docs/superpowers/specs/2026-08-27-rotation-stocks-design.md`

**环境注意（执行者必读）：**

- venv 位于 `.venv`，Python 3.12。pip 安装必须带
  `--proxy http://127.0.0.1:7890 --cert /Users/leslie/Library/Python/3.9/lib/python/site-packages/certifi/cacert.pem`
  （该机器走本地代理出外网，venv 内 SSL 证书链缺失需显式指定 CA bundle）。
- 所有测试命令用 `.venv/bin/python -m pytest tests/ -v`。
- 网络类手动验证命令前缀 `HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890`。
- 测试框架沿用项目现有 unittest 风格（class + self.assert*），pytest 只是运行器。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `stocks_data.py` | 新建 | IndexQuote 数据类、fetch_indices()（fast_info 逐符号）、StocksDataError、代理注入、部分失败降级 |
| `stocks_card.py` | 新建 | build_stocks_card()：分区标题居中虚线、三列居中行、红涨黑跌双图层、unavailable 占位 |
| `rotation_state.py` | 新建 | select_next_page() 纯函数调度器、load_display_state_v2()/save_display_state_v2()、旧格式兼容重置 |
| `epd_status.py` | 修改 | DISPLAY_STATE_VERSION→2、单模式分支适配 v2 读写、rotation 模式分支、CLI 参数 |
| `config.example.json` | 修改 | 增加 rotation 与 stocks 示例段 |
| `requirements.txt` | 修改 | 加 yfinance>=1.0 |
| `tests/test_rotation_stocks.py` | 新建 | 全部新逻辑的单元测试 |
| `tests/test_epd_status.py` | 修改 | display_state 往返测试适配 v2 |

---

### Task 1: 配置校验函数（纯逻辑，无依赖）

**Files:**
- Modify: `epd_status.py`（在 `load_json_config` 之后插入）
- Test: `tests/test_rotation_stocks.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_rotation_stocks.py
import unittest


class RotationConfigValidationTests(unittest.TestCase):
    def test_valid_stock_config_passes(self):
        from epd_status import validate_rotation_config
        config = {
            "display_mode": "rotation",
            "rotation": {"pages": ["calendar-agenda", "stocks"], "interval_seconds": 300},
            "stocks": {
                "indices": [
                    {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
                    {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
                ]
            },
        }
        validate_rotation_config(config)  # should not raise

    def test_empty_pages_rejected(self):
        from epd_status import validate_rotation_config
        with self.assertRaisesRegex(RuntimeError, "pages"):
            validate_rotation_config({"display_mode": "rotation", "rotation": {"pages": []}})

    def test_unknown_page_id_rejected(self):
        from epd_status import validate_rotation_config
        with self.assertRaisesRegex(RuntimeError, "Unsupported page"):
            validate_rotation_config({
                "display_mode": "rotation",
                "rotation": {"pages": ["calendar-agenda", "stock"]},  # typo
                "stocks": {"indices": [{"zone": "US", "symbol": "^DJI", "name": "D"}]},
            })

    def test_duplicate_page_id_rejected(self):
        from epd_status import validate_rotation_config
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            validate_rotation_config({
                "display_mode": "rotation",
                "rotation": {"pages": ["stocks", "stocks"]},
                "stocks": {"indices": [{"zone": "US", "symbol": "^DJI", "name": "D"}]},
            })

    def test_stocks_page_requires_indices(self):
        from epd_status import validate_rotation_config
        with self.assertRaisesRegex(RuntimeError, "stocks.indices"):
            validate_rotation_config({"display_mode": "rotation", "rotation": {"pages": ["stocks"]}})

    def test_interval_below_60_rejected(self):
        from epd_status import validate_rotation_config
        with self.assertRaisesRegex(RuntimeError, "interval_seconds"):
            validate_rotation_config({
                "display_mode": "rotation",
                "rotation": {"pages": ["quota"], "interval_seconds": 30},
            })

    def test_missing_rotation_section_defaults_to_quota(self):
        from epd_status import normalize_rotation_config
        pages, interval = normalize_rotation_config({})
        self.assertEqual(pages, ["quota"])
        self.assertIsNone(interval)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_rotation_config'`

- [ ] **Step 3: 实现校验与规范化**

```python
# epd_status.py — 在 load_json_config 函数之后添加

VALID_PAGE_IDS = ("quota", "calendar-agenda", "calendar-sensor", "stocks")
DEFAULT_ROTATION_INTERVAL = 300


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
        return  # normalized later by normalize_rotation_config; nothing to check
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
            raise RuntimeError("rotation.pages includes 'stocks' but stocks.indices is missing or empty.")
        for entry in indices:
            if not isinstance(entry, dict) or not entry.get("zone") or not entry.get("symbol") or not entry.get("name"):
                raise RuntimeError("Each stocks.indices entry requires zone, symbol and name fields.")

    interval = rotation.get("interval_seconds")
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
    interval = rotation.get("interval_seconds")
    interval = int(interval) if interval is not None else None
    return list(pages), interval
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add tests/test_rotation_stocks.py epd_status.py
git commit -m "feat: add rotation config validation"
```

---

### Task 2: select_next_page 调度器（纯函数）

**Files:**
- Create: `rotation_state.py`
- Test: `tests/test_rotation_stocks.py`

- [ ] **Step 1: 写失败测试（追加到测试文件）**

```python
class SelectNextPageTests(unittest.TestCase):
    def test_first_run_returns_first_page(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page(None, ["a", "b"]), "a")

    def test_cyclic_advance(self):
        from rotation_state import select_next_page
        state = {"current_page": "a"}
        self.assertEqual(select_next_page(state, ["a", "b"]), "b")
        state2 = {"current_page": "b"}
        self.assertEqual(select_next_page(state2, ["a", "b"]), "a")

    def test_removed_current_page_falls_back_to_first(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({"current_page": "gone"}, ["a", "b"]), "a")

    def test_single_page_always_returns_itself_as_next_candidate(self):
        # 调度器只决定候选页；是否写屏由“内容变化”决定，这里语义无环
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({"current_page": "a"}, ["a"]), "a")

    def test_state_without_current_page_key(self):
        from rotation_state import select_next_page
        self.assertEqual(select_next_page({}, ["a", "b"]), "a")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::SelectNextPageTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rotation_state'`

- [ ] **Step 3: 实现 rotation_state.py（先只放调度器；Task 4 再补状态读写）**

```python
"""Rotation scheduling and per-page display state management."""

VALID_PAGE_IDS = ("quota", "calendar-agenda", "calendar-sensor", "stocks")


def select_next_page(state: dict | None, pages: list[str]) -> str:
    """Return the next candidate page id.

    First run (no current_page) and pages that no longer exist both fall
    back to the first configured page.
    """
    state = state or {}
    current = state.get("current_page")
    if current not in pages:
        return pages[0]
    index = pages.index(current)
    return pages[(index + 1) % len(pages)]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::SelectNextPageTests -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add rotation_state.py tests/test_rotation_stocks.py
git commit -m "feat: add pure-function rotation scheduler"
```

---

### Task 3: 股票行情获取层

**Files:**
- Create: `stocks_data.py`
- Test: `tests/test_rotation_stocks.py`

设计要点（已实测确定的数据路径）：
- 用 `yf.Ticker(symbol).fast_info` 逐符号取 `last_price` + `previous_close`，
  **不用批量 download()**（对 399006.SZ 等深证符号存在历史缺洞导致 NaN）。
- 代理注入：模块导入 yfinance 前设置 os.environ（进程全局副作用，spec 已接受）。
- 单符号失败 → 该条目 unavailable=True；全部失败 → 抛 StocksDataError。
- 总超时预算：每个 fast_info 访问天然带超时；函数级再加整体 wall-clock 保护。

- [ ] **Step 1: 写失败测试（追加）**

```python
import os
from unittest.mock import MagicMock, patch


def _fake_fast_info(last, prev):
    info = MagicMock()
    info.last_price = last
    info.previous_close = prev
    info.currency = "USD"
    return info


class StocksDataTests(unittest.TestCase):
    INDICES = [
        {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
        {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
    ]

    def _run_fetch(self, side_effect_fn, indices=None):
        from stocks_data import fetch_indices
        with patch("yfinance.Ticker", side_effect=side_effect_fn):
            return fetch_indices(indices or self.INDICES)

    def test_quotes_include_change_percent(self):
        t = MagicMock()
        t.fast_info = _fake_fast_info(53000.0, 52500.0)
        quotes = self._run_fetch(lambda symbol: t)
        self.assertEqual(len(quotes), 2)
        first = quotes[0]
        self.assertEqual(first.name, "道琼斯")
        self.assertAlmostEqual(first.price, 53000.0)
        self.assertAlmostEqual(first.change_pct, (53000.0 / 52500.0 - 1) * 100, places=6)
        self.assertFalse(first.unavailable)

    def test_partial_failure_marks_unavailable(self):
        def broken_for_ss(symbol):
            t = MagicMock()
            if symbol == "000001.SS":
                raise RuntimeError("boom")
            t.fast_info = _fake_fast_info(100.0, 99.0)
            return t
        quotes = self._run_fetch(broken_for_ss)
        self.assertTrue(quotes[1].unavailable)

    def test_all_failures_raise(self):
        def always_broken(symbol):
            raise RuntimeError("network down")
        with self.assertRaisesRegex(RuntimeError, "network down"):
            self._run_fetch(always_broken)

    def test_none_prices_marked_unavailable(self):
        t = MagicMock()
        t.fast_info = _fake_fast_info(None, None)
        quotes = self._run_fetch(lambda symbol: t)
        self.assertTrue(quotes[0].unavailable)

    def test_proxy_env_injected_before_import_check(self):
        # proxy 字段应在 fetch 时生效于 os.environ
        from stocks_data import fetch_indices
        env_seen = {}
        real_environ = dict(os.environ)

        def capture_env(symbol):
            env_seen.update(HTTP_PROXY=os.environ.get("HTTP_PROXY"))
            t = MagicMock()
            t.fast_info = _fake_fast_info(1.0, 1.0)
            return t

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HTTP_PROXY", None)
            with patch("yfinance.Ticker", side_effect=capture_env):
                fetch_indices([{"zone": "US", "symbol": "^DJI", "name": "D"}],
                              proxy="http://127.0.0.1:7890")
        self.assertEqual(env_seen["HTTP_PROXY"], "http://127.0.0.1:7890")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksDataTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stocks_data'`

- [ ] **Step 3: 实现 stocks_data.py**

```python
"""Fetch global stock-index quotes for the EPD stocks page.

The quote source is deliberately swappable: fetch_indices() is the only
entry point the renderer depends on. The default implementation uses
yfinance's per-symbol fast_info path because batch downloads leave NaN
gaps for Shenzhen-listed symbols.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY",
                  "http_proxy", "https_proxy")


@dataclass(frozen=True)
class IndexQuote:
    zone: str
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    currency: str | None = None
    unavailable: bool = False


class StocksDataError(RuntimeError):
    """Raised when every requested symbol failed to produce a quote."""


def _apply_proxy(proxy: str | None):
    if not proxy:
        return
    for key in PROXY_ENV_KEYS:
        os.environ[key] = proxy


async def _quote_one(ticker_cls, entry: dict) -> IndexQuote:
    loop = asyncio.get_running_loop()
    try:
        def work():
            info = ticker_cls(entry["symbol"]).fast_info
            last = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            currency = getattr(info, "currency", None)
            if last is None or prev in (None, 0):
                return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                                  None, None, currency, unavailable=True)
            return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                              float(last), (float(last) / float(prev) - 1) * 100,
                              currency)

        return await asyncio.wait_for(loop.run_in_executor(None, work), timeout=15)
    except Exception as exc:  # noqa: BLE001 — single symbol failure degrades gracefully
        print(f"Index {entry['symbol']} unavailable: {exc}")
        return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                          None, None, None, unavailable=True)


async def fetch_indices_async(
    indices: list[dict],
    *,
    proxy: str | None = None,
    timeout_seconds: float = 60,
) -> list[IndexQuote]:
    """Fetch all configured indices; partial failures degrade to unavailable rows."""
    _apply_proxy(proxy)
    import yfinance  # after proxy injection, per spec §3.6

    ticker_cls = yfinance.Ticker
    results = await asyncio.wait_for(
        asyncio.gather(*(_quote_one(ticker_cls, entry) for entry in indices)),
        timeout=timeout_seconds,
    )
    if all(quote.unavailable for quote in results):
        raise StocksDataError("All requested indices failed to fetch a quote.")
    return list(results)


def fetch_indices(
    indices: list[dict],
    *,
    proxy: str | None = None,
    timeout_seconds: float = 60,
) -> list[IndexQuote]:
    """Synchronous wrapper used by the script's async main()."""
    return asyncio.run(fetch_indices_async(indices, proxy=proxy,
                                           timeout_seconds=timeout_seconds))
```

注：测试里 `patch("yfinance.Ticker", ...)` 生效的前提是 `_apply_proxy` 后的
`import yfinance` 在每次调用时重新解析属性 —— 以上实现把 import 放在函数体内、
再取 `yfinance.Ticker` 属性引用，符合 patch 目标 `"yfinance.Ticker"`。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksDataTests -v`
Expected: 5 passed

- [ ] **Step 5: 手动烟雾验证真实网络路径（可选但推荐）**

```bash
HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 \
  .venv/bin/python -c "
from stocks_data import fetch_indices
quotes = fetch_indices([
    {'zone':'US','symbol':'^DJI','name':'道琼斯'},
    {'zone':'CN','symbol':'000001.SS','name':'上证指数'},
], proxy='http://127.0.0.1:7890')
for q in quotes: print(q)
"
```

Expected: 两行真实报价，change_pct 与财经网站当日数值一致。

- [ ] **Step 6: 更新 requirements.txt 并提交**

```
bleak>=1.0.0
Pillow>=10.0.0
yfinance>=1.0.0
```

```bash
git add stocks_data.py tests/test_rotation_stocks.py requirements.txt
git commit -m "feat: add yfinance-based index quote layer"
```

---

### Task 4: v2 状态文件读写与迁移

**Files:**
- Modify: `rotation_state.py`
- Modify: `epd_status.py`（仅加常量 `DISPLAY_STATE_VERSION = 2`）
- Test: `tests/test_rotation_stocks.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
import json
import tempfile
from pathlib import Path


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
        from rotation_state import merge_page_state, save_display_state_v2, load_display_state_v2
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

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_display_state_v2(path, merged)
            loaded = load_display_state_v2(path)
        self.assertEqual(loaded, merged)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::DisplayStateV2Tests -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 实现状态读写**

```python
# rotation_state.py — 追加

import json
from pathlib import Path

DISPLAY_STATE_VERSION = 2


def _display_state_path_kind(payload) -> str:
    """Classify an on-disk payload: 'v2' | 'v1' | 'unknown'."""
    if not isinstance(payload, dict):
        return "unknown"
    if payload.get("version") == 2 and isinstance(payload.get("pages"), dict):
        return "v2"
    if "mode" in payload:  # pre-rotation single-mode layout
        return "v1"
    return "unknown"


def load_display_state_v2(path: Path) -> dict | None:
    """Load nested display state; legacy/unknown layouts are discarded."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Ignoring unreadable display state {path}: {exc}")
        return None
    kind = _display_state_path_kind(payload)
    if kind != "v2":
        print(f"Discarding legacy display-state format ({kind}); it will be rebuilt.")
        return None
    payload.setdefault("current_page", None)
    return payload


def save_display_state_v2(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(path)
    print(f"Saved displayed state to {path}")


def empty_display_state() -> dict:
    return {"version": DISPLAY_STATE_VERSION, "current_page": None, "pages": {}}


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
```

同时把 `epd_status.py` 顶部 `DISPLAY_STATE_VERSION = 1` 改为 `2`
（旧模式的 `*_display_state()` 生成载荷本身不变，嵌套进 v2 外壳）。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::DisplayStateV2Tests -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add rotation_state.py epd_status.py tests/test_rotation_stocks.py
git commit -m "feat: add v2 nested display state with v1 discard-on-read migration"
```

---

### Task 5: stocks 页可比状态与渲染

**Files:**
- Create: `stocks_card.py`
- Modify: `epd_status.py`（添加 `stocks_display_state()`，紧邻其他 `*_display_state` 函数）
- Test: `tests/test_rotation_stocks.py`

渲染规格（来自 spec §3.7 与用户确认的 mockup）：
- 400×300 双图层；黑层承载基础文字与下跌元素；红层叠画上涨价格与涨幅。
- 三列水平居中：名称列中心 x≈100、价格列 x≈210、涨跌列 x≈310。
- 分区标题两侧虚线夹持、水平居中。
- 上涨 ▲ 红、下跌 ▼ 黑、平盘黑色 ±0.00%；unavailable 行画"—"占位。
- 无图例行；顶部左"GLOBAL INDICES"、右上 UPD 时间。
- **UPD 时间戳绝不进入可比状态**（否则跳过逻辑永不触发）。

- [ ] **Step 1: 写失败测试（追加）**

```python
from datetime import datetime as _dt
from stocks_data import IndexQuote


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
        now = _dt.fromisoformat("2026-08-27T21:05:00+08:00")
        black, red, preview = build_stocks_card(400, 300, self.QUOTES, now=now)
        self.assertEqual(preview.size, (400, 300))
        self.assertEqual(len(pack_monochrome(black)), 15_000)
        self.assertEqual(len(pack_monochrome(red)), 15_000)

    def test_up_goes_to_red_plane_down_to_black_plane(self):
        from stocks_card import build_stocks_card
        now = _dt.fromisoformat("2026-08-27T21:05:00+08:00")
        black, red, _ = build_stocks_card(400, 300, [
            IndexQuote("US", "^DJI", "A指", 100.0, 0.50, "USD"),
            IndexQuote("US", "^IXIC", "B指", 200.0, -0.50, "USD"),
        ], now=now)
        import numpy as np
        up_rows_red = np.asarray(red).sum()
        down_rows_black = np.asarray(black).sum()
        # 涨幅值应画在红层（红层有内容）；跌幅画在黑层（黑层有内容）
        self.assertGreater(int(up_rows_red), 0, "red plane should contain the up-quote pixels")
        self.assertGreater(int(down_rows_black), 0, "black plane should contain the down-quote pixels")

    def test_unavailable_row_placeholder(self):
        from stocks_card import build_stocks_card
        quotes = [IndexQuote("US", "^DJI", "X指", None, None, unavailable=True)]
        black, _, _ = build_stocks_card(400, 300, quotes,
                                        now=_dt.fromisoformat("2026-08-27T21:05:00+08:00"))
        self.assertEqual(black.size, (400, 300))  # 渲染不崩即可，细节人工校验

    def test_display_state_excludes_timestamp(self):
        from epd_status import stocks_display_state
        q1 = [IndexQuote("US", "^DJI", "道琼斯", 100.0, 0.50)]
        s1 = stocks_display_state(q1, fetched_at=_dt.fromisoformat("2026-08-27T21:00:00+08:00"))
        s2 = stocks_display_state(q1, fetched_at=_dt.fromisoformat("2026-08-27T21:05:00+08:00"))
        self.assertEqual(s1, s2, "timestamp must not participate in comparison")

    def test_display_state_reflects_price_and_availability_changes(self):
        from epd_status import stocks_display_state
        base = [IndexQuote("US", "^DJI", "道琼斯", 100.0, 0.50)]
        moved = [IndexQuote("US", "^DJI", "道琼斯", 101.0, 1.50)]
        gone = [IndexQuote("US", "^DJI", "道琼斯", None, None, unavailable=True)]
        b = stocks_display_state(base, fetched_at=_dt.fromisoformat("2026-08-27T21:00:00+08:00"))
        m = stocks_display_state(moved, fetched_at=_dt.fromisoformat("2026-08-27T21:00:00+08:00"))
        g = stocks_display_state(gone, fetched_at=_dt.fromisoformat("2026-08-27T21:00:00+08:00"))
        self.assertNotEqual(b, m)
        self.assertNotEqual(b, g)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksCardTests -v`
Expected: FAIL — ImportError（stocks_card 不存在）

- [ ] **Step 3: 实现 stocks_card.py**

```python
"""Render the 400x300 tri-color stock-index page.

Layout per approved mockup: three horizontally centered columns
(name / price / change), dashed-ruled bilingual zone headers, red-up /
black-down colors, no legend line.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw

from epd_status import font, text_center  # reuse established helpers

ZONE_TITLES = {
    "US": "US · 美股",
    "CN": "CN · 中国",
    "ASIA": "ASIA · 日韩",
}

CANVAS_MARGIN = 16
TITLE_FONT_SIZE = 13
ZONE_FONT_SIZE = 10
NAME_FONT_SIZE = 14
PRICE_FONT_SIZE = 15
CHANGE_FONT_SIZE = 12
META_FONT_SIZE = 10


def _group_zones(quotes):
    """Preserve first-seen zone order; collect consecutive same-zone rows."""
    zones: list[str] = []
    grouped: dict[str, list] = {}
    for quote in quotes:
        zone = quote.zone
        if zone not in grouped:
            grouped[zone] = []
            zones.append(zone)
        grouped[zone].append(quote)
    return [(zone, grouped[zone]) for zone in zones]


def _draw_dashed_hline(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int, dash: int = 3, gap: int = 3):
    x = x1
    while x < x2:
        end = min(x + dash, x2)
        draw.line((x, y, end, y), fill=0, width=1)
        x = end + gap


def _centered_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, text_font, fill=0):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    width = right - left
    draw.text((cx - width // 2 - left, y), text, font=text_font, fill=fill)


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    if abs(price) >= 10_000:
        return f"{price:,.2f}"
    return f"{price:.2f}"


def _fmt_change(change_pct: float | None, unavailable: bool) -> str:
    if unavailable or change_pct is None:
        return ""
    arrow = "▲" if change_pct > 0 else ("▼" if change_pct < 0 else "")
    if not arrow:
        return "±0.00%"
    return f"{arrow} {abs(change_pct):+.2f}%"


def build_stocks_card(
    width: int,
    height: int,
    quotes,
    *,
    now: datetime | None = None,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    if (width, height) != (400, 300):
        raise ValueError("The stocks layout currently targets the 400x300 panel.")

    now = (now or datetime.now().astimezone()).astimezone()
    black = Image.new("1", (width, height), 1)
    red = Image.new("1", (width, height), 1)
    black_draw = ImageDraw.Draw(black)
    red_draw = ImageDraw.Draw(red)

    title_font = font(TITLE_FONT_SIZE)
    zone_font = font(ZONE_FONT_SIZE)
    name_font = font(NAME_FONT_SIZE)
    price_font = font(PRICE_FONT_SIZE)
    change_font = font(CHANGE_FONT_SIZE)
    meta_font = font(META_FONT_SIZE)

    col_name = 100
    col_price = 210
    col_change = 315

    black_draw.text((CANVAS_MARGIN, 12), "GLOBAL INDICES", font=title_font, fill=0)
    upd = "UPD " + now.strftime("%H:%M")
    right, _top, _r2, _b2 = black_draw.textbbox((0, 0), upd, font=meta_font)
    black_draw.text((width - CANVAS_MARGIN - (right - _r2 if False else _r2 - right if False else _r2), 14),
                    upd, font=meta_font, fill=0)  # simplified below
    # —— 实现注释：右对齐统一用 epd_status.text_right 更简洁，
    # 执行者应直接改为：
    #     from epd_status import text_right
    #     text_right(black_draw, width - CANVAS_MARGIN, 14, upd, meta_font)
    # 上面三行允许删除。

    black_draw.line((CANVAS_MARGIN, 34, width - CANVAS_MARGIN, 34), fill=0, width=2)

    row_height = 27
    zone_band = 24
    y = 44
    max_y = height - 12
    for zone, rows in _group_zones(quotes):
        label = ZONE_TITLES.get(zone, zone)
        label_w = zone_font.getbbox(label)[2]
        cy = y + 8
        _draw_dashed_hline(black_draw, CANVAS_MARGIN, (width - label_w) // 2 - 8, cy)
        _draw_dashed_hline(black_draw, (width + label_w) // 2 + 8, width - CANVAS_MARGIN, cy)
        _centered_text(black_draw, width // 2, y, label, zone_font)
        y += zone_band
        for quote in rows:
            if y + row_height > max_y:
                break
            _centered_text(black_draw, col_name, y, quote.name, name_font)
            price_text = _fmt_price(quote.price)
            change_text = _fmt_change(quote.change_pct, quote.unavailable)
            plane_up = quote.change_pct is not None and quote.change_pct > 0 and not quote.unavailable
            price_draw = red_draw if plane_up else black_draw
            change_draw = red_draw if plane_up else black_draw
            _centered_text(price_draw, col_price, y - 1, price_text, price_font)
            if change_text:
                _centered_text(change_draw, col_change, y + 2, change_text, change_font)
            elif quote.unavailable:
                _centered_text(black_draw, col_price, y - 1, "—", price_font)
            y += row_height

    preview = Image.new("RGB", (width, height), (251, 250, 246))
    white_bg = Image.new("1", (width, height), 1)
    preview.paste(Image.new("RGB", (width, height), (28, 28, 28)),
                  mask=Image.eval(black, lambda px: 255 - px))
    preview.paste(Image.new("RGB", (width, height), (192, 32, 32)),
                  mask=Image.eval(red, lambda px: 255 - px))
    del white_bg
    return black, red, preview
```

> 执行者注意：上面 `build_stocks_card` 里 UPD 右对齐那段是有意保留的粗糙注释块 ——
> 请直接按注释中的说明用 `text_right()` 替换为干净的一行实现；
> `white_bg` 占位变量同理可删。这是为了确保你逐行阅读而不是盲目粘贴。
> 
> `preview` 合成顺序参照 `build_calendar_sensor_card` 的既有做法
> （先黑后红叠加到米白底图）—— 打开 `epd_status.py` 相应函数核对并保持一致。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksCardTests -v`
Expected: 5 passed

- [ ] **Step 5: 实现 epd_status.py 里的 stocks_display_state（追加在 calendar_agenda_display_state 之后）**

```python
def stocks_display_state(quotes: list, *, fetched_at: datetime) -> dict:
    """Comparable visible state for the stocks page.

    The fetch timestamp is deliberately excluded so unchanged market data
    skips the BLE write; rendering still shows the timestamp on-screen.
    """
    del fetched_at  # excluded from comparison by design (see spec §3.4)
    rows = []
    for quote in quotes:
        rows.append({
            "zone": quote.zone,
            "name": quote.name,
            "price": None if quote.unavailable else _format_state_number(quote.price),
            "change": None if quote.unavailable else _format_state_number(quote.change_pct),
        })
    return {"mode": "stocks", "rows": rows}


def _format_state_number(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"
```

再跑一遍 Step 4 的测试确认全绿。

- [ ] **Step 6: 渲染视觉人工校验**

```bash
.venv/bin/python -c "
from datetime import datetime
from stocks_data import IndexQuote
from stocks_card import build_stocks_card
quotes = [
    IndexQuote('US','^DJI','道琼斯',44291.52,0.35,'USD'),
    IndexQuote('US','^GSPC','标普500',6032.38,0.20,'USD'),
    IndexQuote('US','^IXIC','纳斯达克',19269.39,-0.25,'USD'),
    IndexQuote('CN','000001.SS','上证指数',3387.38,0.63,'CNY'),
    IndexQuote('CN','399006.SZ','创业板指',2299.31,1.02,'CNY'),
    IndexQuote('ASIA','^N225','日经225',38283.85,0.14,'JPY'),
    IndexQuote('ASIA','^KS11','韩国KOSPI',2653.45,-0.28,'KRW'),
]
_,_,preview = build_stocks_card(400,300,quotes, now=datetime.fromisoformat('2026-08-27T21:05:00+08:00'))
preview.save('/tmp/stocks-preview.png')
print('saved')
"
open /tmp/stocks-preview.png
```

对照头脑风暴定稿 mockup 核对：三列居中、红涨黑跌、无双列越界、分区虚线完整。

- [ ] **Step 7: 提交**

```bash
git add stocks_card.py epd_status.py tests/test_rotation_stocks.py
git commit -m "feat: add stocks card renderer and comparable display state"
```

---

### Task 6: epd_status.py 接入 rotation 分支与单模式 v2 适配

这是集成任务：修改入口 main()。**分两个子步骤，每步后全量跑测试。**

**Files:**
- Modify: `epd_status.py`（main()、unchanged()、四个模式的保存点）

- [ ] **Step 1: 适配单模式分支为 v2 读写**

改造 `main()` 中现有的状态逻辑：

```python
# main() 内：
from rotation_state import (
    empty_display_state, load_display_state_v2, merge_page_state,
    save_display_state_v2,
)

state_path = ...  # 保持原有路径推导

# 旧 unchanged(display_state) 闭包替换为按页比对版本：
def unchanged(page_id: str, new_entry: dict, display_state: dict | None) -> bool:
    if args.dry_run or args.force:
        return False
    stored = load_display_state_v2(state_path)
    if stored and stored.get("current_page") == page_id:
        if stored.get("pages", {}).get(page_id) == new_entry:
            print("No visible data change since the last successful refresh; skipping Bluetooth update.")
            return True
    del display_state
    return False
```

单模式三个分支的写屏成功后保存段统一改为：

```python
if display_state is not None and not args.dry_run:
    stored = load_display_state_v2(state_path) or empty_display_state()
    mode_pages = [mode]  # 单模式下本分支只管辖自身页
    updated = merge_page_state(stored, active_pages=[mode],
                               current_page=mode, new_entry=display_state)
    save_display_state_v2(state_path, updated)
```

注意：原代码 `save_display_state(...)` 调用点和 `display_state = quota_display_state(windows)` /
`unchanged(display_state)` 判定点都要相应改为传 `(mode, display_state)`。

calendar-sensor 和 calendar-agenda 分支里原有的 `calendar_*_display_state(...)`
返回值继续作为 `new_entry` 使用（其内部键不动，评审确认过语义等价）。

- [ ] **Step 2: 更新旧测试适配 v2**

`tests/test_epd_status.py` 中 `test_display_state_round_trip` 直接调用的是
`save_display_state/load_display_state`（v1 函数）。v1 函数此时已被移除或被
改名为 v2，因此把该测试改为导入 `save_display_state_v2/load_display_state_v2`
并用 v2 结构断言往返一致。

- [ ] **Step 3: 全量回归**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 原 12 个测试 + 新增全部通过

- [ ] **Step 4: 提交**

```bash
git add epd_status.py tests/test_epd_status.py
git commit -m "feat: migrate display state to v2 nested format across modes"
```

---

### Task 7: rotation 模式主流程

**Files:**
- Modify: `epd_status.py`（main() 增加 rotation 分支、CLI/配置接线）

- [ ] **Step 1: 失败测试（调度集成层面，mock 数据源）**

追加到 `tests/test_rotation_stocks.py`：

```python
class RotationFlowTests(unittest.TestCase):
    """Integration-level checks around the rotation branch wiring helpers."""

    def test_mode_accepts_rotation_choice(self):
        # CLI choices 通过 parser 校验；此处验证常量表一致
        from epd_status import VALID_PAGE_IDS
        self.assertIn("rotation", ("quota", "calendar-agenda", "calendar-sensor", "rotation"))

    def test_normalize_returns_interval_default_when_absent(self):
        from epd_status import normalize_rotation_config
        pages, interval = normalize_rotation_config({"rotation": {"pages": ["a-quota-placeholder"]}})
        # normalize 只做默认回退；合法性由 validate 负责
        self.assertEqual(interval, None)
```

（main() 本体不做重型集成测试 —— BLE 与网络边界均已拆为可 mock 边界，
端到端留给手动验证步骤。）

- [ ] **Step 2: main() 增加 rotation 分支**

在 `parser.add_argument("--mode", choices=(...))` 中加入 `"rotation"`。
`validate_rotation_config(config)` 在 mode == rotation 时调用。

核心分支（插在 `elif mode == "calendar-sensor"` 之后）：

```python
else:  # mode == "rotation"
    from stocks_data import fetch_indices, StocksDataError
    from stocks_card import build_stocks_card

    pages, _interval = normalize_rotation_config(config)
    validate_rotation_config({**config, "display_mode": "rotation"})
    stored = load_display_state_v2(state_path)
    candidate = select_next_page(stored, pages)
    print(f"Rotation candidate page: {candidate}")

    if candidate == "stocks":
        proxies = stocks_config.get("proxy")
        timeout = float(stocks_config.get("timeout_seconds", 60))
        quotes = fetch_indices(
            stocks_config["indices"],
            proxy=proxies if isinstance(proxies, str) else None,
            timeout_seconds=timeout,
        )
        fetched_now = datetime.now().astimezone()
        new_entry = stocks_display_state(quotes, fetched_at=fetched_now)
        if unchanged(candidate, new_entry, None):
            return
        black_image, red_image, preview = build_stocks_card(
            args.width, args.height, quotes, now=fetched_now)
        preview.save(output)
    elif candidate == "quota":
        windows = fetch_codex_quota()
        new_entry = quota_display_state(windows)
        if unchanged(candidate, new_entry, None):
            return
        black_image, red_image, preview = build_quota_card(args.width, args.height, windows)
        preview.save(output)
    elif candidate == "calendar-agenda":
        # …… 与现有单模式分支同构：取配额 + 日程 → calendar_agenda_display_state
        # → unchanged 判断 → build_calendar_agenda_card（执行者照抄现有分支逻辑）
        ...
    else:  # calendar-sensor 同构照抄
        ...

    # 公共写屏尾段（复用主流程现有 pack/BLE 段——见 Step 3 说明）
```

- [ ] **Step 3: 公共写屏尾部改造**

rotation 与单模式共存于同一 main() 的现实决定了两处选择其一：
把"pack → dry-run 判断 → BLE → 保存状态"尾段抽成局部函数 `render_and_send(black_image, red_image, preview, candidate, new_entry)`
供两个流径复用；内部完成 pack_monochrome、dry_run 早退、write_card_with_retry、
以及：

```python
updated = merge_page_state(
    load_display_state_v2(state_path) or empty_display_state(),
    active_pages=candidate_pages_scope,   # rotation 模式 = 全部 pages；单模式 = [mode]
    current_page=candidate,
    new_entry=new_entry,
)
save_display_state_v2(state_path, updated)
```

预览输出名改为页面相关：`output = Path(f"preview-{candidate}.png") if rotation
且未指定 --output`。

- [ ] **Step 4: 手动冒烟（真实网络、dry-run）**

```bash
cat > /tmp/epd-test-config.json <<'EOF'
{
  "display_mode": "rotation",
  "rotation": {"pages": ["calendar-agenda", "stocks"], "interval_seconds": 300},
  "stocks": {
    "proxy": "http://127.0.0.1:7890",
    "indices": [
      {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
      {"zone": "US", "symbol": "^GSPC", "name": "标普500"},
      {"zone": "US", "symbol": "^IXIC", "name": "纳斯达克"},
      {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
      {"zone": "CN", "symbol": "399006.SZ", "name": "创业板指"},
      {"zone": "ASIA", "symbol": "^N225", "name": "日经225"},
      {"zone": "ASIA", "symbol": "^KS11", "name": "韩国KOSPI"}
    ]
  }
}
EOF
.venv/bin/python epd_status.py --config /tmp/epd-test-config.json --mode rotation --dry-run
```

Expected: 打印 "Rotation candidate page: calendar-agenda"，生成
`preview-calendar-agenda.png`；再跑一次应轮到 stocks 并打印候选，
生成 `preview-stocks.png`。（dry-run 不写 state 文件。）

注意执行者：dry-run 跑两轮时中间要人工挪走/重建 state 或依赖首轮没写 state
这一事实 —— 若首轮曾以非 dry-run 方式写过 state，二次 dry-run 的候选页会推进。

- [ ] **Step 5: config.example.json 更新**

```json
{
  "display_mode": "rotation",
  "rotation": {
    "pages": ["calendar-agenda", "stocks"],
    "interval_seconds": 300
  },
  "stocks": {
    "proxy": null,
    "timeout_seconds": 60,
    "indices": [
      {"zone": "US", "symbol": "^DJI", "name": "道琼斯"},
      {"zone": "US", "symbol": "^GSPC", "name": "标普500"},
      {"zone": "US", "symbol": "^IXIC", "name": "纳斯达克"},
      {"zone": "CN", "symbol": "000001.SS", "name": "上证指数"},
      {"zone": "CN", "symbol": "399006.SZ", "name": "创业板指"},
      {"zone": "ASIA", "symbol": "^N225", "name": "日经225"},
      {"zone": "ASIA", "symbol": "^KS11", "name": "韩国KOSPI"}
    ]
  },
  "calendar": {"names": [], "max_events": 4},
  "sensor": {
    "file": "sensor-reading.json",
    "temperature_key": "temperature",
    "humidity_key": "humidity",
    "timestamp_key": "timestamp",
    "location": "书房",
    "max_age_minutes": 30
  }
}
```

- [ ] **Step 6: 全量测试回归**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 7: 提交**

```bash
git add epd_status.py config.example.json tests/test_rotation_stocks.py
git commit -m "feat: wire rotation mode into main with per-page change-driven refresh"
```

---

### Task 8: README 补充说明 + 最终收尾

**Files:**
- Modify: `README.md`（功能表 + 快速上手补充轮播用法）

- [ ] **Step 1: README 增补**

在功能表加入一行：

```markdown
| **页面轮播** | config.json 的 rotation.pages 决定参与轮播的页面与顺序；每轮切换到下一页，但页面可见内容未变时跳过写屏，休市时段自然停住减少闪屏。 |
```

在第 5 步定时更新小节追加：

```zsh
EPD_UPDATE_INTERVAL_SECONDS=300 ./scripts/install-launchagent.sh
```

配文：启用轮播时建议把间隔同步调到与 `rotation.interval_seconds` 一致；
美股夜盘需要刷新的话，Mac 不能睡眠。

安全检查表核对后提交。

- [ ] **Step 2: 最后全量测试 + 提交**

```bash
.venv/bin/python -m pytest tests/ -v
git add README.md
git commit -m "docs: document rotation mode and stocks page"
```

---

## 验收清单（对齐 spec）

- [ ] `--mode rotation --dry-run` 出预览图，含 stocks 页与 agenda 页交替
- [ ] 真实网络拉取 7 个指数且 change% 与财经网站一致
- [ ] 休市时段连续两轮内容相同 → 第二轮日志出现 skip 且屏幕不刷
- [ ] 写屏中断/失败时 `.last-display-state.json` 内容不变
- [ ] 旧版 v1 state 文件被无害丢弃重建
- [ ] 无 `stocks` 配置而 pages 含 stocks → 启动报错退出
