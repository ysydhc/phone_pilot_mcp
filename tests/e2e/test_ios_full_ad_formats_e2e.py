#!/usr/bin/env python3
"""E2E Batch Runner & Pytest Suite: iOS 5 Ad Formats Full Verification.

支持单进程批量验证 iOS Demo App 的 5 大广告样式（Banner / Native / Interstitial / Rewarded Video / Splash）：
- 全程零硬编码像素坐标：基于动态元素查找 (`find_text` / `tap_text` / 元素 `bounds` 动态中心点计算)
- 支持环境变量 `IOS_AD_FORMATS` 过滤待验证样式（如 `IOS_AD_FORMATS="banner,interstitial"`，默认全部 5 种）
- 支持统一的系统日志 / 归因回调校验辅助函数与结构化耗时统计输出
- 既可作为独立脚本通过 `phone_run_script` / CLI 直接运行，也可通过 `pytest` 执行（无已启动 iOS 设备时优雅 `pytest.skip`）
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from phone_pilot.core.actions.ad_domain_actions import (  # noqa: E402
    AdDomainActions,
    MonotonicityChecker,
)
from phone_pilot.core.driver_factory import create_driver  # noqa: E402
from phone_pilot.core.ui.page import PageNode, PageSignature  # noqa: E402
from phone_pilot.mcp._page_state_tools import _collect_page_elements  # noqa: E402
from phone_pilot.script_api import ScriptContext, find_text  # noqa: E402

# ===========================================================================
# 1. 常量与 5 大广告样式规格定义
# ===========================================================================

DEFAULT_BUNDLE_ID = os.environ.get("IOS_DEMO_BUNDLE_ID", "com.baidujp.gbusdkdemo1")
DEFAULT_FORMAT_ORDER = ("banner", "native", "interstitial", "rewarded", "splash")


@dataclass(frozen=True)
class IOSAdFormatSpec:
    """iOS 单个广告样式的导航与断言配置（零硬编码坐标）。"""

    key: str
    entry_label: str
    sub_style_label: Optional[str]
    expected_render_keywords: tuple[str, ...]
    canonical_format: str
    back_labels: tuple[str, ...] = ("AdMasterSDK Demo", "Back", "返回", "Close", "Skip")


IOS_AD_FORMAT_SPECS: dict[str, IOSAdFormatSpec] = {
    "banner": IOSAdFormatSpec(
        key="banner",
        entry_label="Banner",
        sub_style_label="320x50",
        expected_render_keywords=("Install", "Banner", "Ad", "Learn More"),
        canonical_format="banner",
    ),
    "native": IOSAdFormatSpec(
        key="native",
        entry_label="Native",
        sub_style_label="Native Feed",
        expected_render_keywords=("Sponsored", "Install", "Learn More", "Native"),
        canonical_format="feed",
    ),
    "interstitial": IOSAdFormatSpec(
        key="interstitial",
        entry_label="Interstitial",
        sub_style_label="16:9 Video",
        expected_render_keywords=("Install", "Skip", "Close", "Interstitial"),
        canonical_format="interstitial",
    ),
    "rewarded": IOSAdFormatSpec(
        key="rewarded",
        entry_label="Rewarded Video",
        sub_style_label="Rewarded Video",
        expected_render_keywords=("Install", "Reward", "Claim", "Close"),
        canonical_format="reward_video",
    ),
    "splash": IOSAdFormatSpec(
        key="splash",
        entry_label="Splash",
        sub_style_label="Load & Show",
        expected_render_keywords=("Skip", "Splash", "Install", "Countdown"),
        canonical_format="splash",
    ),
}


def parse_selected_formats(env_value: Optional[str] = None) -> list[str]:
    """解析 `IOS_AD_FORMATS` 环境变量，默认返回全部 5 大广告样式。"""
    raw = env_value if env_value is not None else os.environ.get("IOS_AD_FORMATS", "")
    if not raw.strip():
        return list(DEFAULT_FORMAT_ORDER)

    alias_map = {
        "banner": "banner",
        "native": "native",
        "feed": "native",
        "interstitial": "interstitial",
        "rewarded": "rewarded",
        "reward_video": "rewarded",
        "rewarded_video": "rewarded",
        "splash": "splash",
    }
    selected: list[str] = []
    for token in raw.split(","):
        norm = token.strip().lower()
        if not norm:
            continue
        mapped = alias_map.get(norm)
        if mapped and mapped not in selected:
            selected.append(mapped)
    return selected or list(DEFAULT_FORMAT_ORDER)


def compute_dynamic_bounds_center(elem: dict[str, Any]) -> Optional[tuple[int, int]]:
    """从 UI 元素的 center 或 bounds 动态计算点击中心点（严禁写死像素坐标）。"""
    center = elem.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        return int(center[0]), int(center[1])

    bounds = elem.get("bounds")
    if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
        x1, y1, x2, y2 = float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])
        return int((x1 + x2) / 2.0), int((y1 + y2) / 2.0)

    if isinstance(bounds, dict):
        x = float(bounds.get("x", 0.0))
        y = float(bounds.get("y", 0.0))
        w = float(bounds.get("width", 0.0))
        h = float(bounds.get("height", 0.0))
        if w > 0 and h > 0:
            return int(x + w / 2.0), int(y + h / 2.0)

    return None


def detect_booted_ios_udid() -> Optional[str]:
    """检测当前已启动 (Booted) 的 iOS 模拟器或真机 UDID。"""
    env_udid = os.environ.get("IOS_DEVICE_UDID", "").strip()
    if env_udid:
        return env_udid

    try:
        proc = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                if "(Booted)" in line and "(" in line:
                    parts = [p.strip("()") for p in line.split("(") if ")" in p]
                    for part in parts:
                        if len(part) >= 24 and "-" in part:
                            return part
    except Exception:
        pass
    return None


# ===========================================================================
# 2. 统一系统日志 / 归因回调核验辅助器
# ===========================================================================


def fetch_ios_unified_logs(
    udid: str,
    bundle_id: str = DEFAULT_BUNDLE_ID,
    last_seconds: int = 30,
) -> list[str]:
    """从 iOS 模拟器采集最近 N 秒的系统日志（用于核验广告生命周期与归因上报）。"""
    if not udid:
        return []
    try:
        proc = subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "log",
                "show",
                "--style",
                "compact",
                "--last",
                f"{max(last_seconds, 5)}s",
                "--predicate",
                f'process CONTAINS "{bundle_id.split(".")[-1]}" OR eventMessage CONTAINS "AdSDK"',
            ],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return [line for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def verify_ios_ad_attribution_logs(
    fmt_key: str,
    log_lines: list[str],
    placement_id: Optional[str] = None,
) -> dict[str, Any]:
    """校验 iOS 广告生命周期单调性与归因上报（Impression / Click / Reward）。"""
    spec = IOS_AD_FORMAT_SPECS[fmt_key]
    ad_actions = AdDomainActions(log_fetcher=lambda: list(log_lines))
    return ad_actions.verify_ad_format(
        spec.canonical_format,
        placement_id=placement_id,
        required_uts_fields=MonotonicityChecker.DEFAULT_UTS_FIELDS,
    )


# ===========================================================================
# 3. iOS 批量巡检上下文与页面节点
# ===========================================================================


class IOSDemoHomePage(PageNode):
    """iOS Demo 首页认知节点。"""

    signature = PageSignature(
        name="IOSDemoHomePage",
        required_elements=["AdMasterSDK Demo"],
    )


@dataclass
class IOSBatchContext:
    """封装 ScriptContext 与 iOSDriver，提供动态文本查找、动态 bounds 中心点击与日志采集。"""

    udid: str
    bundle_id: str = DEFAULT_BUNDLE_ID
    driver: Any = None
    script_ctx: Optional[ScriptContext] = None
    step_timings: list[dict[str, Any]] = field(default_factory=list)
    log_fetcher: Optional[Callable[[], list[str]]] = None

    def __post_init__(self) -> None:
        if self.driver is None:
            self.driver = create_driver(self.udid, platform="ios")
        if self.script_ctx is None:
            self.script_ctx = ScriptContext(
                device_serial=self.udid,
                platform="ios",
                auto_screenshot=False,
                auto_meminfo=False,
                auto_report=False,
                popup_guard=False,
            )
            self.script_ctx._driver = self.driver

    def get_page_state(self) -> dict[str, Any]:
        if hasattr(self.driver, "get_page_state"):
            return self.driver.get_page_state()
        try:
            elements, scrollable, all_texts = _collect_page_elements(self.driver)
            return {"elements": elements, "scrollable": scrollable, "all_texts": all_texts}
        except Exception as exc:
            return {"elements": [], "error": str(exc)}

    def find_element_by_text(self, text: str) -> Optional[dict[str, Any]]:
        """优先通过 ScriptContext.find_text 或页面元素快照查找目标文本控件。"""
        state = self.get_page_state()
        for elem in state.get("elements", []):
            label = str(elem.get("label") or elem.get("text") or elem.get("resource_id") or "")
            if text.lower() in label.lower():
                return elem

        if self.script_ctx is not None:
            try:
                ui_elem = find_text(self.script_ctx, text, retry_attempts=1)
                if ui_elem is not None:
                    raw = getattr(ui_elem, "_raw", None) or {}
                    if isinstance(raw, dict) and raw:
                        return raw
            except Exception:
                pass
        return None

    def tap_text(self, text: str, timeout_s: float = 5.0) -> dict[str, Any]:
        """按文本查找元素并基于动态 bounds 计算中心坐标点击（零写死坐标）。"""
        deadline = time.monotonic() + max(timeout_s, 0.1)
        while True:
            elem = self.find_element_by_text(text)
            if elem is not None:
                center = compute_dynamic_bounds_center(elem)
                if center is not None:
                    cx, cy = center
                    res = self._tap_coords(cx, cy)
                    return {"ok": True, "tapped": text, "center": [cx, cy], "driver_res": res}
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        return {"ok": False, "error": f"Element with text '{text}' not found"}

    def tap_when_ready(self, text: str, timeout_s: float = 5.0) -> dict[str, Any]:
        return self.tap_text(text, timeout_s=timeout_s)

    def _tap_coords(self, x: int, y: int) -> dict[str, Any]:
        if hasattr(self.driver, "tap") and callable(self.driver.tap):
            return self.driver.tap(x, y)
        input_drv = self.driver.input() if callable(self.driver.input) else self.driver.input
        return input_drv.tap(x, y)

    def launch_app(self) -> bool:
        if hasattr(self.driver, "launch_app") and callable(self.driver.launch_app):
            res = self.driver.launch_app(self.bundle_id)
            return bool(res.get("ok", True)) if isinstance(res, dict) else bool(res)
        app_drv = self.driver.app() if callable(self.driver.app) else self.driver.app
        res = app_drv.launch_app(self.bundle_id)
        return bool(res.get("ok", True)) if isinstance(res, dict) else True

    def ensure_home(self) -> bool:
        home = IOSDemoHomePage(self)
        if home.is_current():
            return True
        for back_label in ("Back Home", "AdMasterSDK Demo", "Back", "返回", "Close"):
            res = self.tap_text(back_label, timeout_s=0.6)
            if res.get("ok") and home.is_current():
                return True
        return home.is_current()

    def collect_logs(self) -> list[str]:
        if self.log_fetcher is not None:
            return self.log_fetcher()
        if hasattr(self.driver, "log_buffer"):
            return list(self.driver.log_buffer)
        return fetch_ios_unified_logs(self.udid, self.bundle_id)


# ===========================================================================
# 4. 单进程 5 大广告样式批量执行器
# ===========================================================================


def run_ios_ad_formats_batch(
    ctx: IOSBatchContext,
    formats: Optional[list[str]] = None,
) -> dict[str, Any]:
    """在单次进程内批量执行指定（或全部 5 种）iOS 广告样式的导航、渲染断言与日志归因核验。"""
    target_formats = formats or parse_selected_formats()
    batch_start = time.monotonic()
    ctx.launch_app()

    results: dict[str, dict[str, Any]] = {}
    all_ok = True

    for fmt_key in target_formats:
        spec = IOS_AD_FORMAT_SPECS[fmt_key]
        t0 = time.monotonic()
        ctx.ensure_home()

        nav_res = ctx.tap_text(spec.entry_label, timeout_s=4.0)
        if not nav_res.get("ok"):
            all_ok = False
            results[fmt_key] = {
                "ok": False,
                "stage": "entry_navigation",
                "error": nav_res.get("error"),
                "elapsed_s": round(time.monotonic() - t0, 3),
            }
            continue

        if spec.sub_style_label:
            ctx.tap_text(spec.sub_style_label, timeout_s=2.0)

        # 校验广告渲染关键词
        state = ctx.get_page_state()
        elem_labels = [
            str(e.get("label") or e.get("text") or "")
            for e in state.get("elements", [])
        ]
        rendered = any(
             any(kw.lower() in lbl.lower() for lbl in elem_labels)
            for kw in spec.expected_render_keywords
        )

        # 校验系统日志与归因（若存在日志流则验证单调性）
        logs = ctx.collect_logs()
        log_verification: Optional[dict[str, Any]] = None
        if logs:
            log_verification = verify_ios_ad_attribution_logs(
                fmt_key,
                logs,
                placement_id=f"plc_{spec.canonical_format}_001",
            )
            fmt_ok = rendered and bool(log_verification.get("ok", False))
        else:
            fmt_ok = rendered

        if not fmt_ok:
            all_ok = False

        elapsed = round(time.monotonic() - t0, 3)
        step_record = {
            "ok": fmt_ok,
            "format": fmt_key,
            "rendered": rendered,
            "log_verification": log_verification,
            "elapsed_s": elapsed,
        }
        results[fmt_key] = step_record
        ctx.step_timings.append(step_record)

        # 返回首页准备下一个样式
        ctx.ensure_home()

    total_elapsed = round(time.monotonic() - batch_start, 3)
    return {
        "ok": all_ok,
        "formats_tested": target_formats,
        "total_elapsed_s": total_elapsed,
        "results": results,
    }


# ===========================================================================
# 5. Pytest 测试套件（含离线契约验证 + 真机/模拟器按需跳过）
# ===========================================================================


class SimulatedIOSAdDemoDriver:
    """用于单测与离线回归的确定性 iOS 5 样式模拟器驱动（零硬编码坐标验证）。"""

    def __init__(self) -> None:
        self.current_page = "home"
        self.tapped_points: list[tuple[int, int]] = []
        self.log_buffer: list[str] = []

    def launch_app(self, bundle_id: str) -> dict[str, Any]:
        self.current_page = "home"
        return {"ok": True, "bundle_id": bundle_id}

    def get_page_state(self) -> dict[str, Any]:
        if self.current_page == "home":
            return {
                "ok": True,
                "elements": [
                    {"index": 0, "label": "AdMasterSDK Demo", "bounds": [0, 0, 390, 88]},
                    {"index": 1, "label": "Banner", "bounds": [20, 120, 370, 170]},
                    {"index": 2, "label": "Native", "bounds": [20, 190, 370, 240]},
                    {"index": 3, "label": "Interstitial", "bounds": [20, 260, 370, 310]},
                    {"index": 4, "label": "Rewarded Video", "bounds": [20, 330, 370, 380]},
                    {"index": 5, "label": "Splash", "bounds": [20, 400, 370, 450]},
                ],
            }

        render_text_map = {
            "banner": ("Banner 320x50", "Install"),
            "native": ("Sponsored Native Feed", "Learn More"),
            "interstitial": ("Interstitial 16:9 Video", "Install"),
            "rewarded": ("Rewarded Video Playing", "Claim Reward"),
            "splash": ("Splash Countdown 5s", "Skip"),
        }
        headline, cta = render_text_map[self.current_page]
        return {
            "ok": True,
            "elements": [
                {"index": 0, "label": headline, "bounds": [20, 150, 370, 260]},
                {"index": 1, "label": cta, "bounds": [80, 500, 310, 560]},
                {"index": 2, "label": "Back Home", "bounds": [16, 44, 120, 84]},
            ],
        }

    def tap(self, x: int, y: int) -> dict[str, Any]:
        self.tapped_points.append((x, y))
        if self.current_page == "home":
            y_band_to_fmt = {
                145: "banner",
                215: "native",
                285: "interstitial",
                355: "rewarded",
                425: "splash",
            }
            for center_y, fmt_key in y_band_to_fmt.items():
                if abs(y - center_y) <= 25:
                    self.current_page = fmt_key
                    self._emit_logs(fmt_key)
                    return {"ok": True, "navigated_to": fmt_key}
        else:
            if 16 <= x <= 120 and 44 <= y <= 84:
                self.current_page = "home"
                return {"ok": True, "navigated_to": "home"}
        return {"ok": True}

    def _emit_logs(self, fmt_key: str) -> None:
        canonical = IOS_AD_FORMAT_SPECS[fmt_key].canonical_format
        stages_map = {
            "banner": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION"),
            "feed": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLICK"),
            "interstitial": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLOSE"),
            "reward_video": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "REWARD", "CLOSE"),
            "splash": ("INIT", "LOAD", "READY", "RENDER", "IMPRESSION", "CLOSE"),
        }
        self.log_buffer.clear()
        for idx, stage in enumerate(stages_map[canonical]):
            self.log_buffer.append(
                f'2026-10-29 12:00:0{idx}.000 AdSDK[{idx}] {{"stage": "{stage}", '
                f'"event": "AD_{stage}", "ad_format": "{canonical}", '
                f'"placement_id": "plc_{canonical}_001", "request_id": "req_{canonical}_01"}}'
            )


class TestIOSFullAdFormatsE2E:
    """iOS 5 大广告样式批量巡检与环境过滤测试套件。"""

    def test_parse_selected_formats_and_dynamic_bounds(self) -> None:
        """验证环境变量 IOS_AD_FORMATS 过滤解析及 bounds 动态中心计算。"""
        assert parse_selected_formats("") == [
            "banner",
            "native",
            "interstitial",
            "rewarded",
            "splash",
        ]
        assert parse_selected_formats("banner, reward_video , splash") == [
            "banner",
            "rewarded",
            "splash",
        ]
        assert compute_dynamic_bounds_center({"bounds": [20, 120, 370, 170]}) == (195, 145)
        assert compute_dynamic_bounds_center(
            {"bounds": {"x": 16, "y": 44, "width": 104, "height": 40}}
        ) == (68, 64)

    def test_batch_runner_all_five_formats_deterministic(self) -> None:
        """验证单进程批处理跑通全部 5 种 iOS 广告样式（零写死坐标 + 日志单调性校验）。"""
        sim_driver = SimulatedIOSAdDemoDriver()
        ctx = IOSBatchContext(
            udid="SIMULATED-IOS-UDID",
            driver=sim_driver,
        )
        summary = run_ios_ad_formats_batch(ctx)
        assert summary["ok"] is True
        assert summary["formats_tested"] == [
            "banner",
            "native",
            "interstitial",
            "rewarded",
            "splash",
        ]
        for fmt_key in summary["formats_tested"]:
            assert summary["results"][fmt_key]["ok"] is True
            assert summary["results"][fmt_key]["rendered"] is True
            assert summary["results"][fmt_key]["log_verification"]["ok"] is True

    def test_live_ios_device_full_ad_formats_e2e(self) -> None:
        """真机/模拟器实机 E2E 门禁：当无 Booted iOS 设备时优雅 skip。"""
        udid = detect_booted_ios_udid()
        if not udid:
            pytest.skip("No booted iOS device available")

        # 仅在显式开启 IOS_LIVE_E2E=1 时对真实模拟器执行点击流转，防止误触开发中模拟器
        if os.environ.get("IOS_LIVE_E2E", "0") != "1":
            pytest.skip("Set IOS_LIVE_E2E=1 to execute live iOS simulator UI automation")

        ctx = IOSBatchContext(udid=udid, bundle_id=DEFAULT_BUNDLE_ID)
        summary = run_ios_ad_formats_batch(ctx, formats=parse_selected_formats())
        assert summary["ok"] is True, f"iOS full ad formats E2E failed: {summary}"


def main() -> int:
    """CLI / phone_run_script 入口。"""
    udid = detect_booted_ios_udid()
    if not udid:
        print("ℹ️ No booted iOS device found; running deterministic self-check...")
        sim_driver = SimulatedIOSAdDemoDriver()
        ctx = IOSBatchContext(udid="SIMULATED-IOS-UDID", driver=sim_driver)
    else:
        ctx = IOSBatchContext(udid=udid, bundle_id=DEFAULT_BUNDLE_ID)

    selected = parse_selected_formats()
    print("=================================================================")
    print(f"🚀 iOS 5 大广告样式批量 E2E 巡检启动: {selected}")
    print(f"📱 UDID: {ctx.udid} | 📦 Bundle: {ctx.bundle_id}")
    print("=================================================================")

    summary = run_ios_ad_formats_batch(ctx, formats=selected)
    for fmt_key, item in summary["results"].items():
        status = "✓ PASS" if item["ok"] else "❌ FAIL"
        print(f"  [{status}] {fmt_key:<14} elapsed={item['elapsed_s']:.3f}s rendered={item.get('rendered')}")

    print("-----------------------------------------------------------------")
    print(f"🏁 总耗时: {summary['total_elapsed_s']:.3f}s | 结论: {'PASS' if summary['ok'] else 'FAIL'}")
    print("=================================================================")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
