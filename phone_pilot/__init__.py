"""
phone_pilot - Multi-platform mobile automation framework.

Supports Android, iOS (planned), and HarmonyOS through unified interfaces.

Architecture:
- core/: Platform-agnostic coordination (protocols, storage, run session)
- extensions/: Platform-agnostic capabilities (vision, ocr, diff)
- android/: Android platform implementation
- harmony/: HarmonyOS platform implementation
- ios/: iOS platform (planned)
- mcp/: MCP Server with phone_* tools
- memory_analyze/: Memory profiling and leak detection

Usage:
    # Script API (recommended for automation scripts)
    from phone_pilot.script_api import (
        ScriptContext, find_text, find_image, screenshot,
        run_script, retry, chain,
    )
    ctx = ScriptContext(device_serial="your_device")
    find_text(ctx, "Settings").tap()

    # Driver-level API
    from phone_pilot.android import AndroidDriver
    driver = AndroidDriver("device_serial")
    driver.input.tap(100, 200)

    # MCP Server (for AI agent integration)
    # Start: phone-pilot-mcp
"""

__version__ = "0.5.1"

# Convenience imports
from phone_pilot.core import (
    DeviceSkills,
    DeviceDriver,
    res_add,
    res_update,
    res_delete,
    res_get,
    res_list,
    res_resolve,
    resolve_res_ref,
    resolve_or_cache_path,
)
from phone_pilot.android import AndroidDriver

try:
    from phone_pilot.harmony import HarmonyDriver
except ImportError:
    HarmonyDriver = None  # type: ignore[assignment,misc]

__all__ = [
    "__version__",
    "DeviceSkills",
    "DeviceDriver",
    "AndroidDriver",
    "HarmonyDriver",
    "res_add",
    "res_update",
    "res_delete",
    "res_get",
    "res_list",
    "res_resolve",
    "resolve_res_ref",
    "resolve_or_cache_path",
]
