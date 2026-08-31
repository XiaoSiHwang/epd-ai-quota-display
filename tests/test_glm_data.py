# tests/test_glm_data.py
import json
from unittest.mock import MagicMock, patch

import pytest

from glm_data import GlmQuotaError, fetch_glm_quota


def _response(payload, status=200):
    response = MagicMock()
    response.status = status
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    response.read = MagicMock(return_value=json.dumps(payload).encode())
    return response


SUCCESS_BODY = {
    "code": 200,
    "msg": "操作成功",
    "success": True,
    "data": {
        "level": "pro",
        "limits": [
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 0},
            {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 17,
             "nextResetTime": 1788602435998},
            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 1000,
             "currentValue": 1, "remaining": 999, "percentage": 1},
        ],
    },
}


class TestFetchGlmQuota:
    def test_parses_windows_and_level(self):
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.return_value = _response(SUCCESS_BODY)
            result = fetch_glm_quota("glm-key")
        assert result["level"] == "pro"
        windows = {w["label"]: w for w in result["windows"]}
        assert windows["5 HOURS"]["used"] == 0.0
        assert windows["5 HOURS"]["reset_at"] is None
        assert windows["7 DAYS"]["used"] == 17.0
        assert windows["7 DAYS"]["reset_at"] == 1788602435998 // 1000

    def test_authorization_header_is_raw_key_without_bearer(self):
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.return_value = _response(SUCCESS_BODY)
            fetch_glm_quota("glm-key")
        request = mock_open.call_args[0][0]
        assert request.headers.get("Authorization") == "glm-key"
        assert "Bearer" not in (request.headers.get("Authorization") or "")

    def test_business_error_raises(self):
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.return_value = _response(
                {"code": 401, "success": False, "msg": "鉴权失败"})
            with pytest.raises(GlmQuotaError, match="鉴权失败"):
                fetch_glm_quota("bad-key")

    def test_http_401_raises_friendly_error(self):
        import io
        from urllib.error import HTTPError
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.side_effect = HTTPError(
                "url", 401, "Unauthorized", {}, io.BytesIO(b""))
            with pytest.raises(GlmQuotaError, match="无效或已过期"):
                fetch_glm_quota("expired-key")

    def test_timeout_wrapped_as_glm_error(self):
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.side_effect = TimeoutError("timed out")
            with pytest.raises(GlmQuotaError, match="timed out"):
                fetch_glm_quota("glm-key")

    def test_missing_level_is_none(self):
        body = {"success": True, "data": {"limits": [
            {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 10,
             "nextResetTime": 1788602435998},
        ]}}
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.return_value = _response(body)
            result = fetch_glm_quota("glm-key")
        assert result["level"] is None
        assert len(result["windows"]) == 1
        assert result["windows"][0]["label"] == "7 DAYS"


class TestParseUnitClassification:
    """Fallback heuristic: unknown/missing unit slots windows like cc-switch."""

    def _fetch(self, limits):
        body = {"success": True, "data": {"level": "pro", "limits": limits}}
        with patch("glm_data.https_urlopen") as mock_open:
            mock_open.return_value = _response(body)
            return fetch_glm_quota("glm-key")

    def test_unit_missing_no_reset_goes_to_five_hour(self):
        result = self._fetch([
            {"type": "TOKENS_LIMIT", "percentage": 5},
            {"type": "TOKENS_LIMIT", "percentage": 50,
             "nextResetTime": 1788602435998},
        ])
        windows = {w["label"]: w for w in result["windows"]}
        assert windows["5 HOURS"]["used"] == 5.0
        assert windows["7 DAYS"]["used"] == 50.0

    def test_percentage_clamped_over_100(self):
        result = self._fetch([
            {"type": "TOKENS_LIMIT", "unit": 6, "percentage": 120,
             "nextResetTime": 1788602435998},
        ])
        assert result["windows"][0]["used"] == 100.0

    def test_percentage_clamped_below_zero(self):
        result = self._fetch([
            {"type": "TOKENS_LIMIT", "unit": 6, "percentage": -5,
             "nextResetTime": 1788602435998},
        ])
        assert result["windows"][0]["used"] == 0.0

    def test_percentage_missing_is_zero(self):
        result = self._fetch([
            {"type": "TOKENS_LIMIT", "unit": 3, "number": 5},
        ])
        assert result["windows"][0]["used"] == 0.0

    def test_time_limit_entries_ignored(self):
        result = self._fetch([
            {"type": "TIME_LIMIT", "unit": 5, "percentage": 99},
        ])
        assert result["windows"] == []

    def test_credit_limit_type_also_recognized(self):
        result = self._fetch([
            {"type": "CREDIT_LIMIT", "unit": 3, "percentage": 33},
        ])
        assert result["windows"][0]["label"] == "5 HOURS"
        assert result["windows"][0]["used"] == 33.0
