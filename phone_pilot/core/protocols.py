"""
Driver Protocol definitions for multi-platform support.

These Protocol classes define the interface that each platform driver must implement.
Using Python's Protocol (structural subtyping) allows for duck-typing while maintaining
type safety and IDE support.

Platform implementations:
- Android: phone_pilot/android/driver.py
- iOS: phone_pilot/ios/driver.py (planned)
- HarmonyOS: phone_pilot/harmony/driver.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Optional, runtime_checkable

if TYPE_CHECKING:
    from phone_pilot.core.ui_node import UINode


@runtime_checkable
class InputDriver(Protocol):
    """Input operations driver - handles touch, swipe, text input, and key events."""

    def tap(self, x: int, y: int, wait_s: float = 0.15) -> dict:
        """Tap at screen coordinates."""
        ...

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2)."""
        ...

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> dict:
        """Long press at coordinates."""
        ...

    def input_text(self, text: str, enter: bool = False) -> dict:
        """Input text (requires focused text field)."""
        ...

    def keyevent(self, keycode: str) -> dict:
        """Send a key event (e.g. "KEYCODE_HOME", "KEYCODE_BACK")."""
        ...


@runtime_checkable
class ScreenDriver(Protocol):
    """Screen operations driver - handles screenshots and screen recording."""

    def screenshot(self) -> bytes:
        """Capture current screen as PNG bytes."""
        ...

    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions → (width, height)."""
        ...

    def start_screenrecord(self, path: str, **kwargs) -> dict:
        """Start screen recording."""
        ...

    def stop_screenrecord(self) -> dict:
        """Stop current screen recording."""
        ...


@runtime_checkable
class UIDriver(Protocol):
    """UI tree operations driver - handles UI hierarchy inspection."""

    def dump_ui_hierarchy(self) -> str:
        """Dump current UI hierarchy as XML/JSON string."""
        ...

    def dump_ui_nodes(self) -> list[UINode]:
        """Dump UI hierarchy as a list of universal UINode objects."""
        ...

    def collect_all_texts(self) -> list[str]:
        """Collect all visible text/content-desc/hint from UI tree."""
        ...

    def find_elements(self, selector: dict) -> list[dict]:
        """
        Find UI elements matching selector.

        Selector keys (platform-agnostic):
            text, text_contains, desc, desc_contains,
            resource_id, class_name, clickable, enabled

        Returns list of element dicts. Each dict MUST contain at least:
            center_x, center_y  — screen coordinates of the element center
        and SHOULD contain: text, bounds, resource_id, class_name, etc.
        """
        ...

    def get_current_activity(self) -> dict:
        """Get current foreground activity/ability → {"package": str, "activity": str}."""
        ...


@runtime_checkable
class AppDriver(Protocol):
    """Application management driver - handles app lifecycle."""

    def list_packages(self, include_system: bool = False) -> list[str]:
        """List installed packages."""
        ...

    def launch_app(self, package: str, activity: Optional[str] = None) -> dict:
        """Launch an application."""
        ...

    def force_stop(self, package: str) -> dict:
        """Force stop an application."""
        ...

    def clear_data(self, package: str) -> dict:
        """Clear application data."""
        ...


@runtime_checkable
class DeviceDriver(Protocol):
    """
    Top-level device driver that combines all sub-drivers.

    This is the main interface for platform implementations.
    Each platform (Android, iOS, HarmonyOS) implements this protocol.
    """

    # ---- sub-driver accessors ----

    @property
    def input(self) -> InputDriver:
        """Get input operations driver."""
        ...

    @property
    def screen(self) -> ScreenDriver:
        """Get screen operations driver."""
        ...

    @property
    def ui(self) -> UIDriver:
        """Get UI tree operations driver."""
        ...

    @property
    def app(self) -> AppDriver:
        """Get application management driver."""
        ...

    # ---- identity ----

    @property
    def device_serial(self) -> str:
        """Get device serial/identifier."""
        ...

    @property
    def platform(self) -> str:
        """Get platform identifier → "android" | "ios" | "harmony"."""
        ...

    # ---- device-level control ----

    def go_home(self) -> dict:
        """Navigate to home screen."""
        ...

    def go_back(self) -> dict:
        """Press back button."""
        ...

    def unlock(self, pin: Optional[str] = None) -> dict:
        """Wake + unlock device screen."""
        ...

    def lock_screen(self) -> dict:
        """Lock / turn off screen."""
        ...

    def clear_background(self) -> dict:
        """Best-effort clear background apps."""
        ...

    def device_info(self) -> dict:
        """Return device profile dict (model, OS version, screen size, …)."""
        ...

    # ---- high-level skills ----

    def launch_from_home(self, app_name: str, **kwargs) -> dict:
        """Launch an app from the home screen (go home → find icon → tap)."""
        ...

    def dump_memory_profile(self, package: str, **kwargs) -> dict:
        """Dump memory profile (Android hprof). Returns not_supported on others."""
        ...

    # ---- file transfer ----

    def pull_file(self, remote_path: str, local_path: str) -> dict:
        """Pull a file from device to host.
        从设备拉取文件到主机。

        Returns: {"ok": bool, ...}
        """
        ...

    def remove_remote_file(self, remote_path: str) -> dict:
        """Remove a file from the device.
        删除设备上的文件。

        Returns: {"ok": bool, ...}
        """
        ...

    # ---- log ----

    def read_log(self, lines: int = 200, **kwargs) -> str:
        """Read recent device log (logcat / hilog)."""
        ...

    def clear_log(self) -> dict:
        """Clear device log buffer."""
        ...

    def dump_log(self, path: str, **kwargs) -> dict:
        """Export device log to a file on host."""
        ...

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
        """Start background log capture (logcat/hilog) to a file.
        启动后台日志捕获，输出到文件。

        Args:
            output_path: Host path for the log file.
            tags: Comma-separated tag filter (e.g. "MyApp,Adm-").
            exclude_tags: Comma-separated tags to exclude.
            level: Minimum log level (D/I/W/E/F).
            process: Filter by process name or PID.

        Returns: {"ok": bool, "pid": int, ...}
        """
        ...

    def stop_log_capture(self, output_path: str) -> dict:
        """Stop background log capture for the given output_path.
        停止指定 output_path 的后台日志捕获。

        Returns: {"ok": bool, "lines": int, ...}
        """
        ...
