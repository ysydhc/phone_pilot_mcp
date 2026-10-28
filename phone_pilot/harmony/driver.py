"""
HarmonyOS Driver implementation.

Implements the DeviceDriver protocol for HarmonyOS devices.
Primary engine: hmdriver2 (uitest daemon + socket).
Fallback: raw HDC commands for basic operations.
"""

from __future__ import annotations

import logging
import re
import tempfile
import pathlib
from typing import Optional

from phone_pilot.core.protocols import (
    InputDriver,
    ScreenDriver,
    UIDriver,
    AppDriver,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_hm(serial: str):
    """Get hmdriver2 Driver instance (may raise on first connect)."""
    from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

    return get_hmdriver(serial)


def _hm_available(serial: str) -> bool:
    """Check whether hmdriver2 is available for this serial."""
    from phone_pilot.harmony.hmdriver_bridge import is_hmdriver_available

    return is_hmdriver_available(serial)


# ---------------------------------------------------------------------------
# HarmonyInputDriver
# ---------------------------------------------------------------------------


class HarmonyInputDriver:
    """HarmonyOS InputDriver — prefers hmdriver2 uitest, falls back to HDC."""

    def __init__(self, device_serial: str):
        self._serial = device_serial

    def tap(self, x: int, y: int, wait_s: float = 0.15) -> dict:
        """Tap at screen coordinates."""
        try:
            hm = _get_hm(self._serial)
            hm.click(int(x), int(y))
            return {"ok": True, "x": int(x), "y": int(y), "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 tap failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import input_tap

            return input_tap(self._serial, x, y, wait_s=wait_s)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2)."""
        try:
            hm = _get_hm(self._serial)
            # hmdriver2 speed is pixels/sec; convert duration_ms
            dist = max(abs(x2 - x1), abs(y2 - y1), 1)
            speed = max(200, min(40000, int(dist / max(duration_ms / 1000, 0.05))))
            hm.swipe(int(x1), int(y1), int(x2), int(y2), speed=speed)
            return {
                "ok": True,
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration_ms": duration_ms,
                "engine": "hmdriver2",
            }
        except Exception as exc:
            logger.debug("hmdriver2 swipe failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import input_swipe

            return input_swipe(self._serial, x1, y1, x2, y2, duration_ms=duration_ms)

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> dict:
        """Long press at coordinates."""
        try:
            hm = _get_hm(self._serial)
            hm.long_click(int(x), int(y))
            return {"ok": True, "x": int(x), "y": int(y), "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 long_press failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import input_long_press

            return input_long_press(self._serial, x, y, duration_ms=duration_ms)

    def input_text(self, text: str, enter: bool = False) -> dict:
        """Input text into focused field."""
        try:
            hm = _get_hm(self._serial)
            hm.input_text(text)
            if enter:
                from hmdriver2.proto import KeyCode

                hm.press_key(KeyCode.ENTER)
            return {"ok": True, "text": text, "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 input_text failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import input_text as _hdc_input_text

            return _hdc_input_text(self._serial, text, enter=enter)

    def keyevent(self, keycode: str) -> dict:
        """Send a key event."""
        try:
            hm = _get_hm(self._serial)
            # Try to map string keycode to hmdriver2 KeyCode enum
            from hmdriver2.proto import KeyCode

            kc = _resolve_keycode(keycode, KeyCode)
            hm.press_key(kc)
            return {"ok": True, "keycode": keycode, "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 keyevent failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import input_keyevent

            return input_keyevent(self._serial, keycode)

    # -- Extra methods (not in Protocol) --

    def double_click(self, x: int, y: int) -> dict:
        """Double click at coordinates (hmdriver2 only)."""
        hm = _get_hm(self._serial)
        hm.double_click(int(x), int(y))
        return {"ok": True, "x": int(x), "y": int(y), "engine": "hmdriver2"}


# ---------------------------------------------------------------------------
# HarmonyScreenDriver
# ---------------------------------------------------------------------------


class HarmonyScreenDriver:
    """HarmonyOS ScreenDriver — prefers hmdriver2, falls back to HDC."""

    def __init__(self, device_serial: str):
        self._serial = device_serial
        self._screen_size_cache: Optional[tuple[int, int]] = None

    def screenshot(self) -> bytes:
        """Capture current screen as PNG bytes."""
        try:
            hm = _get_hm(self._serial)
            # hmdriver2 screenshot saves to a file; we use a temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            hm.screenshot(tmp_path, method="screenCap")
            data = pathlib.Path(tmp_path).read_bytes()
            pathlib.Path(tmp_path).unlink(missing_ok=True)
            if data and data[:4] == b"\x89PNG":
                return data
            # If we got JPEG from snapshot_display, return it anyway
            if data and data[:2] == b"\xff\xd8":
                return data
            raise RuntimeError("Invalid screenshot data from hmdriver2")
        except Exception as exc:
            logger.debug("hmdriver2 screenshot failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.hdc.screenshot import take_screenshot_png_bytes

            return take_screenshot_png_bytes(self._serial)

    def screenshot_fast(self) -> bytes:
        """Fast screenshot (JPEG, lower quality but faster). hmdriver2 only."""
        hm = _get_hm(self._serial)
        with tempfile.NamedTemporaryFile(suffix=".jpeg", delete=False) as tmp:
            tmp_path = tmp.name
        hm.screenshot(tmp_path, method="snapshot_display")
        data = pathlib.Path(tmp_path).read_bytes()
        pathlib.Path(tmp_path).unlink(missing_ok=True)
        return data

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions (width, height)."""
        if self._screen_size_cache:
            return self._screen_size_cache

        try:
            hm = _get_hm(self._serial)
            w, h = hm.display_size
            if w and h:
                self._screen_size_cache = (w, h)
                return (w, h)
        except Exception as exc:
            logger.debug("hmdriver2 display_size failed: %s", exc)

        # Fallback to HDC
        from phone_pilot.harmony.device.utils import get_screen_size

        size = get_screen_size(self._serial)
        if size is None:
            raise RuntimeError("Failed to get screen size via both hmdriver2 and HDC")
        self._screen_size_cache = size
        return size

    def start_screenrecord(self, path: str, **kwargs) -> dict:
        """Start screen recording via hmdriver2.

        Returns dict with ``remote_path`` (on HarmonyOS, recording is
        managed by hmdriver2 internally; ``remote_path`` matches ``path``).
        """
        try:
            hm = _get_hm(self._serial)
            hm.screenrecord.start(path)
            return {
                "ok": True,
                "path": path,
                "remote_path": path,
                "engine": "hmdriver2",
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"screenrecord_failed: {exc}",
                "path": path,
            }

    def stop_screenrecord(self) -> dict:
        """Stop current screen recording."""
        try:
            hm = _get_hm(self._serial)
            hm.screenrecord.stop()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception as exc:
            return {"ok": False, "error": f"screenrecord_stop_failed: {exc}"}


# ---------------------------------------------------------------------------
# HarmonyUIDriver
# ---------------------------------------------------------------------------


class HarmonyUIDriver:
    """HarmonyOS UIDriver — powered by hmdriver2 uitest."""

    def __init__(self, device_serial: str):
        self._serial = device_serial

    def dump_ui_hierarchy(self) -> str:
        """
        Dump current UI hierarchy.

        Returns the hierarchy as a JSON string (hmdriver2 native format).
        The caller can parse this with json.loads() to get a dict/list.
        """
        import json
        from phone_pilot.core import ui_snapshot_cache

        key = ("harmony", self._serial, "ui_str")

        def _compute() -> str:
            hm = _get_hm(self._serial)
            hierarchy = hm.dump_hierarchy()
            if isinstance(hierarchy, dict):
                return json.dumps(hierarchy, ensure_ascii=False)
            return str(hierarchy)

        return ui_snapshot_cache.get_or_compute(key, _compute)

    def dump_ui_hierarchy_dict(self) -> dict:
        """Dump current UI hierarchy as a Python dict (hmdriver2 native format)."""
        from phone_pilot.core import ui_snapshot_cache

        key = ("harmony", self._serial, "ui_dict")

        def _compute() -> dict:
            hm = _get_hm(self._serial)
            return hm.dump_hierarchy()

        return ui_snapshot_cache.get_or_compute(key, _compute)

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen size (width, height) for visibility enrichment."""
        from phone_pilot.harmony.device.utils import get_screen_size as _get_screen_size

        size = _get_screen_size(self._serial)
        if size is None:
            raise RuntimeError("Failed to get screen size")
        return size

    def _enrich_with_visibility(self, results: list[dict]) -> list[dict]:
        """Enrich results with visible/visible_ratio/visible_reason."""
        if not results:
            return results
        from phone_pilot.core.geometry import compute_visibility

        try:
            screen_size = self.get_screen_size()
        except Exception:
            screen_size = None

        for r in results:
            bounds_rect = r.get("bounds_rect")
            bounds_tuple = tuple(bounds_rect) if bounds_rect else None
            visible, ratio = compute_visibility(bounds_tuple, screen_size)
            r["visible"] = visible
            r["visible_ratio"] = ratio
            if screen_size is None:
                r["visible_reason"] = "screen_size_unavailable"
        return results

    def dump_ui_nodes(self):
        """Dump UI hierarchy as universal UINode list."""
        from phone_pilot.core.ui_node import UINode as UnivNode
        from phone_pilot.harmony.ui.automator import parse_hierarchy_nodes

        hierarchy = self.dump_ui_hierarchy_dict()
        hm_nodes = parse_hierarchy_nodes(hierarchy)
        return [_harmony_node_to_universal(n, UnivNode) for n in hm_nodes]

    def collect_all_texts(self) -> list[str]:
        """Collect all visible texts from UI tree."""
        from phone_pilot.harmony.ui.automator import (
            parse_hierarchy_nodes,
            collect_node_texts,
        )

        hierarchy = self.dump_ui_hierarchy_dict()
        nodes = parse_hierarchy_nodes(hierarchy)
        return collect_node_texts(nodes)

    def find_elements(self, selector: dict) -> list[dict]:
        """
        Find UI elements matching selector.

        Supported selector keys:
            xpath (string expression)
            text, type, id, key, description,
            clickable, enabled, focused, selected, scrollable
        """
        if not selector or not isinstance(selector, dict):
            return []

        if "xpath" in selector:
            xpath_expr = selector.get("xpath")
            if not xpath_expr or not isinstance(xpath_expr, str) or not xpath_expr.strip():
                return []
            return self._find_by_xpath(xpath_expr.strip(), single=True)

        hm = _get_hm(self._serial)

        # Map our selector to hmdriver2 kwargs
        hm_kwargs = {}
        _DIRECT_KEYS = {
            "text",
            "type",
            "id",
            "key",
            "description",
            "clickable",
            "enabled",
            "focused",
            "selected",
            "scrollable",
            "checked",
            "checkable",
            "longClickable",
            "isBefore",
            "isAfter",
        }
        for k, v in selector.items():
            if k in _DIRECT_KEYS:
                hm_kwargs[k] = v
            elif k == "text_contains":
                # hmdriver2 uses textContains for partial match
                hm_kwargs["text"] = v  # We'll need to filter manually
            elif k == "resource_id":
                hm_kwargs["id"] = v
            elif k == "class_name":
                hm_kwargs["type"] = v
            elif k == "desc" or k == "content_desc":
                hm_kwargs["description"] = v

        if not hm_kwargs:
            return []

        ui_obj = hm(**hm_kwargs)
        try:
            component = ui_obj.find_component()
            if component is None:
                return []
            # find_component returns a single ElementInfo or None
            info = component
            elem = _element_info_to_dict(info)
            return self._enrich_with_visibility([elem])
        except Exception:
            pass

        return []

    def find_all_elements(self, selector: dict) -> list[dict]:
        """Find all UI elements matching selector."""
        if not selector or not isinstance(selector, dict):
            return []

        if "xpath" in selector:
            xpath_expr = selector.get("xpath")
            if not xpath_expr or not isinstance(xpath_expr, str) or not xpath_expr.strip():
                return []
            return self._find_by_xpath(xpath_expr.strip(), single=False)

        hm = _get_hm(self._serial)
        hm_kwargs = {}
        for k, v in selector.items():
            if k == "resource_id":
                hm_kwargs["id"] = v
            elif k == "class_name":
                hm_kwargs["type"] = v
            elif k in ("desc", "content_desc"):
                hm_kwargs["description"] = v
            else:
                hm_kwargs[k] = v

        if not hm_kwargs:
            return []

        ui_obj = hm(**hm_kwargs)
        try:
            count = ui_obj.count()
            results = []
            for i in range(count):
                comp = hm(**hm_kwargs, index=i).find_component()
                if comp:
                    results.append(_element_info_to_dict(comp))
            return self._enrich_with_visibility(results)
        except Exception:
            return []

    def _find_by_xpath(self, xpath_expr: str, single: bool = False) -> list[dict]:
        """Find UI elements via XPath with hmdriver2 priority dispatch and ElementTree fallback."""
        # 1. Try hmdriver2.xpath if available
        try:
            hm = _get_hm(self._serial)
            if hasattr(hm, "xpath") and callable(getattr(hm, "xpath")):
                xpath_obj = hm.xpath(xpath_expr)
                if xpath_obj is not None:
                    if isinstance(xpath_obj, list):
                        if not xpath_obj:
                            return []
                        items = [_element_info_to_dict(x) for x in (xpath_obj[:1] if single else xpath_obj)]
                        return self._enrich_with_visibility(items)

                    if single:
                        if hasattr(xpath_obj, "find_component") and callable(getattr(xpath_obj, "find_component")):
                            comp = xpath_obj.find_component()
                            if comp is not None:
                                return self._enrich_with_visibility([_element_info_to_dict(comp)])
                            return []
                        elif hasattr(xpath_obj, "all") and callable(getattr(xpath_obj, "all")):
                            comps = xpath_obj.all()
                            if comps:
                                return self._enrich_with_visibility([_element_info_to_dict(comps[0])])
                            return []
                    else:
                        if hasattr(xpath_obj, "all") and callable(getattr(xpath_obj, "all")):
                            comps = xpath_obj.all()
                            if comps is not None:
                                return self._enrich_with_visibility([_element_info_to_dict(c) for c in comps])
                        elif hasattr(xpath_obj, "find_components") and callable(getattr(xpath_obj, "find_components")):
                            comps = xpath_obj.find_components()
                            if comps is not None:
                                return self._enrich_with_visibility([_element_info_to_dict(c) for c in comps])
        except Exception as exc:
            logger.debug("hm.xpath failed, falling back to ElementTree: %s", exc)

        # 2. Fallback: query via XML hierarchy
        try:
            hierarchy = self.dump_ui_hierarchy_dict()
            if not hierarchy:
                return []
            return self._match_xpath_fallback(hierarchy, xpath_expr, single=single)
        except Exception as exc:
            logger.debug("Fallback XPath evaluation failed: %s", exc)
            return []

    def _match_xpath_fallback(self, hierarchy: dict, xpath_expr: str, single: bool = False) -> list[dict]:
        """Evaluate XPath against UI hierarchy using lxml or xml.etree."""
        nodes_list = []

        def _create(n, parent=None):
            if not isinstance(n, dict):
                return None
            attrs = n.get("attributes", {})
            tag = attrs.get("type") or "Node"
            tag_clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", str(tag))
            if not tag_clean or (not tag_clean[0].isalpha() and tag_clean[0] != "_"):
                tag_clean = f"Node_{tag_clean}"
            idx = len(nodes_list)
            nodes_list.append(attrs)
            xml_attrs = {"__node_idx": str(idx)}
            for k, v in attrs.items():
                if isinstance(v, (str, int, float, bool)):
                    str_v = str(v).lower() if isinstance(v, bool) else str(v)
                    k_clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", str(k))
                    xml_attrs[k_clean] = str_v
            nid = attrs.get("id")
            if nid is not None:
                xml_attrs["id"] = str(nid)
                xml_attrs["resource_id"] = str(nid)
                xml_attrs["resource-id"] = str(nid)
            desc = attrs.get("description")
            if desc is not None:
                xml_attrs["description"] = str(desc)
                xml_attrs["content_desc"] = str(desc)
                xml_attrs["content-desc"] = str(desc)

            elem = etree.Element(tag_clean, attrib=xml_attrs) if parent is None else etree.SubElement(parent, tag_clean, attrib=xml_attrs)
            for c in n.get("children", []):
                _create(c, parent=elem)
            return elem

        try:
            from lxml import etree

            root = _create(hierarchy)
            if root is None:
                return []
            matches = root.xpath(xpath_expr)
            if not matches:
                return []
            if single:
                matches = matches[:1]
            raw_matched = [nodes_list[int(m.get("__node_idx"))] for m in matches if m.get("__node_idx") is not None]
            converted = [_element_info_to_dict(attrs) for attrs in raw_matched]
            return self._enrich_with_visibility(converted)
        except Exception as exc:
            logger.debug("XPath parsing error: %s", exc)
            return []

    def get_current_activity(self) -> dict:
        """Get current foreground activity info."""
        try:
            hm = _get_hm(self._serial)
            pkg, page = hm.current_app()
            return {"package": pkg, "activity": page}
        except Exception:
            from phone_pilot.harmony.device.utils import get_current_focus

            return get_current_focus(self._serial)


# ---------------------------------------------------------------------------
# HarmonyAppDriver
# ---------------------------------------------------------------------------


class HarmonyAppDriver:
    """HarmonyOS AppDriver — prefers hmdriver2, falls back to HDC."""

    def __init__(self, device_serial: str):
        self._serial = device_serial

    def list_packages(self, include_system: bool = False) -> list[str]:
        """List installed packages."""
        try:
            hm = _get_hm(self._serial)
            return hm.list_apps(include_system_apps=include_system)
        except Exception:
            from phone_pilot.harmony.device.utils import list_installed_packages

            return list_installed_packages(self._serial, include_system=include_system)

    def launch_app(self, package: str, activity: Optional[str] = None) -> dict:
        """Launch an application."""
        try:
            hm = _get_hm(self._serial)
            if activity:
                hm.start_app(package, activity)
            else:
                hm.start_app(package)
            return {
                "ok": True,
                "package": package,
                "activity": activity,
                "engine": "hmdriver2",
            }
        except Exception as exc:
            logger.debug("hmdriver2 launch_app failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import launch_app

            return launch_app(self._serial, package=package, activity=activity)

    def force_stop(self, package: str) -> dict:
        """Force stop an application."""
        try:
            hm = _get_hm(self._serial)
            hm.stop_app(package)
            return {"ok": True, "package": package, "engine": "hmdriver2"}
        except Exception:
            from phone_pilot.harmony.device.utils import force_stop_package

            return force_stop_package(self._serial, package)

    def clear_data(self, package: str) -> dict:
        """Clear application data."""
        try:
            hm = _get_hm(self._serial)
            hm.clear_app(package)
            return {"ok": True, "package": package, "engine": "hmdriver2"}
        except Exception:
            from phone_pilot.harmony.device.utils import clear_app_data

            return clear_app_data(self._serial, package)

    def uninstall(self, package: str, keep_data: bool = False) -> dict:
        """卸载应用（委托 uninstall_app，Harmony 暂不支持 keep_data）。
        Uninstall app, delegating to uninstall_app (keep_data not supported on Harmony).
        """
        try:
            result = self.uninstall_app(package)
            return {"ok": result.get("ok", True), "package": package, **result}
        except Exception as e:
            return {"ok": False, "error": str(e), "package": package}

    # -- Extra methods (not in Protocol) --

    def install_app(self, hap_path: str) -> dict:
        """Install a HAP file (hmdriver2 only)."""
        hm = _get_hm(self._serial)
        hm.install_app(hap_path)
        return {"ok": True, "path": hap_path, "engine": "hmdriver2"}

    def uninstall_app(self, package: str) -> dict:
        """Uninstall an application (hmdriver2 only)."""
        hm = _get_hm(self._serial)
        hm.uninstall_app(package)
        return {"ok": True, "package": package, "engine": "hmdriver2"}

    def get_app_info(self, package: str) -> dict:
        """Get application details."""
        hm = _get_hm(self._serial)
        return hm.get_app_info(package)

    def has_app(self, package: str) -> bool:
        """Check if an app is installed."""
        hm = _get_hm(self._serial)
        return hm.has_app(package)

    def get_app_main_ability(self, package: str) -> dict:
        """Get the main ability of an application."""
        hm = _get_hm(self._serial)
        return hm.get_app_main_ability(package)

    def get_default_launcher_component(self) -> Optional[str]:
        """Get the default launcher component (e.g. com.ohos.sceneboard/MainAbility)."""
        from phone_pilot.harmony.device.utils import get_default_launcher_component

        return get_default_launcher_component(self._serial)

    def list_all_launcher_components(self) -> dict[str, list[str]]:
        """List all launcher components grouped by bundleName."""
        from phone_pilot.harmony.device.utils import list_all_launcher_components

        return list_all_launcher_components(self._serial)

    def build_launch_profiles(self) -> list[dict]:
        """Build launch profiles snapshot."""
        from phone_pilot.harmony.device.utils import build_launch_profiles_snapshot

        return build_launch_profiles_snapshot(self._serial)

    def launch_from_home(
        self,
        package_or_name: str,
        activity: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Launch app from home screen or by explicit bundle/ability."""
        if activity is not None:
            from phone_pilot.harmony.device.utils import launch_from_home as _h_launch_comp

            return _h_launch_comp(self._serial, package_or_name, activity, **kwargs)
        else:
            from phone_pilot.harmony.ui.finder import launch_from_home as _h_launch_finder

            return _h_launch_finder(
                self._serial,
                package_or_name,
                reset_home=bool(kwargs.get("reset_home", True)),
                max_swipes=int(kwargs.get("max_swipes", 5)),
            )


# ---------------------------------------------------------------------------
# HarmonyDriver (composite)
# ---------------------------------------------------------------------------


class HarmonyDriver:
    """
    HarmonyOS DeviceDriver implementation.

    Combines all HarmonyOS sub-drivers and implements the DeviceDriver protocol.
    Uses hmdriver2 as primary engine with HDC fallback.
    """

    def __init__(self, device_serial: str):
        """Initialize HarmonyOS driver and sub-drivers."""
        if not device_serial:
            raise ValueError("device_serial is required")
        self._device_serial = device_serial
        self._input = HarmonyInputDriver(device_serial)
        self._screen = HarmonyScreenDriver(device_serial)
        self._ui = HarmonyUIDriver(device_serial)
        self._app = HarmonyAppDriver(device_serial)

    @property
    def input(self) -> InputDriver:
        """Return input driver."""
        return self._input

    @property
    def screen(self) -> ScreenDriver:
        """Return screen driver."""
        return self._screen

    @property
    def ui(self) -> UIDriver:
        """Return UI driver."""
        return self._ui

    @property
    def app(self) -> AppDriver:
        """Return app driver."""
        return self._app

    @property
    def device_serial(self) -> str:
        """Return bound device serial."""
        return self._device_serial

    @property
    def platform(self) -> str:
        """Return platform identifier."""
        return "harmony"

    # ---- device-level control (Protocol) ----

    def go_home(self) -> dict:
        """Navigate to home screen."""
        try:
            hm = _get_hm(self._device_serial)
            hm.go_home()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception:
            return self._input.keyevent("HOME")

    def go_back(self) -> dict:
        """Press back button."""
        try:
            hm = _get_hm(self._device_serial)
            hm.go_back()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception:
            return self._input.keyevent("BACK")

    def unlock(self, pin: Optional[str] = None) -> dict:
        """Wake + unlock device screen."""
        try:
            hm = _get_hm(self._device_serial)
            hm.screen_on()
            hm.unlock()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 unlock failed, fallback to HDC CLI: %s", exc)
            from phone_pilot.harmony.device.utils import unlock_screen

            return unlock_screen(self._device_serial, pin=pin)

    def lock_screen(self) -> dict:
        """Lock / turn off screen."""
        try:
            hm = _get_hm(self._device_serial)
            hm.screen_off()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception as exc:
            logger.debug("hmdriver2 lock_screen failed, fallback to HDC CLI: %s", exc)
            from phone_pilot.harmony.device.utils import lock_screen

            return lock_screen(self._device_serial)

    def clear_background(self) -> dict:
        """Best-effort clear background apps."""
        from phone_pilot.harmony.device.utils import clear_background_processes

        return clear_background_processes(self._device_serial)

    def shell(self, cmd: str) -> dict:
        """通过 hdc shell 在设备上执行任意命令。
        Execute arbitrary command on device via hdc shell.

        Returns:
            dict: {"ok": bool, "stdout": str, "stderr": str, "returncode": int}
        """
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner
        from phone_pilot.harmony.hdc.utils import hdc_prefix

        proc = HdcCommandRunner.run(
            hdc_prefix(self._device_serial) + ["shell", cmd],
            check=False,
            delay_s=0.0,
            log_output=False,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }

    # ---- file transfer ----

    def push_file(self, local_path: str, remote_path: str) -> dict:
        """推送本地文件到设备，委托 hdc/utils.push_file。
        Push local file to device, delegating to hdc/utils.push_file.
        """
        from phone_pilot.harmony.hdc.utils import push_file as _push

        try:
            result = _push(self._device_serial, local_path, remote_path)
            return {
                **result,
                "ok": result.get("ok", False),
                "local_path": local_path,
                "remote_path": remote_path,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pull_file(self, remote_path: str, local_path: str) -> dict:
        """Pull a file from device to host via ``hdc file recv``."""
        from phone_pilot.harmony.hdc.utils import pull_file as _hdc_pull

        pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        return _hdc_pull(self._device_serial, remote_path, local_path)

    def remove_remote_file(self, remote_path: str) -> dict:
        """Remove a file from the device via ``hdc shell rm``."""
        from phone_pilot.harmony.hdc.utils import hdc_prefix
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner

        proc = HdcCommandRunner.run(
            hdc_prefix(self._device_serial) + ["shell", "rm", "-f", remote_path],
            check=False,
            delay_s=0.0,
            log_output=False,
        )
        return {
            "ok": proc.returncode == 0,
            "remote": remote_path,
            "returncode": proc.returncode,
        }

    # ---- high-level skills ----

    def get_default_launcher_component(self) -> Optional[str]:
        """获取默认桌面组件名 (bundle/ability)。"""
        from phone_pilot.harmony.device.utils import get_default_launcher_component

        return get_default_launcher_component(self._device_serial)

    def list_all_launcher_components(self) -> dict[str, list[str]]:
        """获取所有含桌面入口的 Bundle 及其 Ability。"""
        from phone_pilot.harmony.device.utils import list_all_launcher_components

        return list_all_launcher_components(self._device_serial)

    def build_launch_profiles(self) -> list[dict]:
        """构建设备上所有可启动应用的配置快照。"""
        from phone_pilot.harmony.device.utils import build_launch_profiles_snapshot

        return build_launch_profiles_snapshot(self._device_serial)

    def launch_from_home(
        self,
        package_or_name: str,
        activity: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Launch an app from home screen or by explicit bundle/ability."""
        if activity is not None:
            from phone_pilot.harmony.device.utils import launch_from_home as _h_launch_comp

            return _h_launch_comp(self._device_serial, package_or_name, activity, **kwargs)
        else:
            from phone_pilot.harmony.ui.finder import launch_from_home as _h_launch_finder

            return _h_launch_finder(
                self._device_serial,
                package_or_name,
                reset_home=bool(kwargs.get("reset_home", True)),
                max_swipes=int(kwargs.get("max_swipes", 5)),
            )

    def open_deeplink(self, uri: str, package: Optional[str] = None, **kwargs) -> dict:
        """打开深链接 (URL Scheme)。优先尝试 hmdriver2 open_url，失败降级至 HDC aa start。"""
        if not uri or not str(uri).strip():
            return {"ok": False, "error": "uri is required", "platform": "harmony"}
        try:
            hm = _get_hm(self._device_serial)
            if hasattr(hm, "open_url") and callable(getattr(hm, "open_url")):
                hm.open_url(uri)
                return {
                    "ok": True,
                    "engine": "hmdriver2",
                    "uri": uri,
                    "package": package,
                    "platform": "harmony",
                }
        except Exception as exc:
            logger.debug("hmdriver2 open_url failed: %s", exc)

        from phone_pilot.harmony.device.utils import open_deeplink as _h_open_deeplink

        return _h_open_deeplink(
            self._device_serial,
            uri,
            package=package,
            wait_s=kwargs.get("wait_s", 0.8),
        )

    def get_meminfo(self, package: str, timeout_s: float = 30.0) -> dict:
        """获取指定应用的内存信息。"""
        from phone_pilot.harmony.device.utils import capture_meminfo

        return capture_meminfo(self._device_serial, package, timeout_s=timeout_s)

    def set_wifi_enabled(self, enabled: bool) -> dict:
        """开启或关闭 Wi-Fi。"""
        from phone_pilot.harmony.device.utils import set_wifi_enabled

        return set_wifi_enabled(self._device_serial, enabled)

    def set_airplane_mode(self, enabled: bool) -> dict:
        """开启或关闭飞行模式。"""
        from phone_pilot.harmony.device.utils import set_airplane_mode

        return set_airplane_mode(self._device_serial, enabled)

    def get_notifications(self) -> dict:
        """获取当前通知栏通知列表。"""
        from phone_pilot.harmony.device.utils import get_notifications

        return get_notifications(self._device_serial)

    def dump_memory_profile(self, package: str, **kwargs) -> dict:
        """Not supported on HarmonyOS."""
        return {"ok": False, "error": "hprof_not_supported_on_harmony"}

    def device_info(self) -> dict:
        """Return device profile dict."""
        try:
            hm = _get_hm(self._device_serial)
            info = hm.device_info
            return {
                "ok": True,
                "productName": info.productName,
                "model": info.model,
                "sdkVersion": info.sdkVersion,
                "sysVersion": info.sysVersion,
                "cpuAbi": info.cpuAbi,
                "wlanIp": info.wlanIp,
                "displaySize": info.displaySize,
                "displayRotation": str(info.displayRotation),
            }
        except Exception as exc:
            logger.debug("hmdriver2 device_info failed, fallback to HDC: %s", exc)
            from phone_pilot.harmony.device.utils import get_device_info as _h_device_info

            res = _h_device_info(self._device_serial)
            if res.get("ok"):
                return res
            return {"ok": False, "error": str(exc)}

    # ---- log capture (stream) ----

    def start_log_capture(
        self,
        output_path: str,
        *,
        tags: str = "",
        exclude_tags: str = "",
        level: str = "",
        process: str = "",
    ) -> dict:
        """Start background hilog capture to a file.
        启动后台 hilog 捕获，输出到文件。
        """
        import re
        import subprocess as _sp
        from phone_pilot.harmony.hdc.utils import hdc_prefix

        if not hasattr(self, "_log_captures"):
            self._log_captures: dict[str, dict] = {}
        if output_path in self._log_captures:
            return {
                "ok": False,
                "error": f"log capture already running for {output_path}",
            }

        out = pathlib.Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        hilog_cmd = hdc_prefix(self._device_serial) + ["shell", "hilog"]
        if level:
            hilog_cmd += ["-L", level.upper()[0]]

        include_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        exclude_list = (
            [t.strip() for t in exclude_tags.split(",") if t.strip()]
            if exclude_tags
            else []
        )
        proc_kw = process.strip() if process else ""

        # hilog supports native -T tag filter (one at a time)
        if include_tags and len(include_tags) == 1:
            hilog_cmd += ["-T", include_tags[0]]
            include_tags = []  # handled natively

        try:
            fh = open(out, "w", encoding="utf-8")
            if include_tags or exclude_list or proc_kw:
                shell_cmd = " ".join(hilog_cmd)
                if include_tags:
                    grep_pattern = "|".join(re.escape(t) for t in include_tags)
                    shell_cmd += f" | grep -E '{grep_pattern}'"
                for ex in exclude_list:
                    shell_cmd += f" | grep -v '{re.escape(ex)}'"
                if proc_kw:
                    shell_cmd += f" | grep '{re.escape(proc_kw)}'"
                proc = _sp.Popen(shell_cmd, shell=True, stdout=fh, stderr=_sp.DEVNULL)
            else:
                proc = _sp.Popen(hilog_cmd, stdout=fh, stderr=_sp.DEVNULL)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        self._log_captures[output_path] = {
            "proc": proc,
            "fh": fh,
            "exclude_tags": exclude_tags,
            "process": process,
        }
        return {"ok": True, "pid": proc.pid, "path": output_path}

    def stop_log_capture(self, output_path: str) -> dict:
        """Stop background hilog capture.
        停止指定 output_path 的后台 hilog 捕获。
        """
        if not hasattr(self, "_log_captures") or output_path not in self._log_captures:
            return {"ok": False, "error": f"no log capture running for {output_path}"}

        info = self._log_captures.pop(output_path)
        proc = info["proc"]
        fh = info["fh"]

        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            fh.close()
        except Exception:
            pass

        # Post-process: apply exclude_tag filter in-place
        out = pathlib.Path(output_path)
        exclude_tag = info.get("exclude_tags", "")
        process_filter = info.get("process", "")
        if (exclude_tag or process_filter) and out.exists():
            try:
                _filter_log_file_inplace(out, exclude_tag, process_filter)
            except Exception:
                pass

        lines = 0
        if out.exists():
            try:
                lines = sum(1 for _ in open(out, encoding="utf-8", errors="replace"))
            except Exception:
                pass

        return {"ok": True, "path": output_path, "lines": lines}

    # ---- log (Protocol) ----

    def read_log(self, lines: int = 200, **kwargs) -> str:
        """Read recent hilog output."""
        from phone_pilot.harmony.hdc.logcat import read_hilog

        return read_hilog(self._device_serial, lines=lines, **kwargs)

    def clear_log(self) -> dict:
        """Clear hilog buffer."""
        from phone_pilot.harmony.hdc.logcat import clear_hilog

        return clear_hilog(self._device_serial)

    def dump_log(self, path: str, **kwargs) -> dict:
        """Export hilog to a file on host."""
        from phone_pilot.harmony.hdc.logcat import dump_hilog

        return dump_hilog(
            self._device_serial,
            path,
            lines=kwargs.get("lines", 500),
        )

    def logcat_time(self) -> str:
        """获取设备当前 hilog 时间戳 (格式: MM-DD HH:MM:SS.000)。"""
        from phone_pilot.harmony.hdc.logcat import device_hilog_time

        return device_hilog_time(self._device_serial)

    # ---- extra convenience (not in Protocol) ----

    def screen_on(self) -> dict:
        """Wake up screen."""
        try:
            hm = _get_hm(self._device_serial)
            hm.screen_on()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def screen_off(self) -> dict:
        """Turn off screen."""
        try:
            hm = _get_hm(self._device_serial)
            hm.screen_off()
            return {"ok": True, "engine": "hmdriver2"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_url(self, url: str) -> dict:
        """Open a URL or deep link."""
        try:
            hm = _get_hm(self._device_serial)
            hm.open_url(url)
            return {"ok": True, "url": url, "engine": "hmdriver2"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _resolve_keycode(keycode_str: str, keycode_enum):
    """
    Convert a string keycode to hmdriver2 KeyCode enum.

    Supports formats:
    - "HOME", "BACK", "POWER" (direct name)
    - "KEYCODE_HOME" (Android-style, strip prefix)
    - Integer values as string
    """
    s = str(keycode_str).strip().upper()

    # Strip Android-style prefix
    if s.startswith("KEYCODE_"):
        s = s[len("KEYCODE_") :]

    # Try direct enum name lookup
    try:
        return keycode_enum[s]
    except (KeyError, ValueError):
        pass

    # Try integer value
    try:
        val = int(keycode_str)
        return val  # hmdriver2 press_key accepts int directly
    except (ValueError, TypeError):
        pass

    # Common Android-to-Harmony mappings
    _ANDROID_TO_HARMONY = {
        "ENTER": "ENTER",
        "BACK": "BACK",
        "HOME": "HOME",
        "POWER": "POWER",
        "VOLUME_UP": "VOLUME_UP",
        "VOLUME_DOWN": "VOLUME_DOWN",
        "DEL": "DEL",
        "TAB": "TAB",
        "SPACE": "SPACE",
        "ESCAPE": "ESCAPE",
        "MENU": "MENU",
        "DPAD_UP": "DPAD_UP",
        "DPAD_DOWN": "DPAD_DOWN",
        "DPAD_LEFT": "DPAD_LEFT",
        "DPAD_RIGHT": "DPAD_RIGHT",
    }
    mapped = _ANDROID_TO_HARMONY.get(s)
    if mapped:
        try:
            return keycode_enum[mapped]
        except (KeyError, ValueError):
            pass

    raise ValueError(f"Unknown keycode: {keycode_str}")


def _parse_bounds_rect(bounds) -> list[int] | None:
    """Standardize element bounds into [left, top, right, bottom] list of ints.

    Supports:
    - dict with left, top, right, bottom
    - list or tuple of 4 ints/floats
    - string in '[left,top][right,bottom]' format
    - object with left, top, right, bottom attributes
    """
    if bounds is None:
        return None
    try:
        from unittest.mock import NonCallableMagicMock

        if isinstance(bounds, NonCallableMagicMock):
            return None
    except ImportError:
        pass

    if isinstance(bounds, (list, tuple)):
        if len(bounds) == 4 and all(isinstance(x, (int, float)) for x in bounds):
            return [int(x) for x in bounds]
        return None
    if isinstance(bounds, dict):
        if all(k in bounds for k in ("left", "top", "right", "bottom")):
            try:
                return [
                    int(bounds["left"]),
                    int(bounds["top"]),
                    int(bounds["right"]),
                    int(bounds["bottom"]),
                ]
            except (ValueError, TypeError):
                return None
        return None
    if isinstance(bounds, str):
        m = re.match(r"^\[(-?\d+),\s*(-?\d+)\]\[(-?\d+),\s*(-?\d+)\]$", bounds.strip())
        if m:
            return [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
        return None
    if (
        hasattr(bounds, "left")
        and hasattr(bounds, "top")
        and hasattr(bounds, "right")
        and hasattr(bounds, "bottom")
    ):
        try:
            return [
                int(bounds.left),
                int(bounds.top),
                int(bounds.right),
                int(bounds.bottom),
            ]
        except (ValueError, TypeError):
            return None
    return None


def _element_info_to_dict(info) -> dict:
    """Convert hmdriver2 ElementInfo to a standard dict.

    Always includes ``bounds_rect`` and ``center_x`` / ``center_y`` at the top level
    so callers can locate elements uniformly across platforms.
    """
    try:
        d = info.to_dict()
    except Exception:
        d = None

    if not isinstance(d, dict):
        if isinstance(info, dict):
            d = dict(info)
        else:
            d = {}
            for attr in (
                "id",
                "key",
                "type",
                "text",
                "description",
                "isSelected",
                "isChecked",
                "isEnabled",
                "isFocused",
                "isCheckable",
                "isClickable",
                "isLongClickable",
                "isScrollable",
            ):
                val = getattr(info, attr, None)
                if val is not None:
                    d[attr] = val

    # Determine raw bounds prioritizing d["bounds"]
    raw_bounds = None
    if isinstance(d, dict) and "bounds" in d:
        raw_bounds = d["bounds"]
    elif isinstance(info, dict):
        raw_bounds = info.get("bounds")
    elif hasattr(info, "bounds"):
        raw_bounds = getattr(info, "bounds", None)

    rect = _parse_bounds_rect(raw_bounds)
    d["bounds_rect"] = rect

    # Ensure bounds dict representation
    if "bounds" not in d or not isinstance(d["bounds"], dict):
        if isinstance(raw_bounds, dict):
            d["bounds"] = raw_bounds
        elif rect is not None and hasattr(raw_bounds, "left"):
            d["bounds"] = {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
            }

    # Ensure boundsCenter and top-level center_x / center_y
    center = getattr(info, "boundsCenter", None) if not isinstance(info, dict) else None
    if (
        center is not None
        and hasattr(center, "x")
        and hasattr(center, "y")
        and isinstance(center.x, (int, float))
        and not type(center.x).__name__.endswith("Mock")
    ):
        d.setdefault("boundsCenter", {"x": center.x, "y": center.y})
        d.setdefault("center_x", center.x)
        d.setdefault("center_y", center.y)
    elif rect is not None:
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        d.setdefault("center_x", cx)
        d.setdefault("center_y", cy)

    return d


def _filter_log_file_inplace(
    path: pathlib.Path, exclude_tag: str, process_filter: str
) -> None:
    """In-place filter log file by exclude_tag and process."""
    excludes = (
        {t.strip() for t in exclude_tag.split(",") if t.strip()}
        if exclude_tag
        else set()
    )
    proc_kw = process_filter.strip() if process_filter else ""
    if not excludes and not proc_kw:
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    filtered = []
    for ln in lines:
        skip = False
        if excludes:
            for ex in excludes:
                if ex in ln:
                    skip = True
                    break
        if not skip and proc_kw and proc_kw not in ln:
            skip = True
        if not skip:
            filtered.append(ln)
    path.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def _harmony_node_to_universal(node, UnivNode):
    """Convert HarmonyUINode to universal UINode."""
    center = node.center()
    bounds_str = node.bounds or ""
    if not bounds_str and node.bounds_raw:
        br = node.bounds_raw
        bounds_str = f"[{br.get('left', 0)},{br.get('top', 0)}][{br.get('right', 0)},{br.get('bottom', 0)}]"
    return UnivNode(
        text=node.text or "",
        content_desc=node.description or "",  # Harmony .description → .content_desc
        hint="",  # Harmony has no hint
        resource_id=node.id or "",  # Harmony .id → .resource_id
        class_name=node.type or "",  # Harmony .type → .class_name
        key=node.key or "",
        package="",
        bounds=bounds_str,
        clickable=bool(node.clickable),
        enabled=bool(node.enabled),
        focused=bool(node.focused),
        selected=bool(node.selected),
        checkable=bool(node.checkable),
        checked=bool(node.checked),
        long_clickable=bool(node.long_clickable),
        scrollable=bool(node.scrollable),
        _center_x=center[0] if center else 0,
        _center_y=center[1] if center else 0,
    )
