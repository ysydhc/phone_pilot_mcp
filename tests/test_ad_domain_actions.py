"""Unit tests for Mobile Commercialization Full Ad-Format Suite (ad_domain_actions.py)."""

from __future__ import annotations

from phone_pilot.core.actions import (
    AdDomainActions,
    AdFormat,
    MonotonicityChecker,
    inspect_ad_close_and_countdown,
    parse_countdown_text,
    verify_ad_format,
)


def _make_uts_line(
    stage: str,
    ad_format: str = "reward_video",
    placement_id: str = "slot_001",
    request_id: str = "req_abc",
) -> str:
    return (
        f"I/AdSDK: {stage} "
        f'{{"event": "{stage}", "ad_format": "{ad_format}", '
        f'"placement_id": "{placement_id}", "request_id": "{request_id}"}}'
    )


class TestAdDomainActions:
    """Comprehensive tests for all 5 ad formats, monotonicity, and UTS fields."""

    def test_all_five_ad_formats_pass(self) -> None:
        actions = AdDomainActions()

        # 1. Splash
        splash_logs = [
            _make_uts_line("INIT", "splash", "p_splash"),
            _make_uts_line("LOAD", "splash", "p_splash"),
            _make_uts_line("READY", "splash", "p_splash"),
            _make_uts_line("RENDER", "splash", "p_splash"),
            _make_uts_line("IMPRESSION", "splash", "p_splash"),
            _make_uts_line("CLOSE", "splash", "p_splash"),
        ]
        res_splash = actions.verify_ad_format("splash", splash_logs, placement_id="p_splash")
        assert res_splash["ok"] is True
        assert res_splash["ad_format"] == "splash"
        assert res_splash["out_of_order"] == 0
        assert res_splash["missing_stages"] == []

        # 2. Banner (with trackImpression alias)
        banner_logs = [
            "I/AdSDK: AD_INIT slot=p_banner",
            "I/AdSDK: AD_LOAD slot=p_banner",
            "I/AdSDK: AD_READY slot=p_banner",
            "I/AdSDK: AD_RENDER slot=p_banner",
            "I/AdSDK: trackImpression slot=p_banner",
        ]
        res_banner = actions.verify_ad_format("banner", banner_logs, placement_id="p_banner")
        assert res_banner["ok"] is True
        assert res_banner["ad_format"] == "banner"
        assert "IMPRESSION" in res_banner["stages_found"]

        # 3. Feed / Native
        feed_logs = [
            _make_uts_line("INIT", "feed", "p_feed"),
            _make_uts_line("LOAD", "feed", "p_feed"),
            _make_uts_line("READY", "feed", "p_feed"),
            _make_uts_line("RENDER", "feed", "p_feed"),
            _make_uts_line("IMPRESSION", "feed", "p_feed"),
            _make_uts_line("CLICK", "feed", "p_feed"),
        ]
        res_feed = actions.verify_ad_format(
            "native_feed", feed_logs, placement_id="p_feed", require_click=True
        )
        assert res_feed["ok"] is True
        assert res_feed["ad_format"] == "feed"
        assert "CLICK" in res_feed["stages_found"]

        # 4. Interstitial
        int_logs = [
            _make_uts_line("INIT", "interstitial", "p_int"),
            _make_uts_line("LOAD", "interstitial", "p_int"),
            _make_uts_line("READY", "interstitial", "p_int"),
            _make_uts_line("RENDER", "interstitial", "p_int"),
            _make_uts_line("IMPRESSION", "interstitial", "p_int"),
            _make_uts_line("CLOSE", "interstitial", "p_int"),
        ]
        res_int = actions.verify_ad_format("interstitial", int_logs, placement_id="p_int")
        assert res_int["ok"] is True
        assert res_int["ad_format"] == "interstitial"

        # 5. Reward Video
        rv_logs = [
            _make_uts_line("INIT", "reward_video", "p_rv"),
            _make_uts_line("LOAD", "reward_video", "p_rv"),
            _make_uts_line("READY", "reward_video", "p_rv"),
            _make_uts_line("RENDER", "reward_video", "p_rv"),
            _make_uts_line("IMPRESSION", "reward_video", "p_rv"),
            _make_uts_line("REWARD", "reward_video", "p_rv"),
            _make_uts_line("CLOSE", "reward_video", "p_rv"),
        ]
        res_rv = verify_ad_format(
            "reward_video",
            rv_logs,
            placement_id="p_rv",
            required_uts_fields=MonotonicityChecker.DEFAULT_UTS_FIELDS,
        )
        assert res_rv["ok"] is True
        assert res_rv["ad_format"] == "reward_video"
        assert res_rv["reward_verified"] is True
        assert res_rv["reason"] == "verified"

    def test_out_of_order_detected(self) -> None:
        # IMPRESSION occurs before RENDER, and LOAD occurs after READY
        logs = [
            "I/AdSDK: INIT",
            "I/AdSDK: READY",
            "I/AdSDK: LOAD",
            "I/AdSDK: IMPRESSION",
            "I/AdSDK: RENDER",
            "I/AdSDK: CLOSE",
        ]
        res = verify_ad_format("interstitial", logs)
        assert res["ok"] is False
        assert res["out_of_order"] == 2
        assert "out_of_order=2" in res["reason"]
        details = res["details"]["out_of_order_details"]
        assert len(details) == 2
        assert details[0]["stage"] == "LOAD"
        assert details[0]["after_stage"] == "READY"
        assert details[1]["stage"] == "RENDER"
        assert details[1]["after_stage"] == "IMPRESSION"

    def test_reward_video_missing_reward_fails(self) -> None:
        logs = [
            "I/AdSDK: INIT",
            "I/AdSDK: LOAD",
            "I/AdSDK: READY",
            "I/AdSDK: RENDER",
            "I/AdSDK: IMPRESSION",
            "I/AdSDK: CLOSE",
        ]
        res = verify_ad_format("rewarded_video", logs)
        assert res["ok"] is False
        assert res["reward_verified"] is False
        assert "REWARD" in res["missing_stages"]
        assert "missing_stages=REWARD" in res["reason"]

    def test_uts_missing_fields_detected(self) -> None:
        logs = [
            'I/AdSDK: INIT {"event": "INIT", "ad_format": "banner", "placement_id": "p1", "request_id": "r1"}',
            'I/AdSDK: LOAD {"event": "LOAD", "ad_format": "banner", "placement_id": "p1"}',
            'I/AdSDK: READY {"event": "READY", "placement_id": "p1", "request_id": "r1"}',
            'I/AdSDK: RENDER {"event": "RENDER", "ad_format": "banner", "placement_id": "p1", "request_id": "r1"}',
            'I/AdSDK: IMPRESSION {"event": "IMPRESSION", "ad_format": "banner", "placement_id": "p1", "request_id": "r1"}',
        ]
        res = verify_ad_format(
            AdFormat.BANNER,
            logs,
            placement_id="p1",
            required_uts_fields=("event", "ad_format", "placement_id", "request_id"),
        )
        assert res["ok"] is False
        assert set(res["missing_fields"]) == {"request_id", "ad_format"}

    def test_ad_format_aliases_and_invalid(self) -> None:
        assert AdFormat.from_str("Splash") is AdFormat.SPLASH
        assert AdFormat.from_str("native") is AdFormat.FEED
        assert AdFormat.from_str("int") is AdFormat.INTERSTITIAL
        assert AdFormat.from_str("rewarded") is AdFormat.REWARD_VIDEO
        assert AdFormat.from_str(AdFormat.BANNER) is AdFormat.BANNER

        raised = False
        try:
            AdFormat.from_str("unknown_format")
        except ValueError:
            raised = True
        assert raised is True

    def test_session_log_accumulation_across_clear_log(self) -> None:
        from unittest.mock import patch
        from phone_pilot.android.driver import AndroidDriver
        import phone_pilot.android.adb.logcat as logcat_mod

        live_buffer: list[str] = [
            "04-12 09:00:00.001 I/AdSDK: pre_session_noise_should_be_discarded",
        ]

        def fake_read_logcat(serial, lines=200, since=None, tag_filter=None):
            return "\n".join(live_buffer[-lines:]), {}

        def fake_clear_logcat(serial):
            live_buffer.clear()
            return {"ok": True}

        with (
            patch.object(logcat_mod, "read_logcat", side_effect=fake_read_logcat),
            patch.object(logcat_mod, "clear_logcat", side_effect=fake_clear_logcat),
        ):
            driver = AndroidDriver("mock_serial")
            # Startup clear with archive=False discards pre-session noise
            driver.clear_log(archive=False)
            driver.reset_session_log()

            # Step 1: LOAD + READY emitted, then atomic_ops calls clear_log()
            live_buffer.extend(
                [
                    "04-12 10:00:01.000 I/Interstitial: LOAD_AD start",
                    "04-12 10:00:01.500 I/AdMaster: lifecycle_stage READY",
                ]
            )
            driver.clear_log()  # Archives LOAD + READY before clearing

            # Step 2: RENDER + IMPRESSION emitted, then atomic_ops calls clear_log()
            live_buffer.extend(
                [
                    "04-12 10:00:02.000 I/Interstitial: SHOW_OK",
                    "04-12 10:00:02.100 I/AdMaster: lifecycle_stage IMPRESSION",
                ]
            )
            driver.clear_log()  # Archives RENDER + IMPRESSION before clearing

            # Step 3: CLICK + CLOSE emitted (still in live logcat)
            live_buffer.extend(
                [
                    "04-12 10:00:03.000 I/AdMaster: lifecycle_stage CLICK",
                    "04-12 10:00:04.000 I/AdMaster: lifecycle_stage CLOSE",
                ]
            )

            # Verify that verify_ad_format(..., driver=driver) sees the entire session funnel!
            res = verify_ad_format(
                AdFormat.INTERSTITIAL,
                driver=driver,
                required_stages=("LOAD", "READY", "RENDER", "IMPRESSION", "CLICK", "CLOSE"),
            )
            assert res["ok"] is True
            assert res["stages_found"] == ["LOAD", "READY", "RENDER", "IMPRESSION", "CLICK", "CLOSE"]
            assert res["out_of_order"] == 0

    def test_countdown_text_parsing_variants(self) -> None:
        # 1. Delayed countdown ("15秒后可关闭", "Skip in 5s")
        p1 = parse_countdown_text("15秒后可关闭")
        assert p1["has_countdown"] is True
        assert p1["seconds"] == 15
        assert p1["ready_to_close"] is False
        assert p1["kind"] == "delayed_countdown"

        p1_en = parse_countdown_text("Skip in 5s")
        assert p1_en["has_countdown"] is True
        assert p1_en["seconds"] == 5
        assert p1_en["ready_to_close"] is False

        p1_zero = parse_countdown_text("0秒后可关闭")
        assert p1_zero["has_countdown"] is True
        assert p1_zero["seconds"] == 0
        assert p1_zero["ready_to_close"] is True

        # 2. Countdown with immediate skip ("5s | 跳过")
        p2 = parse_countdown_text("5s | 跳过", allow_early_skip=True)
        assert p2["has_countdown"] is True
        assert p2["seconds"] == 5
        assert p2["is_close_text"] is True
        assert p2["ready_to_close"] is True
        assert p2["kind"] == "countdown_with_skip"

        p2_strict = parse_countdown_text("5s | 跳过", allow_early_skip=False)
        assert p2_strict["ready_to_close"] is False

        # 3. Pure countdown ("12s", "5秒", bare integer in close widget)
        p3 = parse_countdown_text("12s")
        assert p3["has_countdown"] is True
        assert p3["seconds"] == 12
        assert p3["ready_to_close"] is False
        assert p3["kind"] == "pure_countdown"

        p3_bare_outside = parse_countdown_text("12", in_close_widget=False)
        assert p3_bare_outside["has_countdown"] is False

        p3_bare_inside = parse_countdown_text("12", in_close_widget=True)
        assert p3_bare_inside["has_countdown"] is True
        assert p3_bare_inside["seconds"] == 12
        assert p3_bare_inside["ready_to_close"] is False

        # 4. Reward earned & immediate close
        p4 = parse_countdown_text("已获得奖励")
        assert p4["ready_to_close"] is True
        assert p4["kind"] == "reward_earned"

        p5 = parse_countdown_text("跳过广告")
        assert p5["ready_to_close"] is True
        assert p5["kind"] == "immediate_close"

    def test_inspect_ad_close_and_countdown_disambiguates_mute_vs_close(self) -> None:
        # Scenario A: Active countdown ("8秒后可关闭") + left/right AdCloseButtonView
        nodes_counting = [
            {
                "class_name": "com.baidurtb.Step SDK.AdCloseButtonView",
                "bounds": "[28,180][96,248]",  # cx=62, cy=214 (left mute)
                "text": "",
            },
            {
                "class_name": "android.widget.TextView",
                "bounds": "[1000,180][1180,248]",
                "text": "8秒后可关闭",
            },
            {
                "class_name": "com.baidurtb.Step SDK.AdCloseButtonView",
                "bounds": "[1184,180][1252,248]",  # cx=1218, cy=214 (right close)
                "text": "",
            },
        ]
        state_a = inspect_ad_close_and_countdown(
            nodes_counting, screen_width=1280, screen_height=2772
        )
        assert state_a["has_countdown"] is True
        assert state_a["countdown_seconds"] == 8
        assert state_a["ready_to_close"] is False
        assert state_a["reason"] == "countdown_active_8s"
        assert len(state_a["mute_candidates"]) == 1
        assert state_a["mute_candidates"][0]["cx"] == 62

        # Scenario B: Countdown finished -> right-side AdCloseButtonView is selected over left-side mute
        nodes_ready = [
            {
                "class_name": "com.baidurtb.Step SDK.AdCloseButtonView",
                "bounds": "[28,180][96,248]",  # cx=62, cy=214 (left mute)
                "text": "",
            },
            {
                "class_name": "com.baidurtb.Step SDK.AdCloseButtonView",
                "bounds": "[1184,180][1252,248]",  # cx=1218, cy=214 (right close)
                "text": "",
            },
        ]
        state_b = inspect_ad_close_and_countdown(
            nodes_ready, screen_width=1280, screen_height=2772
        )
        assert state_b["ready_to_close"] is True
        assert state_b["close_xy"] == (1218, 214)
        assert len(state_b["mute_candidates"]) == 1
        assert state_b["mute_candidates"][0]["cx"] == 62
