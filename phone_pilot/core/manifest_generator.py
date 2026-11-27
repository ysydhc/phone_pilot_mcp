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


def _scan_ios_devices(target_bundle_id: str = "") -> list[dict[str, Any]]:
    """Scan booted iOS simulators (simctl) and connected iOS real devices (pymobiledevice3)."""
    devices: list[dict[str, Any]] = []
    bundle_id = (target_bundle_id or "").strip()

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
                    if bundle_id:
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


_VALID_PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")
_VALID_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_scene_id(raw_id: str, fallback: str) -> str:
    cand = (raw_id or "").strip()
    if cand and cand not in (".", "..") and _VALID_SCENE_ID_RE.fullmatch(cand):
        return cand
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cand).strip("._-")
    if cleaned and cleaned not in (".", "..") and _VALID_SCENE_ID_RE.fullmatch(cleaned):
        return cleaned[:80]
    return fallback


def get_online_devices_detailed(
    target_package: str = "",
    platform_filter: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Scan all connected Android (ADB) and iOS (Simctl/PMD) devices with metadata."""
    safe_pkg = (target_package or "").strip()
    if safe_pkg and not _VALID_PKG_RE.fullmatch(safe_pkg):
        safe_pkg = ""
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
                pm_clause = f"; pm path {safe_pkg}" if safe_pkg else ""
                cmd = (
                    f"getprop ro.product.brand; "
                    f"getprop ro.product.model; "
                    f"getprop ro.build.version.release; "
                    f"wm size{pm_clause}"
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
        devices.extend(_scan_ios_devices(target_bundle_id=safe_pkg))
    return devices


def _ensure_default_mock_rules(run_dir: Path, pp_profile: Optional[dict[str, Any]] = None) -> None:
    """Ensure mock_rules.json exists in run_dir if `pp_profile.json` supplies `default_mock_rules`."""
    rules_file = run_dir / "mock_rules.json"
    if rules_file.is_file():
        return
    prof = pp_profile if isinstance(pp_profile, dict) else {}
    rules = prof.get("default_mock_rules")
    if not isinstance(rules, list) or not rules:
        return
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
    persist: bool = True,
) -> tuple[dict[str, Any], str]:
    """Ensure a valid scene_manifest.json exists in run_dir (or synthesize in memory when `persist=False`)."""
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

    from phone_pilot.core.pp_profile import (
        load_pp_profile,
        resolve_platform_config,
        resolve_platform_package,
    )

    pp_prof = load_pp_profile(run_dir)

    if manifest_file.is_file() and not force_refresh:
        try:
            raw = manifest_file.read_bytes()
            data = json.loads(raw)
            plat_existing = str((data.get("device") or {}).get("platform") or "").lower()
            acts_existing = data.get("actions", [])
            if persist:
                _ensure_default_mock_rules(run_dir, pp_prof)
            platform_ok = (not meta_platform) or (plat_existing == meta_platform)
            if (
                platform_ok
                and data.get("schema") == SCHEMA
                and len(acts_existing) >= 2
            ):
                ensure_scene_catalog(run_dir, default_manifest=data, persist=persist)
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
    plat_cfg = resolve_platform_config(pp_prof, platform)
    serial = device_serial or meta.get("device", {}).get("serial")
    if platform == "ios":
        pkg = resolve_platform_package(
            pp_prof,
            platform="ios",
            explicit_package=str(
                package_name
                or meta.get("package")
                or meta.get("target_package")
                or (meta.get("device") or {}).get("package")
                or ""
            ),
        ) or "com.example.app"
        ios_devs = _scan_ios_devices(target_bundle_id=pkg)
        ios_serials = [d["serial"] for d in ios_devs]
        if not serial or (ios_serials and serial not in ios_serials):
            serial = ios_serials[0] if ios_serials else (serial or "00000000-0000-0000-0000-000000000000")
        matched_ios = next((d for d in ios_devs if d["serial"] == serial), None)
        fingerprint = (
            f"Apple/{matched_ios.get('model', 'iPhone')}/{matched_ios.get('release', 'iOS')}"
            if matched_ios
            else "Apple/iPhone/iOS"
        )
        act = activity_name or str(plat_cfg.get("activity") or ".Main")
        apk_hash = "0" * 64
        online_devices: list[str] = ios_serials
    else:
        online_devices = get_online_devices()
        if not serial or serial not in online_devices:
            serial = online_devices[0] if online_devices else (serial or "android-device")

        # 2. Build Fingerprint
        fingerprint = get_device_fingerprint(serial) if serial in online_devices else None
        if not fingerprint:
            fingerprint = "android/generic/device:14/release-keys"

        # 3. Package & Activity
        pkg = resolve_platform_package(
            pp_prof,
            platform="android",
            explicit_package=str(
                package_name
                or meta.get("package")
                or meta.get("target_package")
                or (meta.get("device") or {}).get("package")
                or ""
            ),
        )
        if not pkg and script_text:
            m_pkg = re.search(r'["\']([a-z][a-z0-9_]*(?:\.[a-zA-Z0-9_]+){2,})["\']', script_text)
            if m_pkg and m_pkg.group(1) not in ("com.test", "com.example", "android.intent.action.MAIN"):
                pkg = m_pkg.group(1)
        if not pkg:
            pkg = "com.example.app"

        act = activity_name or str(plat_cfg.get("activity") or "")
        if not act and serial in online_devices:
            act = get_main_activity(serial, pkg)
        if not act:
            act = ".MainActivity"
        if not act.startswith(".") and not act.startswith(pkg + ".") and "." in act:
            if act.startswith(pkg + "/"):
                act = act.split("/", 1)[1]
        elif not act.startswith(".") and not act.startswith(pkg + "."):
            act = f".{act}"

        # 4. APK Hash
        apk_hash = get_installed_apk_hash(serial, pkg) if serial in online_devices else None
        if not apk_hash:
            apk_hash = "a" * 64

    # 5. Extract Generic Actions & Target from pp_profile + steps
    actions, target_obj = _build_generic_scene_recipe(
        str(meta.get("script_name") or run_dir.name),
        platform=platform,
        pp_profile=pp_prof,
        steps=steps,
        default_activity=act,
    )
    target_act = target_obj.get("activity") or act
    target_selectors = list(target_obj.get("selectors") or [{"text": "Ready"}])

    actions = actions[:50]
    target_selectors = target_selectors[-10:]

    if persist:
        _ensure_default_mock_rules(run_dir, pp_prof)
    mock_port = int(plat_cfg.get("mock_port") or 8090)
    mock_conf: dict[str, Any] = {"mode": "none"}
    if (run_dir / "mock_rules.json").is_file() or bool(pp_prof.get("default_mock_rules")):
        mock_conf = {"mode": "replay", "port": mock_port, "rules_file": "mock_rules.json"}

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
    if persist:
        with safe_directory(run_dir, writable=True) as folder:
            folder.write_bytes("scene_manifest.json", raw, replace=True)

    ensure_scene_catalog(run_dir, default_manifest=manifest, force_refresh=force_refresh, persist=persist)
    digest = hashlib.sha256(raw).hexdigest()
    return manifest, digest


_DEFAULT_GROUP_LABELS = {
    "core": "核心功能",
    "adapter": "扩展适配",
    "style": "样式渲染",
    "exc": "异常恢复",
    "fraud": "安全风控",
    "cloud_exp": "动态配置与实验",
    "edge": "边角与变体",
    "prod": "生产环境加固",
    "stability": "稳定性门禁",
}


def _match_profile_scene_entry(
    key: str,
    pp_profile: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Match an optional declarative scene recipe from `pp_profile.json` (`scene_recipes`)."""
    prof = pp_profile if isinstance(pp_profile, dict) else {}
    entries = prof.get("scene_recipes")
    if not isinstance(entries, list):
        return None
    low_k = (key or "").lower()
    wildcard_entry: Optional[dict[str, Any]] = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tokens = entry.get("match_any")
        if not isinstance(tokens, list):
            continue
        str_tokens = [str(t).lower() for t in tokens if t]
        if "*" in str_tokens:
            wildcard_entry = wildcard_entry or entry
            continue
        if any(tok in low_k for tok in str_tokens):
            return entry
    return wildcard_entry


def _cached_actions_valid_for_platform(
    actions: Any,
    platform: str,
    pp_profile: Optional[dict[str, Any]] = None,
) -> bool:
    """Reject stale cached `actions.json` whose scheme prefix belongs to another platform."""
    if not isinstance(actions, list) or not actions:
        return False
    prof = pp_profile if isinstance(pp_profile, dict) else {}
    plats = prof.get("platforms") if isinstance(prof.get("platforms"), dict) else {}
    cur_plat = (platform or "android").lower()
    cur_cfg = plats.get(cur_plat) if isinstance(plats.get(cur_plat), dict) else {}
    expected_prefix = str(cur_cfg.get("scheme_prefix") or "").strip().lower()
    foreign_prefixes = [
        str(cfg.get("scheme_prefix") or "").strip().lower()
        for p_name, cfg in plats.items()
        if str(p_name).lower() != cur_plat and isinstance(cfg, dict) and cfg.get("scheme_prefix")
    ]
    for act in actions:
        if not isinstance(act, dict):
            continue
        if act.get("type") == "open_url":
            u = str(act.get("url") or "").strip().lower()
            if any(fp and u.startswith(fp) for fp in foreign_prefixes):
                return False
            if expected_prefix and "://" in u and not u.startswith(("http://", "https://", expected_prefix)):
                return False
    return True


def _ensure_per_scene_restore_files(
    run_dir: Path,
    scene_id: str,
    key: str,
    actions: list[dict[str, Any]],
    *,
    pp_profile: Optional[dict[str, Any]] = None,
    overwrite_synthesized: bool = False,
    persist: bool = True,
) -> bool:
    """Persist scenes/<scene_id>/{actions.json, mock_rules.json} safely when `persist=True`."""
    if not scene_id or not _VALID_SCENE_ID_RE.fullmatch(scene_id) or scene_id in (".", ".."):
        return False
    sc_dir = run_dir / "scenes" / scene_id
    if not persist:
        return bool((sc_dir / "mock_rules.json").is_file() or (pp_profile or {}).get("default_mock_rules"))
    try:
        resolved_root = run_dir.resolve()
        scenes_parent = run_dir / "scenes"
        if scenes_parent.is_symlink():
            return False
        scenes_parent.mkdir(parents=True, exist_ok=True)
        if sc_dir.is_symlink() or not sc_dir.resolve().is_relative_to(resolved_root):
            return False
        sc_dir.mkdir(parents=True, exist_ok=True)
        act_file = sc_dir / "actions.json"
        if not act_file.is_symlink() and (overwrite_synthesized or not act_file.is_file()):
            act_file.write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")
        rules_file = sc_dir / "mock_rules.json"
        if not rules_file.is_symlink() and (overwrite_synthesized or not rules_file.is_file()):
            matched = _match_profile_scene_entry(key, pp_profile)
            rules = (matched or {}).get("mock_rules")
            if not isinstance(rules, list) or not rules:
                prof = pp_profile if isinstance(pp_profile, dict) else {}
                rules = prof.get("default_mock_rules")
            if isinstance(rules, list) and rules:
                rules_file.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        return bool((sc_dir / "mock_rules.json").is_file())
    except Exception:
        return bool((sc_dir / "mock_rules.json").is_file())


def _build_generic_scene_recipe(
    key: str,
    *,
    platform: str = "android",
    pp_profile: Optional[dict[str, Any]] = None,
    steps: Optional[list[dict[str, Any]]] = None,
    default_activity: str = ".MainActivity",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build declarative `(actions, target)` from `pp_profile.json` or recorded generic steps."""
    from phone_pilot.core.pp_profile import resolve_platform_config

    matched = _match_profile_scene_entry(key, pp_profile)
    if matched and isinstance(matched.get("actions"), list) and matched["actions"]:
        target_cfg = matched.get("target") if isinstance(matched.get("target"), dict) else {}
        return list(matched["actions"]), {
            "activity": str(target_cfg.get("activity") or default_activity),
            "selectors": list(target_cfg.get("selectors") or [{"text": "Ready"}]),
        }

    plat_cfg = resolve_platform_config(pp_profile or {}, platform)
    actions: list[dict[str, Any]] = [
        {"type": "force_stop"},
        {"type": "launch"},
    ]
    if platform == "android":
        mock_port = plat_cfg.get("mock_port")
        if isinstance(mock_port, int) and mock_port > 0:
            actions.append({"type": "reverse_port", "device_port": mock_port, "host_port": mock_port})
        env_bcast = plat_cfg.get("env_broadcast")
        if isinstance(env_bcast, dict) and env_bcast.get("action"):
            actions.append(
                {
                    "type": "broadcast",
                    "action": str(env_bcast["action"]),
                    "extras": dict(env_bcast.get("extras") or {}),
                }
            )

    selectors: list[dict[str, Any]] = []
    for st in (steps or [])[:12]:
        if not isinstance(st, dict):
            continue
        act_name = str(st.get("action") or "").strip().lower()
        det = str(st.get("detail") or "").strip()
        if act_name in ("open_url", "open_deeplink") and "://" in det:
            actions.append({"type": "open_url", "url": det})
        elif act_name in ("tap_text", "click_text") and det:
            actions.append({"type": "tap_selector", "selector": {"text": det}})
            selectors.append({"text": det})

    actions.append({"type": "wait_duration", "seconds": 2.5})
    return actions, {
        "activity": default_activity,
        "selectors": selectors[-5:] if selectors else [{"text": "Ready"}],
    }


def _infer_scene_group(low_key: str, pp_profile: Optional[dict[str, Any]] = None) -> str:
    """Infer logical group id using `pp_profile.group_keywords` first, then generic fallbacks."""
    prof = pp_profile if isinstance(pp_profile, dict) else {}
    kw_map = prof.get("group_keywords")
    if isinstance(kw_map, dict):
        for grp_id, tokens in kw_map.items():
            if isinstance(tokens, list) and any(str(tok).lower() in low_key for tok in tokens if tok):
                return str(grp_id)
    if "adapter" in low_key or "plugin" in low_key:
        return "adapter"
    if "cloud" in low_key or "exp" in low_key or "云控" in low_key:
        return "cloud_exp"
    if "edge" in low_key or "边角" in low_key:
        return "edge"
    if "prod" in low_key or "深水区" in low_key or "加固" in low_key:
        return "prod"
    if "style" in low_key or "样式" in low_key:
        return "style"
    if "exc" in low_key or "异常" in low_key:
        return "exc"
    if "fraud" in low_key or "risk" in low_key or "风控" in low_key or "反作弊" in low_key:
        return "fraud"
    return "core"


def build_planned_steps(
    actions: list[dict[str, Any]],
    *,
    route_labels: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    from phone_pilot.core.scene_restore import _action_label

    steps = [
        {
            "index": 1,
            "title": "启动 Mock 服务与设备预检",
            "detail": "锁定目标设备并加载本地 Mock 规则",
            "status": "PENDING",
        }
    ]
    for idx, act in enumerate(actions, start=2):
        t, d = _action_label(act, route_labels=route_labels)
        steps.append({"index": idx, "title": t, "detail": d, "status": "PENDING"})
    steps.append(
        {
            "index": len(steps) + 1,
            "title": "设备终态核验与截屏留证",
            "detail": "采集设备最终画面、UI 树与日志证据",
            "status": "PENDING",
        }
    )
    return steps


def _clean_scene_detail(raw_detail: str) -> str:
    """Remove duplicate '(23.6s, attempts=1) | 2/3' suffixes so card subtitles stay concise."""
    s = str(raw_detail or "").strip()
    if not s:
        return ""
    retry_tag = ""
    m_att = re.search(r"\(([0-9.]+)s,\s*attempts=(\d+)\)", s)
    if m_att:
        att_n = int(m_att.group(2))
        if att_n > 1:
            retry_tag = f" · 重试 {att_n - 1} 次"
    if " | " in s:
        left, right = s.split(" | ", 1)
        left_head = re.sub(r"\s*\([0-9.]+s(?:,\s*attempts=\d+)?\)\s*$", "", left).strip()
        right_clean = right.strip()
        if right_clean == left_head or right_clean in ("PASS", "FAIL"):
            s = f"{left_head}{retry_tag}"
        else:
            s = f"{left_head}{retry_tag} · {right_clean}" if left_head else f"{right_clean}{retry_tag}"
        return s.strip(" ·")
    s = re.sub(r"\s*\([0-9.]+s(?:,\s*attempts=\d+)?\)", retry_tag, s).strip()
    return s


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
    force_refresh: bool = False,
    persist: bool = True,
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

    from phone_pilot.core.pp_profile import load_pp_profile

    pp_prof = load_pp_profile(run_dir)
    group_labels = {
        **_DEFAULT_GROUP_LABELS,
        **(pp_prof.get("group_labels") if isinstance(pp_prof.get("group_labels"), dict) else {}),
    }
    route_labels = pp_prof.get("route_labels") if isinstance(pp_prof.get("route_labels"), dict) else None
    default_actions = list((default_manifest or {}).get("actions") or [])
    default_target = dict((default_manifest or {}).get("target") or {})
    run_start_ts = _parse_iso_seconds(str(meta.get("started_at", "")))
    scenes: list[dict[str, Any]] = []

    def _extract_raw_assertions_and_subcases(
        raw_obj: Any,
        failure_reason: str = "",
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
        asrts: list[dict[str, str]] = []
        sub_cases: list[dict[str, str]] = []
        extra_lines: list[str] = []
        if failure_reason and failure_reason.strip():
            extra_lines.append(f"[✖ FAIL] 失败归因: {failure_reason.strip()}")
        if not isinstance(raw_obj, dict):
            return asrts, sub_cases, extra_lines
        if raw_obj.get("error"):
            err_txt = str(raw_obj.get("error")).strip()
            asrts.append({"dim": "error", "status": "FAIL", "expected": "none", "actual": err_txt[:120]})
            extra_lines.append(f"[✖ FAIL] 异常详情: {err_txt[:180]}")
        for bool_k, val_b in raw_obj.items():
            if str(bool_k).endswith("_ok") and isinstance(val_b, bool):
                st_str = "PASS" if val_b else "FAIL"
                asrts.append({"dim": str(bool_k), "status": st_str, "expected": "true", "actual": str(val_b).lower()})
        merged_sub_dict: dict[str, Any] = {}
        for primary_k in ("results", "outcomes", "cases", "scenes", "scene_outcomes"):
            cand = raw_obj.get(primary_k)
            if isinstance(cand, dict):
                for ck, cv in cand.items():
                    merged_sub_dict.setdefault(str(ck), cv)
        for top_k, top_v in raw_obj.items():
            if top_k in ("results", "outcomes", "cases", "scenes", "scene_outcomes", "oracle"):
                continue
            if isinstance(top_v, dict) and (
                "ok" in top_v
                or (top_v and all(isinstance(x, dict) and "ok" in x for x in top_v.values()))
            ):
                merged_sub_dict.setdefault(str(top_k), top_v)
        sub_res: Any = merged_sub_dict if merged_sub_dict else (raw_obj.get("results") or raw_obj.get("outcomes") or raw_obj.get("cases"))
        if isinstance(sub_res, dict):
            for sk, sv in list(sub_res.items())[:24]:
                if not isinstance(sv, dict):
                    continue
                if "ok" in sv:
                    s_ok = bool(sv.get("ok"))
                    act_str = str(sv.get("passed") or sv.get("detail") or ("PASS" if s_ok else "FAIL"))
                    if not s_ok and "close_xy" in sv and sv.get("close_xy") is None:
                        act_str = "FAIL (close_xy=null)"
                    elif not s_ok and isinstance(sv.get("steps"), dict):
                        failed_st = [k2 for k2, v2 in sv["steps"].items() if isinstance(v2, dict) and not v2.get("ok", True)]
                        if failed_st:
                            act_str = f"FAIL ({','.join(failed_st[:3])})"
                    elif not s_ok and isinstance(sv.get("oracle"), dict):
                        checks_d = sv["oracle"].get("checks")
                        if isinstance(checks_d, dict):
                            failed_chk = [ck for ck, cv in checks_d.items() if not cv]
                            if failed_chk:
                                act_str = f"FAIL ({','.join(failed_chk[:3])})"
                    st_lbl = "PASS" if s_ok else "FAIL"
                    asrts.append({"dim": str(sk), "status": st_lbl, "expected": "ok", "actual": act_str})
                    sub_cases.append({"name": str(sk), "status": st_lbl, "detail": act_str})
                    tag = "✔ PASS" if s_ok else "✖ FAIL"
                    extra_lines.append(f"[{tag}] 子模块 {sk}: {act_str}")
                    for nested_k in ("scenes", "faults"):
                        nested_map = sv.get(nested_k)
                        if isinstance(nested_map, dict):
                            for nk, nv in list(nested_map.items())[:20]:
                                if isinstance(nv, dict) and "ok" in nv:
                                    n_ok = bool(nv.get("ok"))
                                    n_st = "PASS" if n_ok else "FAIL"
                                    sub_cases.append({"name": f"{sk}.{nk}", "status": n_st, "detail": n_st})
                                    if not n_ok:
                                        asrts.append({"dim": f"{sk}.{nk}", "status": "FAIL", "expected": "ok", "actual": "FAIL"})
                                        extra_lines.append(f"  ↳ [✖ FAIL] 子用例 {nk}")
                else:
                    sub_total = len(sv)
                    sub_pass = sum(1 for v2 in sv.values() if isinstance(v2, dict) and v2.get("ok"))
                    s_ok = sub_pass == sub_total and sub_total > 0
                    st_lbl = "PASS" if s_ok else "FAIL"
                    asrts.append({"dim": str(sk), "status": st_lbl, "expected": f"{sub_total}/{sub_total}", "actual": f"{sub_pass}/{sub_total}"})
                    tag = "✔ PASS" if s_ok else "✖ FAIL"
                    extra_lines.append(f"[{tag}] 矩阵组 {sk}: {sub_pass}/{sub_total} 通过")
                    for nk, nv in list(sv.items())[:24]:
                        if isinstance(nv, dict) and "ok" in nv:
                            n_ok = bool(nv.get("ok"))
                            n_st = "PASS" if n_ok else "FAIL"
                            sub_cases.append({"name": str(nk), "status": n_st, "detail": n_st})
                            if not n_ok:
                                asrts.append({"dim": str(nk), "status": "FAIL", "expected": "ok", "actual": "FAIL"})
                                extra_lines.append(f"  ↳ [✖ FAIL] 矩阵子项 {nk}")
        elif isinstance(sub_res, list):
            for s_item in sub_res[:24]:
                if isinstance(s_item, dict):
                    s_name = str(s_item.get("name") or s_item.get("id") or s_item.get("scene") or "case")
                    s_ok = bool(s_item.get("ok", True))
                    st_lbl = "PASS" if s_ok else "FAIL"
                    asrts.append({"dim": s_name, "status": st_lbl, "expected": "ok", "actual": st_lbl})
                    sub_cases.append({"name": s_name, "status": st_lbl, "detail": str(s_item.get("detail") or st_lbl)})
                    tag = "✔ PASS" if s_ok else "✖ FAIL"
                    extra_lines.append(f"[{tag}] 子用例 {s_name}")
        # Sort failed sub-cases first so QA spots anomalies immediately
        sub_cases.sort(key=lambda c: (0 if c.get("status") != "PASS" else 1))
        return asrts[:16], sub_cases[:32], extra_lines

    meta_platform = str((meta.get("device") or {}).get("platform") or "android").lower()
    global_rec_rel: Optional[str] = None
    rec_dir = run_dir / "recordings"
    if rec_dir.is_dir() and not rec_dir.is_symlink():
        try:
            mp4s = sorted(p.name for p in rec_dir.iterdir() if p.is_file() and p.suffix.lower() in (".mp4", ".webm", ".mkv"))
            if mp4s:
                global_rec_rel = f"recordings/{mp4s[0]}"
        except Exception:
            pass

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
        meta_plat_early = meta_platform
        gate_by_title: dict[str, dict[str, Any]] = {}
        if gate_summary and isinstance(gate_summary.get("summary"), list):
            for gs_item in gate_summary["summary"]:
                if isinstance(gs_item, dict) and gs_item.get("suite"):
                    g_key = str(gs_item["suite"])
                    gate_by_title[g_key] = gs_item
                    gate_by_title[re.sub(r"\s+", "", g_key)] = gs_item
        # Also parse console.log timestamps & suite step slices so Priority 1 (business_report) never loses intermediate screenshots or pre-clear_log events
        biz_suite_positions = [
            pos for pos, s in enumerate(steps) if str(s.get("action", "")).startswith("[") and "]" in str(s.get("action", ""))
        ]
        biz_console_timed: list[tuple[float, str]] = []
        biz_date_prefix = str(meta.get("started_at", ""))[:10]
        if biz_date_prefix and (run_dir / "console.log").is_file():
            try:
                raw_con_b = (run_dir / "console.log").read_text(encoding="utf-8", errors="replace")
                ts_re_b = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$")
                for cln in raw_con_b.splitlines():
                    m_b = ts_re_b.match(cln.strip())
                    if not m_b:
                        continue
                    b_txt = m_b.group(2).strip()
                    if "logcat 等待中" in b_txt:
                        continue
                    b_ep = _parse_iso_seconds(f"{biz_date_prefix}T{m_b.group(1)}")
                    if b_ep is not None:
                        if run_start_ts and b_ep < run_start_ts - 60:
                            b_ep += 86400.0
                        biz_console_timed.append((b_ep, b_txt))
            except Exception:
                biz_console_timed = []
        biz_prev_pos = -1
        biz_prev_end_ep = run_start_ts
        biz_cum_offset = 0.0
        for idx, case in enumerate(biz_cases, start=1):
            if not isinstance(case, dict):
                continue
            raw_cid = str(case.get("case_id") or f"scene_{idx}")
            cid = _sanitize_scene_id(raw_cid, f"scene_{idx}")
            ctitle = str(case.get("title") or cid)
            gs_match = gate_by_title.get(ctitle) or gate_by_title.get(re.sub(r"\s+", "", ctitle)) or {}
            c_grp = str(case.get("group") or gs_match.get("group") or "")
            if not c_grp:
                c_grp = _infer_scene_group(f"{cid} {ctitle}".lower(), pp_prof)
            c_grp_label = group_labels.get(c_grp, "场景验收" if is_multi_suite else "单场景验收")
            c_steps = [s for s in biz_steps if isinstance(s, dict) and s.get("case_id") in (raw_cid, cid)]
            if c_steps:
                c_status = "PASS" if all(s.get("status") == "PASS" for s in c_steps) else "FAIL"
            elif isinstance(gs_match.get("ok"), bool):
                c_status = "PASS" if gs_match["ok"] else "FAIL"
            else:
                c_status = "FAIL"
            c_dur = (
                round(float(gs_match["elapsed_s"]), 1)
                if gs_match.get("elapsed_s")
                else round(sum(float(s.get("duration_ms") or 0) for s in c_steps) / 1000.0, 1)
            )
            c_detail = _clean_scene_detail(
                str(gs_match.get("detail") or (c_steps[-1].get("actual") if c_steps else "") or "")
            )

            recipe_acts, recipe_target = _build_generic_scene_recipe(
                f"{gs_match.get('file', '')} {cid} {ctitle}",
                platform=meta_plat_early,
                pp_profile=pp_prof,
                default_activity=str(default_target.get("activity") or ".MainActivity"),
            )
            sc_actions = list(recipe_acts or default_actions)
            sc_target = dict(recipe_target or default_target)
            sc_dir = run_dir / "scenes" / cid
            if not force_refresh and sc_dir.is_dir() and not sc_dir.is_symlink() and (sc_dir / "actions.json").is_file():
                try:
                    loaded_act = json.loads((sc_dir / "actions.json").read_text(encoding="utf-8"))
                    if _cached_actions_valid_for_platform(loaded_act, meta_plat_early, pp_prof):
                        sc_actions = loaded_act
                except Exception:
                    pass
            has_sc_mock_b = _ensure_per_scene_restore_files(
                run_dir,
                cid,
                f"{gs_match.get('file', '')} {cid} {ctitle}",
                sc_actions,
                pp_profile=pp_prof,
                persist=persist,
            )

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

            # Enrich screenshots & console timeline from steps.json suite slice if available
            b_cur_pos = biz_suite_positions[idx - 1] if idx - 1 < len(biz_suite_positions) else -1
            b_sub_slice = steps[biz_prev_pos + 1 : b_cur_pos + 1] if b_cur_pos >= 0 else []
            if b_cur_pos >= 0:
                biz_prev_pos = b_cur_pos
            b_end_ts = _parse_iso_seconds(str(steps[b_cur_pos].get("timestamp", ""))) if b_cur_pos >= 0 else None
            b_start_ts = max(biz_prev_end_ep, b_end_ts - float(c_dur or 15.0) - 2.0) if (b_end_ts and biz_prev_end_ep) else None
            if b_end_ts:
                biz_prev_end_ep = b_end_ts
            for sub_b in b_sub_slice:
                sb_shot = sub_b.get("screenshot")
                if sb_shot:
                    rel_sb = str(sb_shot) if str(sb_shot).startswith("screenshots/") else f"screenshots/{sb_shot}"
                    if rel_sb not in sc_shots:
                        sc_shots.append(rel_sb)
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
            if b_start_ts and b_end_ts and biz_console_timed and len(timeline_logs) < 12:
                existing_lines = {item.get("line", "") for item in timeline_logs}
                span_b = max(float(c_dur or 1.0), 1.0)
                for c_ep, c_txt in biz_console_timed:
                    if b_start_ts - 1.0 <= c_ep <= b_end_ts + 1.0:
                        fmt_ln = f"[LOG] {c_txt}"
                        if fmt_ln not in existing_lines:
                            rel_t = round(max(0.0, min(span_b, c_ep - b_start_ts)), 2)
                            timeline_logs.append({"t": rel_t, "line": fmt_ln})
                            existing_lines.add(fmt_ln)
                timeline_logs.sort(key=lambda x: float(x.get("t", 0.0)))
            if not timeline_logs and sc_logs:
                total_span = max(float(c_dur or 0.0), 1.0)
                step_t = total_span / max(len(sc_logs), 1)
                for l_idx, ln in enumerate(sc_logs):
                    timeline_logs.append({"t": round(l_idx * step_t, 2), "line": ln})

            sc_assertions: list[dict[str, str]] = []
            for s in c_steps:
                # Support both BusinessReport Schema v2 (`s["assertions"]`) and legacy Schema v1 (`s["attempts"][*]["assertions"]`)
                cand_asrt_lists: list[list[Any]] = []
                if isinstance(s.get("assertions"), list):
                    cand_asrt_lists.append(s["assertions"])
                for att in (s.get("attempts") or []):
                    if isinstance(att, dict) and isinstance(att.get("assertions"), list):
                        cand_asrt_lists.append(att["assertions"])
                for asrt_list in cand_asrt_lists:
                    for asrt in asrt_list:
                        if isinstance(asrt, dict):
                            sc_assertions.append(
                                {
                                    "dim": str(asrt.get("dimension") or asrt.get("name") or "check"),
                                    "status": str(asrt.get("status") or "PASS"),
                                    "expected": str(asrt.get("expected") or ""),
                                    "actual": str(asrt.get("actual") or ""),
                                }
                            )
            raw_asrts_b, sc_sub_cases, raw_extra_b = _extract_raw_assertions_and_subcases(
                gs_match.get("raw"),
                str(gs_match.get("failure_reason") or ""),
            )
            if not sc_assertions and raw_asrts_b:
                sc_assertions = raw_asrts_b
            if raw_extra_b:
                existing_tl_lines = {item.get("line", "") for item in timeline_logs}
                tail_base = max(0.1, round(float(c_dur or 1.0) - 0.3, 2))
                for ex_idx, ex_ln in enumerate(raw_extra_b[:16]):
                    if ex_ln not in existing_tl_lines:
                        timeline_logs.append({"t": round(tail_base + ex_idx * 0.02, 2), "line": ex_ln})
                        existing_tl_lines.add(ex_ln)
                    if ex_ln not in sc_logs:
                        sc_logs.append(ex_ln)
                timeline_logs.sort(key=lambda x: float(x.get("t", 0.0)))
            vj_item = vlm_by_cid.get(raw_cid) or vlm_by_cid.get(cid) or {}
            b_off_s = (
                max(0, int(b_start_ts - run_start_ts))
                if (run_start_ts and b_start_ts and b_start_ts >= run_start_ts)
                else int(biz_cum_offset)
            )
            biz_cum_offset += float(c_dur or 0.0)
            scenes.append(
                {
                    "scene_id": cid,
                    "index": idx,
                    "title": ctitle,
                    "group": c_grp,
                    "group_label": c_grp_label,
                    "status": c_status,
                    "detail": c_detail or f"共 {len(sc_actions)} 步原子操作",
                    "elapsed_s": c_dur,
                    "video_offset_s": 0 if video_rel_path else b_off_s,
                    "video_end_s": round((0 if video_rel_path else b_off_s) + float(c_dur or 0.0), 1),
                    "video_time_label": "00:00" if video_rel_path else f"{b_off_s // 60:02d}:{b_off_s % 60:02d}",
                    "screenshot": sc_shot,
                    "screenshots": sc_shots,
                    "video_rel_path": video_rel_path,
                    "global_video_rel_path": None if video_rel_path else global_rec_rel,
                    "actions": sc_actions,
                    "target": sc_target,
                    "planned_steps": build_planned_steps(sc_actions, route_labels=route_labels),
                    "scene_logs": sc_logs,
                    "timeline_logs": timeline_logs,
                    "assertions": sc_assertions,
                    "sub_cases": sc_sub_cases,
                    "vlm_verdict": str(vj_item.get("verdict") or ""),
                    "vlm_reason": str(vj_item.get("summary") or vj_item.get("reason") or ""),
                    "has_scene_mock": bool(has_sc_mock_b),
                }
            )

    if not scenes and is_multi_suite:
        # Build from suite_steps first (exact per-run timestamps & screenshots), enriched with gate_summary if present
        summary_by_name = {}
        if gate_summary and isinstance(gate_summary.get("summary"), list):
            for item in gate_summary["summary"]:
                if isinstance(item, dict) and item.get("suite"):
                    s_key = str(item["suite"])
                    summary_by_name[s_key] = item
                    summary_by_name[re.sub(r"\s+", "", s_key)] = item

        # Parse timestamped console.log entries ([HH:MM:SS] ...) for accurate time-window slicing
        console_timed: list[tuple[float, str]] = []
        base_date_prefix = str(meta.get("started_at", ""))[:10]
        if base_date_prefix and (run_dir / "console.log").is_file():
            try:
                raw_con = (run_dir / "console.log").read_text(encoding="utf-8", errors="replace")
                ts_re = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$")
                for cln in raw_con.splitlines():
                    s_cln = cln.strip()
                    m_ts = ts_re.match(s_cln)
                    if not m_ts:
                        continue
                    body_txt = m_ts.group(2).strip()
                    if "logcat 等待中" in body_txt:
                        continue
                    c_epoch = _parse_iso_seconds(f"{base_date_prefix}T{m_ts.group(1)}")
                    if c_epoch is not None:
                        if run_start_ts and c_epoch < run_start_ts - 60:
                            c_epoch += 86400.0
                        console_timed.append((c_epoch, body_txt))
            except Exception:
                console_timed = []

        def _extract_raw_assertions(raw_obj: Any) -> tuple[list[dict[str, str]], list[str]]:
            asrts: list[dict[str, str]] = []
            extra_lines: list[str] = []
            if not isinstance(raw_obj, dict):
                return asrts, extra_lines
            for bool_k, val_b in raw_obj.items():
                if str(bool_k).endswith("_ok") and isinstance(val_b, bool):
                    asrts.append({"dim": str(bool_k), "status": "PASS" if val_b else "FAIL", "expected": "true", "actual": str(val_b).lower()})
            sub_res = raw_obj.get("results") or raw_obj.get("outcomes") or raw_obj.get("cases")
            if isinstance(sub_res, dict):
                for sk, sv in list(sub_res.items())[:16]:
                    if not isinstance(sv, dict):
                        continue
                    if "ok" in sv:
                        s_ok = bool(sv.get("ok"))
                        act_str = str(sv.get("passed") or ("PASS" if s_ok else "FAIL"))
                        if not s_ok and "close_xy" in sv and sv.get("close_xy") is None:
                            act_str = "FAIL (close_xy=null)"
                        elif not s_ok and isinstance(sv.get("steps"), dict):
                            failed_st = [k2 for k2, v2 in sv["steps"].items() if isinstance(v2, dict) and not v2.get("ok", True)]
                            if failed_st:
                                act_str = f"FAIL ({','.join(failed_st[:3])})"
                        asrts.append({"dim": str(sk), "status": "PASS" if s_ok else "FAIL", "expected": "ok", "actual": act_str})
                        tag = "✔ PASS" if s_ok else "✖ FAIL"
                        extra_lines.append(f"[{tag}] 子模块 {sk}: {act_str}")
                        # Expand nested failed sub-scenes (e.g. edge_scenes.scenes or show_fault_guards.faults)
                        for nested_k in ("scenes", "faults"):
                            nested_map = sv.get(nested_k)
                            if isinstance(nested_map, dict):
                                for nk, nv in nested_map.items():
                                    if isinstance(nv, dict) and not nv.get("ok", True):
                                        asrts.append({"dim": f"{sk}.{nk}", "status": "FAIL", "expected": "ok", "actual": "FAIL"})
                                        extra_lines.append(f"  ↳ [✖ FAIL] 子用例 {nk}")
                    else:
                        # Map of scene outcomes (e.g. #13 scene_outcomes: {cc_close_btn_left: {ok: true}, ...})
                        sub_total = len(sv)
                        sub_pass = sum(1 for v2 in sv.values() if isinstance(v2, dict) and v2.get("ok"))
                        s_ok = sub_pass == sub_total and sub_total > 0
                        asrts.append({"dim": str(sk), "status": "PASS" if s_ok else "FAIL", "expected": f"{sub_total}/{sub_total}", "actual": f"{sub_pass}/{sub_total}"})
                        tag = "✔ PASS" if s_ok else "✖ FAIL"
                        extra_lines.append(f"[{tag}] 矩阵组 {sk}: {sub_pass}/{sub_total} 通过")
                        for nk, nv in sv.items():
                            if isinstance(nv, dict) and not nv.get("ok", True):
                                asrts.append({"dim": str(nk), "status": "FAIL", "expected": "ok", "actual": "FAIL"})
                                extra_lines.append(f"  ↳ [✖ FAIL] 矩阵子项 {nk}")
            elif isinstance(sub_res, list):
                for s_item in sub_res[:16]:
                    if isinstance(s_item, dict):
                        s_name = str(s_item.get("name") or s_item.get("id") or s_item.get("scene") or "case")
                        s_ok = bool(s_item.get("ok", True))
                        asrts.append({"dim": s_name, "status": "PASS" if s_ok else "FAIL", "expected": "ok", "actual": "PASS" if s_ok else "FAIL"})
                        tag = "✔ PASS" if s_ok else "✖ FAIL"
                        extra_lines.append(f"[{tag}] 子用例 {s_name}")
            return asrts[:16], extra_lines

        if suite_steps:
            suite_step_positions = [
                pos for pos, s in enumerate(steps) if str(s.get("action", "")).startswith("[") and "]" in str(s.get("action", ""))
            ]
            cum_offset = 0.0
            prev_pos = -1
            prev_end_epoch = run_start_ts
            for idx, st in enumerate(suite_steps, start=1):
                cur_pos = suite_step_positions[idx - 1] if idx - 1 < len(suite_step_positions) else -1
                sub_slice = steps[prev_pos + 1 : cur_pos] if cur_pos >= 0 else []
                prev_pos = cur_pos

                raw_act = str(st.get("action", ""))
                m_grp = re.match(r"^\[([A-Za-z0-9_]+)\]\s*(.+)$", raw_act)
                grp = m_grp.group(1).lower() if m_grp else "core"
                suite_title = m_grp.group(2).strip() if m_grp else raw_act
                detail_str = str(st.get("detail", ""))
                m_dur = re.search(r"\(([0-9.]+)s", detail_str)
                dur_s = float(m_dur.group(1)) if m_dur else 15.0

                step_end_ts = _parse_iso_seconds(str(st.get("timestamp", "")))
                if step_end_ts and prev_end_epoch and step_end_ts >= prev_end_epoch:
                    suite_start_ts = max(prev_end_epoch, step_end_ts - dur_s - 2.0)
                elif step_end_ts:
                    suite_start_ts = step_end_ts - dur_s
                else:
                    suite_start_ts = (run_start_ts + cum_offset) if run_start_ts else None
                if step_end_ts:
                    prev_end_epoch = step_end_ts

                if run_start_ts and suite_start_ts and suite_start_ts >= run_start_ts:
                    start_offset_s = max(0, int(suite_start_ts - run_start_ts))
                elif run_start_ts and step_end_ts and step_end_ts >= run_start_ts:
                    start_offset_s = max(0, int(step_end_ts - run_start_ts - dur_s))
                else:
                    start_offset_s = int(cum_offset)
                cum_offset += dur_s

                # Collect all sub-step screenshots + timeline logs within this suite
                sc_shots: list[str] = []
                tl_entries: list[dict[str, Any]] = [
                    {"t": 0.0, "line": f"[SCENE] 开始执行套件 #{idx}「{suite_title}」"}
                ]
                for sub_s in sub_slice:
                    s_shot = sub_s.get("screenshot")
                    if s_shot and str(s_shot) not in sc_shots:
                        sc_shots.append(str(s_shot))
                    sub_ts = _parse_iso_seconds(str(sub_s.get("timestamp", "")))
                    rel_t = round(max(0.0, min(dur_s, (sub_ts - suite_start_ts))), 2) if (sub_ts and suite_start_ts) else round(min(dur_s * 0.8, len(tl_entries) * 1.2), 2)
                    sub_act = str(sub_s.get("action", "")).strip()
                    sub_det = str(sub_s.get("detail", "")).strip()
                    if sub_act and sub_act != "find_text":
                        msg_line = f"[STEP] {sub_act}" + (f" — {sub_det}" if sub_det else "")
                        tl_entries.append({"t": rel_t, "line": msg_line})
                if st.get("screenshot") and str(st["screenshot"]) not in sc_shots:
                    sc_shots.append(str(st["screenshot"]))

                if suite_start_ts and step_end_ts and console_timed:
                    for c_ts, c_txt in console_timed:
                        if suite_start_ts - 1.0 <= c_ts <= step_end_ts + 1.0:
                            rel_t = round(max(0.0, min(dur_s, c_ts - suite_start_ts)), 2)
                            tl_entries.append({"t": rel_t, "line": f"[LOG] {c_txt}"})

                sum_item = summary_by_name.get(suite_title) or summary_by_name.get(re.sub(r"\s+", "", suite_title)) or {}
                sc_asrts, sc_sub_cases, raw_extra_lines = _extract_raw_assertions_and_subcases(
                    sum_item.get("raw"),
                    str(sum_item.get("failure_reason") or ""),
                )
                for ex_i, ex_ln in enumerate(raw_extra_lines):
                    tl_entries.append({"t": round(max(0.1, dur_s - 0.5 + ex_i * 0.05), 2), "line": ex_ln})

                status_ok = st.get("status") in ("ok", "passed")
                verdict_tag = "✔ PASS" if status_ok else "✖ FAIL"
                tl_entries.append(
                    {"t": round(max(0.2, dur_s), 2), "line": f"[{verdict_tag}] 套件「{suite_title}」收口结论: {detail_str}"}
                )
                tl_entries.sort(key=lambda x: float(x.get("t", 0.0)))
                sc_log_lines = [item["line"] for item in tl_entries]

                scene_id = re.sub(r"[^a-zA-Z0-9_]+", "_", f"suite_{idx}_{ grp }").strip("_").lower()
                sc_dir = run_dir / "scenes" / scene_id
                actions, target = _build_generic_scene_recipe(
                    str(sum_item.get("file") or "") + " " + suite_title,
                    platform=meta_platform,
                    pp_profile=pp_prof,
                    steps=sub_slice,
                    default_activity=str(default_target.get("activity") or ".MainActivity"),
                )
                if not force_refresh and (sc_dir / "actions.json").is_file() and not (sc_dir / "actions.json").is_symlink():
                    try:
                        loaded_a = json.loads((sc_dir / "actions.json").read_text(encoding="utf-8"))
                        if _cached_actions_valid_for_platform(loaded_a, meta_platform, pp_prof):
                            actions = loaded_a
                    except Exception:
                        pass
                has_sc_mock = _ensure_per_scene_restore_files(
                    run_dir,
                    scene_id,
                    str(sum_item.get("file") or "") + " " + suite_title,
                    actions,
                    pp_profile=pp_prof,
                    overwrite_synthesized=force_refresh,
                    persist=persist,
                )
                ded_vid = f"scenes/{scene_id}/video.mp4" if (sc_dir / "video.mp4").is_file() else None

                scenes.append(
                    {
                        "scene_id": scene_id,
                        "index": idx,
                        "title": suite_title,
                        "group": grp,
                        "group_label": group_labels.get(grp, grp.upper()),
                        "status": "PASS" if status_ok else "FAIL",
                        "detail": _clean_scene_detail(detail_str),
                        "elapsed_s": round(dur_s, 1),
                        "video_offset_s": start_offset_s,
                        "video_end_s": round(start_offset_s + dur_s, 1),
                        "video_time_label": f"{start_offset_s // 60:02d}:{start_offset_s % 60:02d}",
                        "video_rel_path": ded_vid,
                        "global_video_rel_path": None if ded_vid else global_rec_rel,
                        "screenshot": st.get("screenshot") or (sc_shots[-1] if sc_shots else None),
                        "screenshots": sc_shots,
                        "actions": actions,
                        "target": target,
                        "planned_steps": build_planned_steps(actions, route_labels=route_labels),
                        "scene_logs": sc_log_lines,
                        "timeline_logs": tl_entries,
                        "assertions": sc_asrts,
                        "sub_cases": sc_sub_cases,
                        "has_scene_mock": has_sc_mock,
                    }
                )
        elif gate_summary and isinstance(gate_summary.get("summary"), list):
            cum_offset = 0.0
            dedup_items: list[dict[str, Any]] = []
            seen_summary_keys: set[str] = set()
            for raw_it in gate_summary["summary"]:
                if not isinstance(raw_it, dict) or not raw_it.get("suite"):
                    continue
                if raw_it.get("file") in ("stability_check", "builtin_zero_crash"):
                    continue
                dedup_k = str(raw_it.get("file") or raw_it.get("suite"))
                if dedup_k in seen_summary_keys:
                    continue
                seen_summary_keys.add(dedup_k)
                dedup_items.append(raw_it)
            for idx, item in enumerate(dedup_items, start=1):
                suite_title = str(item.get("suite") or f"suite_{idx}")
                dur_s = float(item.get("elapsed_s", 15.0))
                start_offset_s = int(cum_offset)
                cum_offset += dur_s
                grp = str(item.get("group") or "core")
                fname = str(item.get("file", ""))
                if grp == "core":
                    grp = _infer_scene_group(fname.lower(), pp_prof)
                scene_id = re.sub(r"[^a-zA-Z0-9_]+", "_", fname.replace(".py", "") or f"suite_{idx}").strip("_").lower()
                sc_dir = run_dir / "scenes" / scene_id
                actions, target = _build_generic_scene_recipe(
                    fname + " " + suite_title,
                    platform=meta_platform,
                    pp_profile=pp_prof,
                    default_activity=str(default_target.get("activity") or ".MainActivity"),
                )
                has_sc_mock = _ensure_per_scene_restore_files(
                    run_dir,
                    scene_id,
                    fname + " " + suite_title,
                    actions,
                    pp_profile=pp_prof,
                    overwrite_synthesized=force_refresh,
                    persist=persist,
                )
                ded_vid = f"scenes/{scene_id}/video.mp4" if (sc_dir / "video.mp4").is_file() else None
                sc_asrts, sc_sub_cases, raw_extra_lines = _extract_raw_assertions_and_subcases(
                    item.get("raw"),
                    str(item.get("failure_reason") or ""),
                )
                ok_flag = bool(item.get("ok"))
                verdict_tag = "✔ PASS" if ok_flag else "✖ FAIL"
                tl_entries = [{"t": 0.0, "line": f"[SCENE] 开始执行套件 #{idx}「{suite_title}」({fname})"}]
                for ex_i, ex_ln in enumerate(raw_extra_lines):
                    tl_entries.append({"t": round((ex_i + 1) * (dur_s / max(len(raw_extra_lines) + 1, 2)), 2), "line": ex_ln})
                tl_entries.append({"t": round(dur_s, 2), "line": f"[{verdict_tag}] 套件「{suite_title}」耗时 {dur_s}s"})
                scenes.append(
                    {
                        "scene_id": scene_id,
                        "index": idx,
                        "title": suite_title,
                        "group": grp,
                        "group_label": group_labels.get(grp, grp.upper()),
                        "status": "PASS" if ok_flag else "FAIL",
                        "detail": _clean_scene_detail(str(item.get("detail", f"attempts={item.get('attempts', 1)}"))),
                        "elapsed_s": round(dur_s, 1),
                        "video_offset_s": start_offset_s,
                        "video_end_s": round(start_offset_s + dur_s, 1),
                        "video_time_label": f"{start_offset_s // 60:02d}:{start_offset_s % 60:02d}",
                        "video_rel_path": ded_vid,
                        "global_video_rel_path": None if ded_vid else global_rec_rel,
                        "screenshot": None,
                        "screenshots": [],
                        "actions": actions,
                        "target": target,
                        "planned_steps": build_planned_steps(actions, route_labels=route_labels),
                        "scene_logs": [e["line"] for e in tl_entries],
                        "timeline_logs": tl_entries,
                        "assertions": sc_asrts,
                        "sub_cases": sc_sub_cases,
                        "has_scene_mock": has_sc_mock,
                    }
                )

    if not scenes:
        # Single-scene script
        actions = (default_manifest or {}).get("actions", [])
        target = (default_manifest or {}).get("target", {})
        if not actions:
            actions, target = _build_generic_scene_recipe(
                str(meta.get("script_name", "default")),
                platform=meta_platform,
                pp_profile=pp_prof,
                steps=steps,
                default_activity=str(default_target.get("activity") or ".MainActivity"),
            )
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
                "elapsed_s": round(float(meta.get("duration_s") or 0.0), 1),
                "video_offset_s": 0,
                "video_time_label": "00:00",
                "screenshot": last_shot,
                "actions": actions,
                "target": target,
                "planned_steps": build_planned_steps(actions, route_labels=route_labels),
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
    if persist:
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

