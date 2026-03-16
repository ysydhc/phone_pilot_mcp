#!/usr/bin/env python3
"""
Android device/UI helpers shared by MCP tools.

This module intentionally keeps all ADB/Android interaction "plumbing" in one place:
- navigation to home screen
- (re)starting apps via am/monkey
- best-effort foreground focus detection
- reading screen size and touch ABS ranges for coordinate scaling
"""

from __future__ import annotations

import re
import time
import zipfile
from typing import Optional

from phone_pilot.android.adb.parsers import parse_adb_devices
from phone_pilot.core.storage import recordings_root
from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner

import os
import pathlib
import shutil
import subprocess
import tempfile


def _needs_unicode_fallback(text: str) -> bool:
    """Return True when text requires clipboard-based Unicode input."""
    try:
        return any(ord(ch) > 127 for ch in (text or ""))
    except Exception:
        return True


def set_clipboard_text(device_serial: Optional[str], text: str) -> dict:
    """
    Best-effort set clipboard text on device (Android 10+ typically supports this).

    Uses:
      adb shell cmd clipboard set <text>
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    t = "" if text is None else str(text)
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "cmd", "clipboard", "set", t],
        check=False,
        delay_s=0.1,
        log_output=False,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stderr": (proc.stderr or "").strip()}


def input_keyevent(device_serial: Optional[str], key: str, *, wait_s: float = 0.12, silent: bool = False) -> dict:
    """Send a keyevent (supports KEYCODE_* names or numeric codes)."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    k = str(key or "").strip()
    if not k:
        return {"ok": False, "error": "key is required"}
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "input", "keyevent", k],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
        silent=silent,
        timeout_s=15,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "key": k, "stderr": (proc.stderr or "").strip()}


def input_long_press(
    device_serial: Optional[str],
    x: int,
    y: int,
    *,
    duration_ms: int = 800,
    wait_s: float = 0.25,
) -> dict:
    """
    Long-press at (x,y) by using `input swipe x y x y duration_ms`.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    try:
        xi, yi = int(x), int(y)
    except Exception:
        return {"ok": False, "error": "x/y must be integers", "x": x, "y": y}
    try:
        dur = int(duration_ms)
    except Exception:
        dur = 800
    dur = max(50, min(dur, 5000))
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "input", "swipe", str(xi), str(yi), str(xi), str(yi), str(dur)],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "device_serial": device_serial,
        "x": xi,
        "y": yi,
        "duration_ms": dur,
        "stderr": (proc.stderr or "").strip(),
    }


def input_text(device_serial: Optional[str], text: str, *, enter: bool = False, wait_s: float = 0.15) -> dict:
    """
    Input text into currently focused field.

    Strategy:
    - If text contains non-ASCII (e.g. Chinese), use clipboard + KEYCODE_PASTE (279).
    - Else use `adb shell input text` with spaces encoded as %s.
    """
    if not device_serial:
        return {"ok": False, "Added": False, "error": "device_serial is required"}
    t = "" if text is None else str(text)

    # Unicode fallback: clipboard + paste
    if _needs_unicode_fallback(t):
        cb = set_clipboard_text(device_serial, t)
        if not cb.get("ok"):
            return {"ok": False, "mode": "clipboard", "error": "set_clipboard_failed", "detail": cb}
        paste = input_keyevent(device_serial, "279", wait_s=wait_s)  # KEYCODE_PASTE
        if not paste.get("ok"):
            return {"ok": False, "mode": "clipboard", "error": "paste_failed", "detail": paste}
        ent = None
        if enter:
            ent = input_keyevent(device_serial, "KEYCODE_ENTER", wait_s=wait_s)
        return {"ok": True, "mode": "clipboard", "text": t, "enter": bool(enter), "enter_result": ent}

    # ASCII: input text; encode spaces as %s
    safe = t.replace(" ", "%s")
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "input", "text", safe],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    if proc.returncode != 0:
        return {"ok": False, "mode": "input_text", "returncode": proc.returncode, "stderr": (proc.stderr or "").strip()}
    ent2 = None
    if enter:
        ent2 = input_keyevent(device_serial, "KEYCODE_ENTER", wait_s=wait_s)
    return {"ok": True, "mode": "input_text", "text": t, "sent": safe, "enter": bool(enter), "enter_result": ent2}

def list_installed_packages(
    device_serial: Optional[str],
    *,
    include_system: bool = True,
) -> list[str]:
    """
    List all installed app package names on the device.

    Implementation uses:
      adb shell pm list packages
    or, when include_system=False:
      adb shell pm list packages -3

    Returns a sorted list of package names.
    """
    if not device_serial:
        return []
    cmd = adb_prefix(device_serial) + ["shell", "pm", "list", "packages"]
    if not include_system:
        cmd.append("-3")
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False, silent=True)
    out = proc.stdout or ""
    pkgs: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Typical line: "package:com.example.app"
        if line.startswith("package:"):
            pkg = line.split("package:", 1)[1].strip()
        else:
            pkg = line
        if pkg:
            pkgs.add(pkg)
    return sorted(pkgs)

def pm_list_packages_with_apk_path(
    device_serial: Optional[str],
    *,
    include_system: bool = True,
) -> dict:
    """
    Run `pm list packages -f` and return parsed results with status.
    """
    if not device_serial:
        return {"ok": False, "returncode": 2, "packages": [], "stderr": "device_serial is required"}
    cmd = adb_prefix(device_serial) + ["shell", "pm", "list", "packages", "-f"]
    if not include_system:
        cmd.append("-3")
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False, silent=True)
    out = proc.stdout or ""
    pkgs: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("package:"):
            pkg = line.split("package:", 1)[1].strip()
        else:
            pkg = line
        if pkg:
            pkgs.add(pkg)
    return sorted(pkgs)
    err = (proc.stderr or "").strip()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "packages": sorted(pkgs), "stderr": err}


def pm_list_packages(
    device_serial: Optional[str],
    *,
    include_system: bool = True,
) -> dict:
    """
    Run `pm list packages` and return parsed results with status.

    Returns:
      {
        ok: bool,
        returncode: int,
        packages: list[str],
        stderr: str,
      }
    """
    if not device_serial:
        return {"ok": False, "returncode": 2, "packages": [], "stderr": "device_serial is required"}
    cmd = adb_prefix(device_serial) + ["shell", "pm", "list", "packages"]
    if not include_system:
        cmd.append("-3")
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False, silent=True)
    out = proc.stdout or ""
    pkgs: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("package:"):
            pkg = line.split("package:", 1)[1].strip()
        else:
            pkg = line
        if pkg:
            pkgs.add(pkg)
    err = (proc.stderr or "").strip()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "packages": sorted(pkgs), "stderr": err}


_RE_VERSION_NAME = re.compile(r"\bversionName=(?P<name>\S+)")
_RE_VERSION_CODE = re.compile(r"\bversionCode=(?P<code>\d+)")
_RE_APP_LABEL = re.compile(r"^\s*application-label(?:-[^:]+)?:\s*(?P<label>.*)\s*$")
_RE_NON_LOCALIZED_LABEL = re.compile(r"\bnonLocalizedLabel=(?P<label>'[^']*'|\"[^\"]*\"|\S+)")
_RE_LABEL_EQ = re.compile(r"\blabel=(?P<label>'[^']*'|\"[^\"]*\"|\S+)")
_RE_AAPT_VERSION_CODE = re.compile(r"versionCode='([^']*)'")
_RE_AAPT_VERSION_NAME = re.compile(r"versionName='([^']*)'")
_RE_AAPT_APP_ICON = re.compile(r"application-icon(?:-(?P<dpi>\d+))?:'(?P<path>[^']+)'")
_RE_AAPT_APP_ICON_FALLBACK = re.compile(r"application:\s+label='[^']*'\s+icon='(?P<path>[^']+)'")
_DPI_ALIAS = {
    "ldpi": 120,
    "mdpi": 160,
    "hdpi": 240,
    "xhdpi": 320,
    "xxhdpi": 480,
    "xxxhdpi": 640,
}

def get_installed_apps(device_serial: Optional[str], *, include_system: bool = True) -> dict:
    """
    获取已安装应用列表（包名 + APK 路径）。
    """
    if not device_serial:
        return {"ok": False, "returncode": 2, "apps": [], "stderr": "device_serial is required"}
    cmd = adb_prefix(device_serial) + ["shell", "pm", "list", "packages", "-f"]
    if not include_system:
        cmd.append("-3")
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    out = proc.stdout or ""
    apps: list[dict[str, str]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("package:"):
            line = line.split("package:", 1)[1].strip()
        m = re.match(r"(?P<apk>/.*?\.apk)=(?P<pkg>[^\s]+)", line)
        if not m:
            continue
        apk_path = m.group("apk")
        package_name = m.group("pkg")
        if package_name:
            apps.append({"package_name": package_name, "apk_path": apk_path})
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "apps": apps,
        "stderr": (proc.stderr or "").strip(),
    }

def _aapt_dump_badging_info(aapt_bin: str, apk_path: pathlib.Path) -> dict:
    """Parse app label, version, and icons via `aapt dump badging`."""
    try:
        proc = subprocess.run(
            [aapt_bin, "dump", "badging", str(apk_path)],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out = proc.stdout or ""
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip(), "stdout": out}
    label = None
    icons: dict[int, str] = {}
    icon_default = None
    m = re.search(r"application-label:'([^']*)'", out)
    if m and m.group(1).strip():
        label = m.group(1).strip()
    if not label:
        m = re.search(r"application-label-[^:]+:'([^']*)'", out)
        if m and m.group(1).strip():
            label = m.group(1).strip()
    m = _RE_AAPT_APP_ICON_FALLBACK.search(out)
    if m and m.group("path"):
        icon_default = m.group("path")
    for raw in out.splitlines():
        line = raw.strip()
        m_icon = _RE_AAPT_APP_ICON.search(line)
        if not m_icon:
            continue
        dpi = m_icon.group("dpi")
        path = m_icon.group("path")
        if dpi and dpi.isdigit():
            icons[int(dpi)] = path
        elif path:
            icon_default = path

    vcode = None
    vname = None
    for raw in out.splitlines():
        line = raw.strip()
        if not line.startswith("package:"):
            continue
        m1 = _RE_AAPT_VERSION_CODE.search(line)
        if m1:
            try:
                vcode = int(m1.group(1))
            except Exception:
                vcode = None
        m2 = _RE_AAPT_VERSION_NAME.search(line)
        if m2:
            vname = m2.group(1)
        break
    return {
        "ok": True,
        "label": label,
        "version_code": vcode,
        "version_name": vname,
        "icons": icons,
        "icon_default": icon_default,
    }


def _aapt2_dump_xmltree_icon_ref(aapt2_bin: str, apk_path: pathlib.Path) -> Optional[str]:
    """
    Extract android:icon from AndroidManifest.xml using aapt2 xmltree.
    """
    try:
        proc = subprocess.run(
            [aapt2_bin, "dump", "xmltree", str(apk_path), "AndroidManifest.xml"],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout or ""
    icon_ref = None
    round_ref = None
    for raw in out.splitlines():
        line = raw.strip()
        if "android:icon" not in line and "android:roundIcon" not in line:
            continue
        # Examples:
        # A: android:icon(0x01010002)=@0x7f0802ae
        # A: android:icon(0x01010002)=@mipmap/ic_launcher
        if "android:icon" in line:
            m = re.search(r"android:icon\([^)]+\)=@([0-9a-zA-Z_./]+)", line)
            if m:
                icon_ref = "@" + m.group(1)
        if "android:roundIcon" in line:
            m = re.search(r"android:roundIcon\([^)]+\)=@([0-9a-zA-Z_./]+)", line)
            if m:
                round_ref = "@" + m.group(1)
    return icon_ref or round_ref


def _axml_read_u16(data: bytes, offset: int) -> int:
    """Read a little-endian uint16 from bytes at offset."""
    return int.from_bytes(data[offset : offset + 2], "little")


def _axml_read_u32(data: bytes, offset: int) -> int:
    """Read a little-endian uint32 from bytes at offset."""
    return int.from_bytes(data[offset : offset + 4], "little")


def _axml_read_length8(data: bytes, offset: int) -> tuple[int, int]:
    """Read a UTF-8 string length from AXML string pool."""
    first = data[offset]
    if first & 0x80:
        second = data[offset + 1]
        return ((first & 0x7F) << 7) | second, offset + 2
    return first, offset + 1


def _axml_read_length16(data: bytes, offset: int) -> tuple[int, int]:
    """Read a UTF-16 string length from AXML string pool."""
    first = _axml_read_u16(data, offset)
    if first & 0x8000:
        second = _axml_read_u16(data, offset + 2)
        return ((first & 0x7FFF) << 16) | second, offset + 4
    return first, offset + 2


def _axml_decode_string_pool(data: bytes, offset: int) -> tuple[list[str], int]:
    """Decode AXML string pool and return (strings, end_offset)."""
    if len(data) < offset + 28:
        return [], offset
    chunk_type = _axml_read_u16(data, offset)
    header_size = _axml_read_u16(data, offset + 2)
    chunk_size = _axml_read_u32(data, offset + 4)
    if chunk_type != 0x0001 or header_size < 28:
        return [], offset
    string_count = _axml_read_u32(data, offset + 8)
    style_count = _axml_read_u32(data, offset + 12)
    flags = _axml_read_u32(data, offset + 16)
    strings_start = _axml_read_u32(data, offset + 20)
    styles_start = _axml_read_u32(data, offset + 24)
    if string_count == 0:
        return [], offset + chunk_size
    strings_offset = offset + header_size
    offsets: list[int] = []
    for i in range(string_count):
        pos = strings_offset + (i * 4)
        if pos + 4 > len(data):
            return [], offset + chunk_size
        offsets.append(_axml_read_u32(data, pos))
    strings: list[str] = []
    strings_base = offset + strings_start
    is_utf8 = bool(flags & 0x00000100)
    for off in offsets:
        s_off = strings_base + off
        if s_off >= len(data):
            strings.append("")
            continue
        try:
            if is_utf8:
                _, s_off = _axml_read_length8(data, s_off)
                byte_len, s_off = _axml_read_length8(data, s_off)
                raw = data[s_off : s_off + byte_len]
                strings.append(raw.decode("utf-8", errors="replace"))
            else:
                char_len, s_off = _axml_read_length16(data, s_off)
                raw = data[s_off : s_off + (char_len * 2)]
                strings.append(raw.decode("utf-16le", errors="replace"))
        except Exception:
            strings.append("")
    end_offset = offset + (styles_start if style_count and styles_start else chunk_size)
    return strings, end_offset


def _axml_attr_ref_from_parts(strings: list[str], raw_idx: int, value_type: int, value_data: int) -> Optional[str]:
    """Convert raw/value parts into a @resource reference string."""
    raw_val = strings[raw_idx] if raw_idx < len(strings) else ""
    if raw_val:
        return raw_val if raw_val.startswith("@") else f"@{raw_val}"
    if value_type == 0x03 and value_data < len(strings):
        val = strings[value_data]
        return val if val.startswith("@") else f"@{val}"
    if value_type in (0x01, 0x07):
        return f"@0x{value_data:08x}"
    return None


def _axml_icon_ref_from_bytes(axml: bytes, *, prefer_round_icon: bool = False) -> Optional[str]:
    """Extract android:icon reference from binary AXML bytes."""
    if len(axml) < 8:
        return None
    chunk_type = _axml_read_u16(axml, 0)
    if chunk_type != 0x0003:
        return None
    total_size = _axml_read_u32(axml, 4)
    pos = _axml_read_u16(axml, 2)
    if pos < 8:
        pos = 8
    strings: list[str] = []
    app_logo = None
    app_icon = None
    app_round = None
    alias_icon = None
    alias_round = None
    launcher_icon = None
    launcher_round = None
    stack: list[str] = []
    current_activity = None
    in_intent_filter = False
    while pos + 8 <= len(axml) and pos < total_size:
        c_type = _axml_read_u16(axml, pos)
        c_header = _axml_read_u16(axml, pos + 2)
        c_size = _axml_read_u32(axml, pos + 4)
        if c_size <= 0:
            break
        if c_type == 0x0001:
            strings, _ = _axml_decode_string_pool(axml, pos)
        elif c_type == 0x0102 and strings:
            attr_ext = pos + c_header
            if attr_ext + 20 <= len(axml):
                elem_name_idx = _axml_read_u32(axml, attr_ext + 4)
                elem_name = strings[elem_name_idx] if elem_name_idx < len(strings) else ""
                stack.append(elem_name)
                attr_start = _axml_read_u16(axml, attr_ext + 8)
                attr_size = _axml_read_u16(axml, attr_ext + 10)
                attr_count = _axml_read_u16(axml, attr_ext + 12)
                attr_base = attr_ext + attr_start
                attrs: dict[str, str] = {}
                for i in range(attr_count):
                    a_off = attr_base + (i * attr_size)
                    if a_off + 20 > len(axml):
                        continue
                    name_idx = _axml_read_u32(axml, a_off + 4)
                    raw_idx = _axml_read_u32(axml, a_off + 8)
                    value_type = axml[a_off + 15]
                    value_data = _axml_read_u32(axml, a_off + 16)
                    name = strings[name_idx] if name_idx < len(strings) else ""
                    if not name:
                        continue
                    ref = _axml_attr_ref_from_parts(strings, raw_idx, value_type, value_data)
                    if ref:
                        attrs[name] = ref
                if elem_name == "application":
                    if "logo" in attrs:
                        app_logo = app_logo or attrs.get("logo")
                    if "icon" in attrs:
                        app_icon = app_icon or attrs.get("icon")
                    if "roundIcon" in attrs:
                        app_round = app_round or attrs.get("roundIcon")
                    attr_start = _axml_read_u16(axml, attr_ext + 8)
                    attr_size = _axml_read_u16(axml, attr_ext + 10)
                    attr_count = _axml_read_u16(axml, attr_ext + 12)
                    attr_base = attr_ext + attr_start
                    for i in range(attr_count):
                        a_off = attr_base + (i * attr_size)
                        if a_off + 20 > len(axml):
                            continue
                        ns_idx = _axml_read_u32(axml, a_off)
                        name_idx = _axml_read_u32(axml, a_off + 4)
                        raw_idx = _axml_read_u32(axml, a_off + 8)
                        value_type = axml[a_off + 15]
                        value_data = _axml_read_u32(axml, a_off + 16)
                        name = strings[name_idx] if name_idx < len(strings) else ""
                        if name not in ("icon", "roundIcon"):
                            continue
                        ns = strings[ns_idx] if ns_idx < len(strings) else ""
                        if ns and ns != "http://schemas.android.com/apk/res/android":
                            continue
                        ref = _axml_attr_ref_from_parts(strings, raw_idx, value_type, value_data)
                        if not ref:
                            continue
                        if name == "roundIcon":
                            app_round = app_round or ref
                        else:
                            app_icon = app_icon or ref
                elif elem_name in ("activity", "activity-alias"):
                    current_activity = {
                        "icon": attrs.get("icon"),
                        "round": attrs.get("roundIcon"),
                        "has_main": False,
                        "has_launcher": False,
                        "depth": len(stack),
                        "is_alias": elem_name == "activity-alias",
                    }
                elif elem_name == "intent-filter" and current_activity:
                    in_intent_filter = True
                elif elem_name in ("action", "category") and current_activity and in_intent_filter:
                    name_val = attrs.get("name")
                    if elem_name == "action" and name_val == "android.intent.action.MAIN":
                        current_activity["has_main"] = True
                    if elem_name == "category" and name_val == "android.intent.category.LAUNCHER":
                        current_activity["has_launcher"] = True
        elif c_type == 0x0103 and strings:
            end_ext = pos + c_header
            if end_ext + 8 <= len(axml):
                end_name_idx = _axml_read_u32(axml, end_ext + 4)
                end_name = strings[end_name_idx] if end_name_idx < len(strings) else ""
                if stack:
                    stack.pop()
                if current_activity and end_name in ("activity", "activity-alias"):
                    if current_activity.get("has_main") and current_activity.get("has_launcher"):
                        if current_activity.get("is_alias"):
                            alias_icon = alias_icon or current_activity.get("icon")
                            alias_round = alias_round or current_activity.get("round")
                        else:
                            launcher_icon = launcher_icon or current_activity.get("icon")
                            launcher_round = launcher_round or current_activity.get("round")
                    current_activity = None
                if end_name == "intent-filter" and in_intent_filter:
                    in_intent_filter = False
        pos += c_size
    if app_logo:
        return app_logo
    if prefer_round_icon and app_round:
        return app_round
    if app_icon:
        return app_icon
    if app_round:
        return app_round
    if prefer_round_icon and alias_round:
        return alias_round
    if alias_icon:
        return alias_icon
    if alias_round:
        return alias_round
    if prefer_round_icon and launcher_round:
        return launcher_round
    if launcher_icon:
        return launcher_icon
    if launcher_round:
        return launcher_round
    return None


def _axml_manifest_icon_ref(apk_path: pathlib.Path, *, prefer_round_icon: bool = False) -> tuple[Optional[str], Optional[str]]:
    """Extract icon resource reference from APK manifest (binary or xml)."""
    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            try:
                axml = zf.read("AndroidManifest.xml")
            except KeyError:
                return None, "manifest_not_found"
    except Exception as e:
        return None, f"manifest_read_failed:{e}"
    try:
        stripped = axml.lstrip()
        if stripped.startswith(b"<?xml") or stripped.startswith(b"<manifest"):
            import xml.etree.ElementTree as ET

            root = ET.fromstring(stripped.decode("utf-8", errors="replace"))
            app = root.find("application")
            if app is None:
                return None, "axml_icon_not_found"
            ns = "{http://schemas.android.com/apk/res/android}"
            logo = app.get(f"{ns}logo") or app.get("android:logo")
            icon = app.get(f"{ns}icon") or app.get("android:icon")
            round_icon = app.get(f"{ns}roundIcon") or app.get("android:roundIcon")
            alias_icon = None
            alias_round = None
            launcher_icon = None
            launcher_round = None
            for activity in root.iter():
                if activity.tag not in ("activity", "activity-alias"):
                    continue
                icon_attr = activity.get(f"{ns}icon") or activity.get("android:icon")
                round_attr = activity.get(f"{ns}roundIcon") or activity.get("android:roundIcon")
                has_main = False
                has_launcher = False
                for intent in activity.findall("intent-filter"):
                    for action in intent.findall("action"):
                        if action.get(f"{ns}name") == "android.intent.action.MAIN":
                            has_main = True
                    for category in intent.findall("category"):
                        if category.get(f"{ns}name") == "android.intent.category.LAUNCHER":
                            has_launcher = True
                if has_main and has_launcher:
                    if activity.tag == "activity-alias":
                        alias_icon = alias_icon or icon_attr
                        alias_round = alias_round or round_attr
                    else:
                        launcher_icon = launcher_icon or icon_attr
                        launcher_round = launcher_round or round_attr
            if logo:
                return logo, None
            if prefer_round_icon and round_icon:
                return round_icon, None
            if icon:
                return icon, None
            if round_icon:
                return round_icon, None
            if prefer_round_icon and alias_round:
                return alias_round, None
            if alias_icon:
                return alias_icon, None
            if alias_round:
                return alias_round, None
            if prefer_round_icon and launcher_round:
                return launcher_round, None
            if launcher_icon:
                return launcher_icon, None
            if launcher_round:
                return launcher_round, None
            return None, "axml_icon_not_found"
        icon = _axml_icon_ref_from_bytes(axml, prefer_round_icon=prefer_round_icon)
        return (icon, None) if icon else (None, "axml_icon_not_found")
    except Exception:
        return None, "axml_parse_failed"


def _find_sdk_root() -> Optional[pathlib.Path]:
    """Resolve Android SDK root from env or tool locations."""
    env_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if env_root:
        return pathlib.Path(env_root).expanduser().resolve()
    aapt2_bin = shutil.which("aapt2")
    if aapt2_bin:
        p = pathlib.Path(aapt2_bin).resolve()
        # .../build-tools/<ver>/aapt2
        if p.parent.name and p.parent.parent.name == "build-tools":
            return p.parent.parent.parent
    return None


def _run_apkanalyzer(args: list[str]) -> dict:
    """Run apkanalyzer with SDK-aware env setup."""
    sdk_root = _find_sdk_root()
    apkanalyzer = None
    if sdk_root:
        candidate = sdk_root / "cmdline-tools" / "bin" / "apkanalyzer"
        if candidate.exists():
            apkanalyzer = str(candidate)
    if not apkanalyzer:
        apkanalyzer = shutil.which("apkanalyzer")
    if not apkanalyzer:
        return {"ok": False, "error": "apkanalyzer_not_found"}
    env = os.environ.copy()
    if sdk_root:
        tools_dir = sdk_root / "tools"
        if not tools_dir.exists():
            tools_dir = sdk_root / "cmdline-tools"
        env["APKANALYZER_OPTS"] = f"-Dcom.android.sdklib.toolsdir={tools_dir}"
    try:
        proc = subprocess.run(
            [apkanalyzer, *args],
            check=False,
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()}
    return {"ok": True, "stdout": proc.stdout or ""}


def _apkanalyzer_manifest_icon_ref(apk_path: pathlib.Path) -> tuple[Optional[str], Optional[str]]:
    """Extract android:icon from manifest using apkanalyzer."""
    res = _run_apkanalyzer(["manifest", "print", str(apk_path)])
    if not res.get("ok"):
        return None, res.get("error") or "apkanalyzer_failed"
    out = res.get("stdout") or ""
    m = re.search(r'android:icon\\s*=\\s*\"([^\"]+)\"', out)
    if m:
        return m.group(1), None
    m = re.search(r"android:icon\\s*=\\s*'([^']+)'", out)
    if m:
        return m.group(1), None
    return None, "apkanalyzer_icon_not_found"


def _bundletool_manifest_icon_ref(apk_path: pathlib.Path) -> tuple[Optional[str], Optional[str]]:
    """Extract android:icon from manifest using bundletool."""
    cmd = _bundletool_cmd()
    if not cmd:
        return None, "bundletool_not_available"
    res = _run_bundletool(cmd + ["dump", "manifest", "--apk", str(apk_path)])
    if not res.get("ok"):
        err = res.get("error") or "bundletool_failed"
        if "Missing the required --bundle flag" in err:
            return None, "bundletool_apk_not_supported"
        return None, err
    out = res.get("stdout") or ""
    m = re.search(r'android:icon\\s*=\\s*\"([^\"]+)\"', out)
    if m:
        return m.group(1), None
    m = re.search(r"android:icon\\s*=\\s*'([^']+)'", out)
    if m:
        return m.group(1), None
    return None, "bundletool_icon_not_found"


def _bundletool_cmd() -> Optional[list[str]]:
    """Resolve a bundletool command (binary or java -jar)."""
    bundletool = shutil.which("bundletool")
    if bundletool:
        return [bundletool]
    jar = _ensure_bundletool_jar()
    if not jar:
        return None
    java = shutil.which("java")
    if not java:
        return None
    return [java, "-jar", str(jar)]


def _run_bundletool(args: list[str]) -> dict:
    """Run bundletool command and return stdout or error."""
    try:
        proc = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
            timeout=60,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()}
    return {"ok": True, "stdout": proc.stdout or ""}


def _ensure_bundletool_jar() -> Optional[pathlib.Path]:
    """
    Auto-download bundletool jar if missing.
    """
    sdk_root = _find_sdk_root()
    base_dir = (sdk_root / "bundletool") if sdk_root else (pathlib.Path(tempfile.gettempdir()) / "bundletool")
    base_dir.mkdir(parents=True, exist_ok=True)
    jar_path = base_dir / "bundletool-all.jar"
    if jar_path.exists():
        return jar_path
    url = _latest_bundletool_url() or "https://github.com/google/bundletool/releases/download/1.18.3/bundletool-all-1.18.3.jar"
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        if not data or len(data) < 1024 * 1024:
            return None
        jar_path.write_bytes(data)
        return jar_path
    except Exception:
        return None


def _latest_bundletool_url() -> Optional[str]:
    """Fetch latest bundletool release jar URL from GitHub."""
    try:
        import json as _json
        import urllib.request

        with urllib.request.urlopen("https://api.github.com/repos/google/bundletool/releases/latest", timeout=15) as resp:
            data = resp.read()
        if not data:
            return None
        payload = _json.loads(data.decode("utf-8"))
        assets = payload.get("assets") if isinstance(payload, dict) else None
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = asset.get("name") or ""
                if name.endswith(".jar") and "bundletool-all" in name:
                    return asset.get("browser_download_url")
        return None
    except Exception:
        return None


def _aapt2_dump_resources(apk_path: pathlib.Path) -> str:
    """Return aapt2 dump resources output, or empty string on failure."""
    aapt2_bin = shutil.which("aapt2")
    if not aapt2_bin:
        return ""
    try:
        proc = subprocess.run(
            [aapt2_bin, "dump", "resources", "--values", str(apk_path)],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _aapt2_resolve_icon_paths(apk_paths: list[pathlib.Path], icon_ref: str) -> list[str]:
    """
    Resolve icon resource reference to file paths using aapt2 dump resources.
    """
    if not icon_ref:
        return []
    ref = icon_ref.lstrip("@")
    paths: list[str] = []
    for apk in apk_paths:
        out = _aapt2_dump_resources(apk)
        if not out:
            continue
        # Resource header patterns:
        # resource 0x7f0802ae mipmap/ic_launcher: ...
        # resource 0x7f0802ae drawable/ic_launcher: ...
        header_pat = re.compile(r"^resource\s+(0x[0-9a-fA-F]+)\s+([^\s:]+):")
        current_match = False
        for raw in out.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = header_pat.match(line)
            if m:
                res_id = m.group(1)
                res_name = m.group(2)
                current_match = False
                if ref == res_id or ref == res_name:
                    current_match = True
                continue
            if current_match:
                # (file) res/mipmap-xxx/ic_launcher.png
                m_file = re.search(r"\(file\)\s+(\S+)", line)
                if m_file:
                    paths.append(m_file.group(1))
                # (raw) res/drawable/ic_launcher.xml
                m_raw = re.search(r"\(raw\)\s+(\S+)", line)
                if m_raw:
                    paths.append(m_raw.group(1))
    return list(dict.fromkeys(paths))


def _axml_collect_drawable_refs(axml: bytes) -> list[str]:
    """Collect drawable references from binary AXML."""
    if len(axml) < 8:
        return []
    chunk_type = _axml_read_u16(axml, 0)
    if chunk_type != 0x0003:
        return []
    total_size = _axml_read_u32(axml, 4)
    pos = _axml_read_u16(axml, 2)
    if pos < 8:
        pos = 8
    strings: list[str] = []
    stack: list[str] = []
    collected: list[str] = []
    while pos + 8 <= len(axml) and pos < total_size:
        c_type = _axml_read_u16(axml, pos)
        c_header = _axml_read_u16(axml, pos + 2)
        c_size = _axml_read_u32(axml, pos + 4)
        if c_size <= 0:
            break
        if c_type == 0x0001:
            strings, _ = _axml_decode_string_pool(axml, pos)
        elif c_type == 0x0102 and strings:
            attr_ext = pos + c_header
            if attr_ext + 20 <= len(axml):
                elem_name_idx = _axml_read_u32(axml, attr_ext + 4)
                elem_name = strings[elem_name_idx] if elem_name_idx < len(strings) else ""
                stack.append(elem_name)
                attr_start = _axml_read_u16(axml, attr_ext + 8)
                attr_size = _axml_read_u16(axml, attr_ext + 10)
                attr_count = _axml_read_u16(axml, attr_ext + 12)
                attr_base = attr_ext + attr_start
                for i in range(attr_count):
                    a_off = attr_base + (i * attr_size)
                    if a_off + 20 > len(axml):
                        continue
                    name_idx = _axml_read_u32(axml, a_off + 4)
                    raw_idx = _axml_read_u32(axml, a_off + 8)
                    value_type = axml[a_off + 15]
                    value_data = _axml_read_u32(axml, a_off + 16)
                    name = strings[name_idx] if name_idx < len(strings) else ""
                    if name != "drawable":
                        continue
                    ref = _axml_attr_ref_from_parts(strings, raw_idx, value_type, value_data)
                    if not ref:
                        continue
                    if stack and stack[-1] in ("foreground", "monochrome", "background"):
                        collected.append(ref)
        elif c_type == 0x0103 and strings:
            if stack:
                stack.pop()
        pos += c_size
    # Prefer foreground/monochrome refs by order collected.
    return collected


def _resolve_icon_xml_paths(apk_paths: list[pathlib.Path], xml_rel_path: str) -> list[str]:
    """Resolve drawable refs in XML icon to concrete file paths."""
    rel = (xml_rel_path or "").lstrip("/")
    if not rel:
        return []
    xml_bytes = None
    for apk in apk_paths:
        try:
            with zipfile.ZipFile(apk, "r") as zf:
                if rel in zf.namelist():
                    xml_bytes = zf.read(rel)
                    break
        except Exception:
            continue
    if not xml_bytes:
        return []
    refs: list[str] = []
    stripped = xml_bytes.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(stripped.decode("utf-8", errors="replace"))
            ns = "{http://schemas.android.com/apk/res/android}"
            for tag in ("foreground", "monochrome", "background"):
                for node in root.findall(f".//{tag}"):
                    ref = node.get(f"{ns}drawable") or node.get("android:drawable")
                    if ref:
                        refs.append(ref)
        except Exception:
            refs = []
    else:
        refs = _axml_collect_drawable_refs(xml_bytes)
    paths: list[str] = []
    for ref in refs:
        paths.extend(_aapt2_resolve_icon_paths(apk_paths, ref))
    return paths


def _expand_icon_paths(apk_paths: list[pathlib.Path], icon_paths: list[str], *, depth: int = 0) -> list[str]:
    """Expand XML icon paths into raster paths (bounded recursion)."""
    if not icon_paths:
        return []
    if depth > 2:
        return icon_paths
    expanded: list[str] = []
    for path in icon_paths:
        if path.lower().endswith(".xml"):
            resolved = _resolve_icon_xml_paths(apk_paths, path)
            if resolved:
                expanded.extend(_expand_icon_paths(apk_paths, resolved, depth=depth + 1))
            else:
                expanded.append(path)
        else:
            expanded.append(path)
    return list(dict.fromkeys(expanded))


def _dpi_from_path(path: str) -> Optional[int]:
    """Extract DPI from resource path suffix (e.g. -xxhdpi)."""
    p = (path or "").lower()
    m = re.search(r"-([0-9]{2,4})dpi", p)
    if m and m.group(1).isdigit():
        return int(m.group(1))
    for key, dpi in _DPI_ALIAS.items():
        if f"-{key}" in p:
            return dpi
    return None


def _pick_icon_candidate(info: dict, *, density_dpi: Optional[int]) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """Pick best icon path based on target density or default."""
    icons = info.get("icons") if isinstance(info, dict) else None
    icon_default = info.get("icon_default") if isinstance(info, dict) else None
    if isinstance(icons, dict) and icons:
        if density_dpi:
            dpi, path = min(icons.items(), key=lambda it: abs(int(it[0]) - int(density_dpi)))
        else:
            dpi, path = max(icons.items(), key=lambda it: int(it[0]))
        return path, int(dpi), "aapt_badging"
    if icon_default:
        return icon_default, None, "aapt_badging_default"
    return None, None, None


def _icon_output_path(cache_dir: pathlib.Path, pkg: str, icon_rel_path: str) -> pathlib.Path:
    """Compute output path for extracted icon under cache dir."""
    safe_pkg = re.sub(r"[^\w\.-]+", "_", pkg).strip("_") or "pkg"
    rel = pathlib.PurePosixPath(icon_rel_path)
    name = rel.name or f"{safe_pkg}.png"
    out_dir = cache_dir / "icons" / safe_pkg
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / name


def _extract_icon_from_apk(
    local_apk: pathlib.Path,
    *,
    pkg: str,
    cache_dir: pathlib.Path,
    icon_rel_path: str,
) -> dict:
    """Extract an icon file from a local APK into cache."""
    if not icon_rel_path:
        return {"ok": False, "error": "icon_rel_path_empty"}
    if not local_apk.exists():
        return {"ok": False, "error": "local_apk_missing", "apk": str(local_apk)}
    try:
        with zipfile.ZipFile(local_apk, "r") as zf:
            rel = icon_rel_path.lstrip("/")
            if rel in zf.namelist():
                out_path = _icon_output_path(cache_dir, pkg, rel)
                zf.extract(rel, out_path.parent)
                # ensure final path matches (Zip extracts with full folders)
                extracted = out_path.parent / rel
                if extracted.exists():
                    return {"ok": True, "icon_path": str(extracted)}
                return {"ok": True, "icon_path": str(out_path)}
            if rel.endswith(".xml"):
                stem = pathlib.PurePosixPath(rel).stem
                fallback = _find_raster_icon_by_stem(zf, stem)
                if fallback:
                    out_path = _icon_output_path(cache_dir, pkg, fallback)
                    zf.extract(fallback, out_path.parent)
                    extracted = out_path.parent / fallback
                    return {"ok": True, "icon_path": str(extracted), "fallback": True}
                return {"ok": False, "error": "icon_xml_no_raster", "icon_rel_path": rel}
            return {"ok": False, "error": "icon_path_not_in_apk", "icon_rel_path": rel}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _find_raster_icon_by_stem(zf: zipfile.ZipFile, stem: str) -> Optional[str]:
    """Find best raster icon path matching a stem in an APK."""
    if not stem:
        return None
    candidates: list[tuple[int, str]] = []
    for name in zf.namelist():
        if not name.startswith("res/"):
            continue
        if not (name.endswith(".png") or name.endswith(".webp")):
            continue
        if pathlib.PurePosixPath(name).stem != stem:
            continue
        dpi = _dpi_from_path(name) or 0
        candidates.append((dpi, name))
    if not candidates:
        return None
    candidates.sort(key=lambda it: it[0], reverse=True)
    return candidates[0][1]


def _adb_pm_paths(device_serial: str, package_name: str) -> list[str]:
    """Return APK paths for a package via `pm path`."""
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "pm", "path", package_name],
        check=False,
        delay_s=0.0,
        log_output=False,
    )
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("package:"):
            p = line.split("package:", 1)[1].strip()
            if p:
                paths.append(p)
    return paths


def _cached_apk_path(cache: pathlib.Path, pkg: str, remote_path: str) -> pathlib.Path:
    """Compute cache path for a pulled APK split."""
    base = pathlib.PurePosixPath(remote_path).name
    if base == "base.apk":
        return cache / f"{pkg}.apk"
    safe = re.sub(r"[^\w\.-]+", "_", base).strip("_") or "split.apk"
    return cache / f"{pkg}__{safe}"


def _pull_apk(device_serial: str, remote_path: str, local_path: pathlib.Path) -> bool:
    """Pull an APK from device to local path."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        adb_prefix(device_serial) + ["pull", remote_path, str(local_path)],
        check=False,
        text=True,
        capture_output=True,
        timeout=90,
    )
    return proc.returncode == 0 and local_path.exists()


def _find_icon_candidates(zf: zipfile.ZipFile) -> list[tuple[int, int, str]]:
    """
    Return list of (score, dpi, rel_path) candidates.
    """
    candidates: list[tuple[int, int, str]] = []
    for name in zf.namelist():
        if not name.startswith("res/"):
            continue
        if not (name.endswith(".png") or name.endswith(".webp")):
            continue
        lower = name.lower()
        if "/mipmap" not in lower and "/drawable" not in lower:
            continue
        base = pathlib.PurePosixPath(name).stem.lower()
        score = 0
        if "ic_launcher" in base:
            score += 3
        if "launcher" in base and "ic_launcher" not in base:
            score += 1
        if "app_icon" in base or "appicon" in base:
            score += 2
        if score <= 0:
            continue
        dpi = _dpi_from_path(name) or 0
        candidates.append((score, dpi, name))
    return candidates


def _extract_icon_from_apk_set(
    apk_paths: list[pathlib.Path],
    *,
    pkg: str,
    cache_dir: pathlib.Path,
    density_dpi: Optional[int],
) -> dict:
    """Extract best icon candidate across multiple APK splits."""
    all_candidates: list[tuple[int, int, str, pathlib.Path]] = []
    for apk in apk_paths:
        if not apk.exists():
            continue
        try:
            with zipfile.ZipFile(apk, "r") as zf:
                for score, dpi, rel in _find_icon_candidates(zf):
                    all_candidates.append((score, dpi, rel, apk))
        except Exception:
            continue
    if not all_candidates:
        return {"ok": False, "error": "no_icon_candidates"}

    def _rank(item: tuple[int, int, str, pathlib.Path]) -> tuple[int, int, int]:
        """Ranking key for icon candidates (score, density distance, dpi)."""
        score, dpi, _, _ = item
        if density_dpi:
            dist = abs(int(dpi or 0) - int(density_dpi))
        else:
            dist = 0
        return (score, -dist, dpi)

    best = sorted(all_candidates, key=_rank, reverse=True)[0]
    _, _, rel_path, apk_path = best
    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            out_path = _icon_output_path(cache_dir, pkg, rel_path)
            zf.extract(rel_path, out_path.parent)
            extracted = out_path.parent / rel_path
            if extracted.exists():
                return {"ok": True, "icon_path": str(extracted)}
            return {"ok": True, "icon_path": str(out_path)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _extract_icon_from_apk_set_by_path(
    apk_paths: list[pathlib.Path],
    *,
    pkg: str,
    cache_dir: pathlib.Path,
    icon_rel_path: str,
) -> dict:
    """Extract icon by relative path from any APK split."""
    rel = (icon_rel_path or "").lstrip("/")
    if not rel:
        return {"ok": False, "error": "icon_rel_path_empty"}
    for apk in apk_paths:
        if not apk.exists():
            continue
        try:
            with zipfile.ZipFile(apk, "r") as zf:
                if rel in zf.namelist():
                    out_path = _icon_output_path(cache_dir, pkg, rel)
                    zf.extract(rel, out_path.parent)
                    extracted = out_path.parent / rel
                    if extracted.exists():
                        return {"ok": True, "icon_path": str(extracted)}
                    return {"ok": True, "icon_path": str(out_path)}
        except Exception:
            continue
    return {"ok": False, "error": "icon_path_not_in_any_apk"}


def get_app_info(
    device_serial: Optional[str],
    package_name: str,
    *,
    apk_path_on_device: Optional[str] = None,
    cache_dir: Optional[str] = None,
    aapt_path: Optional[str] = None,
    density_dpi: Optional[int] = None,
) -> dict:
    """
    Best-effort app info for an installed package:
    - AppName (label)
    - PackageName
    - VersionName
    - VersionCode
    """
    pkg = (package_name or "").strip()
    if not device_serial or not pkg:
        return {
            "AppName": pkg or None,
            "PackageName": pkg or None,
            "VersionName": None,
            "VersionCode": None,
            "IconPath": None,
            "IconDpi": None,
            "IconSource": None,
            "IconError": "device_serial_or_package_missing",
        }

    aapt_bin = aapt_path or shutil.which("aapt") or shutil.which("aapt2")
    if not aapt_bin:
        return {
            "AppName": pkg,
            "PackageName": pkg,
            "VersionName": None,
            "VersionCode": None,
            "AppNameError": "aapt/aapt2 not found on PATH",
            "IconPath": None,
            "IconDpi": None,
            "IconSource": None,
            "IconError": "aapt_not_found",
        }

    remote_apk = (apk_path_on_device or "").strip() or _adb_pm_path(device_serial, pkg)
    if not remote_apk:
        return {
            "AppName": pkg,
            "PackageName": pkg,
            "VersionName": None,
            "VersionCode": None,
            "AppNameError": "failed to resolve apk path via pm path",
            "IconPath": None,
            "IconDpi": None,
            "IconSource": None,
            "IconError": "apk_path_not_found",
        }

    cache = (
        pathlib.Path(cache_dir)
        if cache_dir
        else (recordings_root(None) / "cache" / "apps_apk_cache")
    )
    cache.mkdir(parents=True, exist_ok=True)
    local_apk = cache / f"{pkg}.apk"

    if not local_apk.exists():
        pull_proc = subprocess.run(
            adb_prefix(device_serial) + ["pull", remote_apk, str(local_apk)],
            check=False,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if pull_proc.returncode != 0 or not local_apk.exists():
            return {
                "AppName": pkg,
                "PackageName": pkg,
                "VersionName": None,
                "VersionCode": None,
                "AppNameError": "adb pull apk failed",
                "IconPath": None,
                "IconDpi": None,
                "IconSource": None,
                "IconError": "apk_pull_failed",
            }

    info = _aapt_dump_badging_info(aapt_bin, local_apk)
    app_label = (info.get("label") if isinstance(info, dict) else None) or pkg
    version_name = info.get("version_name") if isinstance(info, dict) else None
    version_code = info.get("version_code") if isinstance(info, dict) else None
    icon_path = None
    icon_dpi = None
    icon_source = None
    icon_error = None
    if isinstance(info, dict) and info.get("ok"):
        rel_path, rel_dpi, rel_source = _pick_icon_candidate(info, density_dpi=density_dpi)
        if rel_path:
            icon_dpi = rel_dpi
            icon_source = rel_source
            extract = _extract_icon_from_apk(
                local_apk,
                pkg=pkg,
                cache_dir=cache,
                icon_rel_path=rel_path,
            )
            if extract.get("ok"):
                icon_path = extract.get("icon_path")
                if extract.get("fallback"):
                    icon_source = "adaptive_fallback"
            else:
                icon_error = extract.get("error") or "icon_extract_failed"
        else:
            icon_error = "icon_not_in_badging"
    else:
        if isinstance(info, dict):
            icon_error = info.get("error") or "aapt_badging_failed"
        else:
            icon_error = "aapt_badging_failed"

    if not icon_path:
        pm_paths = _adb_pm_paths(device_serial, pkg)
        if pm_paths:
            apk_paths: list[pathlib.Path] = []
            for rp in pm_paths:
                local = _cached_apk_path(cache, pkg, rp)
                if not local.exists():
                    _pull_apk(device_serial, rp, local)
                apk_paths.append(local)

            # Fallback 2: aapt2 resource table resolution (split-aware)
            aapt2_bin = shutil.which("aapt2")
            icon_ref = None
            icon_ref_errs: list[str] = []
            if aapt2_bin:
                icon_ref = _aapt2_dump_xmltree_icon_ref(aapt2_bin, local_apk)
            else:
                icon_ref_errs.append("aapt2_not_found")
            if not icon_ref:
                axml_err = None
                icon_ref, axml_err = _axml_manifest_icon_ref(local_apk)
                if not icon_ref and apk_paths:
                    for other_apk in apk_paths:
                        if other_apk == local_apk:
                            continue
                        icon_ref, axml_err = _axml_manifest_icon_ref(other_apk)
                        if icon_ref:
                            break
                if axml_err and not icon_ref:
                    icon_ref_errs.append(f"axml:{axml_err}")
            if icon_ref:
                icon_paths = _aapt2_resolve_icon_paths(apk_paths, icon_ref) if icon_ref else []
                icon_paths = _expand_icon_paths(apk_paths, icon_paths)
                image_paths = [p for p in icon_paths if pathlib.PurePosixPath(p).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
                if image_paths:
                    icon_paths = image_paths
                if icon_paths:
                    best = None
                    best_dpi = None
                    if density_dpi:
                        best = min(icon_paths, key=lambda p: abs((_dpi_from_path(p) or 0) - int(density_dpi)))
                        best_dpi = _dpi_from_path(best)
                    else:
                        best = icon_paths[0]
                        best_dpi = _dpi_from_path(best)
                    extract = _extract_icon_from_apk_set_by_path(
                        apk_paths,
                        pkg=pkg,
                        cache_dir=cache,
                        icon_rel_path=best,
                    )
                    if extract.get("ok"):
                        icon_path = extract.get("icon_path")
                        icon_source = "aapt2_resources"
                        icon_dpi = best_dpi
                        icon_error = None
            if not icon_path and icon_ref_errs:
                if icon_error:
                    icon_error = f"{icon_error}; " + "; ".join(icon_ref_errs)
                else:
                    icon_error = "; ".join(icon_ref_errs)

            # Fallback 3: heuristic scan for icon-like files in APKs
            if not icon_path:
                fallback = _extract_icon_from_apk_set(
                    apk_paths,
                    pkg=pkg,
                    cache_dir=cache,
                    density_dpi=density_dpi,
                )
                if fallback.get("ok"):
                    icon_path = fallback.get("icon_path")
                    icon_source = "apk_scan"
                    icon_error = None
                else:
                    icon_error = icon_error or fallback.get("error") or "icon_fallback_failed"
    return {
        "AppName": app_label or pkg,
        "PackageName": pkg,
        "VersionName": version_name,
        "VersionCode": version_code,
        "IconPath": icon_path,
        "IconDpi": icon_dpi,
        "IconSource": icon_source,
        "IconError": icon_error,
    }


def _adb_pm_path(device_serial: str, package_name: str) -> Optional[str]:
    """Return the first APK path for a package via `pm path`."""
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "pm", "path", package_name],
        check=False,
        delay_s=0.0,
        log_output=False,
    )
    if proc.returncode != 0:
        return None
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("package:"):
            return line.split("package:", 1)[1].strip()
    return None


_RE_APK_PATH_PKG = re.compile(r"/([a-zA-Z0-9_.]+)-[a-zA-Z0-9=]+/base\.apk$")


def _extract_package_from_apk_path(apk_path_on_device: str) -> Optional[str]:
    """
    Best-effort extract package name from a device apk path like:
      /data/app/.../com.foo.bar-xxxx/base.apk  -> com.foo.bar
    """
    p = (apk_path_on_device or "").strip()
    if not p:
        return None
    m = _RE_APK_PATH_PKG.search(p)
    if m:
        return (m.group(1) or "").strip() or None
    # Fallback patterns seen on some devices:
    #   /data/app/com.foo.bar-1/base.apk
    #   /data/app/com.foo.bar/base.apk
    m2 = re.search(r"/([a-zA-Z0-9_.]+)(?:-[^/]+)?/base\.apk$", p)
    if m2:
        return (m2.group(1) or "").strip() or None
    return None


def _extract_label_from_dumpsys(stdout: str) -> Optional[str]:
    """
    Extract a human-readable label from `dumpsys package <pkg>` output.
    Ignore resource refs like @string/..., @7f....
    """
    out = stdout or ""
    # Prefer ApplicationInfo label=... (matches the user's sample logic)
    m = _RE_LABEL_EQ.search(out)
    if m:
        lab = (m.group("label") or "").strip().strip("'").strip('"')
        if lab and not lab.startswith("@") and lab.lower() != "null":
            return lab
    # Fallback: nonLocalizedLabel='Facebook'
    m2 = _RE_NON_LOCALIZED_LABEL.search(out)
    if m2:
        lab = (m2.group("label") or "").strip().strip("'").strip('"')
        if lab and not lab.startswith("@") and lab.lower() != "null":
            return lab
    # Fallback: application-label:'TikTok'
    for raw in out.splitlines():
        mm = _RE_APP_LABEL.match(raw)
        if not mm:
            continue
        lab = (mm.group("label") or "").strip().strip("'").strip('"')
        if lab and not lab.startswith("@") and lab.lower() != "null":
            return lab
    return None


def get_app_label_via_pyaxmlparser(
    device_serial: Optional[str],
    package_name: str,
    *,
    cache_dir: str,
) -> dict:
    """
    AppName resolver (updated, no pyaxmlparser):
    - pm path <pkg> to find base.apk on device
    - try `dumpsys package <pkg>` (fast) to get a non-resource label=
    - fallback: adb pull base.apk to a temp file + local `aapt dump badging`
    """
    pkg = (package_name or "").strip()
    if not device_serial or not pkg:
        return {"ok": False, "error": "device_serial and package_name are required", "PackageName": pkg or None}

    remote_apk = _adb_pm_path(device_serial, pkg)
    if not remote_apk:
        return {"ok": False, "PackageName": pkg, "error": "failed to resolve apk path via pm path"}

    # Step 1/2: try dumpsys label first (no pulling, faster).
    # Prefer parsing package from remote_apk to match the user's requested approach,
    # but fall back to the given pkg if parsing fails.
    pkg_for_dumpsys = _extract_package_from_apk_path(remote_apk) or pkg
    try:
        proc = subprocess.run(
            adb_prefix(device_serial) + ["shell", "dumpsys", "package", pkg_for_dumpsys],
            check=False,
            text=True,
            capture_output=True,
            timeout=100,
        )
        if proc.returncode == 0:
            label = _extract_label_from_dumpsys(proc.stdout or "")
            if label:
                return {
                    "ok": True,
                    "PackageName": pkg,
                    "AppName": label,
                    "remote_apk": remote_apk,
                    "method": "dumpsys",
                }
    except subprocess.TimeoutExpired:
        # Continue to aapt fallback.
        pass
    except Exception:
        # Continue to aapt fallback.
        pass

    # Step 3: fallback - pull base.apk and use local aapt (most reliable).
    aapt_bin = shutil.which("aapt") or shutil.which("aapt2")
    if not aapt_bin:
        return {
            "ok": False,
            "PackageName": pkg,
            "error": "aapt/aapt2 not found on PATH (install Android build-tools and ensure aapt is available).",
            "remote_apk": remote_apk,
        }

    with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as tf:
        temp_apk_path = tf.name
    try:
        pull_proc = subprocess.run(
            adb_prefix(device_serial) + ["pull", remote_apk, temp_apk_path],
            check=False,
            text=True,
            capture_output=True,
            timeout=30,
        )
        if pull_proc.returncode != 0 or not pathlib.Path(temp_apk_path).exists():
            return {
                "ok": False,
                "PackageName": pkg,
                "error": "adb pull apk failed",
                "stderr": (pull_proc.stderr or "").strip(),
                "remote_apk": remote_apk,
            }

        # Use aapt output to extract application-label.
        try:
            aapt_proc = subprocess.run(
                [aapt_bin, "dump", "badging", temp_apk_path],
                check=False,
                text=True,
                capture_output=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "PackageName": pkg, "error": "aapt dump badging timeout", "remote_apk": remote_apk}
        # print(f"stdout:{aapt_proc.stdout}")
        out = aapt_proc.stdout or ""
        why = 0
        if aapt_proc.returncode == 0:

            # Try Chinese label explicitly (common requirement).
            why = 2
            m = re.search(r"application-label-zh-CN:'([^']*)'", out)
            if m and m.group(1).strip():
                print(f"2 m.group(1).strip():{m.group(1).strip()}")
                return {
                    "ok": True,
                    "PackageName": pkg,
                    "AppName": m.group(1).strip(),
                    "remote_apk": remote_apk,
                    "method": "aapt",
                }
            # Prefer non-localized application-label first.
            m = re.search(r"application-label:'([^']*)'", out)
            why = 1
            if m and m.group(1).strip():
                print(f"1 m.group(1).strip():{m.group(1).strip()}")
                return {
                    "ok": True,
                    "PackageName": pkg,
                    "AppName": m.group(1).strip(),
                    "remote_apk": remote_apk,
                    "method": "aapt",
                }
            
            # Last resort: return package name from aapt.
            pm = re.search(r"package:\\s+name='([^']*)'", out)
            why = 3
            if pm and pm.group(1).strip():
                print(f"3 m.group(1).strip():{m.group(1).strip()}")
                return {
                    "ok": True,
                    "PackageName": pkg,
                    "AppName": pm.group(1).strip(),
                    "remote_apk": remote_apk,
                    "method": "aapt_pkg_fallback",
                }
        print(f"stdout:{aapt_proc.stdout}") 
        return {
            "ok": False,
            "PackageName": pkg,
            "error": f"failed to extract label via dumpsys/aapt: {aapt_proc.returncode} {why}",
            "remote_apk": remote_apk,
        }
    finally:
        try:
            os.unlink(temp_apk_path)
        except Exception:
            pass


def _aapt_dump_label(aapt_bin: str, apk_path: pathlib.Path) -> Optional[str]:
    """Extract application label from `aapt dump badging` output."""
    # aapt dump badging base.apk | grep "application-label:'...'"
    try:
        proc = subprocess.run(
            [aapt_bin, "dump", "badging", str(apk_path)],
            check=False,
            text=True,
            capture_output=True,
        )
    except Exception:
        return None
    out = proc.stdout or ""
    # Prefer non-localized application-label first.
    m = re.search(r"application-label:'([^']*)'", out)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # Fallback: application-label-xx:'...'
    m = re.search(r"application-label-[^:]+:'([^']*)'", out)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def get_app_label_via_aapt(
    device_serial: Optional[str],
    package_name: str,
    *,
    cache_dir: str,
    aapt_path: Optional[str] = None,
) -> dict:
    """
    Heavy but more accurate AppName resolver:
    - pm path <pkg> to find base.apk on device
    - adb pull to local cache
    - aapt dump badging to extract application-label
    """
    pkg = (package_name or "").strip()
    if not device_serial or not pkg:
        return {"ok": False, "error": "device_serial and package_name are required", "PackageName": pkg or None}

    aapt_bin = aapt_path or shutil.which("aapt") or shutil.which("aapt2")
    if not aapt_bin:
        return {
            "ok": False,
            "PackageName": pkg,
            "error": "aapt/aapt2 not found on PATH (install Android build-tools and ensure aapt is available).",
        }

    remote_apk = _adb_pm_path(device_serial, pkg)
    if not remote_apk:
        return {"ok": False, "PackageName": pkg, "error": "failed to resolve apk path via pm path"}

    cache = pathlib.Path(cache_dir).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    local_apk = cache / f"{pkg}.apk"
    # Pull (best-effort overwrite; keep adb output quiet)
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["pull", remote_apk, str(local_apk)],
        check=False,
        delay_s=0.0,
        log_output=False,
    )
    if proc.returncode != 0 or not local_apk.exists():
        return {"ok": False, "PackageName": pkg, "error": "adb pull apk failed", "stderr": (proc.stderr or "").strip()}

    label = _aapt_dump_label(aapt_bin, local_apk)
    return {"ok": True, "PackageName": pkg, "AppName": label, "aapt": aapt_bin, "apk": str(local_apk)}


_RE_DEBUGGABLE_TRUE = re.compile(r"\bdebuggable\s*=\s*true\b", re.IGNORECASE)
_RE_DEBUGGABLE_FLAG = re.compile(r"\bDEBUGGABLE\b")


def is_app_debuggable(device_serial: Optional[str], package_name: str) -> dict:
    """
    Best-effort check whether an app is debuggable (ApplicationInfo.FLAG_DEBUGGABLE / android:debuggable).

    Returns:
      { ok, PackageName, IsDebuggable, Evidence }
    """
    pkg = (package_name or "").strip()
    if not device_serial or not pkg:
        return {"ok": False, "error": "device_serial and package_name are required", "PackageName": pkg or None}

    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "dumpsys", "package", pkg],
        check=False,
        delay_s=0.0,
        log_output=False,
    )
    out = proc.stdout or ""

    # Heuristics:
    # - Some Android versions show `debuggable=true`
    # - Others show flags including `DEBUGGABLE`
    is_dbg = False
    evidence: list[str] = []
    if _RE_DEBUGGABLE_TRUE.search(out):
        is_dbg = True
        evidence.append("dumpsys: debuggable=true")
    if _RE_DEBUGGABLE_FLAG.search(out):
        # This token can appear in multiple contexts; still a strong signal.
        is_dbg = True
        evidence.append("dumpsys: contains DEBUGGABLE")

    return {
        "ok": True,
        "PackageName": pkg,
        "IsDebuggable": is_dbg,
        "Evidence": evidence,
    }


def open_deeplink(
    device_serial: Optional[str],
    uri: str,
    *,
    package: Optional[str] = None,
    wait_s: float = 0.8,
) -> dict:
    """
    Open a deeplink/scheme URI using Android Activity Manager:

      adb shell am start -a android.intent.action.VIEW -d <uri> [-p <package>]

    Notes:
    - This is best-effort. Some schemes require the target app to be installed,
      and some devices restrict certain intents.
    """
    u = (uri or "").strip()
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    if not u:
        return {"ok": False, "error": "uri is required"}

    cmd = adb_prefix(device_serial) + ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", u]
    if package:
        cmd += ["-p", package]
    proc = CommandRunner.run(cmd, check=False, delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "device_serial": device_serial,
        "uri": u,
        "package": package,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def am_kill_all(device_serial: Optional[str]) -> dict:
    """
    Best-effort: ask ActivityManager to kill background processes.

    Uses:
      adb shell am kill-all
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "am", "kill-all"],
        check=False,
        delay_s=0.0,
        log_output=False,
        silent=True,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "").strip(),
    }


def force_stop_package(device_serial: Optional[str], package_name: str) -> dict:
    """
    Force-stop an app package (kills its process and clears its tasks).

    Uses:
      adb shell am force-stop <package>
    """
    pkg = (package_name or "").strip()
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "PackageName": pkg or None}
    if not pkg:
        return {"ok": False, "error": "package_name is required", "PackageName": None}
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "am", "force-stop", pkg],
        check=False,
        delay_s=0.0,
        log_output=False,
        silent=True,  # 逐个 force-stop 不打印，由 clear_background_processes 打摘要
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "PackageName": pkg,
        "stderr": (proc.stderr or "").strip(),
    }


def clear_background_processes(
    device_serial: Optional[str],
    *,
    mode: str = "force_stop_3p",
    exclude_packages: Optional[list[str]] = None,
    include_system: bool = False,
    max_packages: int = 400,
    run_kill_all_first: bool = True,
) -> dict:
    """
    Best-effort "clear background apps" helper.

    Modes:
    - kill_all: only run `am kill-all`
    - force_stop_3p: force-stop third-party packages (pm list packages -3) (+ optional kill-all first)
    - force_stop_running_3p: force-stop third-party packages that currently have a running process (best-effort)
    - force_stop_all: force-stop all packages (NOT recommended; may break the device session)

    Notes:
    - Android does not provide a universal "clear recents" command; this approach uses AM/pm APIs.
    - We default to third-party apps only for safety.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    mode2 = (mode or "").strip().lower()
    if mode2 not in ("kill_all", "force_stop_3p", "force_stop_running_3p", "force_stop_all"):
        return {"ok": False, "error": "invalid mode (use: kill_all|force_stop_3p|force_stop_running_3p|force_stop_all)"}

    excl = {p.strip() for p in (exclude_packages or []) if p and p.strip()}

    kill_all_res = None
    if run_kill_all_first:
        kill_all_res = am_kill_all(device_serial)

    if mode2 == "kill_all":
        return {
            "ok": bool(kill_all_res.get("ok") if isinstance(kill_all_res, dict) else True),
            "device_serial": device_serial,
            "mode": mode2,
            "kill_all": kill_all_res,
            "stopped_count": 0,
            "stopped_packages": [],
            "errors": [],
        }

    # Collect packages
    if mode2 == "force_stop_all":
        res = pm_list_packages(device_serial, include_system=True)
    elif mode2 == "force_stop_running_3p":
        # Best-effort: use `ps` to find currently running app processes, then force-stop only those.
        # This avoids needing the user to know exact package names and is usually much faster than
        # iterating over all installed 3p packages.
        ps = CommandRunner.run(adb_prefix(device_serial) + ["shell", "ps", "-A", "-o", "NAME"], check=False, delay_s=0.0, log_output=False, silent=True)
        out = (ps.stdout or "")
        if ps.returncode != 0 or not out.strip():
            # Fallback for older / different toybox ps variants.
            ps2 = CommandRunner.run(adb_prefix(device_serial) + ["shell", "ps", "-A"], check=False, delay_s=0.0, log_output=False, silent=True)
            out = (ps2.stdout or "")
            ps = ps2
        names = []
        for ln in out.splitlines():
            s = ln.strip()
            if not s or s.lower() == "name":
                continue
            # For `ps -A` table output, take the last column as NAME.
            if " " in s:
                s = s.split()[-1]
            names.append(s)
        # Heuristic filters: process names that look like Android package names.
        # Examples:
        # - com.example.app
        # - com.example.app:push
        cand = []
        pkg_re = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+(?:[:][A-Za-z0-9_]+)?$")
        for n in names:
            if not isinstance(n, str):
                continue
            if not pkg_re.fullmatch(n):
                continue
            # exclude common system prefixes
            if n.startswith(("android.", "com.android.", "com.google.android.", "com.miui.", "com.xiaomi.")):
                continue
            cand.append(n)
        # Dedup preserving order
        seen = set()
        pkgs_running = []
        for p in cand:
            if p in seen:
                continue
            seen.add(p)
            pkgs_running.append(p)
        res = {"ok": True, "mode": "ps", "count": len(pkgs_running), "packages": pkgs_running, "ps_returncode": ps.returncode, "ps_stderr": (ps.stderr or "").strip()}
        pkgs = pkgs_running
    else:
        # third-party only by default
        res = pm_list_packages(device_serial, include_system=bool(include_system))

    pkgs = list((res or {}).get("packages") or []) if isinstance(res, dict) else []
    if mode2 == "force_stop_3p":
        # For third-party mode, force include_system=False regardless of caller.
        res = pm_list_packages(device_serial, include_system=False)
        pkgs = list((res or {}).get("packages") or []) if isinstance(res, dict) else []

    # Apply exclusions and cap.
    filtered = [p for p in pkgs if p and p not in excl]
    cap = max(1, int(max_packages))
    target = filtered[:cap]

    stopped: list[str] = []
    errors: list[dict] = []
    for p in target:
        r = force_stop_package(device_serial, p)
        if isinstance(r, dict) and r.get("ok"):
            stopped.append(p)
        else:
            errors.append({"PackageName": p, "result": r})

    ok = True
    if kill_all_res is not None and isinstance(kill_all_res, dict) and kill_all_res.get("ok") is False:
        ok = False
    if errors:
        ok = False

    # -- 摘要输出 (替代逐行打印每个 force-stop) --
    from phone_pilot.android.adb.runner import _log
    scope = "三方应用" if mode2 in ("force_stop_3p", "force_stop_running_3p") else "全部应用"
    err_hint = f", {len(errors)} 个失败" if errors else ""
    _log(f"  清理后台 | 已停止 {len(stopped)} 个{scope}{err_hint}")

    return {
        "ok": ok,
        "device_serial": device_serial,
        "mode": mode2,
        "run_kill_all_first": run_kill_all_first,
        "kill_all": kill_all_res,
        "pm_list_packages": res,
        "exclude_packages": sorted(excl),
        "max_packages": cap,
        "candidates_count": len(filtered),
        "target_count": len(target),
        "stopped_count": len(stopped),
        "stopped_packages": stopped,
        "errors": errors,
        "note": "仅能 best-effort 清理后台；默认只 force-stop 三方应用以避免破坏系统进程。",
    }


def input_tap(device_serial: Optional[str], x: int, y: int, *, wait_s: float = 0.15) -> dict:
    """
    Click/tap a screen coordinate.

    Uses:
      adb shell input tap <x> <y>
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    try:
        xi = int(x)
        yi = int(y)
    except Exception:
        return {"ok": False, "error": "x/y must be integers", "x": x, "y": y}
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "input", "tap", str(xi), str(yi)],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "device_serial": device_serial,
        "x": xi,
        "y": yi,
        "stderr": (proc.stderr or "").strip(),
    }


def input_swipe(
    device_serial: Optional[str],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    duration_ms: int = 250,
    wait_s: float = 0.25,
) -> dict:
    """
    Swipe from (x1,y1) to (x2,y2).

    Uses:
      adb shell input swipe <x1> <y1> <x2> <y2> [duration_ms]
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    try:
        a1, b1, a2, b2 = int(x1), int(y1), int(x2), int(y2)
    except Exception:
        return {"ok": False, "error": "x1/y1/x2/y2 must be integers", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
    try:
        dur = int(duration_ms)
    except Exception:
        dur = 250
    dur = max(1, min(dur, 2000))
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "input", "swipe", str(a1), str(b1), str(a2), str(b2), str(dur)],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "device_serial": device_serial,
        "x1": a1,
        "y1": b1,
        "x2": a2,
        "y2": b2,
        "duration_ms": dur,
        "stderr": (proc.stderr or "").strip(),
    }



def _parse_pattern(path: str) -> list[int]:
    """
    Parse pattern path such as:
    - "1-2-3-6-9"
    - "1,2,3,6,9"
    - "12369"
    Returns list of ints (1..9).
    """
    s = (path or "").strip()
    if not s:
        return []
    # Allow separators
    if any(ch in s for ch in "-, >"):
        parts = [p.strip() for p in re.split(r"[^0-9]+", s) if p.strip()]
        out: list[int] = []
        for p in parts:
            if not p.isdigit():
                continue
            out.append(int(p))
        return out
    # Compact digits
    if s.isdigit():
        return [int(ch) for ch in s]
    return []


def _pattern_grid_points(
    screen_w: int,
    screen_h: int,
    *,
    grid_size_ratio: float = 0.72,
    grid_center_y_ratio: float = 0.52,
) -> dict[int, tuple[int, int]]:
    """
    Compute 3x3 pattern grid centers in screen pixels.

    Heuristic defaults target common Android keyguard layouts:
    - square grid ~72% of screen width
    - centered horizontally
    - vertically centered slightly above mid (0.52h)
    """
    w = max(1, int(screen_w))
    h = max(1, int(screen_h))
    r = float(grid_size_ratio)
    r = 0.4 if r < 0.4 else (0.9 if r > 0.9 else r)

    grid = int(round(w * r))
    grid = max(200, min(grid, w))
    left = int(round((w - grid) / 2))
    cy = int(round(h * float(grid_center_y_ratio)))
    top = int(round(cy - grid / 2))
    top = max(0, min(top, h - grid))

    # Pattern dots are not at the extreme corners; they are centered in a 3x3 grid.
    # Use 3 equal cells: centers at (col+0.5)*cell.
    cell = grid / 3
    centers: dict[int, tuple[int, int]] = {}
    n = 1
    for row in range(3):
        for col in range(3):
            x = int(round(left + (col + 0.5) * cell))
            y = int(round(top + (row + 0.5) * cell))
            centers[n] = (x, y)
            n += 1
    return centers


def _pattern_grid_points_from_bounds(
    bounds: tuple[int, int, int, int],
) -> dict[int, tuple[int, int]]:
    """
    Compute 3x3 pattern grid centers within a given bounds rect [x1,y1,x2,y2].
    This is more accurate than screen-wide heuristics when we can locate the pattern view.
    """
    x1, y1, x2, y2 = bounds
    w = max(1, int(x2 - x1))
    h = max(1, int(y2 - y1))
    size = min(w, h)
    # Center a square inside the bounds.
    left = int(round(x1 + (w - size) / 2))
    top = int(round(y1 + (h - size) / 2))
    cell = size / 3
    centers: dict[int, tuple[int, int]] = {}
    n = 1
    for row in range(3):
        for col in range(3):
            x = int(round(left + (col + 0.5) * cell))
            y = int(round(top + (row + 0.5) * cell))
            centers[n] = (x, y)
            n += 1
    return centers


def _find_lock_pattern_bounds_via_uiautomator(device_serial: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    """
    Best-effort locate the lock pattern view bounds using UIAutomator dump.

    We search for nodes where:
    - resource-id contains 'lockPattern' / 'lock_pattern'
    - or class contains 'LockPattern' / 'LockPatternView'
    """
    if not device_serial:
        return None
    try:
        # Lazy import to avoid circular deps.
        from phone_pilot.android.ui.automator import dump_ui_xml, parse_uiautomator_nodes
    except Exception:
        return None

    try:
        res = dump_ui_xml(device_serial, compressed=True, wake_and_unlock=False, force_home=False)
        if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
            return None
        nodes = parse_uiautomator_nodes(str(res.get("xml") or ""))
    except Exception:
        return None

    best = None
    best_area = -1
    for n in nodes:
        rid = (n.resource_id or "").lower()
        cls = (n.class_name or "").lower()
        if not (
            ("lockpattern" in rid)
            or ("lock_pattern" in rid)
            or ("lockpattern" in cls)
            or ("lockpatternview" in cls)
        ):
            continue
        bt = n.bounds_tuple()
        if not bt:
            continue
        x1, y1, x2, y2 = bt
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if area > best_area:
            best_area = area
            best = bt
    return best


def _interpolate_points(a: tuple[int, int], b: tuple[int, int], steps: int) -> list[tuple[int, int]]:
    """
    Create intermediate points from a->b (excluding a, including b) with `steps` segments.
    If steps<=1, returns [b].
    """
    if steps <= 1:
        return [b]
    ax, ay = a
    bx, by = b
    out: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        x = int(round(ax + (bx - ax) * t))
        y = int(round(ay + (by - ay) * t))
        out.append((x, y))
    return out


def _unlock_pattern_via_monkey(
    device_serial: Optional[str],
    pattern: str,
    *,
    step_wait_ms: int = 80,
    start_wait_ms: int = 150,
    end_wait_ms: int = 120,
    grid_size_ratio: float = 0.72,
    grid_center_y_ratio: float = 0.52,
    use_uiautomator_bounds: bool = True,
    segment_points: int = 6,
) -> dict:
    """
    Unlock using a pattern path by generating and executing a Monkey `.mks` script.

    This approach supports a continuous gesture (DOWN -> MOVE... -> UP), which is
    required for pattern unlock and cannot be reliably achieved with multiple
    `adb shell input swipe` segments.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    seq = _parse_pattern(pattern)
    if not seq:
        return {"ok": False, "error": "pattern is required", "pattern": pattern}
    if any((d < 1 or d > 9) for d in seq):
        return {"ok": False, "error": "pattern digits must be 1..9", "pattern": pattern, "parsed": seq}
    # Typical patterns should not repeat nodes; allow but warn.
    warn_repeat = len(set(seq)) != len(seq)

    wh = get_screen_size(device_serial)
    if not wh:
        return {"ok": False, "error": "failed_to_get_screen_size"}
    sw, sh = int(wh[0]), int(wh[1])
    used_bounds = None
    pts = None
    if use_uiautomator_bounds:
        used_bounds = _find_lock_pattern_bounds_via_uiautomator(device_serial)
        if used_bounds:
            pts = _pattern_grid_points_from_bounds(used_bounds)
    if not pts:
        pts = _pattern_grid_points(sw, sh, grid_size_ratio=grid_size_ratio, grid_center_y_ratio=grid_center_y_ratio)

    coords: list[tuple[int, int]] = [pts[d] for d in seq if d in pts]
    if len(coords) < 2:
        return {"ok": False, "error": "pattern_too_short", "parsed": seq}

    # Expand each segment into multiple move points to keep the gesture continuous/smooth.
    seg_n = max(1, min(int(segment_points), 20))
    path: list[tuple[int, int]] = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        path.extend(_interpolate_points(a, b, seg_n))

    # Build monkey script lines (same format as covert_touch.to_monkey_script)
    body: list[str] = []
    event_time = 0
    down_time = 0

    # Small wait to ensure keyguard UI is ready.
    if start_wait_ms and start_wait_ms > 0:
        body.append(f"UserWait({int(start_wait_ms)})")
        event_time += int(start_wait_ms)

    x0, y0 = path[0]
    body.append(
        "DispatchPointer("
        f"{down_time},{event_time},0,"
        f"{float(x0):.1f},{float(y0):.1f},"
        "1.0,1.0,0,0,0,0,0)"
    )

    # Slower movement can be required on some keyguards; allow a wider range.
    step = max(20, min(int(step_wait_ms), 1200))
    # Distribute time across interpolated points so the overall pace matches step_wait_ms.
    # Total moves per original segment = seg_n; total moves overall = len(path)-1.
    per_move = max(12, int(round(step / max(1, seg_n))))
    for (x, y) in path[1:]:
        body.append(f"UserWait({per_move})")
        event_time += per_move
        body.append(
            "DispatchPointer("
            f"{down_time},{event_time},2,"
            f"{float(x):.1f},{float(y):.1f},"
            "1.0,1.0,0,0,0,0,0)"
        )

    # Lift up at the last point
    if end_wait_ms and end_wait_ms > 0:
        body.append(f"UserWait({int(end_wait_ms)})")
        event_time += int(end_wait_ms)
    xl, yl = path[-1]
    body.append(
        "DispatchPointer("
        f"{down_time},{event_time},1,"
        f"{float(xl):.1f},{float(yl):.1f},"
        "1.0,1.0,0,0,0,0,0)"
    )

    lines: list[str] = [
        "type= raw events",
        f"count= {len(body)}",
        "speed= 1.0",
        "start data >>",
        *body,
    ]
    script = "\n".join(lines) + "\n"

    remote = "/data/local/tmp/phone_pilot_unlock_pattern.mks"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="phone_pilot_unlock_", suffix=".mks", delete=False) as f:
            tmp_path = pathlib.Path(f.name)
            f.write(script.encode("utf-8", errors="replace"))
        # push + run
        push = CommandRunner.run(adb_prefix(device_serial) + ["push", str(tmp_path), remote], check=False)
        run = CommandRunner.run(adb_prefix(device_serial) + ["shell", "monkey", "-f", remote, "1"], check=False)
        return {
            "ok": push.returncode == 0 and run.returncode == 0,
            "device_serial": device_serial,
            "pattern": pattern,
            "parsed": seq,
            "warning_repeat": warn_repeat,
            "timing": {
                "start_wait_ms": int(start_wait_ms),
                "step_wait_ms": int(step_wait_ms),
                "end_wait_ms": int(end_wait_ms),
                "segment_points": seg_n,
                "per_move_wait_ms": per_move,
            },
            "screen": {"w": sw, "h": sh},
            "grid": {"grid_size_ratio": grid_size_ratio, "grid_center_y_ratio": grid_center_y_ratio},
            "lock_pattern_bounds": list(used_bounds) if used_bounds else None,
            "coords": coords,
            "path_points": path,
            "remote_script": remote,
            "push_rc": push.returncode,
            "run_rc": run.returncode,
            "push_stderr": (push.stderr or "").strip(),
            "run_stderr": (run.stderr or "").strip(),
        }
    finally:
        try:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def reset_to_home(
    device_serial: Optional[str],
    *,
    presses: int = 2,
    delay_s: float = 0.25,
    settle_s: float = 0.4,
) -> None:
    """
    Best-effort: reset UI to the launcher home screen.

    - Sends KEYCODE_HOME multiple times; on many launchers, the 2nd HOME returns to the first page.
    - Uses small delays to let the UI settle.
    """
    cmd = adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_HOME"]
    presses_i = max(1, int(presses))
    for i in range(presses_i):
        CommandRunner.run(cmd, check=False)
        if delay_s > 0 and i < presses_i - 1:
            time.sleep(delay_s)
    if settle_s and settle_s > 0:
        time.sleep(float(settle_s))


def _looks_like_leakcanary_activity(component_or_activity: Optional[str]) -> bool:
    """
    Heuristic: detect LeakCanary launcher/activity so we can avoid launching it when the app
    also has a real main launcher activity.
    """
    s = (component_or_activity or "").strip().lower()
    if not s:
        return False
    return ("leakcanary" in s) or ("leaklauncheractivity" in s)


def get_launcher_component_for_package(device_serial: Optional[str], package: str) -> Optional[str]:
    """
    Get the LAUNCHER activity component for a single package (fast path).
    Uses: cmd package resolve-activity --brief -a MAIN -c LAUNCHER <package>
    Returns pkg/activity or None.
    """
    if not device_serial or not (package or "").strip():
        return None
    pkg = (package or "").strip()
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            pkg,
        ],
        check=False,
        log_output=False,
        timeout_s=5,
    )
    out = (proc.stdout or "").strip()
    for line in out.splitlines():
        line = line.strip()
        if line and "/" in line and line.startswith(pkg + "/"):
            return line
    return None


def list_launcher_components(device_serial: Optional[str], package: str) -> list[str]:
    """
    List LAUNCHER components (pkg/activity) for a specific package.

    Uses:
      adb shell cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER

    We filter the global output by `package/`.
    """
    if not device_serial:
        return []
    pkg = (package or "").strip()
    if not pkg:
        return []
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "cmd",
            "package",
            "query-activities",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
        ],
        check=False,
        log_output=False,
        timeout_s=10,
    )
    out = proc.stdout or ""
    comps: list[str] = []
    prefix = pkg + "/"
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("No activities found"):
            continue
        if line.startswith(prefix):
            comps.append(line)
    return sorted(set(comps))


def pick_best_launcher_component(device_serial: Optional[str], package: str) -> Optional[str]:
    """
    Choose the most likely "real app" launcher component for a package.
    Avoid LeakCanary's launcher activity if the app also has another launcher activity.
    """
    comps = list_launcher_components(device_serial, package)
    if not comps:
        return None
    non_lc = [c for c in comps if not _looks_like_leakcanary_activity(c)]
    return non_lc[0] if non_lc else comps[0]


def restart_app(
    device_serial: Optional[str],
    *,
    package: Optional[str] = None,
    component: Optional[str] = None,
    deeplink: Optional[str] = None,
    force_stop: bool = True,
    wait_s: float = 1.0,
) -> None:
    """
    Best-effort: restart an app before recording/replay.

    Provide one of:
    - component: "com.example/.MainActivity"
    - deeplink: "myapp://path"
    - package: "com.example" (fallback launch using monkey 1 event)
    """
    if not device_serial:
        return

    if package and force_stop:
        CommandRunner.run(adb_prefix(device_serial) + ["shell", "am", "force-stop", package], check=False, log_output=False, silent=True)

    if component:
        CommandRunner.run(adb_prefix(device_serial) + ["shell", "am", "start", "-n", component], check=False, log_output=False)
    elif deeplink:
        CommandRunner.run(
            adb_prefix(device_serial)
            + ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", deeplink],
            check=False, log_output=False,
        )
    elif package:
        # Fast path: resolve-activity for this package only (no global query).
        best = get_launcher_component_for_package(device_serial, package)
        if not best or "/" not in best:
            best = pick_best_launcher_component(device_serial, package)
        if best and "/" in best:
            CommandRunner.run(
                adb_prefix(device_serial) + ["shell", "am", "start", "-n", best],
                check=False, log_output=False, timeout_s=10,
            )
        else:
            # Fallback: launch via a single monkey event (timeout so we don't block forever).
            CommandRunner.run(
                adb_prefix(device_serial)
                + ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                check=False, log_output=False, timeout_s=15,
            )

    if wait_s and wait_s > 0:
        time.sleep(float(wait_s))


_FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^}]*\s+u\d+\s+([^\s/]+)/([^\s}]+)")


def get_current_focus(device_serial: Optional[str]) -> dict[str, Optional[str]]:
    """
    Best-effort foreground focus from `dumpsys window`.
    Returns: {package, activity}
    """
    if not device_serial:
        return {"package": None, "activity": None}
    # `dumpsys window` output can be huge and slow (and can cause client timeouts).
    # We only need focus lines; filter on-device to keep output small.
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "dumpsys window | grep -E 'mCurrentFocus=|mFocusedApp=' | head -n 5",
        ],
        check=False,
        log_output=False,
    )
    m = _FOCUS_RE.search(proc.stdout or "")
    if not m:
        return {"package": None, "activity": None}
    return {"package": m.group(1), "activity": m.group(2)}


def is_launcher_package(pkg: Optional[str]) -> bool:
    """Heuristic check if package name looks like a launcher."""
    if not pkg:
        return False
    p = pkg.lower()
    return (
        "launcher" in p
        or p.endswith(".launcher3")
        or p.endswith(".launcher")
        or p.startswith("com.miui.home")
        or p.startswith("com.mi.android.globallauncher")
    )


def get_default_launcher_component(device_serial: Optional[str]) -> Optional[str]:
    """
    Resolve the default launcher (HOME) component.

    Uses:
      adb shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME
    """
    if not device_serial:
        return None
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.HOME",
        ],
        check=False,
        log_output=False,
    )
    out = proc.stdout or ""
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("No activity found"):
            continue
        if line.startswith("name="):
            return line.split("name=", 1)[1].strip() or None
        if line.startswith("activity="):
            return line.split("activity=", 1)[1].strip() or None
        if "/" in line:
            return line
    return None


def list_all_launcher_components(device_serial: Optional[str]) -> dict[str, list[str]]:
    """
    List all launcher components (pkg/activity) and group by package.
    """
    comps: dict[str, list[str]] = {}
    if not device_serial:
        return comps
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "cmd",
            "package",
            "query-activities",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
        ],
        check=False,
        log_output=False,
    )
    out = proc.stdout or ""
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("No activities found"):
            continue
        if "/" not in line:
            continue
        pkg = line.split("/", 1)[0].strip()
        if not pkg:
            continue
        comps.setdefault(pkg, []).append(line)
    return {k: sorted(set(v)) for k, v in comps.items()}


def build_launch_profiles_snapshot(device_serial: Optional[str]) -> list[dict[str, str]]:
    """
    Build launch profiles from launcher components (no UI changes).
    """
    profiles: list[dict[str, str]] = []
    if not device_serial:
        return profiles
    comp_map = list_all_launcher_components(device_serial)
    for pkg, comps in comp_map.items():
        if not comps:
            continue
        non_lc = [c for c in comps if not _looks_like_leakcanary_activity(c)]
        component = non_lc[0] if non_lc else comps[0]
        activity = component.split("/", 1)[1].strip() if "/" in component else None
        serial_part = f"-s {device_serial} " if device_serial else ""
        adb_cmd = f"adb {serial_part}shell am start -n {component}" if component else None
        profiles.append(
            {
                "package": pkg,
                "activity": activity,
                "component": component,
                "adb_cmd": adb_cmd,
                "source": "query_activities",
            }
        )
    return profiles


def list_launchable_packages(device_serial: Optional[str]) -> list[str]:
    """
    List launchable app package names (best-effort) from the device.

    Uses:
      adb shell cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER

    Output lines are typically:
      com.pkg/.MainActivity
      com.pkg/com.pkg.MainActivity
    """
    if not device_serial:
        return []
    proc = CommandRunner.run(
        adb_prefix(device_serial)
        + [
            "shell",
            "cmd",
            "package",
            "query-activities",
            "--brief",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
        ],
        check=False,
    )
    out = proc.stdout or ""
    pkgs: set[str] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line or line.startswith("No activities found"):
            continue
        # Prefer the part before "/"
        if "/" in line:
            pkg = line.split("/", 1)[0].strip()
            if pkg:
                pkgs.add(pkg)
    return sorted(pkgs)


def _parse_desc_kv(desc: str) -> dict[str, str]:
    """
    Parse `adb devices -l` description tail:
      product:xxx model:yyy device:zzz transport_id:12
    """
    out: dict[str, str] = {}
    for tok in (desc or "").split():
        if ":" not in tok:
            continue
        k, v = tok.split(":", 1)
        if k and v:
            out[k] = v
    return out


def _getprop(device_serial: Optional[str], key: str) -> Optional[str]:
    """Read a system property via `getprop`."""
    if not device_serial:
        return None
    proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "getprop", key], check=False)
    val = (proc.stdout or "").strip()
    return val or None


def _parse_locales(raw: Optional[str]) -> list[str]:
    """
    Parse Android locales string into a list of BCP-47 tags.

    Examples:
    - "en-US"
    - "zh-Hans-CN,zh-Hant-TW,en-US"
    - "" / None
    """
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    # Android uses comma-separated list in settings/system_locales.
    parts = [p.strip() for p in s.split(",") if p.strip()]
    # Normalize underscores -> hyphens
    out: list[str] = []
    for p in parts:
        p2 = p.replace("_", "-").strip()
        if p2:
            out.append(p2)
    return out


def get_device_locales(device_serial: Optional[str]) -> dict:
    """
    Best-effort detect device language/locale(s).

    Primary source:
    - `settings get system system_locales` (Android 7+)

    Fallback sources:
    - getprop persist.sys.locale / persist.sys.language / persist.sys.country
    - getprop ro.product.locale / ro.product.locale.language / ro.product.locale.region
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    # 1) settings system_locales
    settings_raw = None
    try:
        proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "settings", "get", "system", "system_locales"], check=False, delay_s=0.0, log_output=False)
        settings_raw = (proc.stdout or "").strip()
        # On some devices "null" is printed
        if settings_raw.lower() == "null":
            settings_raw = ""
    except Exception:
        settings_raw = None
    locales = _parse_locales(settings_raw)
    if locales:
        return {"ok": True, "device_serial": device_serial, "method": "settings_system_locales", "raw": settings_raw, "locales": locales, "primary": locales[0]}

    # 2) persist.sys.locale
    loc = _getprop(device_serial, "persist.sys.locale")
    locales2 = _parse_locales(loc)
    if locales2:
        return {"ok": True, "device_serial": device_serial, "method": "getprop_persist.sys.locale", "raw": loc, "locales": locales2, "primary": locales2[0]}

    # 3) persist.sys.language + persist.sys.country
    lang = _getprop(device_serial, "persist.sys.language")
    country = _getprop(device_serial, "persist.sys.country") or _getprop(device_serial, "persist.sys.region")
    if lang:
        tag = f"{lang}-{country}" if country else lang
        locales3 = _parse_locales(tag)
        return {"ok": True, "device_serial": device_serial, "method": "getprop_persist.sys.language_country", "raw": {"language": lang, "country": country}, "locales": locales3, "primary": locales3[0] if locales3 else None}

    # 4) ro.product.locale
    ro_loc = _getprop(device_serial, "ro.product.locale")
    locales4 = _parse_locales(ro_loc)
    if locales4:
        return {"ok": True, "device_serial": device_serial, "method": "getprop_ro.product.locale", "raw": ro_loc, "locales": locales4, "primary": locales4[0]}

    ro_lang = _getprop(device_serial, "ro.product.locale.language")
    ro_region = _getprop(device_serial, "ro.product.locale.region") or _getprop(device_serial, "ro.product.locale.country")
    if ro_lang:
        tag2 = f"{ro_lang}-{ro_region}" if ro_region else ro_lang
        locales5 = _parse_locales(tag2)
        return {"ok": True, "device_serial": device_serial, "method": "getprop_ro.product.locale_language_region", "raw": {"language": ro_lang, "region": ro_region}, "locales": locales5, "primary": locales5[0] if locales5 else None}

    return {"ok": False, "device_serial": device_serial, "error": "locale_not_found"}


def get_app_locales(device_serial: Optional[str], package_name: str) -> dict:
    """
    Best-effort detect per-app locales (Android 13+ supports per-app language).

    Tries:
    - `cmd locale get-app-locales <package>`
    Fallback:
    - returns unknown if command not supported.
    """
    pkg = (package_name or "").strip()
    if not device_serial or not pkg:
        return {"ok": False, "error": "device_serial and package_name are required", "package": pkg or None}
    # Note: Some Android versions require --user 0; we'll try both.
    cmds = [
        adb_prefix(device_serial) + ["shell", "cmd", "locale", "get-app-locales", pkg],
        adb_prefix(device_serial) + ["shell", "cmd", "locale", "get-app-locales", "--user", "0", pkg],
    ]
    last_err = None
    for cmd in cmds:
        proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            last_err = err or out or f"returncode={proc.returncode}"
            continue
        # Prefer parsing the bracket list. AOSP typically prints:
        # "Locales for <pkg> for user 0 are []"
        # or "... are [en-US,zh-CN]"
        m = re.search(r"\[(?P<inner>[^\]]*)\]", out)
        if m is not None:
            inner = (m.group("inner") or "").strip()
            locales = _parse_locales(inner)
            return {
                "ok": True,
                "device_serial": device_serial,
                "package": pkg,
                "method": "cmd_locale_get_app_locales",
                "raw": out,
                "locales": locales,
                "primary": locales[0] if locales else None,
                "is_set": bool(locales),
            }
        # If output format is unexpected, return ok but mark unknown/unset.
        return {
            "ok": True,
            "device_serial": device_serial,
            "package": pkg,
            "method": "cmd_locale_get_app_locales_unparsed",
            "raw": out,
            "locales": [],
            "primary": None,
            "is_set": False,
        }
    return {"ok": False, "device_serial": device_serial, "package": pkg, "error": "app_locale_not_supported_or_failed", "detail": last_err}

def get_android_os_info(device_serial: Optional[str]) -> dict:
    """
    Best-effort OS/build info via getprop.

    Includes:
    - Android release / SDK
    - Build fingerprint / incremental / security patch
    """
    if not device_serial:
        return {}
    release = _getprop(device_serial, "ro.build.version.release")
    sdk = _getprop(device_serial, "ro.build.version.sdk")
    security_patch = _getprop(device_serial, "ro.build.version.security_patch")
    incremental = _getprop(device_serial, "ro.build.version.incremental")
    build_id = _getprop(device_serial, "ro.build.id")
    fingerprint = _getprop(device_serial, "ro.build.fingerprint")
    codename = _getprop(device_serial, "ro.build.version.codename")
    return {
        "android_release": release,
        "sdk_int": int(sdk) if (sdk and sdk.isdigit()) else sdk,
        "security_patch": security_patch,
        "incremental": incremental,
        "build_id": build_id,
        "fingerprint": fingerprint,
        "codename": codename,
    }


def get_device_profile(device_serial: Optional[str]) -> dict:
    """
    Collect device identification info:
    - marketing name (e.g. 'POCO X5', 'moto g 5G (2023)') if available
    - model/brand/manufacturer
    - adb devices -l model/product/device fields
    """
    if not device_serial:
        return {}

    # From getprop
    market = (
        _getprop(device_serial, "ro.product.marketname")
        or _getprop(device_serial, "ro.product.odm.marketname")
        or _getprop(device_serial, "ro.product.system.marketname")
        or _getprop(device_serial, "ro.product.vendor.marketname")
    )
    model = _getprop(device_serial, "ro.product.model")
    brand = _getprop(device_serial, "ro.product.brand")
    manufacturer = _getprop(device_serial, "ro.product.manufacturer")
    device = _getprop(device_serial, "ro.product.device")
    product = _getprop(device_serial, "ro.product.name")

    # From `adb devices -l`
    adb_info: dict[str, str] = {}
    from phone_pilot.android.adb.utils import adb_executable

    proc = CommandRunner.run([adb_executable(), "devices", "-l"], check=False)
    for d in parse_adb_devices(proc.stdout or ""):
        if d.get("serial") == device_serial:
            adb_info = _parse_desc_kv(d.get("description", ""))
            break

    adb_model = adb_info.get("model")
    # Make adb model more human readable if needed
    adb_model_pretty = adb_model.replace("_", " ") if adb_model else None

    display_name = market or model or adb_model_pretty or device_serial
    # Optionally prefix brand when market/model doesn't already include it
    if brand and display_name and brand.lower() not in display_name.lower():
        display_name = f"{brand} {display_name}"

    return {
        "serial": device_serial,
        "display_name": display_name,
        "market_name": market,
        "model": model,
        "brand": brand,
        "manufacturer": manufacturer,
        "device": device,
        "product": product,
        "adb": adb_info,
    }


_WM_SIZE_RE = re.compile(r"^(?:Physical|Override)\s+size:\s*(\d+)x(\d+)\s*$", re.MULTILINE)
_WM_DENSITY_RE = re.compile(r"^(?:Physical|Override)\s+density:\s*(\d+)\s*$", re.MULTILINE)
_ABS_X_RE = re.compile(r"ABS_MT_POSITION_X\s*:.*\bmax\s+(\d+)\b")
_ABS_Y_RE = re.compile(r"ABS_MT_POSITION_Y\s*:.*\bmax\s+(\d+)\b")


def get_screen_size(device_serial: Optional[str]) -> Optional[tuple[int, int]]:
    """Return screen size (width, height) from `wm size`."""
    if not device_serial:
        return None
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "wm", "size"],
        check=False,
        timeout_s=6.0,
        log_output=False,
        silent=True,
    )
    m = _WM_SIZE_RE.search(proc.stdout or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def get_screen_density(device_serial: Optional[str]) -> Optional[int]:
    """
    Best-effort screen density (dpi) from `adb shell wm density`.
    """
    if not device_serial:
        return None
    proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "wm", "density"], check=False)
    text = proc.stdout or ""
    # Prefer Override density if present by scanning all matches and taking last.
    matches = _WM_DENSITY_RE.findall(text)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except Exception:
        return None


def get_touch_abs_max(device_serial: Optional[str], keep_device: Optional[str]) -> Optional[tuple[int, int]]:
    """
    Find ABS_MT_POSITION_X/Y max from `adb shell getevent -lp`.

    If keep_device is provided, prefer a block matching that substring; otherwise pick the
    first device block that contains both ABS_MT_POSITION_X and ABS_MT_POSITION_Y.
    """
    if not device_serial:
        return None
    proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "getevent", "-lp"], check=False)
    text = proc.stdout or ""
    if not text:
        return None

    # Split into blocks starting at "add device"
    blocks = re.split(r"(?=^add device\s+\d+:\s+/dev/input/event\d+)", text, flags=re.MULTILINE)
    candidates: list[tuple[int, int]] = []
    for b in blocks:
        if "ABS_MT_POSITION_X" not in b or "ABS_MT_POSITION_Y" not in b:
            continue
        if keep_device and keep_device not in b:
            continue
        mx = _ABS_X_RE.search(b)
        my = _ABS_Y_RE.search(b)
        if not (mx and my):
            continue
        candidates.append((int(mx.group(1)), int(my.group(1))))
    if candidates:
        return candidates[0]

    # fallback: ignore keep_device filter
    for b in blocks:
        if "ABS_MT_POSITION_X" not in b or "ABS_MT_POSITION_Y" not in b:
            continue
        mx = _ABS_X_RE.search(b)
        my = _ABS_Y_RE.search(b)
        if mx and my:
            return int(mx.group(1)), int(my.group(1))
    return None


# =============================================================================
# P2 MCP tools - clipboard, notifications, wifi, airplane, shell, device info
# =============================================================================

# 允许的 shell 命令前缀 / Allowed command prefixes for execute_shell
_SHELL_WHITELIST = frozenset(
    [
        "pm",
        "am",
        "dumpsys",
        "getprop",
        "cmd",
        "input",
        "wm",
        "settings",
        "content",
        "service",
        "logcat",
        "ps",
        "top",
        "cat",
        "ls",
        "df",
        "id",
    ]
)

# 禁止的命令前缀 / Forbidden command prefixes
_SHELL_FORBIDDEN = frozenset(["su", "reboot", "shutdown", "mkfs", "dd"])


def get_clipboard_text(device_serial: Optional[str] = None) -> dict:
    """获取剪贴板文本（Android 10+）。
    Get clipboard text (Android 10+).

    Args:
        device_serial: 设备序列号 / Device serial

    Returns:
        dict: {"ok": True, "text": str} 或 {"ok": False, "error": str}
    """
    try:
        cmd = adb_prefix(device_serial) + ["shell", "cmd", "clipboard", "get-primary-clip"]
        proc = CommandRunner.run(cmd, check=False, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"clipboard command failed: {proc.stderr or proc.stdout}"}
        return {"ok": True, "text": (proc.stdout or "").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_notifications(device_serial: Optional[str] = None) -> dict:
    """获取通知栏通知列表。
    Get notification list from notification bar.

    Args:
        device_serial: 设备序列号 / Device serial

    Returns:
        dict: {"ok": True, "notifications": [{"package": str, "title": str, "text": str}]}
    """
    try:
        cmd = adb_prefix(device_serial) + ["shell", "dumpsys", "notification", "--noredact"]
        proc = CommandRunner.run(cmd, check=False, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"dumpsys notification failed: {proc.stderr or proc.stdout}", "notifications": []}
        out = proc.stdout or ""
        notifications: list[dict[str, str]] = []
        # 按 NotificationRecord 分段 / Split by NotificationRecord blocks
        blocks = re.split(r"\n\s*NotificationRecord\s*\(", out)
        for block in blocks:
            pkg = ""
            title = ""
            text = ""
            for line in block.splitlines():
                line_stripped = line.strip()
                # pkg from first line or inside block: pkg=com.example
                pkg_m = re.search(r"pkg=([^\s]+)", line_stripped)
                if pkg_m:
                    pkg = pkg_m.group(1).strip()
                # android.title=Value or android.title=String (Value)
                if "android.title=" in line_stripped:
                    m = re.search(r"android\.title=String\s*\(\s*([^)]*)\s*\)", line_stripped)
                    if m:
                        title = m.group(1).strip()
                    else:
                        title = line_stripped.split("android.title=", 1)[1].strip()
                if "android.text=" in line_stripped:
                    m = re.search(r"android\.text=String\s*\(\s*([^)]*)\s*\)", line_stripped)
                    if m:
                        text = m.group(1).strip()
                    else:
                        text = line_stripped.split("android.text=", 1)[1].strip()
            if pkg:
                notifications.append({"package": pkg, "title": title, "text": text})
        return {"ok": True, "notifications": notifications}
    except Exception as e:
        return {"ok": False, "error": str(e), "notifications": []}


def set_wifi_enabled(device_serial: Optional[str] = None, enabled: bool = True) -> dict:
    """开关 WiFi。
    Toggle WiFi on/off.

    Args:
        device_serial: 设备序列号 / Device serial
        enabled: True 开启 / False 关闭 / True to enable, False to disable

    Returns:
        dict: {"ok": True, "wifi_on": bool}
    """
    try:
        action = "enable" if enabled else "disable"
        cmd = adb_prefix(device_serial) + ["shell", "svc", "wifi", action]
        proc = CommandRunner.run(cmd, check=False, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"svc wifi {action} failed: {proc.stderr or proc.stdout}", "wifi_on": enabled}
        return {"ok": True, "wifi_on": enabled}
    except Exception as e:
        return {"ok": False, "error": str(e), "wifi_on": enabled}


def set_airplane_mode(device_serial: Optional[str] = None, enabled: bool = True) -> dict:
    """开关飞行模式。
    Toggle airplane mode.

    Args:
        device_serial: 设备序列号 / Device serial
        enabled: True 开启 / False 关闭 / True to enable, False to disable

    Returns:
        dict: {"ok": True, "airplane_on": bool}
    """
    try:
        val = "1" if enabled else "0"
        cmd1 = adb_prefix(device_serial) + ["shell", "settings", "put", "global", "airplane_mode_on", val]
        proc1 = CommandRunner.run(cmd1, check=False, log_output=False)
        if proc1.returncode != 0:
            return {"ok": False, "error": f"settings put failed: {proc1.stderr or proc1.stdout}", "airplane_on": enabled}
        cmd2 = adb_prefix(device_serial) + ["shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE"]
        CommandRunner.run(cmd2, check=False, log_output=False)
        return {"ok": True, "airplane_on": enabled}
    except Exception as e:
        return {"ok": False, "error": str(e), "airplane_on": enabled}


def execute_shell(
    device_serial: Optional[str] = None,
    command: str = "",
    timeout_s: float = 30.0,
) -> dict:
    """执行受限 shell 命令（白名单限制）。
    Execute restricted shell command (whitelist limited).

    Args:
        device_serial: 设备序列号 / Device serial
        command: shell 命令 / Shell command
        timeout_s: 超时秒数 / Timeout in seconds

    Returns:
        dict: {"ok": True, "stdout": str, "stderr": str, "returncode": int}
        或 {"ok": False, "error": str}
    """
    try:
        cmd_stripped = (command or "").strip()
        if not cmd_stripped:
            return {"ok": False, "error": "command is required"}
        parts = cmd_stripped.split()
        first = parts[0] if parts else ""
        # 检查白名单 / Check whitelist
        if first not in _SHELL_WHITELIST:
            return {"ok": False, "error": f"command prefix '{first}' not in whitelist"}
        # 检查禁止前缀 / Check forbidden prefixes
        for forbidden in _SHELL_FORBIDDEN:
            if cmd_stripped.startswith(forbidden):
                return {"ok": False, "error": f"command prefix '{forbidden}' is forbidden"}
        if cmd_stripped.startswith("rm -rf") or cmd_stripped.startswith("rm -fr"):
            return {"ok": False, "error": "rm -rf is forbidden"}
        full_cmd = adb_prefix(device_serial) + ["shell"] + parts
        proc = CommandRunner.run(full_cmd, check=False, log_output=False, timeout_s=timeout_s)
        return {
            "ok": True,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_device_model_and_version(device_serial: Optional[str]) -> tuple[str, str]:
    """获取设备型号和系统版本（用于 phone_get_device_info）。
    Get device model and OS version from getprop.
    """
    model = _getprop(device_serial, "ro.product.model") or ""
    version = _getprop(device_serial, "ro.build.version.release") or ""
    return (model, version)

