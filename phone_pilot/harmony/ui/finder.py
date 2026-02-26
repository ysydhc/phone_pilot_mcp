"""
HarmonyOS UI finding and interaction (powered by hmdriver2).

Provides launch_from_home implementation that mirrors
the Android UI finder interface.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from phone_pilot.harmony.hmdriver_bridge import get_hmdriver
from phone_pilot.harmony.ui.automator import (
    dump_hierarchy_dict,
    parse_hierarchy_nodes,
    find_nodes,
)

logger = logging.getLogger(__name__)


def launch_from_home(
    device_serial: str,
    app_name: str,
    *,
    reset_home: bool = True,
    max_swipes: int = 5,
    timeout_s: float = 15.0,
) -> dict:
    """
    Find and launch an app from the home screen.

    Uses three strategies in order:
    1. hmdriver2 native text selector on home screen
    2. UI hierarchy dump + node search
    3. Fallback: resolve bundle name from installed apps → start_app()

    Args:
        device_serial: Device serial.
        app_name: App display name to search for.
        reset_home: Whether to press HOME first.
        max_swipes: Max swipes to find the app.
        timeout_s: Max search time.

    Returns:
        {"ok": True, ...} on success.
    """
    hm = get_hmdriver(device_serial)

    if reset_home:
        hm.go_home()
        time.sleep(0.8)
        hm.go_home()
        # Wait for home screen to fully render (animation completes)
        time.sleep(1.0)

    deadline = time.monotonic() + timeout_s

    for swipe_idx in range(max_swipes + 1):
        if time.monotonic() > deadline:
            break

        # Strategy 1: Use hmdriver2 native text selector
        try:
            ui_obj = hm(text=app_name)
            if ui_obj.exists():
                ui_obj.click()
                time.sleep(1.0)
                return {
                    "ok": True,
                    "app_name": app_name,
                    "swipes": swipe_idx,
                    "engine": "hmdriver2",
                }
        except Exception as exc:
            logger.debug("hmdriver2 text selector failed (swipe %d): %s", swipe_idx, exc)

        # Strategy 2: Dump hierarchy and search
        try:
            hierarchy = dump_hierarchy_dict(device_serial)
            nodes = parse_hierarchy_nodes(hierarchy)
            matches = find_nodes(nodes, app_name, field="text", exact=False, limit=1)
            if matches:
                cx, cy = matches[0].center()
                if cx > 0 and cy > 0:
                    hm.click(cx, cy)
                    time.sleep(1.0)
                    return {
                        "ok": True,
                        "app_name": app_name,
                        "x": cx, "y": cy,
                        "swipes": swipe_idx,
                        "engine": "hierarchy_search",
                    }
        except Exception as exc:
            logger.debug("Hierarchy search failed (swipe %d): %s", swipe_idx, exc)

        # Swipe to next page
        if swipe_idx < max_swipes:
            try:
                w, h = hm.display_size
                hm.swipe(int(w * 0.8), int(h * 0.5), int(w * 0.2), int(h * 0.5))
                time.sleep(1.0)
            except Exception:
                pass

    # Strategy 3 (Fallback): Resolve bundle name and use start_app()
    logger.info("Icon not found on home screen, trying start_app fallback for '%s'", app_name)
    bundle = _resolve_bundle_name(hm, app_name)
    if bundle:
        try:
            hm.start_app(bundle)
            time.sleep(1.0)
            return {
                "ok": True,
                "app_name": app_name,
                "bundle": bundle,
                "engine": "start_app_fallback",
            }
        except Exception as exc:
            logger.warning("start_app(%s) failed: %s", bundle, exc)
            return {"ok": False, "app_name": app_name, "error": f"start_app failed: {exc}"}

    return {"ok": False, "app_name": app_name, "error": "app_not_found"}


def _resolve_bundle_name(hm, app_name: str) -> Optional[str]:
    """
    Try to find a bundle name matching the given display name.

    Uses a name-to-bundle heuristic: searches installed app list
    for bundles whose names contain the app name keywords.
    """
    # Common well-known app name → bundle mappings
    _KNOWN_APPS = {
        "抖音": "com.ss.hm.ugc.aweme",
        "微信": "com.tencent.wechat",
        "QQ": "com.tencent.qq",
        "支付宝": "com.eg.android.AlipayGphone",
        "淘宝": "com.taobao.taobao4hmos",
        "京东": "com.jd.app.reader.hos",
        "百度": "com.baidu.infoflow",
        "高德地图": "com.amap.android",
        "哔哩哔哩": "yylx.danmaku.bili",
        "今日头条": "com.ss.hm.article.news",
        "美团": "com.meituan.app",
    }

    if app_name in _KNOWN_APPS:
        return _KNOWN_APPS[app_name]

    # Try to match via list_apps + get_app_info
    try:
        all_apps = hm.list_apps()
        # Simple heuristic: check if the app name appears in the bundle name
        name_lower = app_name.lower()
        for bundle in all_apps:
            if name_lower in str(bundle).lower():
                return str(bundle)
    except Exception:
        pass

    return None


__all__ = [
    "launch_from_home",
]
