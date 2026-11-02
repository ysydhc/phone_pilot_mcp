"""End-to-End Integration Suite: Full Ad-Format Verification & Cognitive Evolution Pipeline.

Verifies the complete 4-layer phone_pilot architecture evolution in a unified E2E pipeline:
1. Pre-E2E Binary Freshness Gatekeeper (`FreshnessChecker` / `check_freshness`)
2. Semantic State Projection & Context Compression (`project_page_state` / `compute_state_delta` / `assert_contract_events`)
3. Cognitive UI Knowledge Graph & Self-Healing Navigation (`UIKnowledgeGraph` / `UIKnowledgeBase` / `UINavigator`)
4. Mobile Commercialization 5 Ad Formats (`splash`, `banner`, `feed`, `interstitial`, `reward_video`) & Stage Monotonicity (`AdDomainActions` / `MonotonicityChecker`)
"""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any
import zipfile

from phone_pilot.core.actions.ad_domain_actions import (
    AdDomainActions,
    AdFormat,
    MonotonicityChecker,
    verify_ad_format,
)
from phone_pilot.core.guard.freshness_checker import (
    FreshnessChecker,
    check_freshness,
)
from phone_pilot.core.ui.graph import UIKnowledgeGraph
from phone_pilot.core.ui.knowledge_base import UIKnowledgeBase
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.page import PageSignature
from phone_pilot.core.ui.projection import (
    assert_contract_events,
    compute_state_delta,
    project_page_state,
)


class SimulatedCommercialAppDriver:
    """In-memory deterministic driver simulating a mobile commercialization SDK Demo App.

    Supports all 5 ad formats (Splash, Banner, Feed/Native, Interstitial, RewardVideo),
    dynamic UI state transitions, and an injected popup interruption for self-healing verification.
    """

    def __init__(self, *, inject_popup_on_format: str = "reward_video") -> None:
        self.current_page = "home"
        self.inject_popup_on_format = inject_popup_on_format
        self.popup_active = False
        self.popup_dismissed_count = 0
        self.tapped_coordinates: list[tuple[int, int]] = []
        self.log_buffer: list[str] = []

    def _build_home_state(self) -> dict[str, Any]:
        elements: list[dict[str, Any]] = [
            {
                "index": 0,
                "text": "AdMasterSDK Demo",
                "resource_id": "com.demo:id/title_bar",
                "class_name": "android.widget.TextView",
                "bounds": [0, 0, 1080, 120],
                "center": [540, 60],
                "clickable": False,
                "enabled": True,
                "visible": True,
            },
            {
                "index": 1,
                "text": "Splash Ad",
                "resource_id": "com.demo:id/btn_splash",
                "class_name": "android.widget.Button",
                "bounds": [80, 180, 1000, 280],
                "center": [540, 230],
                "clickable": True,
                "enabled": True,
                "visible": True,
            },
            {
                "index": 2,
                "text": "Banner Ad",
                "resource_id": "com.demo:id/btn_banner",
                "class_name": "android.widget.Button",
                "bounds": [80, 320, 1000, 420],
                "center": [540, 370],
                "clickable": True,
                "enabled": True,
                "visible": True,
            },
            {
                "index": 3,
                "text": "Native Feed",
                "resource_id": "com.demo:id/btn_feed",
                "class_name": "android.widget.Button",
                "bounds": [80, 460, 1000, 560],
                "center": [540, 510],
                "clickable": True,
                "enabled": True,
                "visible": True,
            },
            {
                "index": 4,
                "text": "Interstitial",
                "resource_id": "com.demo:id/btn_interstitial",
                "class_name": "android.widget.Button",
                "bounds": [80, 600, 1000, 700],
                "center": [540, 650],
                "clickable": True,
                "enabled": True,
                "visible": True,
            },
            {
                "index": 5,
                "text": "Rewarded Video",
                "resource_id": "com.demo:id/btn_reward_video",
                "class_name": "android.widget.Button",
                "bounds": [80, 740, 1000, 840],
                "center": [540, 790],
                "clickable": True,
                "enabled": True,
                "visible": True,
            },
        ]
        # Add 60 noisy decorative layout containers to verify >=70% semantic compression
        for idx in range(6, 66):
            elements.append(
                {
                    "index": idx,
                    "text": "",
                    "content_desc": "",
                    "resource_id": "",
                    "class_name": "android.widget.FrameLayout",
                    "bounds": [0, 0, 1080, 1920],
                    "center": [540, 960],
                    "clickable": False,
                    "enabled": True,
                    "visible": True,
                    "package_name": "com.baidujp.gbusdkdemo1",
                }
            )
        return {
            "ok": True,
            "package": "com.baidujp.gbusdkdemo1",
            "activity": ".MainActivity",
            "elements": elements,
            "screenshot_base64": "A" * 12000,
        }

    def _build_popup_overlay_state(self) -> dict[str, Any]:
        return {
            "ok": True,
            "package": "com.baidujp.gbusdkdemo1",
            "activity": ".DialogOverlay",
            "elements": [
                {
                    "index": 0,
                    "text": "System Announcement",
                    "resource_id": "com.demo:id/dialog_title",
                    "bounds": [140, 600, 940, 700],
                    "center": [540, 650],
                    "clickable": False,
                    "visible": True,
                },
                {
                    "index": 1,
                    "text": "允许",
                    "resource_id": "com.demo:id/dialog_allow",
                    "bounds": [340, 900, 740, 1000],
                    "center": [540, 950],
                    "clickable": True,
                    "visible": True,
                },
            ],
        }

    def _build_ad_page_state(self, fmt: str) -> dict[str, Any]:
        title_map = {
            "splash": ("SplashPage", "Splash Countdown 5s", "Skip"),
            "banner": ("BannerPage", "Banner Slot 320x50", "Install"),
            "feed": ("FeedPage", "Sponsored Feed Item", "Learn More"),
            "interstitial": ("InterstitialPage", "Interstitial 16:9 Video", "Install"),
            "reward_video": ("RewardVideoPage", "Reward Video Playing", "Claim Reward"),
        }
        act_name, headline, cta = title_map[fmt]
        return {
            "ok": True,
            "package": "com.baidujp.gbusdkdemo1",
            "activity": f".{act_name}",
            "elements": [
                {
                    "index": 0,
                    "text": headline,
                    "resource_id": f"com.demo:id/{fmt}_headline",
                    "bounds": [60, 200, 1020, 400],
                    "center": [540, 300],
                    "clickable": False,
                    "visible": True,
                },
                {
                    "index": 1,
                    "text": cta,
                    "resource_id": f"com.demo:id/{fmt}_cta",
                    "bounds": [240, 900, 840, 1020],
                    "center": [540, 960],
                    "clickable": True,
                    "visible": True,
                },
                {
                    "index": 2,
                    "text": "Back Home",
                    "resource_id": "com.demo:id/btn_back_home",
                    "bounds": [40, 60, 280, 140],
                    "center": [160, 100],
                    "clickable": True,
                    "visible": True,
                },
            ],
        }

    def get_page_state(self) -> dict[str, Any]:
        if self.popup_active:
            return self._build_popup_overlay_state()
        if self.current_page == "home":
            return self._build_home_state()
        return self._build_ad_page_state(self.current_page)

    def tap(self, x: int, y: int) -> dict[str, Any]:
        self.tapped_coordinates.append((x, y))
        if self.popup_active:
            # Tapping "允许" at (540, 950) dismisses popup
            if 340 <= x <= 740 and 900 <= y <= 1000:
                self.popup_active = False
                self.popup_dismissed_count += 1
                return {"ok": True, "dismissed_popup": True}
            return {"ok": False, "reason": "blocked_by_popup"}

        if self.current_page == "home":
            target_by_y = {
                230: "splash",
                370: "banner",
                510: "feed",
                650: "interstitial",
                790: "reward_video",
            }
            for center_y, fmt in target_by_y.items():
                if abs(y - center_y) <= 50:
                    if fmt == self.inject_popup_on_format and self.popup_dismissed_count == 0:
                        self.popup_active = True
                        return {"ok": True, "popup_triggered": True}
                    self.current_page = fmt
                    self._emit_ad_lifecycle_logs(fmt)
                    return {"ok": True, "navigated_to": fmt}
        else:
            # Tapping Back Home at (160, 100) returns to home
            if abs(x - 160) <= 120 and abs(y - 100) <= 50:
                self.current_page = "home"
                return {"ok": True, "navigated_to": "home"}

        return {"ok": True}

    def _emit_ad_lifecycle_logs(self, fmt: str) -> None:
        placement_id = f"plc_{fmt}_001"
        req_id = f"req_{fmt}_20261029"
        stages_map = {
            "splash": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLOSE"),
            "banner": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION"),
            "feed": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLICK"),
            "interstitial": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLOSE"),
            "reward_video": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "REWARD", "CLOSE"),
        }
        self.log_buffer.clear()
        for idx, stage in enumerate(stages_map[fmt]):
            line = (
                f'10-29 16:00:0{idx}.100 I/AdSDK: {{"stage": "{stage}", '
                f'"event": "AD_{stage}", "ad_format": "{fmt}", '
                f'"placement_id": "{placement_id}", "request_id": "{req_id}"}}'
            )
            self.log_buffer.append(line)


class TestFullAdFormatsE2E:
    """Comprehensive E2E integration tests across all 4 architecture evolution pillars."""

    def test_step1_freshness_gatekeeper_blocks_stale_and_passes_fresh(self) -> None:
        """Verify Pre-E2E Binary Freshness Gatekeeper blocks stale builds and passes fresh APK with symbol."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_dir = root / "src"
            src_dir.mkdir()
            src_file = src_dir / "AdManager.kt"
            src_file.write_text("class AdManager { fun loadRewardVideo() {} }", encoding="utf-8")

            apk_path = root / "app-debug.apk"
            with zipfile.ZipFile(apk_path, "w") as zf:
                zf.writestr("classes.dex", b"DEX_HEADER_loadRewardVideo_SYMBOL_TABLE")

            import os

            # 1. Stale binary (source modified after APK build) -> MUST block
            os.utime(apk_path, (1700000000.0, 1700000000.0))
            os.utime(src_file, (1700000100.0, 1700000100.0))
            stale_report = check_freshness(
                source_dir=src_dir,
                binary_path=apk_path,
                symbol_name="loadRewardVideo",
            )
            assert stale_report.ok is False
            assert "stale_binary" in stale_report.reason

            # 2. Fresh binary + fresh process + symbol verified -> MUST pass
            os.utime(apk_path, (1700000200.0, 1700000200.0))
            fresh_report = FreshnessChecker().verify(
                source_dir=src_dir,
                binary_path=apk_path,
                pid=43210,
                pid_mtime=1700000205.0,
                install_mtime=1700000201.0,
                symbol_name="loadRewardVideo",
            )
            assert fresh_report.ok is True
            assert fresh_report.symbol_present is True
            assert fresh_report.reason == "fresh"

    def test_step2_semantic_projection_and_state_delta_on_demo_home(self) -> None:
        """Verify >=70% context compression, index preservation, and state delta on Demo Home."""
        driver = SimulatedCommercialAppDriver()
        raw_home = driver.get_page_state()

        projected = project_page_state(raw_home, focus="Rewarded Video", max_elements=10)
        assert projected["ok"] is True
        assert projected["compression_ratio"] >= 0.70

        # Original index for "Rewarded Video" is 5 — must be preserved!
        reward_elems = [e for e in projected["elements"] if e.get("text") == "Rewarded Video"]
        assert len(reward_elems) == 1
        assert reward_elems[0]["index"] == 5

        # Trigger navigation to splash and compute state delta
        driver.tap(540, 230)
        splash_state = driver.get_page_state()
        delta = compute_state_delta(raw_home, splash_state)
        assert delta["changed"] is True
        assert delta["activity_changed"] is True
        assert any(
            "Splash Countdown 5s" in str(item.get("label") or item.get("text") or "")
            for item in delta["added_elements"]
        )

    def test_step3_and_step4_knowledge_graph_navigation_and_all_five_ad_formats(self) -> None:
        """End-to-end flow: Graph learning -> persistence -> self-healing navigation -> 5 ad formats verification."""
        driver = SimulatedCommercialAppDriver(inject_popup_on_format="reward_video")
        graph = UIKnowledgeGraph()

        home_state = driver._build_home_state()
        button_coords = {
            "splash": ("Splash Ad", [540, 230]),
            "banner": ("Banner Ad", [540, 370]),
            "feed": ("Native Feed", [540, 510]),
            "interstitial": ("Interstitial", [540, 650]),
            "reward_video": ("Rewarded Video", [540, 790]),
        }

        target_node_ids: dict[str, str] = {}
        for fmt, (btn_text, center) in button_coords.items():
            ad_state = driver._build_ad_page_state(fmt)
            _, edge, dst_node = graph.record_transition(
                home_state,
                {"type": "tap", "text": btn_text, "center": center},
                ad_state,
                success=True,
            )
            target_node_ids[fmt] = dst_node.node_id
            # Also record return edge back to home
            graph.record_transition(
                ad_state,
                {"type": "tap", "text": "Back Home", "center": [160, 100]},
                home_state,
                success=True,
            )

        # Verify persistence round-trip via UIKnowledgeBase
        with tempfile.TemporaryDirectory() as tmp:
            graph_file = Path(tmp) / "com.baidujp.gbusdkdemo1.json"
            kb = UIKnowledgeBase(storage_path=graph_file, graph=graph)
            kb.save()
            assert graph_file.exists()
            reloaded_graph = UIKnowledgeBase(storage_path=graph_file).load()
            assert len(reloaded_graph.nodes) == 6  # 1 home + 5 ad pages

        navigator = UINavigator(reloaded_graph, driver=driver)
        ad_suite = AdDomainActions(log_fetcher=lambda: list(driver.log_buffer))

        # Loop through all 5 ad formats: navigate from home -> verify ad lifecycle -> return home
        for fmt in ("splash", "banner", "feed", "interstitial", "reward_video"):
            # Navigate from home to the target ad page
            nav_res = navigator.navigate_to(target_node_ids[fmt], max_hops=5, max_heals=2)
            assert nav_res["ok"] is True, f"Navigation failed for {fmt}: {nav_res}"
            assert driver.current_page == fmt

            # Verify ad format lifecycle logs & UTS fields
            ad_res = ad_suite.verify_ad_format(
                fmt,
                placement_id=f"plc_{fmt}_001",
                required_uts_fields=MonotonicityChecker.DEFAULT_UTS_FIELDS,
            )
            assert ad_res["ok"] is True, f"Ad verification failed for {fmt}: {ad_res}"
            assert ad_res["out_of_order"] == 0
            assert ad_res["missing_stages"] == []
            assert ad_res["missing_fields"] == []
            if fmt == "reward_video":
                assert ad_res["reward_verified"] is True

            # Also verify contract events via projection.assert_contract_events
            contract_res = assert_contract_events(
                driver.log_buffer,
                ["AD_INIT", "AD_LOAD", "AD_READY", "AD_RENDER", "AD_IMPRESSION"],
                required_fields=["ad_format", "placement_id", "request_id"],
            )
            assert contract_res["ok"] is True
            assert contract_res["out_of_order"] == 0

            # Navigate back to home via PageSignature
            home_sig = PageSignature(
                name="HomePage",
                required_elements=["AdMasterSDK Demo", "Splash Ad", "Banner Ad"],
            )
            back_res = navigator.navigate_to(home_sig, max_hops=3)
            assert back_res["ok"] is True
            assert driver.current_page == "home"

        # Confirm that popup self-healing was triggered and succeeded during reward_video navigation
        assert driver.popup_dismissed_count == 1

    def test_step5_negative_guards_catch_out_of_order_and_missing_reward(self) -> None:
        """Verify negative ad logs (out-of-order stages and missing reward callback) are strictly rejected."""
        reversed_logs = [
            'I/AdSDK: {"stage": "IMPRESSION", "event": "AD_IMPRESSION", "ad_format": "banner", "placement_id": "p1", "request_id": "r1"}',
            'I/AdSDK: {"stage": "LOAD", "event": "AD_LOAD", "ad_format": "banner", "placement_id": "p1", "request_id": "r1"}',
        ]
        res_order = verify_ad_format(AdFormat.BANNER, reversed_logs)
        assert res_order["ok"] is False
        assert res_order["out_of_order"] == 1

        no_reward_logs = [
            'I/AdSDK: {"stage": "INIT", "event": "AD_INIT", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
            'I/AdSDK: {"stage": "LOAD", "event": "AD_LOAD", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
            'I/AdSDK: {"stage": "READY", "event": "AD_READY", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
            'I/AdSDK: {"stage": "RENDER", "event": "AD_RENDER", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
            'I/AdSDK: {"stage": "IMPRESSION", "event": "AD_IMPRESSION", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
            'I/AdSDK: {"stage": "CLOSE", "event": "AD_CLOSE", "ad_format": "reward_video", "placement_id": "p2", "request_id": "r2"}',
        ]
        res_reward = verify_ad_format("rewarded_video", no_reward_logs)
        assert res_reward["ok"] is False
        assert "REWARD" in res_reward["missing_stages"]
        assert res_reward["reward_verified"] is False
