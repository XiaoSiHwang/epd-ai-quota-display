# 轮播模式与股票指数页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 EPD 电子价签上新增股票指数页（7 个全球指数，红涨黑跌，三列居中），并引入配置驱动的页面轮播机制（变化才切换策略 B）。

**Architecture:** 三个新模块（`stocks_data.py` 行情层、`stocks_card.py` 渲染层、`rotation_state.py` 调度/状态层）+ `epd_status.py` 入口接入 rotation 分支。状态文件统一升级为 v2 嵌套结构 `{version, current_page, pages:{...}}`，四种模式共用；v1 读写函数删除。数据源用 yfinance 的逐符号 `fast_info` 路径（已实测：批量 `download()` 对部分深证符号有 NaN 缺洞，不可用）。行情获取只有 async 入口 `await fetch_indices_async(...)` —— `main()` 是 async 函数，禁止 asyncio.run 包装。

**Tech Stack:** Python 3.12 / Pillow / yfinance 1.7.0（新增）/ bleak / numpy / unittest 风格测试 + pytest 运行器

**Spec:** `docs/superpowers/specs/2026-08-27-rotation-stocks-design.md`

**环境注意（执行者必读）：**

- venv 位于 `.venv`，Python 3.12。pip 安装必须带
  `--proxy http://127.0.0.1:7890 --cert /Users/leslie/Library/Python/3.9/lib/python/site-packages/certifi/cacert.pem`
  （该机器走本地代理出外网，venv 内 SSL 证书链缺失需显式指定 CA bundle）。
  yfinance 已装好（1.7.0），requirements.txt 更新见 Task 3。
- 所有测试命令用 `.venv/bin/python -m pytest tests/ -v`。
- 网络类手动验证命令前缀 `HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890`。
- 测试框架沿用项目现有 unittest 风格（class + self.assert*），pytest 只是运行器。
- **测试文件组织**：本计划所有新代码的测试都追加到同一个文件
  `tests/test_rotation_stocks.py`；每个 Task 的 import 放在该 Task 指示的位置，
  最终整个文件的 import 全部集中在文件顶部（Task 8 Step 3 会整理一次）。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `rotation_state.py` | 新建 | 常量源（VALID_PAGE_IDS/DISPLAY_STATE_VERSION）、select_next_page 纯函数、v2 状态读写、merge_page_state |
| `stocks_data.py` | 新建 | IndexQuote 数据类、fetch_indices_async()（fast_info 逐符号）、StocksDataError、代理注入、部分失败降级 |
| `stocks_card.py` | 新建 | build_stocks_card()：分区标题居中虚线（支持 zone_label 覆盖）、三列居中行、红涨黑跌双图层、unavailable 占位 |
| `epd_status.py` | 修改 | 删除 v1 load/save_display_state；四个 display_state 载荷不变但由 v2 外壳承载；rotation 分支；stock page 状态函数 |
| `config.example.json` | 修改 | 增加 rotation 与 stocks 示例段 |
| `requirements.txt` | 修改 | 加 yfinance、numpy |
| `tests/test_rotation_stocks.py` | 新建 | 全部新逻辑单元测试 |
| `tests/test_epd_status.py` | 修改 | import 与往返测试适配 v2 |
| `README.md` | 修改 | 轮播功能说明 |

依赖方向：`epd_status.py → rotation_state.py / stocks_data.py / stocks_card.py`；
`stocks_card.py → stocks_data.py`（类型）与 `epd_status.py`（font/text helpers，
复用既有的 font()/text_right()/text_center()）。

---

### Task 1: rotation_state.py — 常量 + 配置校验 + 调度器

**Files:**
- Create: `rotation_state.py`
- Test: `tests/test_rotation_stocks.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rotation_state'`

- [ ] **Step 3: 实现 rotation_state.py**

```python
"""Rotation scheduling and per-page display state management.

Constants live here so every consumer imports one source of truth.
"""

import json
from pathlib import Path

VALID_PAGE_IDS = ("quota", "calendar-agenda", "calendar-sensor", "stocks")
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add rotation_state.py tests/test_rotation_stocks.py
git commit -m "feat: add rotation config validation and pure scheduler"
```

---

### Task 2: 股票行情获取层

**Files:**
- Create: `stocks_data.py`
- Modify: `requirements.txt`
- Test: `tests/test_rotation_stocks.py`

设计要点（已实测确定的数据路径）：
- 用 `yf.Ticker(symbol).fast_info` 逐符号取 `last_price` + `previous_close`，
  **不用批量 download()**（对 399006.SZ 等深证符号存在历史缺洞导致 NaN）。
- 并发取数：`asyncio.gather` + executor，单符号超时 15s。
- 代理注入：在首次 import yfinance 前设置 os.environ（进程全局副作用，spec 已接受；
  该机器所有外部流量本就走同一代理）。
- 单符号失败 → 该条目 unavailable=True；全部失败 → 抛 StocksDataError。
- **只提供 async 入口**：main() 是 async 函数，直接 await；
  不提供 asyncio.run 包装（会撞 running loop）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_rotation_stocks.py`：

```python
# 文件顶部 import 区添加：
#   import os
#   from unittest.mock import MagicMock, patch

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

    async def test_all_failures_raise(self):
        def always_broken(symbol):
            raise RuntimeError("network down")
        with self.assertRaisesRegex(RuntimeError, "network down"):
            await self._run_fetch(always_broken)

    async def test_none_prices_marked_unavailable(self):
        t = MagicMock()
        t.fast_info = _fake_fast_info(None, None)
        quotes = await self._run_fetch(lambda symbol: t)
        self.assertTrue(quotes[0].unavailable)

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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksDataTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stocks_data'`

- [ ] **Step 3: 实现 stocks_data.py**

```python
"""Fetch global stock-index quotes for the EPD stocks page.

The quote source is deliberately swappable: fetch_indices_async() is the only
entry point the renderer depends on. The default implementation uses
yfinance's per-symbol fast_info path because batch downloads leave NaN gaps
for Shenzhen-listed symbols.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


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

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, work), timeout=15)
    except Exception as exc:
        print(f"Index {entry['symbol']} unavailable: {exc}")
        return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                          None, None, None, unavailable=True)


async def fetch_indices_async(
    indices: list[dict],
    *,
    proxy: str | None = None,
    timeout_seconds: float = 60,
) -> list[IndexQuote]:
    """Fetch all configured indices concurrently; partial failures degrade to unavailable rows."""
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
```

测试里 `patch("yfinance.Ticker")` 能生效的原因：import 写在函数体内且每次调用
解析属性引用，patch 替换的是 yfinance 模块对象的 Ticker 属性。

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksDataTests -v`
Expected: 5 passed

- [ ] **Step 5: 手动烟雾验证真实网络路径**

```bash
HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 \
  .venv/bin/python -c "
import asyncio
from stocks_data import fetch_indices_async
quotes = asyncio.run(fetch_indices_async([
    {'zone':'US','symbol':'^DJI','name':'道琼斯'},
    {'zone':'CN','symbol':'000001.SS','name':'上证指数'},
], proxy='http://127.0.0.1:7890'))
for q in quotes: print(q)
"
```

Expected: 两行真实报价，change_pct 与财经网站当日数值一致。

- [ ] **Step 6: requirements.txt 追加两行后提交**

```
bleak>=1.0.0
Pillow>=10.0.0
yfinance>=1.0.0
numpy>=1.26
```

```bash
git add stocks_data.py tests/test_rotation_stocks.py requirements.txt
git commit -m "feat: add yfinance-based index quote layer"
```

---

### Task 3: v2 状态文件读写与迁移

**Files:**
- Modify: `rotation_state.py`
- Test: `tests/test_rotation_stocks.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_rotation_stocks.py`：

```python
# 文件顶部 import 区添加：
#   import json
#   import tempfile
#   from pathlib import Path


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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::DisplayStateV2Tests -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 实现状态读写（追加到 rotation_state.py）**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::DisplayStateV2Tests -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add rotation_state.py tests/test_rotation_stocks.py
git commit -m "feat: add v2 nested display state with legacy discard-on-read"
```

---

### Task 4: epd_status.py 接入 v2 —— 单模式分支改造

此任务把现有 main() 的状态逻辑整体切到 v2。**改完后三种单模式行为必须与现在
完全一致（跳过语义不变），全量旧测试通过。**

**Files:**
- Modify: `epd_status.py`
- Test: `tests/test_epd_status.py`

关键改动清单（对照 epd_status.py 当前实现）：

1. **删除 v1 函数** `load_display_state()`（约 L545）和 `save_display_state()`（约 L556），
   以及顶部 `DISPLAY_STATE_VERSION = 1` 常量（L32）—— 版本常量移到
   rotation_state.py 统一管理。
2. **删除主尾部旧保存点**：main() 结尾的
   `if display_state is not None: save_display_state(state_path, display_state)`
   （约 L1266-L1268）整段移除 —— 保存统一走新的 merge/save 模式，防止扁平载荷
   覆写嵌套状态导致 skip 机制永久失效。
3. 各 `*_display_state()` 构造函数（quota/calendar_sensor/calendar_agenda）
   保持原样返回载荷字典（它们的键就是 v2 外壳内每页的条目内容）。

- [ ] **Step 1: 先改 tests/test_epd_status.py 的导入**

从 import 块中删掉 `load_display_state` 和 `save_display_state` 两项，
改为在文件头补上：

```python
from rotation_state import (
    empty_display_state,
    load_display_state_v2,
    merge_page_state,
    save_display_state_v2,
)
```

并把 `test_display_state_round_trip` 整个替换为：

```python
    def test_display_state_round_trip(self):
        payload = quota_display_state([
            {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
        ])
        wrapped = merge_page_state(empty_display_state(),
                                   active_pages=["quota"],
                                   current_page="quota",
                                   new_entry=payload)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_display_state_v2(path, wrapped)
            loaded = load_display_state_v2(path)
        self.assertEqual(loaded, wrapped)
```

其余三个 quota/calendar 状态构造测试不动。

- [ ] **Step 2: 改造 main() 的状态读取与比对**

main() 内原 `unchanged(state)` 闭包替换为按页比对版本：

```python
    from rotation_state import (
        empty_display_state, load_display_state_v2, merge_page_state, save_display_state_v2,
    )
    from rotation_state import DISPLAY_STATE_VERSION as STATE_VERSION  # noqa: F401

    # 原: display_state = None（变量保留，含义变为“当前页的可比载荷”）
    def unchanged(page_id: str, new_entry: dict) -> bool:
        if args.dry_run or args.force:
            return False
        stored = load_display_state_v2(state_path)
        if (stored
                and stored.get("current_page") == page_id
                and stored.get("pages", {}).get(page_id) == new_entry):
            print("No visible data change since the last successful refresh; skipping Bluetooth update.")
            return True
        return False
```

各单模式分支的判定调用相应从 `unchanged(display_state)` 改为
`unchanged(mode, display_state)`：

- quota 分支：`if unchanged(mode, quota_display_state(windows)): return`（先赋值 display_state 再判定，顺序不变）
- calendar-agenda 分支：同构替换
- calendar-sensor 分支：同构替换

- [ ] **Step 3: 替换保存点**

main() 尾部（原 L1266 处）改为：

```python
    if display_state is not None and not args.dry_run:
        stored = load_display_state_v2(state_path) or empty_display_state()
        updated = merge_page_state(stored, active_pages=[mode],
                                   current_page=mode, new_entry=display_state)
        save_display_state_v2(state_path, updated)
```

注意位置：这段必须在 `write_card_with_retry(...)` 成功之后执行（保持原尾部的
先后顺序）；`fixed_test` / `calendar_test` / `device_calendar_temperature` 早退
路径不携带 display_state（它们本来就是 None 或提前 return），不受影响。

- [ ] **Step 4: 全量回归**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 原 15 个测试 + 新增任务测试全部通过

再手动冒烟一次旧行为未破坏：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 \
  .venv/bin/python epd_status.py --dry-run && .venv/bin/python --version
```

Expected: 正常生成 test-card.png（网络可达时含真实配额数据；不可达则报错退出属正常）。

- [ ] **Step 5: 提交**

```bash
git add epd_status.py tests/test_epd_status.py
git commit -m "feat: migrate single-mode flows to v2 nested display state"
```

---

### Task 5: 股票页渲染 + 可比状态

**Files:**
- Create: `stocks_card.py`
- Modify: `epd_status.py`（新增 `stocks_display_state()`，紧邻其他 `*_display_state`）
- Test: `tests/test_rotation_stocks.py`

渲染规格（spec §3.7 + 用户定稿 mockup）：
- 400×300 双图层；黑层承载基础文字与下跌元素；红层叠画上涨价格与涨幅。
- 三列水平居中：名称列中心 x≈100、价格列 x≈200、涨跌列 x≈310。
- 分区标题两侧虚线夹持、水平居中；条目可选 `zone_label` 字段覆盖分区标题。
- 上涨 ▲ 红、下跌 ▼ 黑、平盘黑色 ±0.00%；unavailable 行画"—"占位。
- 无图例行；顶部左"GLOBAL INDICES"、右上 UPD 时间。
- **UPD 时间戳绝不进入可比状态**（否则跳过逻辑永不触发）。
- preview 合成用项目既有模式：
  `preview.paste((23, 21, 19), mask=ImageOps.invert(black.convert("L")))`、
  红 `(188, 46, 46)`（与 build_quota_card/build_calendar_*_card 一致）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_rotation_stocks.py`：

```python
# 文件顶部 import 区添加：
#   import numpy as np
#   from datetime import datetime as dt
#   from unittest.mock import patch
# from epd_status import pack_monochrome  (已在别的区块? 统一放此处)
# from stocks_data import IndexQuote      (Task 2 已加则不重复)

STOCKS_NOW = dt.fromisoformat("2026-08-27T21:05:00+08:00")


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
        # zone_label 由配置条目带来；渲染器通过 quotes 附带字段显示
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
```

注：`IndexQuote` 需要 `zone_label` 可选字段 —— 计入 Task 2 的实现调整，
此时回头补一行并在该 Task 测试确认。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py::StocksCardTests tests/test_rotation_stocks.py::StocksDisplayStateTests -v`
Expected: FAIL — ImportError（stocks_card 不存在 / zone_label 字段不存在）

- [ ] **Step 3: 给 IndexQuote 补 zone_label 字段**

`stocks_data.py` 的 dataclass 增加：

```python
@dataclass(frozen=True)
class IndexQuote:
    zone: str
    symbol: str
    name: str
    price: float | None
    change_pct: float | None
    currency: str | None = None
    unavailable: bool = False
    zone_label: str | None = None     # optional per-entry partition title override
```

同时在 `_quote_one` 里把 `entry.get("zone_label")` 透传到所有 IndexQuote 构造处
（成功与 unavailable 两路都要带），避免覆盖值丢失。

- [ ] **Step 4: 实现 stocks_card.py**

```python
"""Render the 400x300 tri-color stock-index page.

Layout per approved mockup: three horizontally centered columns
(name / price / change), dashed-ruled bilingual zone headers, red-up /
black-down colors, no legend line.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageOps

from epd_status import font, text_right

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
    """Preserve first-seen zone order; collect rows per zone."""
    zones: list[str] = []
    grouped: dict[str, list] = {}
    labels: dict[str, str] = {}
    for quote in quotes:
        zone = quote.zone
        if zone not in grouped:
            grouped[zone] = []
            zones.append(zone)
            labels[zone] = quote.zone_label or ZONE_TITLES.get(zone, zone)
        grouped[zone].append(quote)
    return [(zone, labels[zone], grouped[zone]) for zone in zones]


def _draw_dashed_hline(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int,
                       dash: int = 3, gap: int = 3):
    x = x1
    while x < x2:
        end = min(x + dash, x2)
        draw.line((x, y, end, y), fill=0, width=1)
        x = end + gap


def _centered_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str,
                   text_font, fill=0):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    width = right - left
    draw.text((cx - width // 2 - left, y), text, font=text_font, fill=fill)


def _fmt_price(price: float | None) -> str:
    if price is None:
        return "—"
    return f"{price:,.2f}"


def _fmt_change(change_pct: float | None) -> str | None:
    if change_pct is None:
        return None
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
    col_price = 200
    col_change = 310

    black_draw.text((CANVAS_MARGIN, 12), "GLOBAL INDICES", font=title_font, fill=0)
    text_right(black_draw, width - CANVAS_MARGIN, 14,
               "UPD " + now.strftime("%H:%M"), meta_font)
    black_draw.line((CANVAS_MARGIN, 34, width - CANVAS_MARGIN, 34), fill=0, width=2)

    row_height = 27
    zone_band = 24
    max_y = height - 12
    y = 44
    for zone, label, rows in _group_zones(quotes):
        label_width = zone_font.getbbox(label)[2]
        dashed_y = y + zone_band // 2 - 4
        _draw_dashed_hline(black_draw, CANVAS_MARGIN, width // 2 - label_width // 2 - 8, dashed_y)
        _draw_dashed_hline(black_draw, width // 2 + label_width // 2 + 8, width - CANVAS_MARGIN, dashed_y)
        _centered_text(black_draw, width // 2, y, label, zone_font)
        y += zone_band
        for quote in rows:
            if y + row_height > max_y:
                break
            _centered_text(black_draw, col_name, y, quote.name, name_font)
            if quote.unavailable:
                _centered_text(black_draw, col_price, y - 1, "—", price_font)
                _centered_text(black_draw, col_change, y + 2, "unavailable", change_font)
            else:
                going_up = quote.change_pct is not None and quote.change_pct > 0
                price_plane = red_draw if going_up else black_draw
                _centered_text(price_plane, col_price, y - 1,
                               _fmt_price(quote.price), price_font)
                change_text = _fmt_change(quote.change_pct)
                if change_text:
                    _centered_text(price_plane, col_change, y + 2, change_text, change_font)
            y += row_height

    preview = Image.new("RGB", (width, height), (251, 250, 246))
    preview.paste((23, 21, 19), mask=ImageOps.invert(black.convert("L")))
    preview.paste((188, 46, 46), mask=ImageOps.invert(red.convert("L")))
    return black, red, preview
```

- [ ] **Step 5: 实现 epd_status.py 里的 stocks_display_state（追加在 calendar_agenda_display_state 之后）**

```python
def stocks_display_state(quotes: list, *, fetched_at: datetime) -> dict:
    """Comparable visible state for the stocks page.

    The fetch timestamp is deliberately excluded so unchanged market data
    skips the BLE write; rendering still shows the timestamp on-screen
    (spec §3.4: UPD reflects the last write's fetch time).
    """
    del fetched_at
    rows = []
    for quote in quotes:
        rows.append({
            "zone": quote.zone,
            "zone_label": quote.zone_label,
            "name": quote.name,
            "price": _format_state_number(quote.price),
            "change": _format_state_number(quote.change_pct),
            "unavailable": bool(quote.unavailable),
        })
    return {"mode": "stocks", "rows": rows}


def _format_state_number(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"
```

- [ ] **Step 6: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_rotation_stocks.py -v`
Expected: 本阶段全部通过（含此前任务的）

- [ ] **Step 7: 渲染视觉人工校验**

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

对照头脑风暴定稿 mockup 核对：三列居中、红涨黑跌、分区虚线完整、无越界。
微调行距/字号允许，但每次改后重跑测试。

- [ ] **Step 8: 提交**

```bash
git add stocks_card.py stocks_data.py epd_status.py tests/test_rotation_stocks.py
git commit -m "feat: add stocks card renderer with comparable display state"
```

---

### Task 6: rotation 模式接入 main()

**Files:**
- Modify: `epd_status.py`（CLI choices、运行时 gate、rotation 分支、公共渲染发送路径重构）
- Test: 手动冒烟为主（调度纯函数已测；集成安全网 = 全量回归 + 冒烟步骤）

前置事实（对照现实现）：
- argparse choices 在 L1086：`parser.add_argument("--mode", choices=("quota", "calendar-agenda", "calendar-sensor"), ...)`；
- **运行时 gate 在 L1148**：`if mode not in ("quota", "calendar-agenda", "calendar-sensor"): raise ...` —— 两处都必须加 `"rotation"`，漏掉 gate 则一切 rotation 调用在分支前就崩；
- `sensor_config`/`calendar_config` 提取防护在 L1131–1136，stocks 同样要加；
- mode 解析链：`mode = configured(args.mode, "EPD_DISPLAY_MODE", config.get("display_mode"), "quota")`，rotation 不需要动这行，只要 gate 元组扩容。

- [ ] **Step 1: CLI 与 gate 扩容 + stocks 配置提取**

```python
# argparse:
    parser.add_argument("--mode", choices=("quota", "calendar-agenda", "calendar-sensor", "rotation"), help="display layout")

# gate:
    mode = configured(args.mode, "EPD_DISPLAY_MODE", config.get("display_mode"), "quota")
    if mode not in ("quota", "calendar-agenda", "calendar-sensor", "rotation"):
        raise RuntimeError(f"Unsupported display mode: {mode}")

# 配置提取（紧跟 sensor/calendar_config 后）：
    stocks_config = config.get("stocks") or {}
    if not isinstance(stocks_config, dict):
        raise RuntimeError("The stocks configuration must be a JSON object.")
```

- [ ] **Step 2: 抽出 render_and_send 公共路径**

把 main() 尾部现有的 pack/dry-run/BLE 序列抽成闭包或局部函数（仍持有
args/state_path 等），rotation 与单模式共用：

```python
    async def render_and_send(page_id: str, new_entry: dict, black_image, red_image,
                              output_path: Path, scope_pages: list[str]):
        """Pack planes, send over BLE when needed, persist state on success."""
        black_payload = pack_monochrome(black_image)
        red_payload = pack_monochrome(red_image) if red_image is not None else None
        layer_count = 2 if red_payload is not None else 1
        print(f"Rendered {output_path} ({len(black_payload)} bytes x "
              f"{layer_count} layer{'s' if layer_count > 1 else ''})")
        if args.dry_run:
            return
        await write_card_with_retry(args.name_prefix, black_payload, red_payload,
                                    clear_first=args.clear_first)
        stored = load_display_state_v2(state_path) or empty_display_state()
        updated = merge_page_state(stored, active_pages=scope_pages,
                                   current_page=page_id, new_entry=new_entry)
        save_display_state_v2(state_path, updated)
```

单模式三分支的尾部相应改为调用它（active_pages=[mode]、page_id=mode），
从而 Task 4 Step 3 加过的尾部保存块被这次收编取代（不要留下双份保存点）。

重要次序保持：`fixed_test` / `calendar_test` / `device_calendar_temperature`
的特殊早退仍在渲染前分流，不走 render_and_send（它们不需要状态管理）。

- [ ] **Step 3: rotation 分支本体**

插在最后的 else 前面（即 mode == "rotation" 的 elif 分支）：

```python
    elif mode == "rotation":
        from rotation_state import select_next_page, validate_rotation_config
        from stocks_data import fetch_indices_async
        from stocks_card import build_stocks_card

        pages, _interval = normalize_rotation_config(config)
        validate_rotation_config(config)
        candidate = select_next_page(load_display_state_v2(state_path), pages)
        print(f"Rotation candidate page: {candidate}")

        if args.output:
            output = Path(args.output).expanduser()
        elif args.dry_run:
            output = Path(__file__).with_name(f"preview-{candidate}.png")
        # 其余情形维持 test-card.png 默认

        if candidate == "stocks":
            fetched_at = datetime.now().astimezone()
            try:
                proxies_value = stocks_config.get("proxy")
                quotes = await fetch_indices_async(
                    stocks_config["indices"],
                    proxy=proxies_value if isinstance(proxies_value, str) else None,
                    timeout_seconds=float(stocks_config.get("timeout_seconds", 60)),
                )
            except StocksDataError as exc:
                print(f"Stocks page skipped this round: {exc}")
                raise SystemExit(1)
            display_state = stocks_display_state(quotes, fetched_at=fetched_at)
            if unchanged(candidate, display_state):
                return
            black_image, red_image, preview = build_stocks_card(
                args.width, args.height, quotes, now=fetched_at)
            preview.save(output)
            await render_and_send(candidate, display_state, black_image, red_image, output, pages)
        else:
            # quota / calendar-agenda / calendar-sensor 三页：与本文件既有单模式
            # 分支逻辑完全同构 —— 取数 → display_state → unchanged 判定 → build 卡片
            # → preview.save(output) → render_and_send(candidate, ..., scope_pages=pages)。
            # 执行者复制对应分支代码，仅把 unchanged/save 相关调用改成上述形状。
            ...
        return
```

注意：
- `unchanged()` 已是 Task 4 的 (page_id, entry) 签名，rotation 下比较目标
  是该页自己的条目；
- dry-run 下 render_and_send 在 BLE 前早退，不会碰状态文件；
- `normalize_rotation_config` 从 rotation_state 导入（Task 1 里已实现），
  不要在本文件重复定义。

- [ ] **Step 4: 更新 exports 检查（防循环导入）**

`stocks_card.py` 从 `epd_status` 导入 helper（font/text_right），而
`epd_status.main()` 才动态 import stocks_card —— 无模块级循环。
验证：`.venv/bin/python -c "import epd_status, stocks_card, stocks_data, rotation_state; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 5: 全量回归 + 手动冒烟**

```bash
.venv/bin/python -m pytest tests/ -v      # 全部通过
cp /tmp/epd-test-config.json . 2>/dev/null || true
cat > /tmp/epd-test-config.json <<'EOF'
{
  "display_mode": "rotation",
  "rotation": {"pages": ["calendar-agenda", "stocks"], "interval_seconds": 300},
  "stocks": {
    "proxy": "http://127.0.0.1:7890",
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
  "calendar": {"names": [], "max_events": 4}
}
EOF
rm -f .last-display-state.json
.venv/bin/python epd_status.py --config /tmp/epd-test-config.json --mode rotation --dry-run
ls preview-*.png
.venv/bin/python epd_status.py --config /tmp/epd-test-config.json --mode rotation --dry-run
ls preview-*.png
```

Expected 行为说明（正确预期，不是 bug）：
dry-run 不写状态，因此两次连续 dry-run 的候选页相同（首轮均为
"calendar-agenda"，生成 `preview-calendar-agenda.png`）。要观察到候选页推进，
须以非 dry-run 方式真实写屏一次（或人工放置一个 v2 state 文件把 current_page
置为 "calendar-agenda"，再用 --dry-run 试 stocks 页渲染）：

```bash
printf '{"version":2,"current_page":"calendar-agenda","pages":{"calendar-agenda":{"mode":"calendar-agenda"}}}' > .last-display-state.json
.venv/bin/python epd_status.py --config /tmp/epd-test-config.json --mode rotation --dry-run
ls preview-stocks.png && echo OK
```

Expected: 打印 "Rotation candidate page: stocks" 且生成 preview-stocks.png
（真实行情数据，7 行齐全）。

故障注入冒烟（验证 StocksDataError 路径）：

```bash
.venv/bin/python epd_status.py --config <(python3 - <<'PY'
import json
cfg = json.load(open('/tmp/epd-test-config.json'))
cfg['stocks']['proxy'] = 'http://127.0.0.1:1'
print(json.dumps(cfg))
PY
) --mode rotation --dry-run 2>&1 | tail -2
```

Expected: 打印 "Stocks page skipped this round: ..." 后退出码 1
（配额/candidate 为其他页时不受影响）。

- [ ] **Step 6: config.example.json 更新 + 提交**

照 Task 6 Step 5 的结构更新 `config.example.json`
（display_mode 改 "rotation"、加 rotation 与 stocks 段，保留 calendar/sensor）。

```bash
git add epd_status.py config.example.json
git commit -m "feat: wire rotation mode into main with change-driven refresh"
```

---

### Task 7: README 补充 + 收尾整理

**Files:**
- Modify: `README.md`
- Modify: `tests/test_rotation_stocks.py`（仅整理 import）

- [ ] **Step 1: 整理测试文件 imports 到顶部**

把逐 Task 追加在中部/nearby 的 `import json/tempfile/os/...` 与模块级辅助
（_fake_fast_info、STOCKS_NOW）上提到文件顶部 import 区下方，保持
`if __name__ == "__main__"` 守卫只在文件尾出现一次；跑一遍全量测试确认无碍。

- [ ] **Step 2: README 增补**

功能表加入一行：

```markdown
| **页面轮播** | `config.json` 的 `rotation.pages` 决定参与轮播的页面与顺序；每轮切向下一页，但页面可见内容未变时跳过写屏 —— 休市时段自然停住，减少闪屏。股票页数据来自 Yahoo Finance。 |
```

在第 5 步定时更新小节末尾追加：

```markdown
启用轮播时建议把间隔调到与 `rotation.interval_seconds` 一致：

\`\`\`zsh
EPD_UPDATE_INTERVAL_SECONDS=300 ./scripts/install-launchagent.sh
\`\`\`

股票页走 Yahoo Finance 国际行情，机器需要能访问外网；launchd 任务不继承终端
环境变量，若需要代理请在 `config.json` 的 `stocks.proxy` 中显式配置。
美股夜盘刷新要求 Mac 不睡眠。
```

- [ ] **Step 3: 最终全量回归 + spec 验收清单核对**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 全绿

逐条核对 spec §验收 清单（plan 末尾附）：

- [ ] `--mode rotation --dry-run` 出预览图，agenda 与 stocks 均可渲染
- [ ] 真实网络拉取 7 个指数且 change% 与财经网站一致
- [ ] 相同内容第二轮日志出现 skip（可用 v2 state 文件手工模拟）
- [ ] 写屏中断/失败时 state 文件不变（BLE mock 测试覆盖该不变式）
- [ ] 旧版 v1 state 文件被无害丢弃重建
- [ ] 无 stocks 配置而 pages 含 stocks → 启动报错退出

- [ ] **Step 4: 提交**

```bash
git add README.md tests/test_rotation_stocks.py
git commit -m "docs: document rotation mode and tidy test imports"
```

---

## 验收清单（对齐 spec）

- [ ] `--mode rotation --dry-run` 出预览图，agenda 与 stocks 均可交替渲染
- [ ] 真实网络拉取 7 个指数且 change% 与财经网站一致
- [ ] 休市时段内容相同的下一轮 → skip 日志且屏幕不刷
- [ ] 写屏中断/失败时 `.last-display-state.json` 内容不变
- [ ] 旧版 v1 state 文件被无害丢弃重建
- [ ] 无 `stocks` 配置而 pages 含 stocks → 启动报错退出
