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
from typing import Optional

from android_tool.adb_parsers import parse_adb_devices
from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner
from typing import List, Dict

import os
import pathlib
import shutil
import subprocess
import tempfile


def _needs_unicode_fallback(text: str) -> bool:
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


def input_keyevent(device_serial: Optional[str], key: str, *, wait_s: float = 0.12) -> dict:
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
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
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
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
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
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
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
    m = re.search(r"application-label:'([^']*)'", out)
    if m and m.group(1).strip():
        label = m.group(1).strip()
    if not label:
        m = re.search(r"application-label-[^:]+:'([^']*)'", out)
        if m and m.group(1).strip():
            label = m.group(1).strip()
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
    return {"ok": True, "label": label, "version_code": vcode, "version_name": vname}


def get_app_info(
    device_serial: Optional[str],
    package_name: str,
    *,
    apk_path_on_device: Optional[str] = None,
    cache_dir: Optional[str] = None,
    aapt_path: Optional[str] = None,
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
        }

    aapt_bin = aapt_path or shutil.which("aapt") or shutil.which("aapt2")
    if not aapt_bin:
        return {
            "AppName": pkg,
            "PackageName": pkg,
            "VersionName": None,
            "VersionCode": None,
            "AppNameError": "aapt/aapt2 not found on PATH",
        }

    remote_apk = (apk_path_on_device or "").strip() or _adb_pm_path(device_serial, pkg)
    if not remote_apk:
        return {
            "AppName": pkg,
            "PackageName": pkg,
            "VersionName": None,
            "VersionCode": None,
            "AppNameError": "failed to resolve apk path via pm path",
        }

    cache = pathlib.Path(cache_dir) if cache_dir else (pathlib.Path(tempfile.gettempdir()) / "phone_touch_apk_cache")
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
            }

    info = _aapt_dump_badging_info(aapt_bin, local_apk)
    app_label = (info.get("label") if isinstance(info, dict) else None) or pkg
    version_name = info.get("version_name") if isinstance(info, dict) else None
    version_code = info.get("version_code") if isinstance(info, dict) else None
    return {
        "AppName": app_label or pkg,
        "PackageName": pkg,
        "VersionName": version_name,
        "VersionCode": version_code,
    }


def _adb_pm_path(device_serial: str, package_name: str) -> Optional[str]:
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
        ps = CommandRunner.run(adb_prefix(device_serial) + ["shell", "ps", "-A", "-o", "NAME"], check=False, delay_s=0.0, log_output=False)
        out = (ps.stdout or "")
        if ps.returncode != 0 or not out.strip():
            # Fallback for older / different toybox ps variants.
            ps2 = CommandRunner.run(adb_prefix(device_serial) + ["shell", "ps", "-A"], check=False, delay_s=0.0, log_output=False)
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
        from android_tool.uiautomator import dump_ui_xml, parse_uiautomator_nodes
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

    remote = "/data/local/tmp/phone_touch_unlock_pattern.mks"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="phone_touch_unlock_", suffix=".mks", delete=False) as f:
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
        CommandRunner.run(adb_prefix(device_serial) + ["shell", "am", "force-stop", package], check=False)

    if component:
        CommandRunner.run(adb_prefix(device_serial) + ["shell", "am", "start", "-n", component], check=False)
    elif deeplink:
        CommandRunner.run(
            adb_prefix(device_serial)
            + ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", deeplink],
            check=False,
        )
    elif package:
        # Prefer a real launcher activity over LeakCanary launcher (common in debug builds).
        best = pick_best_launcher_component(device_serial, package)
        if best and "/" in best:
            CommandRunner.run(adb_prefix(device_serial) + ["shell", "am", "start", "-n", best], check=False)
        else:
            # Fallback: launch via a single monkey event.
            CommandRunner.run(
                adb_prefix(device_serial)
                + ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
                check=False,
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
    from android_tool.adb_utils import adb_executable

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
    if not device_serial:
        return None
    proc = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "wm", "size"],
        check=False,
        timeout_s=6.0,
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


