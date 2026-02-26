import os
from pathlib import Path
from typing import Optional, Tuple

import pytest

from phone_pilot.android.adb.parsers import parse_adb_devices
from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.adb import screenshot as adb_screenshot
from phone_pilot.android.ui.automator import dump_ui_xml, parse_uiautomator_nodes
from phone_pilot.extensions.vision.template import find_template_on_screen_with_fallback


RESOURCE_DIR = Path(__file__).resolve().parents[1] / "phone_pilot" / "resource"


def _list_item_templates() -> list[Path]:
    return sorted(RESOURCE_DIR.glob("item*.png"))


def _parse_bounds(bounds: Optional[str]) -> Optional[Tuple[int, int, int, int]]:
    if not bounds:
        return None
    try:
        left = bounds.index("[")
        mid = bounds.index("][")
        right = bounds.index("]", mid + 2)
        a = bounds[left + 1 : mid]
        b = bounds[mid + 2 : right]
        x1, y1 = [int(v) for v in a.split(",")]
        x2, y2 = [int(v) for v in b.split(",")]
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2
    except Exception:
        return None


def _to_roi(bounds: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = bounds
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def _get_device_serial() -> Optional[str]:
    env_serial = os.environ.get("ANDROID_SERIAL")
    if env_serial:
        return env_serial
    try:
        proc = CommandRunner.run(
            ["adb", "devices", "-l"],
            check=False,
            delay_s=0.0,
            log_output=False,
        )
    except Exception:
        return None
    devices = parse_adb_devices(proc.stdout or "")
    for dev in devices:
        if dev.get("state") == "device":
            return dev.get("serial")
    return None


def _with_screenshot_bytes(png_bytes: bytes):
    original = adb_screenshot.take_screenshot_png_bytes

    def _fake_take_screenshot_png_bytes(device_serial: Optional[str] = None) -> bytes:
        return png_bytes

    adb_screenshot.take_screenshot_png_bytes = _fake_take_screenshot_png_bytes
    return original


def test_item_templates_on_device_stdout() -> None:
    templates = _list_item_templates()
    if not templates:
        pytest.skip("no item*.png templates under phone_pilot/resource")

    device_serial = _get_device_serial()
    if not device_serial:
        pytest.skip("no ANDROID_SERIAL and no adb device detected")

    dump = dump_ui_xml(device_serial, compressed=True)
    if not dump.get("ok"):
        pytest.skip(f"uiautomator dump failed: {dump}")

    nodes = parse_uiautomator_nodes(dump.get("xml", ""))
    candidates = []
    for n in nodes:
        if n.checkable is True and n.bounds:
            bounds = _parse_bounds(n.bounds)
            if bounds:
                candidates.append((n, bounds))

    if not candidates:
        pytest.skip("no checkable nodes found on screen")

    candidates = candidates[:20]
    screenshot_bytes = adb_screenshot.take_screenshot_png_bytes(device_serial)
    original = _with_screenshot_bytes(screenshot_bytes)
    try:
        print("\n[DEVICE] item template verification")
        for idx, (node, bounds) in enumerate(candidates):
            roi = _to_roi(bounds)
            best = None
            for tpl in templates:
                res = find_template_on_screen_with_fallback(
                    device_serial,
                    template_path=str(tpl),
                    threshold=0.80,
                    roi=roi,
                    max_results=1,
                )
                matches = res.get("matches") or []
                top = matches[0] if matches else None
                score = float(top.get("score")) if top else 0.0
                if best is None or score > best["score"]:
                    best = {
                        "template": tpl.name,
                        "score": score,
                        "match": top,
                        "method": res.get("method_used"),
                    }
            print(
                f"- item[{idx}] bounds={bounds} checked={node.checked} "
                f"best_template={best['template']} score={best['score']:.3f} "
                f"method={best.get('method')} match={best.get('match')}"
            )
    finally:
        adb_screenshot.take_screenshot_png_bytes = original


def test_item_templates_on_demo_image_stdout() -> None:
    templates = _list_item_templates()
    if not templates:
        pytest.skip("no item*.png templates under phone_pilot/resource")

    demo_path = RESOURCE_DIR / "test_item_demo.png"
    if not demo_path.exists():
        pytest.skip("test_item_demo.png not found")

    demo_bytes = demo_path.read_bytes()
    original = _with_screenshot_bytes(demo_bytes)
    try:
        print("\n[DEMO] test_item_demo.png template verification")
        for tpl in templates:
            res = find_template_on_screen_with_fallback(
                "DEMO",
                template_path=str(tpl),
                threshold=0.80,
                max_results=1,
            )
            matches = res.get("matches") or []
            top = matches[0] if matches else None
            score = float(top.get("score")) if top else 0.0
            print(
                f"- template={tpl.name} score={score:.3f} "
                f"method={res.get('method_used')} match={top}"
            )
    finally:
        adb_screenshot.take_screenshot_png_bytes = original
