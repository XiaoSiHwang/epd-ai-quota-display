# glm_data.py
"""Fetch GLM Coding Plan quota from bigmodel.cn.

Endpoint verified live (2026-08-31): GET /api/monitor/usage/quota/limit with
`Authorization: <api_key>` (raw key, no Bearer prefix). Response carries
data.limits[] where unit 3 = 5-hour rolling window, unit 6 = weekly window;
percentage = used percent; nextResetTime = epoch milliseconds; data.level =
plan tier. TIME_LIMIT entries (e.g. web-search counts) are ignored.
"""

import json
from urllib.error import HTTPError
from urllib.request import Request

from http_utils import https_urlopen

GLM_QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"


class GlmQuotaError(RuntimeError):
    """Raised for any GLM quota fetch/parse failure."""


def _classify_window(item: dict):
    """Return the window label for a TOKENS_LIMIT entry, or None.

    unit 3 = 5-hour window, unit 6 = weekly window (cc-switch anchors on
    `unit` only — both `number: 7` and `number: 1` have been observed for
    the weekly window). Missing/unknown units fall back to the reset-time
    heuristic in fetch_glm_quota.
    """
    unit = item.get("unit")
    if unit == 3:
        return "5 HOURS"
    if unit == 6:
        return "7 DAYS"
    return None


def fetch_glm_quota(api_key: str) -> dict:
    """Fetch GLM quota windows. Returns {"level": str|None, "windows": [...]}.

    Each window: {"label": "5 HOURS"|"7 DAYS", "used": float(0-100 clamped),
    "reset_at": epoch seconds | None}.
    """
    request = Request(
        GLM_QUOTA_URL,
        headers={
            "Authorization": api_key,
            "User-Agent": "epd-ai-quota-display",
            "Accept": "application/json",
        },
    )
    try:
        with https_urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise GlmQuotaError("GLM API key 无效或已过期") from exc
        raise GlmQuotaError(f"GLM quota request failed (HTTP {exc.code}).") from exc
    except Exception as exc:
        raise GlmQuotaError(f"GLM quota request failed: {exc}") from exc

    if payload.get("success") is False:
        message = payload.get("msg") or "Unknown GLM error"
        raise GlmQuotaError(f"GLM API error: {message}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise GlmQuotaError("GLM quota response is missing the data field.")

    slots = {"5 HOURS": None, "7 DAYS": None}
    unclassified = []
    for item in data.get("limits", []):
        if not isinstance(item, dict):
            continue
        limit_type = str(item.get("type") or "")
        if limit_type.upper() not in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            continue
        percentage = item.get("percentage")
        used = float(percentage) if percentage is not None else 0.0
        used = max(0.0, min(100.0, used))
        reset_ms = item.get("nextResetTime")
        reset_at = int(reset_ms // 1000) if reset_ms else None
        entry = (reset_at, used)
        label = _classify_window(item)
        if label and slots[label] is None:
            slots[label] = entry
        else:
            unclassified.append(entry)

    # Fallback heuristic (unit missing/unknown), mirroring cc-switch:
    # entries WITHOUT a reset time sort first and go to the 5-hour slot (the
    # 5-hour bucket can sit at 0% with no reset); the rest fill remaining
    # slots in reset-time order.
    unclassified.sort(key=lambda entry: (entry[0] is not None, entry[0] or 0))
    for reset_at, used in unclassified:
        if slots["5 HOURS"] is None:
            slots["5 HOURS"] = (reset_at, used)
        elif slots["7 DAYS"] is None:
            slots["7 DAYS"] = (reset_at, used)

    windows = []
    for label in ("5 HOURS", "7 DAYS"):
        entry = slots[label]
        if entry is None:
            continue
        reset_at, used = entry
        windows.append({"label": label, "used": used, "reset_at": reset_at})

    level = data.get("level")
    return {"level": str(level) if level else None, "windows": windows}
