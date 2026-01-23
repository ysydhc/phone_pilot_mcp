import asyncio
import pathlib
from typing import Any, Optional

from android_tool.android_device_utils import get_current_focus
from android_tool.logcat import dump_logcat
from android_tool.recordings_store import (
    iso_now,
    now_dirname,
    safe_name,
    write_json,
)
from android_tool.screenshot import take_screenshot_png_bytes


async def _collect_artifacts_into_dir(
    *,
    device_serial: Optional[str],
    base_dir: pathlib.Path,
    name: str,
    logcat_lines: int = 5000,
    logcat_filter_spec: Optional[str] = None,
    logcat_package: Optional[str] = None,
    logcat_tag_filter: Optional[str] = None,
    include_focus: bool = True,
    include_screenshot: bool = True,
) -> dict:
    safe = safe_name(name or "artifacts")
    ts = now_dirname()
    serial_part = device_serial or "default"
    art_dir = base_dir / "artifacts" / f"{ts}_{serial_part}_{safe}"
    art_dir.mkdir(parents=True, exist_ok=True)

    out: dict[str, Any] = {
        "ok": True,
        "device_serial": device_serial,
        "artifacts_dir": str(art_dir),
        "created_at": iso_now(),
    }

    # Screenshot
    if include_screenshot:
        try:
            png = await asyncio.to_thread(take_screenshot_png_bytes, device_serial)
            sp = art_dir / "screenshot.png"
            sp.write_bytes(png)
            out["screenshot_path"] = str(sp)
        except Exception as e:
            out["ok"] = False
            out["screenshot_error"] = str(e)

    # Focus / current activity
    if include_focus:
        try:
            focus = await asyncio.to_thread(get_current_focus, device_serial)
            fp = art_dir / "focus.json"
            await asyncio.to_thread(write_json, fp, focus if isinstance(focus, dict) else {"focus": focus})
            out["focus_path"] = str(fp)
            if isinstance(focus, dict):
                out["focused_package"] = focus.get("package")
                out["focused_activity"] = focus.get("activity")
        except Exception as e:
            out["ok"] = False
            out["focus_error"] = str(e)

    # Logcat dump
    try:
        lp = art_dir / "logcat.txt"
        pkg = logcat_package
        if not pkg:
            pkg = out.get("focused_package") if isinstance(out.get("focused_package"), str) else None
        log_res = await asyncio.to_thread(
            dump_logcat,
            device_serial,
            lp,
            lines=int(logcat_lines),
            fmt="threadtime",
            filter_spec=logcat_filter_spec,
            tag_filter=logcat_tag_filter,
            package_name=pkg,
        )
        out["logcat"] = log_res
    except Exception as e:
        out["ok"] = False
        out["logcat_error"] = str(e)

    return out