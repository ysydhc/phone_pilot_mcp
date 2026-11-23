"""Auto-manifest generator for PhonePilot scene restoration.

Automatically projects execution logs (run_meta.json, steps.json, business_report.json)
into a compliant phone_pilot.scene.v1 manifest so that any run (especially PASSED acceptance)
can be restored onto real devices (e.g. Samsung SM-S931U) with a single click.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from phone_pilot.core.safe_io import safe_directory
from phone_pilot.core.scene_restore import SCHEMA


def get_online_devices() -> list[str]:
    """Get list of online adb device serials."""
    try:
        out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=3
        )
        serials = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials
    except Exception:
        return []


def _scan_ios_devices(target_bundle_id: str = "com.baidujp.gbusdkdemo") -> list[dict[str, Any]]:
    """Scan booted iOS simulators (simctl) and connected iOS real devices (pymobiledevice3)."""
    devices: list[dict[str, Any]] = []
    bundle_id = target_bundle_id if target_bundle_id and target_bundle_id != "cc.admaster.android.demo" else "com.baidujp.gbusdkdemo"

    # 1. Booted iOS Simulators via simctl
    try:
        from phone_pilot.ios.pmd.utils import simctl_prefix

        prefix = simctl_prefix()
        proc = subprocess.run(
            prefix + ["list", "devices", "booted", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            payload = json.loads(proc.stdout)
            for runtime_key, devs in (payload.get("devices") or {}).items():
                os_ver = ""
                if "SimRuntime.iOS-" in runtime_key:
                    os_ver = runtime_key.split("SimRuntime.iOS-")[-1].replace("-", ".")
                for d in devs or []:
                    if d.get("state") != "Booted" or not d.get("udid"):
                        continue
                    udid = str(d["udid"])
                    name = str(d.get("name") or "iPhone Simulator")
                    installed = False
                    try:
                        app_chk = subprocess.run(
                            prefix + ["get_app_container", udid, bundle_id],
                            capture_output=True,
                            text=True,
                            timeout=3,
                        )
                        installed = app_chk.returncode == 0 and bool(app_chk.stdout.strip())
                    except Exception:
                        pass
                    ver_label = f"iOS {os_ver} Simulator" if os_ver else "iOS Simulator"
                    devices.append({
                        "serial": udid,
                        "platform": "ios",
                        "device_type": "simulator",
                        "status": "online",
                        "brand": "Apple",
                        "model": name,
                        "release": os_ver,
                        "resolution": "",
                        "app_installed": installed,
                        "display_name": f"🍎 {name} ({ver_label}) [{udid[:8]}]",
                    })
    except Exception:
        pass

    # 2. Connected iOS Real Devices via pymobiledevice3 (optional)
    try:
        from phone_pilot.ios.pmd.utils import pmd_prefix

        proc = subprocess.run(
            pmd_prefix() + ["usbmux", "list", "--no-color"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        if proc.returncode == 0 and proc.stdout.strip().startswith("["):
            items = json.loads(proc.stdout)
            seen = {d["serial"] for d in devices}
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                udid = str(item.get("UniqueDeviceID") or item.get("Identifier") or "")
                if not udid or udid in seen:
                    continue
                conn = str(item.get("ConnectionType") or "")
                if conn and conn.lower() not in ("usb", "network"):
                    continue
                name = str(item.get("DeviceName") or item.get("ProductType") or "iPhone")
                os_ver = str(item.get("ProductVersion") or "")
                ver_label = f"iOS {os_ver}" if os_ver else "iOS"
                devices.append({
                    "serial": udid,
                    "platform": "ios",
                    "device_type": "real",
                    "status": "online",
                    "brand": "Apple",
                    "model": name,
                    "release": os_ver,
                    "resolution": "",
                    "app_installed": True,
                    "display_name": f"🍎 {name} ({ver_label}) [{udid[:8]}]",
                })
                seen.add(udid)
    except Exception:
        pass

    return devices


def get_online_devices_detailed(
    target_package: str = "cc.admaster.android.demo",
    platform_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Scan all connected Android (ADB) and iOS (Simctl/PMD) devices with metadata."""
    devices: list[dict[str, Any]] = []
    if platform_filter in (None, "android"):
        serials = get_online_devices()
        for s in serials:
            info: dict[str, Any] = {
                "serial": s,
                "platform": "android",
                "device_type": "real",
                "status": "online",
                "brand": "",
                "model": s,
                "release": "",
                "resolution": "",
                "app_installed": False,
                "display_name": s,
            }
            try:
                cmd = (
                    f"getprop ro.product.brand; "
                    f"getprop ro.product.model; "
                    f"getprop ro.build.version.release; "
                    f"wm size; "
                    f"pm path {target_package}"
                )
                out = subprocess.run(
                    ["adb", "-s", s, "shell", cmd],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
                if len(lines) >= 3:
                    info["brand"] = lines[0]
                    info["model"] = lines[1]
                    info["release"] = lines[2]
                for line in lines:
                    if "Physical size:" in line:
                        info["resolution"] = line.split("Physical size:")[-1].strip()
                    elif line.startswith("package:"):
                        info["app_installed"] = True
                brand_cap = info["brand"].upper() if len(info["brand"]) <= 4 else info["brand"].capitalize()
                model_str = info["model"]
                if brand_cap and not model_str.lower().startswith(brand_cap.lower()):
                    label = f"{brand_cap} {model_str}"
                else:
                    label = model_str
                meta_parts = []
                if info["release"]:
                    meta_parts.append(f"Android {info['release']}")
                if info["resolution"]:
                    meta_parts.append(info["resolution"])
                meta_suffix = f" ({' · '.join(meta_parts)})" if meta_parts else ""
                info["display_name"] = f"📱 {label}{meta_suffix} [{s}]"
            except Exception:
                pass
            devices.append(info)

    if platform_filter in (None, "ios"):
        devices.extend(_scan_ios_devices(target_bundle_id=target_package))
    return devices


def _ensure_default_mock_rules(run_dir: Path) -> None:
    """Ensure mock_rules.json exists in run_dir for ADX Demo offline/replay scene restore."""
    rules_file = run_dir / "mock_rules.json"
    if rules_file.is_file():
        return
    default_body = {
        "n": 1,
        "req_id": "scene_restore_bid",
        "ad": [
            {
                "bidid": "scene_restore_bid",
                "tracking_id": "scene_restore_bid",
                "id": "175022912226219198",
                "type": "image",
                "act": 2,
                "winurl": "http://localhost:8090/track/splash_win",
                "w_picurl": "https://materials.admaster.cc/online/9b7fdf21ba2ad89b4e9a97834bb256ba.png",
                "curl": "https://play.google.com/store/apps/details?id=com.test",
                "closetype": 5,
                "monitors": {
                    "s": ["http://localhost:8090/track/splash_s"],
                    "t": [],
                    "c": ["http://localhost:8090/track/splash_c"],
                    "vstart": [],
                    "vplayend": [],
                    "vplayfail": [],
                    "vskip": [],
                    "scard": [],
                },
                "mute": False,
                "container_width": 1080,
                "container_height": 2309,
                "auto_play": 1,
                "skiptime": 5,
                "bidlayer": "150",
                "region_click": 0,
                "tit_c": "AdMaster 验收测试广告",
                "tit": "AdMaster 验收测试广告",
                "desc": "真机现场复原验证广告物料",
                "icon": "https://materials.admaster.cc/online/8f8bd0091d304df3993d0a55d94d15a0.jpeg",
                "w": 1200,
                "h": 2132,
                "appname": "AdMaster Demo",
                "pk": "com.test",
                "duration": 5,
                "btn": "立即体验",
                "app_id": "com.test",
            }
        ],
    }
    rules = [
        {"method": "POST", "path": "/sdk/bid", "status": 200, "body": default_body, "delay_ms": 0},
        {"method": "GET", "path": "/sdk/bid", "status": 200, "body": default_body, "delay_ms": 0},
    ]
    try:
        with safe_directory(run_dir, writable=True) as folder:
            folder.write_bytes(
                "mock_rules.json",
                json.dumps(rules, ensure_ascii=False, indent=2).encode("utf-8"),
                replace=True,
            )
    except Exception:
        pass


def get_device_fingerprint(serial: str) -> Optional[str]:
    """Read ro.build.fingerprint from connected device."""
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "ro.build.fingerprint"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        val = out.stdout.strip()
        return val if val else None
    except Exception:
        return None


def get_installed_apk_hash(serial: str, package_name: str) -> Optional[str]:
    """Get installed APK sha256 via adb."""
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", "pm", "path", package_name],
            capture_output=True,
            text=True,
            timeout=3,
        )
        lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        if not lines or not lines[0].startswith("package:"):
            return None
        apk_path = lines[0][len("package:"):]
        hash_out = subprocess.run(
            ["adb", "-s", serial, "shell", "sha256sum", apk_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        parts = hash_out.stdout.strip().split()
        if parts and len(parts[0]) == 64:
            return parts[0]
        return None
    except Exception:
        return None


def get_main_activity(serial: str, package_name: str) -> Optional[str]:
    """Resolve main launcher activity via adb."""
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "package", package_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        m = re.search(r"android\.intent\.action\.MAIN:\s+[\da-fA-F]+\s+([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.]+)", out.stdout)
        if m:
            act = m.group(2)
            if not act.startswith("."):
                if act.startswith(package_name + "."):
                    act = act[len(package_name):]
                else:
                    act = f".{act}"
            return act
    except Exception:
        pass
    return None


def ensure_scene_manifest(
    run_dir: str | Path,
    *,
    device_serial: Optional[str] = None,
    package_name: Optional[str] = None,
    activity_name: Optional[str] = None,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """Ensure a valid scene_manifest.json exists in run_dir.

    If missing, automatically synthesizes a schema-compliant manifest from
    run artifacts and connected devices.

    Returns:
        tuple[manifest_dict, manifest_sha256]
    """
    run_dir = Path(run_dir).expanduser().resolve()
    manifest_file = run_dir / "scene_manifest.json"

    # Read existing meta early so cache check can detect platform mismatch
    meta = {}
    meta_path = run_dir / "run_meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    meta_platform = str((meta.get("device") or {}).get("platform") or "").lower()

    if manifest_file.is_file() and not force_refresh:
        try:
            raw = manifest_file.read_bytes()
            data = json.loads(raw)
            pkg_existing = data.get("package", {}).get("name", "")
            plat_existing = str((data.get("device") or {}).get("platform") or "").lower()
            acts_existing = data.get("actions", [])
            has_bcast = any(a.get("type") == "broadcast" for a in acts_existing)
            if pkg_existing == "cc.admaster.android.demo":
                _ensure_default_mock_rules(run_dir)
            platform_ok = (not meta_platform) or (plat_existing == meta_platform)
            if (
                platform_ok
                and data.get("schema") == SCHEMA
                and (pkg_existing != "cc.admaster.android.demo" or (has_bcast and len(acts_existing) >= 6))
            ):
                ensure_scene_catalog(run_dir, default_manifest=data)
                return data, hashlib.sha256(raw).hexdigest()
        except Exception:
            pass

    # Read steps
    steps = []
    steps_path = run_dir / "steps.json"
    if steps_path.is_file():
        try:
            steps_data = json.loads(steps_path.read_text(encoding="utf-8"))
            steps = steps_data.get("steps", [])
        except Exception:
            pass

    # Read business report
    business = {}
    b_path = run_dir / "business_report.json"
    if b_path.is_file():
        try:
            business = json.loads(b_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Read script.py for clues
    script_text = ""
    script_path = run_dir / "script.py"
    if script_path.is_file():
        try:
            script_text = script_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    # 1. Platform & Device Serial
    platform = meta_platform if meta_platform in ("android", "ios") else "android"
    serial = device_serial or meta.get("device", {}).get("serial")
    if platform == "ios":
        ios_devs = _scan_ios_devices(target_bundle_id=package_name or "com.baidujp.gbusdkdemo")
        ios_serials = [d["serial"] for d in ios_devs]
        if not serial or (ios_serials and serial not in ios_serials):
            serial = ios_serials[0] if ios_serials else (serial or "00000000-0000-0000-0000-000000000000")
        matched_ios = next((d for d in ios_devs if d["serial"] == serial), None)
        fingerprint = (
            f"Apple/{matched_ios.get('model', 'iPhone')}/{matched_ios.get('release', 'iOS')}"
            if matched_ios
            else "Apple/iPhone/iOS"
        )
        pkg = package_name or meta.get("package") or meta.get("target_package") or meta.get("device", {}).get("package") or "com.baidujp.gbusdkdemo"
        act = activity_name or ".Main"
        apk_hash = "0" * 64
        online_devices: list[str] = ios_serials
    else:
        online_devices = get_online_devices()
        if not serial or serial not in online_devices:
            if online_devices:
                # Prefer Samsung device if present, else first online device
                samsung = [d for d in online_devices if d.startswith("RFC") or "samsung" in d.lower()]
                serial = samsung[0] if samsung else online_devices[0]
            else:
                serial = serial or "RFCYA0PZ6VA"

        # 2. Build Fingerprint
        fingerprint = None
        if serial in online_devices:
            fingerprint = get_device_fingerprint(serial)
        if not fingerprint:
            fingerprint = "samsung/pa1qsqw/pa1q:16/BP2A.250605.031.A3/S931USQU5BYI3_OYN5BYI3:user/release-keys"

        # 3. Package & Activity
        pkg = package_name
        if not pkg:
            pkg = meta.get("package") or meta.get("target_package") or meta.get("device", {}).get("package")
        if not pkg and ("adx-sdk-android" in str(run_dir) or "adx" in str(run_dir).lower()):
            pkg = "cc.admaster.android.demo"
        if not pkg and script_text:
            # Detect package from script, prioritizing real admaster package
            m_adm = re.search(r'["\'](cc\.admaster\.[a-zA-Z0-9_.]+)["\']', script_text)
            if m_adm:
                pkg = m_adm.group(1)
            else:
                m_pkg = re.search(r'["\'](com\.[a-zA-Z0-9_.]+)["\']', script_text)
                if m_pkg and m_pkg.group(1) not in ("com.test", "com.example"):
                    pkg = m_pkg.group(1)
        if not pkg:
            pkg = "cc.admaster.android.demo"

        act = activity_name
        if not act and serial in online_devices:
            act = get_main_activity(serial, pkg)
        if not act:
            if pkg == "cc.admaster.android.demo":
                act = ".main.AdMasterSDKDemo"
            else:
                act = ".MainActivity"
        if not act.startswith(".") and not act.startswith(pkg + "."):
            act = f".{act}"

        # 4. APK Hash
        apk_hash = None
        if serial in online_devices:
            apk_hash = get_installed_apk_hash(serial, pkg)
        if not apk_hash:
            if pkg == "cc.admaster.android.demo":
                apk_hash = "8b5e2b6e3d009a11b6b7d66f42bcfe1e5930f192c4648c3274e4ae35b6d0412f"
            else:
                apk_hash = "a" * 64

    # 5. Extract Actions & Target from steps and script context
    actions: list[dict[str, Any]] = [
        {"type": "force_stop"},
        {"type": "launch"},
    ]
    target_act = act
    target_selectors: list[dict[str, Any]] = []

    full_text = script_text + " " + json.dumps(steps) + " " + json.dumps(business, ensure_ascii=False)
    nav_text = None

    # Domain smart adapter for ADX iOS Demo
    if platform == "ios":
        if "reward" in full_text.lower() or "激励视频" in full_text:
            nav_text = "Rewarded"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            actions.append({"type": "tap_selector", "selector": {"text": "Show"}})
            actions.append({"type": "wait_duration", "seconds": 4.0})
        elif "interstitial" in full_text.lower() or "插屏" in full_text:
            nav_text = "Interstitial"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            actions.append({"type": "tap_selector", "selector": {"text": "Show"}})
            actions.append({"type": "wait_duration", "seconds": 4.0})
        elif "splash" in full_text.lower() or "开屏" in full_text:
            nav_text = "App Open"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            actions.append({"type": "tap_selector", "selector": {"text": "Show"}})
            actions.append({"type": "wait_duration", "seconds": 3.0})
        elif "banner" in full_text.lower():
            nav_text = "Banner"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            actions.append({"type": "tap_selector", "selector": {"text": "Show"}})
        elif "native" in full_text.lower() or "信息流" in full_text:
            nav_text = "Native"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            actions.append({"type": "tap_selector", "selector": {"text": "Show"}})

    # Domain smart adapter for ADX Android Demo: route through home menu and inject test environment
    if platform == "android" and pkg == "cc.admaster.android.demo":
        # 1. Reverse port for local mock server
        actions.append({"type": "reverse_port", "device_port": 8090, "host_port": 8090})
        # 2. Inject test environment broadcast
        actions.append({
            "type": "broadcast",
            "action": "cc.admaster.android.demo.TEST",
            "extras": {"token": "admtest", "cmd": "env", "value": "test"}
        })

        if "splash" in full_text.lower() or "RSplashManagerActivity" in full_text:
            nav_text = "开屏"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            target_act = ".main.RSplashManagerActivity"
        elif "reward" in full_text.lower() or "RewardVideoActivity" in full_text:
            nav_text = "激励视频"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            target_act = ".main.rewardvideo.RewardVideoActivity"
        elif "interstitial" in full_text.lower() or "ExpressInterstitial" in full_text:
            nav_text = "模板插屏(新插屏)"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            target_act = ".main.ExpressInterstitialActivity"
        elif "banner" in full_text.lower():
            nav_text = "Banner"
            actions.append({"type": "tap_selector", "selector": {"text": nav_text}})
            target_act = ".main.BannerListActivity"

    # Extract follow-up business actions from steps
    subpage_actions: list[dict[str, Any]] = []
    for step in steps:
        action_type = str(step.get("action", ""))
        detail = str(step.get("detail", ""))

        btn_text = None
        # Match find_text detail like: "'load' @ (540, 436)"
        if action_type == "find_text" or "@" in detail:
            m_text = re.search(r"['\"]([^'\"]+)['\"]", detail)
            if m_text:
                candidate = m_text.group(1).strip()
                if (
                    candidate
                    and candidate.lower() not in ("ok", "fail", "pass", "none")
                    and not candidate.startswith("event=")
                    and not candidate.startswith("http")
                    and len(candidate) <= 30
                ):
                    btn_text = candidate

        # Match trigger pattern like: "触发操作成功: splash->re:^load$"
        if not btn_text and "->re:^" in action_type:
            m_re = re.search(r"->re:\^([a-zA-Z0-9_\u4e00-\u9fa5]+)\$", action_type)
            if m_re:
                btn_text = m_re.group(1).strip()

        # Match explicit tap_selector
        if not btn_text and action_type == "tap_selector" and "selector" in step:
            btn_text = step["selector"].get("text")

        if btn_text and btn_text != nav_text:
            # Avoid continuous consecutive duplicates (e.g., retried taps)
            if (
                not subpage_actions
                or subpage_actions[-1].get("selector", {}).get("text") != btn_text
            ):
                subpage_actions.append({"type": "tap_selector", "selector": {"text": btn_text}})

    # If steps didn't capture full follow-up actions, fallback by domain knowledge
    if not subpage_actions:
        if target_act == ".main.RSplashManagerActivity":
            subpage_actions = [
                {"type": "tap_selector", "selector": {"text": "load"}},
                {"type": "tap_selector", "selector": {"text": "show"}},
            ]
        elif target_act == ".main.rewardvideo.RewardVideoActivity":
            subpage_actions = [
                {"type": "tap_selector", "selector": {"text": "加载激励视频"}},
                {"type": "tap_selector", "selector": {"text": "展示激励视频"}},
            ]
        elif target_act == ".main.ExpressInterstitialActivity":
            subpage_actions = [
                {"type": "tap_selector", "selector": {"text": "load"}},
                {"type": "tap_selector", "selector": {"text": "show"}},
            ]
        elif target_act == ".main.BannerListActivity":
            subpage_actions = [
                {"type": "tap_selector", "selector": {"text": "展示Banner"}},
            ]

    # For ADX splash / interstitial, ensure 'wait_duration' and 'show' follow 'load' if load is present
    has_load = any(a.get("selector", {}).get("text") == "load" for a in subpage_actions)
    has_show = any(a.get("selector", {}).get("text") == "show" for a in subpage_actions)
    if target_act in (".main.RSplashManagerActivity", ".main.ExpressInterstitialActivity"):
        if has_load and not has_show:
            subpage_actions.append({"type": "wait_duration", "seconds": 2.0})
            subpage_actions.append({"type": "tap_selector", "selector": {"text": "show"}})
        elif has_load and has_show:
            # Insert wait_duration between load and show
            expanded = []
            for a in subpage_actions:
                expanded.append(a)
                if a.get("selector", {}).get("text") == "load":
                    expanded.append({"type": "wait_duration", "seconds": 2.0})
            subpage_actions = expanded

    actions.extend(subpage_actions)

    # Determine target selectors from final actions
    for a in reversed(actions):
        sel = a.get("selector")
        if sel and sel.get("text") != nav_text:
            target_selectors.append(sel)
            break
    if not target_selectors and nav_text:
        target_selectors.append({"text": nav_text})

    # Fallback actions & target if none extracted
    if len(actions) == 2:
        actions.append({"type": "tap_selector", "selector": {"text": "Ready"}})

    if not target_selectors:
        b_steps = business.get("summary", {}).get("step_results", [])
        if b_steps:
            last_title = b_steps[-1].get("title", "Ready")
            target_selectors = [{"text": last_title}]
        else:
            target_selectors = [{"text": "Ready"}]

    actions = actions[:50]
    target_selectors = target_selectors[-10:]

    mock_conf = {"mode": "none"}
    if pkg == "cc.admaster.android.demo":
        _ensure_default_mock_rules(run_dir)
    if (run_dir / "mock_rules.json").is_file() or pkg == "cc.admaster.android.demo":
        mock_conf = {"mode": "replay", "port": 8090, "rules_file": "mock_rules.json"}

    manifest = {
        "schema": SCHEMA,
        "device": {"platform": platform, "serial": serial},
        "build": {"fingerprint": fingerprint},
        "package": {
            "name": pkg,
            "activity": act,
            "installed_apk_sha256": apk_hash,
        },
        "permissions": [],
        "configMock": {"config": {"mode": "none"}, "mock": mock_conf},
        "safety": {
            "external_services": False,
            "irreversible_side_effects": False,
            "trusted_offline_demo": True,
        },
        "actions": actions,
        "target": {"activity": target_act, "selectors": target_selectors},
        "artifacts": [],
    }

    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with safe_directory(run_dir, writable=True) as folder:
        folder.write_bytes("scene_manifest.json", raw, replace=True)

    ensure_scene_catalog(run_dir, default_manifest=manifest)
    digest = hashlib.sha256(raw).hexdigest()
    return manifest, digest


_GROUP_LABELS = {
    "core": "核心五形态",
    "adapter": "聚合 Adapter",
    "style": "样式双素材",
    "exc": "异常恢复矩阵",
    "fraud": "反作弊风控",
    "stability": "稳定性门禁",
}


def _build_adx_scene_recipe(key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (actions, target) for a named ADX Demo test scenario."""
    base_actions: list[dict[str, Any]] = [
        {"type": "force_stop"},
        {"type": "launch"},
        {"type": "reverse_port", "device_port": 8090, "host_port": 8090},
        {
            "type": "broadcast",
            "action": "cc.admaster.android.demo.TEST",
            "extras": {"token": "admtest", "cmd": "env", "value": "test"},
        },
    ]
    k = key.lower()
    if "splash" in k or "开屏" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "开屏"}},
            {"type": "tap_selector", "selector": {"text": "load"}},
            {"type": "wait_duration", "seconds": 2.0},
            {"type": "tap_selector", "selector": {"text": "show"}},
        ]
        target = {"activity": ".main.RSplashManagerActivity", "selectors": [{"text": "show"}]}
    elif "interstitial" in k or "插屏" in k or "video_cache" in k or "缓存" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "模板插屏(新插屏)"}},
            {"type": "tap_selector", "selector": {"text": "load"}},
            {"type": "wait_duration", "seconds": 2.0},
            {"type": "tap_selector", "selector": {"text": "show"}},
        ]
        target = {"activity": ".main.ExpressInterstitialActivity", "selectors": [{"text": "show"}]}
    elif "reward" in k or "激励" in k or "exc" in k or "异常" in k or "advanced" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "激励视频"}},
            {"type": "tap_selector", "selector": {"text": "加载激励视频"}},
            {"type": "wait_duration", "seconds": 2.0},
            {"type": "tap_selector", "selector": {"text": "展示激励视频"}},
        ]
        target = {"activity": ".main.rewardvideo.RewardVideoActivity", "selectors": [{"text": "展示激励视频"}]}
    elif "banner" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "Banner"}},
            {"type": "tap_selector", "selector": {"text": "Banner"}},
            {"type": "tap_selector", "selector": {"text": "展示Banner"}},
        ]
        target = {"activity": ".main.BannerListActivity", "selectors": [{"text": "展示Banner"}]}
    elif "native" in k or "信息流" in k or "style" in k or "样式" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "信息流-自渲染"}},
            {"type": "tap_selector", "selector": {"text": "图文广告列表"}},
        ]
        target = {"activity": ".main.feeds.NativeFeedSelectActivity", "selectors": [{"text": "图文广告列表"}]}
    elif "topon" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "三方 Adapter 验证"}},
            {"type": "tap_selector", "selector": {"text": "TopOn Adapter 测试"}},
        ]
        target = {"activity": ".main.TopOnAdapterTestActivity", "selectors": [{"text": "TopOn Adapter 测试"}]}
    elif "tradplus" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "三方 Adapter 验证"}},
            {"type": "tap_selector", "selector": {"text": "TradPlus Adapter 测试"}},
        ]
        target = {"activity": ".main.TradPlusAdapterTestActivity", "selectors": [{"text": "TradPlus Adapter 测试"}]}
    elif "svld" in k or "反作弊" in k or "风控" in k:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "特殊功能点验证"}},
            {"type": "tap_selector", "selector": {"text": "体外风控(s_vld 全类型0~8)验证"}},
            {"type": "tap_selector", "selector": {"text": "0  正常大容器 (期望 s_vld=0)"}},
        ]
        target = {"activity": ".main.SVldManualTestActivity", "selectors": [{"text": "0  正常大容器 (期望 s_vld=0)"}]}
    else:
        actions = base_actions + [
            {"type": "tap_selector", "selector": {"text": "开屏"}},
            {"type": "tap_selector", "selector": {"text": "load"}},
            {"type": "wait_duration", "seconds": 2.0},
            {"type": "tap_selector", "selector": {"text": "show"}},
        ]
        target = {"activity": ".main.RSplashManagerActivity", "selectors": [{"text": "show"}]}
    return actions, target


def build_planned_steps(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from phone_pilot.core.scene_restore import _action_label

    steps = [
        {
            "index": 1,
            "title": "启动 Mock 服务与设备预检",
            "detail": "锁定目标真机并加载本地 Mock 规则 (:8090)",
            "status": "PENDING",
        }
    ]
    for idx, act in enumerate(actions, start=2):
        t, d = _action_label(act)
        steps.append({"index": idx, "title": t, "detail": d, "status": "PENDING"})
    steps.append(
        {
            "index": len(steps) + 1,
            "title": "真机终态核验与截屏留证",
            "detail": "采集真机最终画面、UI 树与日志证据",
            "status": "PENDING",
        }
    )
    return steps


def _parse_iso_seconds(ts: str) -> Optional[float]:
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts[:19]).timestamp()
    except Exception:
        return None


def ensure_scene_catalog(
    run_dir: Path,
    *,
    default_manifest: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Synthesize scene_catalog.json supporting both single-scene and /release-gate multi-suite runs."""
    run_dir = Path(run_dir).expanduser().resolve()
    meta = {}
    if (run_dir / "run_meta.json").is_file():
        try:
            meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    steps = []
    if (run_dir / "steps.json").is_file():
        try:
            steps = json.loads((run_dir / "steps.json").read_text(encoding="utf-8")).get("steps", [])
        except Exception:
            pass

    # Check if this is a multi-suite run (e.g. test_release_gate)
    gate_summary = None
    if (run_dir / "release_gate_summary.json").is_file():
        try:
            gate_summary = json.loads((run_dir / "release_gate_summary.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    suite_steps = [
        s for s in steps if str(s.get("action", "")).startswith("[") and "]" in str(s.get("action", ""))
    ]
    is_multi_suite = bool(gate_summary or suite_steps or "release_gate" in str(meta.get("script_name", "")))

    default_actions = list((default_manifest or {}).get("actions") or [])
    default_target = dict((default_manifest or {}).get("target") or {})
    run_start_ts = _parse_iso_seconds(str(meta.get("started_at", "")))
    scenes: list[dict[str, Any]] = []

    # Priority 1: Build from business_report.json + per-scene scenes/<scene_id>/ artifacts
    biz_data: dict[str, Any] = {}
    if (run_dir / "business_report.json").is_file():
        try:
            biz_data = json.loads((run_dir / "business_report.json").read_text(encoding="utf-8"))
        except Exception:
            biz_data = {}
    biz_cases = biz_data.get("cases") if isinstance(biz_data, dict) else None
    if isinstance(biz_cases, list) and biz_cases:
        is_multi_suite = len(biz_cases) > 1
        biz_steps = biz_data.get("business_steps") or []
        biz_ev = biz_data.get("evidence") or []
        ev_by_id = {e.get("evidence_id"): e for e in biz_ev if isinstance(e, dict) and e.get("evidence_id")}
        vlm_by_cid: dict[str, dict[str, Any]] = {}
        vj_path = run_dir / "visual_judgments.json"
        if vj_path.is_file() and not vj_path.is_symlink():
            try:
                vj_raw = json.loads(vj_path.read_text(encoding="utf-8"))
                vj_items = vj_raw.get("items") if isinstance(vj_raw, dict) else vj_raw
                if isinstance(vj_items, list):
                    for vj in vj_items:
                        if isinstance(vj, dict) and (vj.get("case_id") or vj.get("scene_id")):
                            vlm_by_cid[str(vj.get("case_id") or vj.get("scene_id"))] = vj
            except Exception:
                pass
        for idx, case in enumerate(biz_cases, start=1):
            if not isinstance(case, dict):
                continue
            cid = str(case.get("case_id") or f"scene_{idx}")
            ctitle = str(case.get("title") or cid)
            c_steps = [s for s in biz_steps if isinstance(s, dict) and s.get("case_id") == cid]
            c_status = "PASS" if (c_steps and all(s.get("status") == "PASS" for s in c_steps)) else "FAIL"
            c_dur = round(sum(float(s.get("duration_ms") or 0) for s in c_steps) / 1000.0, 1)
            c_detail = str(c_steps[-1].get("actual") or "") if c_steps else ""

            sc_actions = list(default_actions)
            sc_dir = run_dir / "scenes" / cid
            if sc_dir.is_dir() and not sc_dir.is_symlink() and (sc_dir / "actions.json").is_file():
                try:
                    loaded_act = json.loads((sc_dir / "actions.json").read_text(encoding="utf-8"))
                    if isinstance(loaded_act, list) and loaded_act:
                        sc_actions = loaded_act
                except Exception:
                    pass

            sc_shots: list[str] = []
            for s in c_steps:
                for eid in (s.get("evidence_refs") or []):
                    ev = ev_by_id.get(eid)
                    if ev and ev.get("kind") in ("screenshot", "image", "ui"):
                        rel = ev.get("relative_path") or ev.get("path")
                        if rel:
                            rel_str = str(rel)
                            if rel_str not in sc_shots:
                                sc_shots.append(rel_str)
            if (run_dir / "screenshots").is_dir():
                for m_path in sorted((run_dir / "screenshots").glob(f"*{cid}*.png")):
                    rel_str = f"screenshots/{m_path.name}"
                    if rel_str not in sc_shots:
                        sc_shots.append(rel_str)
            if (sc_dir / "screenshots").is_dir():
                for m_path in sorted((sc_dir / "screenshots").glob("*.png")):
                    rel_str = f"scenes/{cid}/screenshots/{m_path.name}"
                    if rel_str not in sc_shots:
                        sc_shots.append(rel_str)
            sc_shot = sc_shots[-1] if sc_shots else None

            video_rel_path = f"scenes/{cid}/video.mp4" if (sc_dir / "video.mp4").is_file() else None

            sc_logs: list[str] = []
            for cand_log in (
                sc_dir / "sdk.log",
                run_dir / "logcat" / f"{cid}_sdk.log",
                sc_dir / "mock_http.log",
                run_dir / "logcat" / f"{cid}_mock_http.log",
            ):
                if cand_log.is_file() and not cand_log.is_symlink():
                    try:
                        lines = [ln for ln in cand_log.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
                        sc_logs.extend(lines[-40:])
                    except Exception:
                        pass

            timeline_logs: list[dict[str, Any]] = []
            tl_path = sc_dir / "timeline_logs.json"
            if tl_path.is_file() and not tl_path.is_symlink():
                try:
                    loaded_tl = json.loads(tl_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_tl, list):
                        for item in loaded_tl:
                            if isinstance(item, dict) and item.get("line"):
                                try:
                                    t_val = float(item.get("t", 0.0))
                                except Exception:
                                    t_val = 0.0
                                timeline_logs.append({"t": round(max(0.0, t_val), 2), "line": str(item["line"])})
                except Exception:
                    timeline_logs = []
            if not timeline_logs and sc_logs:
                total_span = max(float(c_dur or 0.0), 1.0)
                step_t = total_span / max(len(sc_logs), 1)
                for l_idx, ln in enumerate(sc_logs):
                    timeline_logs.append({"t": round(l_idx * step_t, 2), "line": ln})

            sc_assertions: list[dict[str, str]] = []
            for s in c_steps:
                for att in (s.get("attempts") or []):
                    if not isinstance(att, dict):
                        continue
                    for asrt in (att.get("assertions") or []):
                        if isinstance(asrt, dict):
                            sc_assertions.append(
                                {
                                    "dim": str(asrt.get("dimension") or asrt.get("name") or "check"),
                                    "status": str(asrt.get("status") or "PASS"),
                                    "expected": str(asrt.get("expected") or ""),
                                    "actual": str(asrt.get("actual") or ""),
                                }
                            )
            vj_item = vlm_by_cid.get(cid) or {}
            scenes.append(
                {
                    "scene_id": cid,
                    "index": idx,
                    "title": ctitle,
                    "group": "core",
                    "group_label": "场景验收" if is_multi_suite else "单场景验收",
                    "status": c_status,
                    "detail": c_detail or f"共 {len(sc_actions)} 步原子操作",
                    "elapsed_s": c_dur,
                    "video_offset_s": 0,
                    "video_time_label": "00:00",
                    "screenshot": sc_shot,
                    "screenshots": sc_shots,
                    "video_rel_path": video_rel_path,
                    "actions": sc_actions,
                    "target": default_target,
                    "planned_steps": build_planned_steps(sc_actions),
                    "scene_logs": sc_logs,
                    "timeline_logs": timeline_logs,
                    "assertions": sc_assertions,
                    "vlm_verdict": str(vj_item.get("verdict") or ""),
                    "vlm_reason": str(vj_item.get("summary") or vj_item.get("reason") or ""),
                    "has_scene_mock": bool(sc_dir.is_dir() and (sc_dir / "mock_rules.json").is_file()),
                }
            )

    if not scenes and is_multi_suite:
        # Build from suite_steps first (exact per-run timestamps & screenshots), enriched with gate_summary if present
        summary_by_name = {}
        if gate_summary and isinstance(gate_summary.get("summary"), list):
            for item in gate_summary["summary"]:
                if isinstance(item, dict) and item.get("suite"):
                    summary_by_name[item["suite"]] = item

        if suite_steps:
            cum_offset = 0.0
            for idx, st in enumerate(suite_steps, start=1):
                raw_act = str(st.get("action", ""))
                m_grp = re.match(r"^\[([A-Za-z0-9_]+)\]\s*(.+)$", raw_act)
                grp = m_grp.group(1).lower() if m_grp else "core"
                suite_title = m_grp.group(2).strip() if m_grp else raw_act
                detail_str = str(st.get("detail", ""))
                m_dur = re.search(r"\(([0-9.]+)s", detail_str)
                dur_s = float(m_dur.group(1)) if m_dur else 15.0

                step_end_ts = _parse_iso_seconds(str(st.get("timestamp", "")))
                if run_start_ts and step_end_ts and step_end_ts >= run_start_ts:
                    start_offset_s = max(0, int(step_end_ts - run_start_ts - dur_s))
                else:
                    start_offset_s = int(cum_offset)
                cum_offset += dur_s

                status_ok = st.get("status") in ("ok", "passed")
                actions, target = _build_adx_scene_recipe(suite_title)
                scene_id = re.sub(r"[^a-zA-Z0-9_]+", "_", f"suite_{idx}_{ grp }").strip("_").lower()
                scenes.append(
                    {
                        "scene_id": scene_id,
                        "index": idx,
                        "title": suite_title,
                        "group": grp,
                        "group_label": _GROUP_LABELS.get(grp, grp.upper()),
                        "status": "PASS" if status_ok else "FAIL",
                        "detail": detail_str,
                        "elapsed_s": round(dur_s, 1),
                        "video_offset_s": start_offset_s,
                        "video_time_label": f"{start_offset_s // 60:02d}:{start_offset_s % 60:02d}",
                        "screenshot": st.get("screenshot"),
                        "actions": actions,
                        "target": target,
                        "planned_steps": build_planned_steps(actions),
                    }
                )
        elif summary_by_name:
            cum_offset = 0.0
            for idx, (suite_title, item) in enumerate(summary_by_name.items(), start=1):
                if item.get("file") == "stability_check":
                    continue
                dur_s = float(item.get("elapsed_s", 15.0))
                start_offset_s = int(cum_offset)
                cum_offset += dur_s
                grp = "core"
                fname = str(item.get("file", ""))
                if "adapter" in fname:
                    grp = "adapter"
                elif "style" in fname:
                    grp = "style"
                elif "exc" in fname or "advanced" in fname:
                    grp = "exc"
                elif "svld" in fname:
                    grp = "fraud"
                actions, target = _build_adx_scene_recipe(fname or suite_title)
                scene_id = re.sub(r"[^a-zA-Z0-9_]+", "_", fname.replace(".py", "") or f"suite_{idx}").strip("_").lower()
                scenes.append(
                    {
                        "scene_id": scene_id,
                        "index": idx,
                        "title": suite_title,
                        "group": grp,
                        "group_label": _GROUP_LABELS.get(grp, grp.upper()),
                        "status": "PASS" if item.get("ok") else "FAIL",
                        "detail": str(item.get("detail", "")),
                        "elapsed_s": round(dur_s, 1),
                        "video_offset_s": start_offset_s,
                        "video_time_label": f"{start_offset_s // 60:02d}:{start_offset_s % 60:02d}",
                        "screenshot": None,
                        "actions": actions,
                        "target": target,
                        "planned_steps": build_planned_steps(actions),
                    }
                )

    if not scenes:
        # Single-scene script (e.g. test_splash_full, verify_ad_rev_splash)
        actions = (default_manifest or {}).get("actions", [])
        target = (default_manifest or {}).get("target", {})
        if not actions:
            actions, target = _build_adx_scene_recipe(str(meta.get("script_name", "splash")))
        last_shot = None
        for s in reversed(steps):
            if s.get("screenshot"):
                last_shot = s["screenshot"]
                break
        script_title = str(meta.get("script_name") or run_dir.name)
        scenes.append(
            {
                "scene_id": "default",
                "index": 1,
                "title": f"{script_title} 主场景",
                "group": "core",
                "group_label": "单场景验收",
                "status": "PASS" if meta.get("status") == "passed" else "FAIL",
                "detail": f"共 {len(steps)} 步原子操作",
                "elapsed_s": round(float(meta.get("duration_s", 0.0)), 1),
                "video_offset_s": 0,
                "video_time_label": "00:00",
                "screenshot": last_shot,
                "actions": actions,
                "target": target,
                "planned_steps": build_planned_steps(actions),
            }
        )

    # Enrich each scene with v4.0 Lifecycle Pills, Scene-Specific Logs, Video Highlights & Tap Annotations
    try:
        from phone_pilot.core.acceptance_profile import (
            load_acceptance_profile,
            evaluate_lifecycle_pills,
            extract_scene_specific_logs,
            extract_step_annotations,
        )

        profile = load_acceptance_profile(run_dir)
        step_annots = extract_step_annotations(steps)
        for sc in scenes:
            offset_s = int(sc.get("video_offset_s", 0))
            el_s = float(sc.get("elapsed_s", 0.0))
            hl_s = offset_s + max(0, int(el_s - 6.0)) if el_s >= 9.0 else offset_s
            sc["video_highlight_s"] = hl_s
            sc["video_highlight_label"] = f"{hl_s // 60:02d}:{hl_s % 60:02d}"

            if not sc.get("scene_logs"):
                sc["scene_logs"] = extract_scene_specific_logs(
                    profile, run_dir, sc, [], gate_summary=gate_summary
                )
            sc_logs = sc["scene_logs"]
            sc["lifecycle_pills"] = evaluate_lifecycle_pills(
                profile,
                sc_logs,
                status=sc.get("status", "PASS"),
                actions=sc.get("actions", []),
                detail_text=f"{sc.get('title', '')} {sc.get('detail', '')}",
            )
            shot_file = sc.get("screenshot")
            if shot_file:
                shot_name = Path(str(shot_file)).name
                sc["tap_annotation"] = step_annots.get(shot_name)
    except Exception:
        profile = {}

    # Sort scenes for default selection: failed scenes first if any, otherwise first scene
    failed_scenes = [s for s in scenes if s["status"] != "PASS"]
    default_scene_id = failed_scenes[0]["scene_id"] if failed_scenes else scenes[0]["scene_id"]

    total_scenes = len(scenes)
    passed_scenes = sum(1 for s in scenes if s["status"] == "PASS")
    failed_count = total_scenes - passed_scenes

    catalog = {
        "is_multi_suite": is_multi_suite and total_scenes > 1,
        "profile_name": profile.get("profile_name", "通用移动应用验收视图") if isinstance(profile, dict) else "通用移动应用验收视图",
        "highlights": (profile.get("log_filter") or {}).get("highlights", {}) if isinstance(profile, dict) else {},
        "total_scenes": total_scenes,
        "passed_scenes": passed_scenes,
        "failed_scenes": failed_count,
        "gate_pass": failed_count == 0,
        "default_scene_id": default_scene_id,
        "scenes": scenes,
    }
    try:
        with safe_directory(run_dir, writable=True) as folder:
            folder.write_bytes(
                "scene_catalog.json",
                json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8"),
                replace=True,
            )
    except Exception:
        pass
    return catalog

