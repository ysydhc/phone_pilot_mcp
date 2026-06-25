"""
Tests for iOS driver skeleton (A1 TDD anchors).

T1: test_ios_package_importable - iOSDriver importable, platform == "ios"
T2: test_idriver_has_all_42_entries - all 42 protocol entries exist and are callable
T3: test_stub_methods_do_not_raise - sample dict-returning stubs don't raise
"""

from __future__ import annotations

import inspect
import pytest


# ---------------------------------------------------------------------------
# T1: Package importable
# ---------------------------------------------------------------------------


def test_ios_package_importable():
    """iOSDriver is importable and platform returns 'ios'."""
    from phone_pilot.ios import iOSDriver

    drv = iOSDriver("test-udid")
    assert drv.platform == "ios"
    assert drv.device_serial == "test-udid"


# ---------------------------------------------------------------------------
# T2: All 42 entries exist and are callable
# ---------------------------------------------------------------------------


def _get_protocol_entries():
    """Enumerate all method/property names from protocols.py."""
    from phone_pilot.core.protocols import (
        InputDriver,
        ScreenDriver,
        UIDriver,
        AppDriver,
        DeviceDriver,
    )

    entries = set()

    for proto in (InputDriver, ScreenDriver, UIDriver, AppDriver, DeviceDriver):
        for name, member in inspect.getmembers(proto):
            # Skip private/dunder members
            if name.startswith("_"):
                continue
            # Skip non-callable non-property annotations (skip class vars)
            if callable(member) or isinstance(
                inspect.getattr_static(proto, name, None), property
            ):
                entries.add(name)

    return entries


def test_idriver_has_all_42_entries():
    """All 42 protocol entries exist on iOSDriver instance and sub-drivers."""
    from phone_pilot.ios.driver import (
        iOSDriver,
    )

    drv = iOSDriver("test-udid")

    # Sub-driver method tables from protocols.py (no isinstance assertions)
    input_methods = ("tap", "swipe", "long_press", "input_text", "keyevent")
    screen_methods = (
        "screenshot",
        "get_screen_size",
        "start_screenrecord",
        "stop_screenrecord",
    )
    ui_methods = (
        "dump_ui_hierarchy",
        "dump_ui_nodes",
        "collect_all_texts",
        "find_elements",
        "get_current_activity",
    )
    app_methods = (
        "list_packages",
        "launch_app",
        "force_stop",
        "clear_data",
        "uninstall",
    )
    device_properties = ("input", "screen", "ui", "app", "device_serial", "platform")
    device_methods = (
        "go_home",
        "go_back",
        "unlock",
        "lock_screen",
        "clear_background",
        "device_info",
        "open_deeplink",
        "launch_from_home",
        "dump_memory_profile",
        "push_file",
        "pull_file",
        "remove_remote_file",
        "read_log",
        "clear_log",
        "dump_log",
        "start_log_capture",
        "stop_log_capture",
    )

    # Check iOSDriver top-level
    for name in device_properties:
        assert hasattr(drv, name), f"iOSDriver missing property/attribute: {name}"
    for name in device_methods:
        assert callable(getattr(drv, name)), f"iOSDriver missing callable: {name}"

    # Check sub-drivers are accessible
    inp = drv.input
    scr = drv.screen
    ui = drv.ui
    app = drv.app

    for name in input_methods:
        assert callable(getattr(inp, name)), f"iOSInputDriver missing: {name}"
    for name in screen_methods:
        assert callable(getattr(scr, name)), f"iOSScreenDriver missing: {name}"
    for name in ui_methods:
        assert callable(getattr(ui, name)), f"iOSUIDriver missing: {name}"
    for name in app_methods:
        assert callable(getattr(app, name)), f"iOSAppDriver missing: {name}"

    # Verify no isinstance - just check sub-driver class names
    assert type(inp).__name__ == "iOSInputDriver"
    assert type(scr).__name__ == "iOSScreenDriver"
    assert type(ui).__name__ == "iOSUIDriver"
    assert type(app).__name__ == "iOSAppDriver"


# ---------------------------------------------------------------------------
# T3: Stub methods do not raise
# ---------------------------------------------------------------------------


def test_stub_methods_do_not_raise(monkeypatch):
    """Every entry returns its declared type and never raises, with all external
    boundaries (WDA / simctl / pymobiledevice3) mocked so the check is hermetic."""
    import subprocess
    from unittest.mock import MagicMock
    from phone_pilot.ios import driver as drv_mod
    from phone_pilot.ios.pmd import utils as pmd_utils
    from phone_pilot.ios.device import log_capture

    # WDA calls → run fn against a MagicMock client (no network). Mirror the
    # real with_reconnect contract: exceptions become an error dict, never raise.
    def _fake_wda_call(serial, fn):
        try:
            return fn(MagicMock())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "wda_mock_error", "detail": str(exc)}

    monkeypatch.setattr(drv_mod, "_wda_call", _fake_wda_call)
    # Form factor / discovery → no subprocess.
    monkeypatch.setattr(pmd_utils, "detect_form_factor", lambda s: "real")
    monkeypatch.setattr(pmd_utils, "pmd_executable", lambda: "pmd")
    monkeypatch.setattr(pmd_utils, "simctl_prefix", lambda: ["simctl"])
    fake = subprocess.CompletedProcess(["mock"], 0, "{}", "")
    monkeypatch.setattr("phone_pilot.ios.driver.pmd_runner.run", lambda *a, **k: fake)
    monkeypatch.setattr(log_capture, "start_log_capture", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(log_capture, "stop_log_capture", lambda *a, **k: {"ok": True})
    # launch_from_home delegates to finder, which uses wda_bridge directly.
    monkeypatch.setattr(
        drv_mod.finder, "launch_from_home", lambda *a, **k: {"ok": True}
    )

    from phone_pilot.ios.driver import iOSDriver

    drv = iOSDriver("test-udid")

    # InputDriver stubs
    result = drv.input.tap(100, 200)
    assert isinstance(result, dict)

    result = drv.input.swipe(0, 0, 100, 100)
    assert isinstance(result, dict)

    result = drv.input.input_text("hello")
    assert isinstance(result, dict)

    result = drv.input.keyevent("home")
    assert isinstance(result, dict)

    # ScreenDriver stubs
    screenshot = drv.screen.screenshot()
    assert isinstance(screenshot, bytes)

    size = drv.screen.get_screen_size()
    assert isinstance(size, tuple)
    assert len(size) == 2

    result = drv.screen.start_screenrecord("/tmp/test.mp4")
    assert isinstance(result, dict)

    result = drv.screen.stop_screenrecord()
    assert isinstance(result, dict)

    # UIDriver stubs
    hierarchy = drv.ui.dump_ui_hierarchy()
    assert isinstance(hierarchy, str)

    nodes = drv.ui.dump_ui_nodes()
    assert isinstance(nodes, list)

    texts = drv.ui.collect_all_texts()
    assert isinstance(texts, list)

    elements = drv.ui.find_elements({"text": "test"})
    assert isinstance(elements, list)

    activity = drv.ui.get_current_activity()
    assert isinstance(activity, dict)

    # AppDriver stubs
    packages = drv.app.list_packages()
    assert isinstance(packages, list)

    result = drv.app.launch_app("com.example.app")
    assert isinstance(result, dict)

    result = drv.app.force_stop("com.example.app")
    assert isinstance(result, dict)

    result = drv.app.clear_data("com.example.app")
    assert isinstance(result, dict)

    result = drv.app.uninstall("com.example.app")
    assert isinstance(result, dict)

    # DeviceDriver device-level stubs
    result = drv.go_home()
    assert isinstance(result, dict)

    result = drv.go_back()
    assert isinstance(result, dict)

    result = drv.unlock()
    assert isinstance(result, dict)

    result = drv.lock_screen()
    assert isinstance(result, dict)

    result = drv.clear_background()
    assert isinstance(result, dict)

    result = drv.device_info()
    assert isinstance(result, dict)

    result = drv.open_deeplink("myapp://test")
    assert isinstance(result, dict)

    result = drv.launch_from_home("TestApp")
    assert isinstance(result, dict)

    result = drv.dump_memory_profile("com.example.app")
    assert isinstance(result, dict)

    result = drv.push_file("/local/path", "/remote/path")
    assert isinstance(result, dict)

    result = drv.pull_file("/remote/path", "/local/path")
    assert isinstance(result, dict)

    result = drv.remove_remote_file("/remote/path")
    assert isinstance(result, dict)

    log = drv.read_log()
    assert isinstance(log, str)

    result = drv.clear_log()
    assert isinstance(result, dict)

    result = drv.dump_log("/tmp/log.txt")
    assert isinstance(result, dict)

    result = drv.start_log_capture("/tmp/capture.log")
    assert isinstance(result, dict)

    result = drv.stop_log_capture("/tmp/capture.log")
    assert isinstance(result, dict)


def test_empty_serial_raises_value_error():
    """iOSDriver with empty serial raises ValueError."""
    from phone_pilot.ios.driver import iOSDriver

    with pytest.raises(ValueError, match="device_serial"):
        iOSDriver("")
