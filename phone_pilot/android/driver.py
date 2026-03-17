"""
Android Driver implementation.

Implements the DeviceDriver protocol for Android devices using ADB.
"""

from __future__ import annotations

import pathlib
from typing import Optional

from phone_pilot.core.protocols import (
    InputDriver,
    ScreenDriver,
    UIDriver,
    AppDriver,
)

# Import implementations from phone_pilot
from phone_pilot.android.device.utils import (
    input_tap,
    input_swipe,
    input_long_press,
    input_text,
    input_keyevent,
    list_installed_packages,
    force_stop_package,
    restart_app,
    get_current_focus,
    get_screen_size as _get_screen_size,
)
from phone_pilot.android.adb.screenshot import take_screenshot_png_bytes
from phone_pilot.android.ui.automator import (
    dump_ui_xml,
    parse_uiautomator_nodes,
)


class AndroidInputDriver:
    """Android implementation of InputDriver using ADB."""

    def __init__(self, device_serial: str):
        """Bind input driver to a device serial."""
        self._device_serial = device_serial

    def tap(self, x: int, y: int, wait_s: float = 0.15) -> dict:
        """Tap at screen coordinates."""
        return input_tap(self._device_serial, x, y, wait_s=wait_s)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2)."""
        return input_swipe(
            self._device_serial, x1, y1, x2, y2, duration_ms=duration_ms
        )

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> dict:
        """Long press at coordinates."""
        return input_long_press(
            self._device_serial, x, y, duration_ms=duration_ms
        )

    def input_text(self, text: str, enter: bool = False) -> dict:
        """Input text into focused field."""
        return input_text(self._device_serial, text, enter=enter)

    def keyevent(self, keycode: str) -> dict:
        """Send a key event."""
        return input_keyevent(self._device_serial, keycode)


class AndroidScreenDriver:
    """Android implementation of ScreenDriver using ADB."""

    def __init__(self, device_serial: str):
        """Bind screen driver to a device serial."""
        self._device_serial = device_serial
        self._recording_process = None
        self._recording_path = None

    def screenshot(self) -> bytes:
        """Capture current screen as PNG bytes."""
        return take_screenshot_png_bytes(self._device_serial)

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions."""
        size = _get_screen_size(self._device_serial)
        if size is None:
            raise RuntimeError("Failed to get screen size")
        return size

    def start_screenrecord(self, path: str, **kwargs) -> dict:
        """Start screen recording.

        Args:
            path: Filename (e.g. ``record_123456.mp4``).

        Returns:
            dict with ``remote_path`` – the actual on-device path.
        """
        # Import here to avoid circular deps
        from phone_pilot.android.adb.utils import adb_prefix
        import subprocess

        time_limit = kwargs.get("time_limit_s", 180)
        bitrate = kwargs.get("bitrate", "4M")

        remote_path = f"/sdcard/{path}"

        # Start recording in background
        cmd = adb_prefix(self._device_serial) + [
            "shell", "screenrecord",
            "--time-limit", str(time_limit),
            "--bit-rate", str(bitrate),
            remote_path,
        ]

        self._recording_path = path
        self._recording_remote = remote_path
        # Use Popen for background process
        self._recording_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )

        return {
            "ok": True,
            "path": path,
            "remote_path": remote_path,
            "device_serial": self._device_serial,
            "time_limit_s": time_limit,
        }

    def stop_screenrecord(self) -> dict:
        """Stop current screen recording."""
        import time
        
        if self._recording_process is None:
            return {"ok": False, "error": "no_recording_in_progress"}
        
        # Send SIGINT to stop recording gracefully
        self._recording_process.terminate()
        try:
            self._recording_process.wait(timeout=5)
        except Exception:
            self._recording_process.kill()
        
        time.sleep(0.5)  # Let file finalize
        
        path = self._recording_path
        self._recording_process = None
        self._recording_path = None
        
        return {
            "ok": True,
            "path": path,
            "device_serial": self._device_serial,
        }


class AndroidUIDriver:
    """Android implementation of UIDriver using UIAutomator."""

    def __init__(self, device_serial: str):
        """Bind UI driver to a device serial."""
        self._device_serial = device_serial

    def dump_ui_hierarchy(self) -> str:
        """Dump current UI hierarchy as XML."""
        result = dump_ui_xml(self._device_serial)
        if not result.get("ok"):
            raise RuntimeError(f"Failed to dump UI: {result.get('error')}")
        return result.get("xml", "")

    def dump_ui_nodes(self):
        """Dump UI nodes as universal UINode list."""
        from phone_pilot.core.ui_node import UINode as UnivNode
        xml = self.dump_ui_hierarchy()
        if not xml:
            return []
        android_nodes = parse_uiautomator_nodes(xml)
        return [_android_node_to_universal(n, UnivNode) for n in android_nodes]

    def collect_all_texts(self) -> list[str]:
        """Collect all visible texts from UI tree."""
        from phone_pilot.android.ui.query import collect_node_texts as _collect
        xml = self.dump_ui_hierarchy()
        if not xml:
            return []
        nodes = parse_uiautomator_nodes(xml)
        return _collect(nodes)

    def find_elements(self, selector: dict) -> list[dict]:
        """
        Find UI elements matching selector.
        
        Selector keys:
        - text: Exact text match
        - text_contains: Partial text match
        - desc: Content description match
        - desc_contains: Partial content description match
        - resource_id: Resource ID match
        - class_name: Class name match
        - clickable: Boolean
        - enabled: Boolean
        """
        # Dump and parse UI
        xml = self.dump_ui_hierarchy()
        nodes = parse_uiautomator_nodes(xml)
        
        # Apply filters
        results = []
        for node in nodes:
            match = True

            # Text / Hint matching
            if "text" in selector:
                val = selector["text"]
                if node.text != val and node.hint != val:
                    match = False
            if "text_contains" in selector and match:
                val = selector["text_contains"]
                text_ok = bool(node.text and val in node.text)
                hint_ok = bool(node.hint and val in node.hint)
                if not (text_ok or hint_ok):
                    match = False
            if "hint" in selector and match:
                if node.hint != selector["hint"]:
                    match = False
            if "hint_contains" in selector and match:
                if not node.hint or selector["hint_contains"] not in node.hint:
                    match = False

            # Description matching
            if "desc" in selector and match:
                if node.content_desc != selector["desc"]:
                    match = False
            if "desc_contains" in selector and match:
                if not node.content_desc or selector["desc_contains"] not in node.content_desc:
                    match = False

            # Resource ID matching
            if "resource_id" in selector and match:
                if not node.resource_id or selector["resource_id"] not in node.resource_id:
                    match = False

            # Class name matching
            if "class_name" in selector and match:
                if node.class_name != selector["class_name"]:
                    match = False

            # Boolean attributes
            if "clickable" in selector and match:
                if node.clickable != selector["clickable"]:
                    match = False
            if "enabled" in selector and match:
                if node.enabled != selector["enabled"]:
                    match = False

            if match:
                results.append(node.to_dict())
        
        # Translation fallback for text/desc selectors
        if not results and isinstance(selector, dict):
            try:
                from phone_pilot.extensions.lang.translate import translate_candidates
            except Exception:
                translate_candidates = None  # type: ignore
            if translate_candidates:
                for key in ("text", "text_contains", "desc", "desc_contains"):
                    if key not in selector:
                        continue
                    candidates = translate_candidates(str(selector.get(key) or ""))
                    for cand in candidates[1:]:
                        selector2 = dict(selector)
                        selector2[key] = cand
                        for node in nodes:
                            match = True
                            if "text" in selector2:
                                val = selector2["text"]
                                if node.text != val and node.hint != val:
                                    match = False
                            if "text_contains" in selector2 and match:
                                val = selector2["text_contains"]
                                text_ok = bool(node.text and val in node.text)
                                hint_ok = bool(node.hint and val in node.hint)
                                if not (text_ok or hint_ok):
                                    match = False
                            if "hint" in selector2 and match:
                                if node.hint != selector2["hint"]:
                                    match = False
                            if "hint_contains" in selector2 and match:
                                if not node.hint or selector2["hint_contains"] not in node.hint:
                                    match = False
                            if "desc" in selector2 and match:
                                if node.content_desc != selector2["desc"]:
                                    match = False
                            if "desc_contains" in selector2 and match:
                                if not node.content_desc or selector2["desc_contains"] not in node.content_desc:
                                    match = False
                            if "resource_id" in selector2 and match:
                                if not node.resource_id or selector2["resource_id"] not in node.resource_id:
                                    match = False
                            if "class_name" in selector2 and match:
                                if node.class_name != selector2["class_name"]:
                                    match = False
                            if "clickable" in selector2 and match:
                                if node.clickable != selector2["clickable"]:
                                    match = False
                            if "enabled" in selector2 and match:
                                if node.enabled != selector2["enabled"]:
                                    match = False
                            if match:
                                node_dict = node.to_dict()
                                node_dict["translated_from"] = selector.get(key)
                                results.append(node_dict)
                        if results:
                            break
                    if results:
                        break

        return results

    def get_current_activity(self) -> dict:
        """Get current foreground activity info."""
        return get_current_focus(self._device_serial)


class AndroidAppDriver:
    """Android implementation of AppDriver using ADB."""

    def __init__(self, device_serial: str):
        """Bind app driver to a device serial."""
        self._device_serial = device_serial

    def list_packages(self, include_system: bool = False) -> list[str]:
        """List installed packages."""
        return list_installed_packages(
            self._device_serial, include_system=include_system
        )

    def launch_app(self, package: str, activity: Optional[str] = None) -> dict:
        """Launch an application."""
        component = f"{package}/{activity}" if activity else None
        restart_app(
            self._device_serial,
            package=package,
            component=component,
            force_stop=False,
        )
        return {"ok": True, "package": package, "activity": activity}

    def force_stop(self, package: str) -> dict:
        """Force stop an application."""
        return force_stop_package(self._device_serial, package)

    def clear_data(self, package: str) -> dict:
        """Clear application data."""
        from phone_pilot.android.adb.utils import adb_prefix
        from phone_pilot.android.adb.runner import CommandRunner
        
        proc = CommandRunner.run(
            adb_prefix(self._device_serial) + ["shell", "pm", "clear", package],
            check=False,
            delay_s=0.0,
            log_output=False,
        )
        return {
            "ok": proc.returncode == 0,
            "package": package,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip(),
        }

    def uninstall(self, package: str, keep_data: bool = False) -> dict:
        """卸载应用（通过 adb shell pm uninstall）。
        Uninstall app via adb shell pm uninstall.
        """
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.android.adb.utils import adb_prefix

        cmd = adb_prefix(self._device_serial) + ["shell", "pm", "uninstall"]
        if keep_data:
            cmd.append("-k")
        cmd.append(package)
        try:
            proc = CommandRunner.run(cmd, check=False)
            # pm uninstall 成功时 stdout 包含 "Success"
            success = proc.returncode == 0 and "Success" in (proc.stdout or "")
            return {"ok": success, "package": package, "output": (proc.stdout or "").strip()}
        except Exception as e:
            return {"ok": False, "error": str(e), "package": package}


class AndroidDriver:
    """
    Android DeviceDriver implementation.

    Combines all Android sub-drivers and implements the DeviceDriver protocol.

    Usage:
        driver = AndroidDriver("device_serial")
        driver.input.tap(100, 200)
        screenshot = driver.screen.screenshot()
    """

    def __init__(self, device_serial: str):
        """Initialize Android driver and sub-drivers."""
        if not device_serial:
            raise ValueError("device_serial is required")
        self._device_serial = device_serial
        self._input = AndroidInputDriver(device_serial)
        self._screen = AndroidScreenDriver(device_serial)
        self._ui = AndroidUIDriver(device_serial)
        self._app = AndroidAppDriver(device_serial)

    # ---- sub-driver accessors ----

    @property
    def input(self) -> InputDriver:
        return self._input

    @property
    def screen(self) -> ScreenDriver:
        return self._screen

    @property
    def ui(self) -> UIDriver:
        return self._ui

    @property
    def app(self) -> AppDriver:
        return self._app

    # ---- identity ----

    @property
    def device_serial(self) -> str:
        return self._device_serial

    @property
    def platform(self) -> str:
        return "android"

    # ---- device-level control ----

    def go_home(self) -> dict:
        """Navigate to home screen."""
        return self._input.keyevent("KEYCODE_HOME")

    def go_back(self) -> dict:
        """Press back button."""
        return self._input.keyevent("KEYCODE_BACK")

    def unlock(self, pin: Optional[str] = None) -> dict:
        """Wake + unlock device screen."""
        from phone_pilot.android.device.lock import wake_and_unlock
        return wake_and_unlock(self._device_serial, pin=pin)

    def lock_screen(self) -> dict:
        """Lock / turn off screen."""
        return self._input.keyevent("KEYCODE_POWER")

    def clear_background(self) -> dict:
        """Best-effort clear background apps (only currently running 3p apps)."""
        from phone_pilot.android.device.utils import (
            clear_background_processes as _clear,
        )
        return _clear(self._device_serial, mode="force_stop_running_3p")

    def device_info(self) -> dict:
        """Return device profile dict."""
        from phone_pilot.android.device.store import capture_device_profile
        return capture_device_profile(self._device_serial)

    # ---- file transfer ----

    def push_file(self, local_path: str, remote_path: str) -> dict:
        """推送文件到设备，委托 adb/utils.push_file。
        Push file to device, delegating to adb/utils.push_file.
        """
        from phone_pilot.android.adb.utils import push_file as _push

        try:
            _push(self._device_serial, local_path, remote_path)
            return {"ok": True, "local_path": local_path, "remote_path": remote_path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pull_file(self, remote_path: str, local_path: str) -> dict:
        """Pull a file from device to host via ``adb pull``."""
        from phone_pilot.android.adb.utils import adb_prefix
        from phone_pilot.android.adb.runner import CommandRunner
        import pathlib
        pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        proc = CommandRunner.run(
            adb_prefix(self._device_serial) + ["pull", remote_path, local_path],
            check=False, delay_s=0.0, log_output=False,
        )
        return {
            "ok": proc.returncode == 0,
            "remote": remote_path,
            "local": local_path,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip(),
        }

    def remove_remote_file(self, remote_path: str) -> dict:
        """Remove a file from the device via ``adb shell rm``."""
        from phone_pilot.android.adb.utils import adb_prefix
        from phone_pilot.android.adb.runner import CommandRunner
        proc = CommandRunner.run(
            adb_prefix(self._device_serial) + ["shell", "rm", "-f", remote_path],
            check=False, delay_s=0.0, log_output=False,
        )
        return {
            "ok": proc.returncode == 0,
            "remote": remote_path,
            "returncode": proc.returncode,
        }

    # ---- high-level skills ----

    def open_deeplink(self, uri: str, package: Optional[str] = None, **kwargs) -> dict:
        """通过深链接打开页面，委托 device/utils.open_deeplink。
        Open page via deeplink, delegating to device/utils.open_deeplink.
        """
        from phone_pilot.android.device.utils import open_deeplink
        return open_deeplink(self._device_serial, uri, package=package)

    def launch_from_home(self, app_name: str, **kwargs) -> dict:
        """Launch an app from the home screen."""
        import asyncio
        from phone_pilot.android.ui.finder import launch_from_home_impl
        return asyncio.run(
            launch_from_home_impl(
                device_serial=self._device_serial,
                query=app_name,
                out_dir=kwargs.get("out_dir", "./.recordings"),
                **{k: v for k, v in kwargs.items() if k != "out_dir"},
            )
        )

    def dump_memory_profile(self, package: str, **kwargs) -> dict:
        """Dump heap profile (hprof) for the given package."""
        from phone_pilot.memory_analyze.dumper import dump_hprof
        return dump_hprof(
            self._device_serial, package,
            out_dir=kwargs.get("out_dir", "./.recordings"),
            name=kwargs.get("name", "hprof"),
            timeout_s=kwargs.get("timeout_s", 60.0),
        )

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
        """Start background logcat capture to a file.
        启动后台 logcat 捕获，输出到文件。
        """
        import re
        import subprocess as _sp
        from phone_pilot.android.adb.utils import adb_prefix

        if not hasattr(self, "_log_captures"):
            self._log_captures: dict[str, dict] = {}
        if output_path in self._log_captures:
            return {"ok": False, "error": f"log capture already running for {output_path}"}

        out = pathlib.Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        logcat_cmd = adb_prefix(self._device_serial) + ["logcat", "-v", "threadtime"]
        if level and not tags:
            logcat_cmd += [f"*:{level.upper()}"]

        include_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        exclude_list = [t.strip() for t in exclude_tags.split(",") if t.strip()] if exclude_tags else []
        proc_kw = process.strip() if process else ""

        try:
            fh = open(out, "w", encoding="utf-8")
            if include_tags or exclude_list or proc_kw or (level and tags):
                if include_tags:
                    grep_pattern = "|".join(re.escape(t) for t in include_tags)
                    shell_cmd = " ".join(logcat_cmd) + f" | grep -E '{grep_pattern}'"
                else:
                    shell_cmd = " ".join(logcat_cmd)
                for ex in exclude_list:
                    shell_cmd += f" | grep -v '{re.escape(ex)}'"
                if proc_kw:
                    shell_cmd += f" | grep '{re.escape(proc_kw)}'"
                proc = _sp.Popen(shell_cmd, shell=True, stdout=fh, stderr=_sp.DEVNULL)
            else:
                proc = _sp.Popen(logcat_cmd, stdout=fh, stderr=_sp.DEVNULL)
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
        """Stop background logcat capture.
        停止指定 output_path 的后台 logcat 捕获。
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
                self._filter_log_file(out, exclude_tag, process_filter)
            except Exception:
                pass

        lines = 0
        if out.exists():
            try:
                lines = sum(1 for _ in open(out, encoding="utf-8", errors="replace"))
            except Exception:
                pass

        return {"ok": True, "path": output_path, "lines": lines}

    @staticmethod
    def _filter_log_file(path: pathlib.Path, exclude_tag: str, process_filter: str) -> None:
        """In-place filter log file by exclude_tag and process."""
        excludes = {t.strip() for t in exclude_tag.split(",") if t.strip()} if exclude_tag else set()
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

    # ---- log ----

    def read_log(self, lines: int = 200, **kwargs) -> str:
        """Read recent logcat output."""
        from phone_pilot.android.adb.logcat import read_logcat
        content, _meta = read_logcat(self._device_serial, lines=lines)
        return content

    def clear_log(self) -> dict:
        """Clear logcat buffer."""
        from phone_pilot.android.adb.logcat import clear_logcat
        return clear_logcat(self._device_serial)

    def dump_log(self, path: str, **kwargs) -> dict:
        """Export logcat to a file on host."""
        from phone_pilot.android.adb.logcat import dump_logcat
        return dump_logcat(
            self._device_serial, path,
            lines=kwargs.get("lines", 500),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _android_node_to_universal(node, UnivNode):
    """Convert Android UINode to universal UINode."""
    center = node.center()
    return UnivNode(
        text=node.text or "",
        content_desc=node.content_desc or "",
        hint=node.hint or "",
        resource_id=node.resource_id or "",
        class_name=node.class_name or "",
        package=node.package or "",
        bounds=node.bounds or "",
        clickable=bool(node.clickable),
        enabled=bool(node.enabled),
        focusable=bool(node.focusable) if node.focusable is not None else False,
        focused=bool(node.focused) if node.focused is not None else False,
        selected=bool(node.selected) if node.selected is not None else False,
        checkable=bool(node.checkable) if node.checkable is not None else False,
        checked=bool(node.checked) if node.checked is not None else False,
        long_clickable=bool(node.long_clickable) if node.long_clickable is not None else False,
        scrollable=bool(node.scrollable) if node.scrollable is not None else False,
        password=bool(node.password) if node.password is not None else False,
        _center_x=center[0] if center else 0,
        _center_y=center[1] if center else 0,
    )
