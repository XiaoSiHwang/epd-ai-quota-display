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
    error: str | None = None


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
                              None, None, currency, unavailable=True,
                              error="quote provider returned no price")
        return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                          float(last), (float(last) / float(prev) - 1) * 100,
                          currency)

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, work), timeout=15)
    except Exception as exc:
        print(f"Index {entry['symbol']} unavailable: {exc}")
        return IndexQuote(entry["zone"], entry["symbol"], entry["name"],
                          None, None, None, unavailable=True, error=str(exc))


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
        reasons = "; ".join(
            f"{quote.symbol}: {quote.error or 'unavailable'}"
            for quote in results
        )
        raise StocksDataError(f"All requested indices failed to fetch a quote. ({reasons})")
    return list(results)
