"""Card rendering tests for the quota_glm page (CODEX + GLM)."""

class TestBuildQuotaGlmCard:
    def test_renders_both_providers(self):
        from epd_status import build_quota_glm_card, pack_monochrome
        codex = [
            {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
            {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
        ]
        glm = [
            {"label": "5 HOURS", "used": 0, "reset_at": 1_800_100_000},
            {"label": "7 DAYS", "used": 17, "reset_at": 1_800_200_000},
        ]
        black, red, preview = build_quota_glm_card(
            400, 300, codex, glm, glm_level="pro")
        assert preview.size == (400, 300)
        assert len(pack_monochrome(black)) == 15_000
        assert len(pack_monochrome(red)) == 15_000

    def test_glm_missing_window_renders_unavailable(self):
        from epd_status import build_quota_glm_card
        codex = [
            {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
            {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
        ]
        glm = [{"label": "5 HOURS", "used": 0, "reset_at": 1_800_100_000}]
        black, red, preview = build_quota_glm_card(
            400, 300, codex, glm, glm_level="pro")
        assert preview.size == (400, 300)

    def test_glm_failure_renders_not_connected(self):
        from epd_status import build_quota_glm_card
        codex = [
            {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
            {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
        ]
        black, red, preview = build_quota_glm_card(
            400, 300, codex, None, glm_level=None)
        assert preview.size == (400, 300)

    def test_glm_level_none_title_is_plain_glm(self):
        from epd_status import build_quota_glm_card
        codex = [
            {"label": "5 HOURS", "used": 1, "reset_at": 1_800_000_000},
            {"label": "7 DAYS", "used": 26, "reset_at": 1_800_086_400},
        ]
        glm = [
            {"label": "5 HOURS", "used": 0, "reset_at": 1_800_100_000},
            {"label": "7 DAYS", "used": 17, "reset_at": 1_800_200_000},
        ]
        # Must not raise; title renders "GLM" without level suffix
        black, red, preview = build_quota_glm_card(
            400, 300, codex, glm, glm_level=None)
        assert preview.size == (400, 300)
