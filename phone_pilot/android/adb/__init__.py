"""
ADB (Android Debug Bridge) utilities.

Provides command execution, output parsing, and device communication.
"""

from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.adb.parsers import parse_adb_devices
from phone_pilot.android.adb.utils import (
    adb_executable,
    adb_prefix,
    wait_for_device,
    push_file,
    push_script,
)
from phone_pilot.android.adb.logcat import (
    clear_logcat,
    read_logcat,
    dump_logcat,
)
from phone_pilot.android.adb.screenshot import (
    take_screenshot_png_bytes,
    save_screenshot_png,
    png_bytes_to_base64,
)
from phone_pilot.android.adb.screenrecord import (
    start_screenrecord_detached,
    stop_screenrecord_detached,
    WorkflowScreenRecorder,
)

__all__ = [
    # Runner
    "CommandRunner",
    # Parsers
    "parse_adb_devices",
    # Utils
    "adb_executable",
    "adb_prefix",
    "wait_for_device",
    "push_file",
    "push_script",
    # Logcat
    "clear_logcat",
    "read_logcat",
    "dump_logcat",
    # Screenshot
    "take_screenshot_png_bytes",
    "save_screenshot_png",
    "png_bytes_to_base64",
    # Screenrecord
    "start_screenrecord_detached",
    "stop_screenrecord_detached",
    "WorkflowScreenRecorder",
]
