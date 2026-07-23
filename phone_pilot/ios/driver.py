"""
iOS Driver implementation — skeleton with stubs.

Implements the DeviceDriver protocol for iOS devices.
Primary engine: WebDriverAgent (XCUITest) + pymobiledevice3.

A1 skeleton: all 42 protocol entries present with correct signatures.
Method bodies return stub values; no method raises (except __init__ ValueError
for empty serial, and wda_bridge._ensure_wda_class ImportError).

Real implementations delivered in batches B1/B2/B3/C1.
"""

from __future__ import annotations

import base64
import logging
import pathlib
import subprocess
from typing import Optional

from phone_pilot.core.protocols import (
    AppDriver,
    InputDriver,
    ScreenDriver,
    UIDriver,
)
from phone_pilot.ios import wda_bridge
from phone_pilot.ios.device import log_capture
from phone_pilot.ios.pmd import runner as pmd_runner
from phone_pilot.ios.pmd import utils as pmd_utils
from phone_pilot.ios.ui import automator
from phone_pilot.ios.ui import finder

logger = logging.getLogger(__name__)


def _wda_call(serial: str, fn):
    """Run a WDA operation through the bridge's keep-alive reconnect."""
    return wda_bridge.with_reconnect(serial, fn)


# Android-style keycode → WDA pressButton name (design §4.1).
_KEYCODE_TO_WDA_BUTTON = {
    "HOME": "home",
    "VOLUME_UP": "volumeUp",
    "VOLUME_DOWN": "volumeDown",
    "POWER": "power",
    "SNAPSHOT": "snapshot",
}


def _resolve_wda_button(keycode: str) -> Optional[str]:
    """Map an Android-style keycode string to a WDA hardware button name."""
    s = str(keycode).strip().upper()
    if s.startswith("KEYCODE_"):
        s = s[len("KEYCODE_") :]
    return _KEYCODE_TO_WDA_BUTTON.get(s)


def _err(error: str, **extra) -> dict:
    """Build a failure envelope."""
    return {"ok": False, "error": error, "platform": "ios", **extra}


def _parse_simctl_listapps(text: str, include_system: bool) -> list[str]:
    """
    Extract bundle IDs from `simctl listapps` output (an old-style plist dict
    whose top-level keys, indented by 4 spaces, are bundle IDs). We track each
    top-level block to honor the include_system filter via its ApplicationType.
    """
    import re

    # Top-level key line: exactly 4 leading spaces, then "bundle.id" =   {
    key_re = re.compile(r'^ {4}"?([A-Za-z0-9_.\-]+)"?\s*=\s*\{\s*$')
    type_re = re.compile(r"ApplicationType\s*=\s*([A-Za-z]+)")

    bundles: list[str] = []
    current: Optional[str] = None
    app_type: str = ""
    for line in text.splitlines():
        m = key_re.match(line)
        if m and "." in m.group(1):
            # flush previous block
            if current is not None and (include_system or app_type != "System"):
                bundles.append(current)
            current = m.group(1)
            app_type = ""
            continue
        if current is not None:
            tm = type_re.search(line)
            if tm:
                app_type = tm.group(1)
    # flush last
    if current is not None and (include_system or app_type != "System"):
        bundles.append(current)
    return bundles


# ---------------------------------------------------------------------------
# iOSInputDriver  (5 entries — InputDriver protocol)
# ---------------------------------------------------------------------------


class iOSInputDriver:
    """iOS InputDriver — primary engine WDA. A1: stubs only."""

    def __init__(self, device_serial: str) -> None:
        self._serial = device_serial

    def tap(self, x: int, y: int, wait_s: float = 0.15) -> dict:
        """Tap at screen coordinates via WDA."""

        def _do(client):
            client.tap(int(x), int(y))
            return {"ok": True, "x": int(x), "y": int(y), "engine": "wda"}

        return _wda_call(self._serial, _do)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2) via WDA drag-for-duration."""

        def _do(client):
            client.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms / 1000.0)
            return {
                "ok": True,
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration_ms": duration_ms,
                "engine": "wda",
            }

        return _wda_call(self._serial, _do)

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> dict:
        """Long press at coordinates via WDA tap_hold."""

        def _do(client):
            client.tap_hold(int(x), int(y), duration_ms / 1000.0)
            return {"ok": True, "x": int(x), "y": int(y), "engine": "wda"}

        return _wda_call(self._serial, _do)

    def input_text(self, text: str, enter: bool = False) -> dict:
        """Input text into the focused field via WDA send_keys."""

        def _do(client):
            try:
                client.send_keys(text)
                if enter:
                    client.send_keys("\n")
            except Exception as exc:  # noqa: BLE001 — translate to error envelope
                return _err("no_active_input_field", detail=str(exc))
            return {"ok": True, "text": text, "engine": "wda"}

        return _wda_call(self._serial, _do)

    def keyevent(self, keycode: str) -> dict:
        """Send a hardware-button key event mapped to WDA pressButton."""
        button = _resolve_wda_button(keycode)
        if button is None:
            return _err("not_supported", keycode=keycode)

        def _do(client):
            client.press(button)
            return {"ok": True, "keycode": keycode, "button": button, "engine": "wda"}

        return _wda_call(self._serial, _do)


# ---------------------------------------------------------------------------
# iOSScreenDriver  (4 entries — ScreenDriver protocol)
# ---------------------------------------------------------------------------


class iOSScreenDriver:
    """iOS ScreenDriver — primary engine WDA. A1: stubs only."""

    def __init__(self, device_serial: str) -> None:
        self._serial = device_serial
        self._screen_size_cache: Optional[tuple[int, int]] = None
        self._record_proc: Optional[subprocess.Popen] = None
        self._record_path: Optional[str] = None

    def screenshot(self) -> bytes:
        """Capture current screen as PNG bytes via WDA."""

        def _do(client):
            # facebook-wda returns a PIL Image by default; format="raw" yields
            # PNG bytes directly (verified against live WDA on the simulator).
            try:
                raw = client.screenshot(format="raw")
            except TypeError:
                raw = client.screenshot()
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw)
            if isinstance(raw, str):
                return base64.b64decode(raw)
            # PIL.Image fallback → encode to PNG bytes
            save = getattr(raw, "save", None)
            if callable(save):
                import io

                buf = io.BytesIO()
                save(buf, format="PNG")
                return buf.getvalue()
            return b""

        result = _wda_call(self._serial, _do)
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
        # error envelope (dict) → no image; return empty bytes (never raise)
        logger.debug("screenshot failed: %s", result)
        return b""

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions in PIXELS (WDA points × scale). Cached."""
        if self._screen_size_cache:
            return self._screen_size_cache

        def _do(client):
            w, h = client.window_size()
            scale = getattr(client, "scale", 1) or 1
            return (int(w * scale), int(h * scale))

        result = _wda_call(self._serial, _do)
        if isinstance(result, tuple):
            self._screen_size_cache = result
            return result
        logger.debug("get_screen_size failed: %s", result)
        return (0, 0)

    def start_screenrecord(self, path: str, **kwargs) -> dict:
        """Start screen recording. Simulator: simctl recordVideo; real device: not_supported."""
        form = pmd_utils.detect_form_factor(self._serial)
        if form != "simulator":
            return _err("not_supported", reason="screenrecord_real_device_v1")

        try:
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            cmd = pmd_utils.simctl_prefix() + [
                "io",
                self._serial,
                "recordVideo",
                "--force",
                path,
            ]
            self._record_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._record_path = path
        except Exception as exc:  # noqa: BLE001
            return _err("screenrecord_start_failed", detail=str(exc))
        return {"ok": True, "path": path, "engine": "simctl"}

    def stop_screenrecord(self) -> dict:
        """Stop simulator screen recording; tolerate an already-exited process.

        `simctl recordVideo` only finalizes (writes the moov atom) the MP4 on
        SIGINT — SIGTERM/kill leaves a 0-byte/corrupt file. So we interrupt,
        not terminate.
        """
        import signal

        if self._record_proc is None:
            return _err("no_active_recording")
        path = self._record_path
        proc = self._record_proc
        try:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except Exception:
                # last resort if it ignored SIGINT
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    pass
        except (ProcessLookupError, OSError) as exc:
            logger.debug("recording process already exited: %s", exc)
        finally:
            self._record_proc = None
            self._record_path = None
        return {"ok": True, "path": path, "engine": "simctl"}


# ---------------------------------------------------------------------------
# iOSUIDriver  (5 entries — UIDriver protocol)
# ---------------------------------------------------------------------------


class iOSUIDriver:
    """iOS UIDriver — primary engine WDA source/automator. A1: stubs only."""

    def __init__(self, device_serial: str) -> None:
        self._serial = device_serial

    def _source(self) -> str:
        """Fetch WDA source as a JSON string (empty string on failure)."""

        def _do(client):
            return client.source(format="json")

        result = _wda_call(self._serial, _do)
        if isinstance(result, str):
            return result
        if isinstance(result, dict) and "type" in result:
            import json as _json

            return _json.dumps(result)
        logger.debug("dump source failed: %s", result)
        return ""

    def dump_ui_hierarchy(self) -> str:
        """Dump current UI hierarchy as a WDA JSON source string."""
        return self._source()

    def dump_ui_nodes(self) -> list:
        """Dump UI hierarchy as universal UINode list."""
        return automator.wda_source_to_ui_nodes(self._source())

    def collect_all_texts(self) -> list[str]:
        """Collect all visible text/label/value from UI tree."""
        return automator.collect_texts(self._source())

    def find_elements(self, selector: dict) -> list[dict]:
        """Find UI elements matching selector. Each result contains center_x/center_y."""
        return automator.find_in_source(self._source(), selector)

    def get_current_activity(self) -> dict:
        """Get current foreground app → {"package": bundleId, "activity": ""}."""

        def _do(client):
            info = client.app_current()
            bundle = ""
            if isinstance(info, dict):
                bundle = info.get("bundleId") or info.get("bundleID") or ""
            return {"ok": True, "package": bundle, "activity": ""}

        result = _wda_call(self._serial, _do)
        if isinstance(result, dict) and result.get("ok"):
            return result
        return _err("get_current_activity_failed", detail=str(result))


# ---------------------------------------------------------------------------
# iOSAppDriver  (5 entries — AppDriver protocol)
# ---------------------------------------------------------------------------


class iOSAppDriver:
    """iOS AppDriver — primary engine pymobiledevice3. A1: stubs only."""

    def __init__(self, device_serial: str) -> None:
        self._serial = device_serial

    def _is_sim(self) -> bool:
        """True if this serial is a (booted) simulator (design ADR-6 split)."""
        return pmd_utils.detect_form_factor(self._serial) == "simulator"

    def _simctl(self, args: list[str], *, timeout_s: float = 30.0):
        """Run a `simctl <args> <udid>` command (simulator path)."""
        return pmd_runner.run(pmd_utils.simctl_prefix() + args, timeout_s=timeout_s)

    def _pmd(self, args: list[str], *, timeout_s: float = 30.0):
        """Run a `pymobiledevice3 ... --udid <serial>` command (real-device path)."""
        exe = pmd_utils.pmd_executable()
        return pmd_runner.run([exe, *args, "--udid", self._serial], timeout_s=timeout_s)

    def list_packages(self, include_system: bool = False) -> list[str]:
        """List installed bundle IDs. simctl on simulators, pymobiledevice3 on real devices."""
        import json as _json

        try:
            if self._is_sim():
                # `simctl listapps <udid>` emits a plist-like dict; bundle IDs are the keys.
                proc = self._simctl(["listapps", self._serial])
                return _parse_simctl_listapps(proc.stdout or "", include_system)
            args = ["apps", "list"]
            if not include_system:
                args.append("--no-system")
            proc = self._pmd(args)
        except Exception as exc:  # noqa: BLE001
            logger.debug("list_packages failed: %s", exc)
            return []
        try:
            data = _json.loads(proc.stdout or "")
        except (ValueError, TypeError):
            return []
        if isinstance(data, dict):
            return list(data.keys())
        if isinstance(data, list):
            return [str(x) for x in data]
        return []

    def launch_app(self, package: str, activity: Optional[str] = None) -> dict:
        """Launch an app by bundle ID. activity ignored on iOS. Form-factor split."""
        try:
            if self._is_sim():
                proc = self._simctl(["launch", self._serial, package])
                engine = "simctl"
            else:
                proc = self._pmd(["developer", "dvt", "launch", package])
                engine = "pymobiledevice3"
        except Exception as exc:  # noqa: BLE001
            return _err("launch_failed", package=package, detail=str(exc))
        if proc.returncode != 0:
            return _err("launch_failed", package=package, detail=proc.stderr or "")
        return {"ok": True, "package": package, "engine": engine}

    def force_stop(self, package: str) -> dict:
        """Force stop an app. Form-factor split (simctl terminate / pmd kill)."""
        try:
            if self._is_sim():
                proc = self._simctl(["terminate", self._serial, package])
                engine = "simctl"
            else:
                proc = self._pmd(["developer", "dvt", "kill", package])
                engine = "pymobiledevice3"
        except Exception as exc:  # noqa: BLE001
            return _err("force_stop_failed", package=package, detail=str(exc))
        if proc.returncode != 0:
            return _err("force_stop_failed", package=package, detail=proc.stderr or "")
        return {"ok": True, "package": package, "engine": engine}

    def clear_data(self, package: str) -> dict:
        """Clear application data. iOS v1: not_supported (no direct API; see design §4.4)."""
        return _err("not_supported", package=package)

    def uninstall(self, package: str, keep_data: bool = False) -> dict:
        """Uninstall an app. keep_data ignored on iOS. Form-factor split."""
        try:
            if self._is_sim():
                proc = self._simctl(["uninstall", self._serial, package])
                engine = "simctl"
            else:
                proc = self._pmd(["apps", "uninstall", package])
                engine = "pymobiledevice3"
        except Exception as exc:  # noqa: BLE001
            return _err("uninstall_failed", package=package, detail=str(exc))
        if proc.returncode != 0:
            return _err("uninstall_failed", package=package, detail=proc.stderr or "")
        return {"ok": True, "package": package, "engine": engine}


# ---------------------------------------------------------------------------
# iOSDriver (composite — DeviceDriver protocol, 42 entries total)
# ---------------------------------------------------------------------------


class iOSDriver:
    """
    iOS DeviceDriver implementation — skeleton with stubs.

    Combines iOSInputDriver / iOSScreenDriver / iOSUIDriver / iOSAppDriver.
    Satisfies all 42 DeviceDriver protocol entries.

    NO eager imports of pymobiledevice3 or facebook-wda in __init__.
    Those are deferred to bridge/runner layers (A2/A3).
    """

    def __init__(self, device_serial: str) -> None:
        """
        Initialize iOS driver.

        Args:
            device_serial: Device UDID (40-hex or UUID style for iOS).

        Raises:
            ValueError: If device_serial is empty.
        """
        if not device_serial:
            raise ValueError("device_serial is required")
        self._device_serial = device_serial
        self._input = iOSInputDriver(device_serial)
        self._screen = iOSScreenDriver(device_serial)
        self._ui = iOSUIDriver(device_serial)
        self._app = iOSAppDriver(device_serial)

    # ---- sub-driver accessors (DeviceDriver protocol) ----

    @property
    def input(self) -> InputDriver:
        """Return input driver."""
        return self._input  # type: ignore[return-value]

    @property
    def screen(self) -> ScreenDriver:
        """Return screen driver."""
        return self._screen  # type: ignore[return-value]

    @property
    def ui(self) -> UIDriver:
        """Return UI driver."""
        return self._ui  # type: ignore[return-value]

    @property
    def app(self) -> AppDriver:
        """Return app driver."""
        return self._app  # type: ignore[return-value]

    # ---- identity (DeviceDriver protocol) ----

    @property
    def device_serial(self) -> str:
        """Return bound device UDID."""
        return self._device_serial

    @property
    def platform(self) -> str:
        """Return platform identifier."""
        return "ios"

    # ---- device-level control (DeviceDriver protocol, 17 entries) ----

    def _pmd(self, args: list[str], *, timeout_s: float = 30.0):
        """Run a `pymobiledevice3 ... --udid <serial>` command."""
        exe = pmd_utils.pmd_executable()
        return pmd_runner.run(
            [exe, *args, "--udid", self._device_serial], timeout_s=timeout_s
        )

    def go_home(self) -> dict:
        """Navigate to home screen via WDA."""

        def _do(client):
            client.home()
            return {"ok": True, "engine": "wda"}

        result = _wda_call(self._device_serial, _do)
        return result if isinstance(result, dict) else _err("go_home_failed")

    def go_back(self) -> dict:
        """
        iOS has no global back button. Degrade to a left-edge swipe and verify
        the foreground state actually changed (design §4.6 / Plan T9b).
        """

        def _do(client):
            before = ""
            try:
                info = client.app_current()
                before = info.get("bundleId", "") if isinstance(info, dict) else ""
            except Exception:
                before = ""
            # left-edge swipe back
            client.swipe(0, 400, 200, 400, 0.2)
            after = ""
            try:
                info2 = client.app_current()
                after = info2.get("bundleId", "") if isinstance(info2, dict) else ""
            except Exception:
                after = ""
            res = {"ok": True, "engine": "wda"}
            if before and after and before == after:
                res["warning"] = "go_back_may_not_have_worked"
            return res

        result = _wda_call(self._device_serial, _do)
        return result if isinstance(result, dict) else _err("go_back_failed")

    def unlock(self, pin: Optional[str] = None) -> dict:
        """Wake + unlock device screen via WDA (pin entry not supported in v1)."""

        def _do(client):
            client.unlock()
            return {"ok": True, "engine": "wda"}

        result = _wda_call(self._device_serial, _do)
        return result if isinstance(result, dict) else _err("unlock_failed")

    def lock_screen(self) -> dict:
        """Lock screen via WDA."""

        def _do(client):
            client.lock()
            return {"ok": True, "engine": "wda"}

        result = _wda_call(self._device_serial, _do)
        return result if isinstance(result, dict) else _err("lock_failed")

    def clear_background(self) -> dict:
        """Best-effort clear background apps. v1: not_supported."""
        return _err("not_supported")

    def shell(self, cmd: str) -> dict:
        """iOS 无宿主可执行的设备端 shell（非越狱）。v1: not_supported.
        iOS has no device-side shell without jailbreak. v1: not_supported."""
        return _err("not_supported", cmd=cmd)

    def device_info(self) -> dict:
        """Return device profile dict. Form-factor split (simctl / pmd lockdown)."""
        import json as _json

        if pmd_utils.detect_form_factor(self._device_serial) == "simulator":
            try:
                proc = pmd_runner.run(
                    pmd_utils.simctl_prefix() + ["list", "devices", "-j"],
                    timeout_s=15.0,
                )
                data = _json.loads(proc.stdout or "{}")
            except Exception as exc:  # noqa: BLE001
                return _err("device_info_failed", detail=str(exc))
            for runtime, items in (data.get("devices", {}) or {}).items():
                for it in items if isinstance(items, list) else []:
                    if it.get("udid") == self._device_serial:
                        return {
                            "ok": True,
                            "model": it.get("name", ""),
                            "os_version": runtime.split(".")[-1].replace("-", "."),
                            "device_name": it.get("name", ""),
                            "form_factor": "simulator",
                            "raw": it,
                        }
            return _err("device_info_failed", detail="simulator not found in list")

        try:
            proc = self._pmd(["lockdown", "info"])
            data = _json.loads(proc.stdout or "")
        except Exception as exc:  # noqa: BLE001
            return _err("device_info_failed", detail=str(exc))
        if not isinstance(data, dict):
            return _err("device_info_failed", detail="unparseable lockdown output")
        return {
            "ok": True,
            "model": data.get("ProductType", ""),
            "os_version": data.get("ProductVersion", ""),
            "device_name": data.get("DeviceName", ""),
            "form_factor": "real",
            "raw": data,
        }

    def open_deeplink(self, uri: str, package: Optional[str] = None, **kwargs) -> dict:
        """Open a page via deeplink/scheme URI through WDA."""

        def _do(client):
            client.open_url(uri)
            return {"ok": True, "uri": uri, "engine": "wda"}

        result = _wda_call(self._device_serial, _do)
        return result if isinstance(result, dict) else _err("open_deeplink_failed")

    def launch_from_home(self, app_name: str, **kwargs) -> dict:
        """Launch app from home screen (delegates to finder)."""
        return finder.launch_from_home(self._device_serial, app_name, **kwargs)

    def dump_memory_profile(self, package: str, **kwargs) -> dict:
        """hprof is Android-only. iOS: not_supported."""
        return _err("not_supported", package=package)

    def push_file(self, local_path: str, remote_path: str) -> dict:
        """Push a local file to the device's AFC media sandbox (real device only)."""
        try:
            proc = self._pmd(["afc", "push", local_path, remote_path])
        except Exception as exc:  # noqa: BLE001
            return _err("push_failed", detail=str(exc))
        if proc.returncode != 0 or "Usage:" in (proc.stderr or ""):
            return _err("push_failed", detail=(proc.stderr or "").strip()[:200])
        return {"ok": True, "local_path": local_path, "remote_path": remote_path}

    def pull_file(self, remote_path: str, local_path: str) -> dict:
        """Pull a file from the device's AFC sandbox to host (real device only)."""
        try:
            pathlib.Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            # `-i/--ignore-errors` is required by pymobiledevice3 afc pull.
            proc = self._pmd(["afc", "pull", "-i", remote_path, local_path])
        except Exception as exc:  # noqa: BLE001
            return _err("pull_failed", detail=str(exc))
        if proc.returncode != 0 or "Usage:" in (proc.stderr or ""):
            return _err("pull_failed", detail=(proc.stderr or "").strip()[:200])
        return {"ok": True, "remote_path": remote_path, "local_path": local_path}

    def remove_remote_file(self, remote_path: str) -> dict:
        """Remove a file from the device's AFC sandbox (real device only)."""
        try:
            proc = self._pmd(["afc", "rm", remote_path])
        except Exception as exc:  # noqa: BLE001
            return _err("remove_failed", detail=str(exc))
        if proc.returncode != 0 or "Usage:" in (proc.stderr or ""):
            return _err("remove_failed", detail=(proc.stderr or "").strip()[:200])
        return {"ok": True, "remote": remote_path}

    def read_log(self, lines: int = 200, **kwargs) -> str:
        """Read recent syslog output (best-effort). Form-factor split.

        Supports the same kwargs as the Android driver so the script_api log
        helpers (logcat_since / logcat_assert_sequence) work uniformly:
          - ``since``: iOS timestamp "YYYY-MM-DD HH:MM:SS[.ffffff]" → log show --start
          - ``tag_filter``: substring filter applied host-side per line
        """
        since = kwargs.get("since")
        tag_filter = kwargs.get("tag_filter")
        try:
            if pmd_utils.detect_form_factor(self._device_serial) == "simulator":
                cmd = pmd_utils.simctl_prefix() + [
                    "spawn",
                    self._device_serial,
                    "log",
                    "show",
                    "--style",
                    "syslog",
                ]
                if since:
                    cmd += ["--start", str(since)]
                else:
                    cmd += ["--last", "30s"]
                proc = pmd_runner.run(cmd, timeout_s=25.0)
            else:
                # pymobiledevice3 streams syslog; bound it with a short timeout.
                proc = self._pmd(["syslog"], timeout_s=6.0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("read_log failed: %s", exc)
            return ""
        out = proc.stdout or ""
        if not out:
            return ""
        result_lines = out.splitlines()
        # Drop the "Timestamp ... (process)[PID]" header simctl prints.
        if result_lines and result_lines[0].lstrip().startswith("Timestamp"):
            result_lines = result_lines[1:]
        if tag_filter:
            result_lines = [ln for ln in result_lines if str(tag_filter) in ln]
        # `since` already bounds the window server-side; only tail when no `since`.
        if not since and lines:
            result_lines = result_lines[-int(lines) :]
        return "\n".join(result_lines)

    def logcat_time(self) -> Optional[str]:
        """Return the current host time as an iOS log marker for ``read_log(since=)``.

        iOS `log show --start` accepts local wall-clock "YYYY-MM-DD HH:MM:SS";
        the simulator shares the host clock, so host time is the correct marker.
        """
        import datetime

        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def clear_log(self) -> dict:
        """iOS syslog has no clear API. Returns not_supported."""
        return _err("not_supported")

    def dump_log(self, path: str, **kwargs) -> dict:
        """Export a slice of syslog to a host file via a short capture window."""
        return _err("not_implemented")

    def start_log_capture(
        self,
        output_path: str,
        *,
        tags: str = "",
        exclude_tags: str = "",
        level: str = "",
        process: str = "",
    ) -> dict:
        """Start background syslog capture to a file (delegates to log_capture)."""
        return log_capture.start_log_capture(
            self._device_serial,
            output_path,
            tags=tags,
            exclude_tags=exclude_tags,
            level=level,
            process=process,
        )

    def stop_log_capture(self, output_path: str) -> dict:
        """Stop background syslog capture for the given output_path."""
        return log_capture.stop_log_capture(self._device_serial, output_path)
