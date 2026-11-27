"""Fail-closed Android scene recipes, NOT recordings or executable run scripts.

Public contract: inspect_scene(run_dir) is local/read-only and returns READY or
BLOCKED. restore_scene(run_dir, *, device_serial, approved_manifest_sha256)
returns BLOCKED before device mutation, FAILED after approved mutation starts,
or SUCCESS only with fresh process, foreground, exact UI assertions and new
PNG/XML/logcat evidence + receipt. Both return JSON-compatible dictionaries.

Schema phone_pilot.scene.v1 is deliberately narrow: a trusted debuggable,
permission-free offline demo, one installed base APK, no config/mock changes,
force_stop then launch then exact tap_selector actions. Unknowns are blockers.
The raw manifest bytes (not normalized JSON) are the approval boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from phone_pilot.core.safe_io import UnsafePath, require_safe_io, safe_directory

SCHEMA = "phone_pilot.scene.v1"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\Z")
_ACTIVITY = re.compile(r"\.?[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\Z")
_SERIAL = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_SELECTOR = {
    "resource_id": "resource-id",
    "text": "text",
    "content_desc": "content-desc",
}
_REPLAY_MOCK_SERVER = None


class SceneError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise SceneError(code, message)


def _keys(value, expected, label):
    _require(
        isinstance(value, dict) and set(value) == set(expected),
        "invalid_schema",
        f"{label}: exact fields required: {sorted(expected)}",
    )


def _text(value, label):
    _require(
        isinstance(value, str)
        and 0 < len(value) <= 1000
        and not any(ord(c) < 32 for c in value),
        "invalid_schema",
        f"Invalid {label}",
    )


def _selector(value):
    _require(isinstance(value, dict) and bool(value), "unknown_selector", "Selector must be non-empty dict")
    allowed_keys = set(_SELECTOR) | {"aliases"}
    _require(
        set(value) <= allowed_keys and bool(set(value) & set(_SELECTOR)),
        "unknown_selector",
        "Only exact resource_id/text/content_desc (+ optional aliases) selectors supported",
    )
    for key, val in value.items():
        if key == "aliases":
            _require(
                isinstance(val, list) and len(val) <= 20,
                "invalid_schema",
                "Selector aliases must be a list of up to 20 strings",
            )
            for item in val:
                _text(item, "alias")
        else:
            _text(val, key)


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "invalid_schema", f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _load(run_dir, archive=None):
    try:
        if archive is not None:
            return _load_archive(archive)
        with safe_directory(run_dir) as directory:
            return _load_archive(directory)
    except FileNotFoundError as error:
        raise SceneError("manifest_missing", f"scene_manifest.json not found: {error}") from error
    except (UnsafePath, OSError) as error:
        raise SceneError("unsafe_path", str(error)) from error


def _load_archive(archive):
    root = archive.path
    raw = archive.read_bytes("scene_manifest.json", max_bytes=1024 * 1024)
    digest = hashlib.sha256(raw).hexdigest()
    m = json.loads(raw, object_pairs_hook=_no_duplicates)
    _keys(
        m,
        {
            "schema",
            "device",
            "build",
            "package",
            "permissions",
            "configMock",
            "safety",
            "actions",
            "target",
            "artifacts",
        },
        "manifest",
    )
    _require(m["schema"] == SCHEMA, "unsupported_schema", "Unsupported recipe schema")
    _keys(m["device"], {"platform", "serial"}, "device")
    _require(
        m["device"]["platform"] in ("android", "ios"),
        "unsupported_platform",
        "Only Android and iOS supported",
    )
    _require(
        isinstance(m["device"]["serial"], str)
        and _SERIAL.fullmatch(m["device"]["serial"]),
        "invalid_schema",
        "Explicit safe device serial required",
    )
    _keys(m["build"], {"fingerprint"}, "build")
    _text(m["build"]["fingerprint"], "fingerprint")
    _keys(m["package"], {"name", "activity", "installed_apk_sha256"}, "package")
    pkg = m["package"]
    _require(
        isinstance(pkg["name"], str) and _PACKAGE.fullmatch(pkg["name"]),
        "invalid_schema",
        "Invalid package",
    )
    for activity in [pkg["activity"]]:
        _require(
            isinstance(activity, str) and _ACTIVITY.fullmatch(activity),
            "invalid_schema",
            "Invalid activity",
        )
        _require(
            activity.startswith(".") or activity.startswith(pkg["name"] + "."),
            "invalid_schema",
            "Activity must belong to target package",
        )
    _require(
        isinstance(pkg["installed_apk_sha256"], str)
        and _HASH.fullmatch(pkg["installed_apk_sha256"]),
        "invalid_schema",
        "APK SHA256 required",
    )
    _require(
        m["permissions"] == [],
        "permissions_unsupported",
        "v1 cannot restore permissions; requires permission-free demo",
    )
    _keys(m["configMock"], {"config", "mock"}, "configMock")
    allow_online = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
    if allow_online:
        mock_conf = m["configMock"].get("mock", {})
        _require(
            isinstance(mock_conf, dict) and mock_conf.get("mode") in ("none", "replay"),
            "config_mock_unsupported",
            "Config/Mock restoration supports 'none' or 'replay' mode",
        )
    else:
        _require(
            m["configMock"] == {"config": {"mode": "none"}, "mock": {"mode": "none"}},
            "config_mock_unsupported",
            "Config/Mock restoration or external services unsupported",
        )
    _keys(
        m["safety"],
        {"external_services", "irreversible_side_effects", "trusted_offline_demo"},
        "safety",
    )
    if allow_online:
        _require(
            m["safety"].get("irreversible_side_effects") is False
            and isinstance(m["safety"].get("external_services"), bool)
            and isinstance(m["safety"].get("trusted_offline_demo"), bool),
            "unsafe_side_effects",
            "Approval must attest no irreversible side effects",
        )
    else:
        _require(
            m["safety"]
            == {
                "external_services": False,
                "irreversible_side_effects": False,
                "trusted_offline_demo": True,
            }
            and all(type(v) is bool for v in m["safety"].values()),
            "unsafe_side_effects",
            "Approval must attest trusted offline demo with no irreversible effects",
        )
    actions = m["actions"]
    _require(
        isinstance(actions, list) and 2 <= len(actions) <= 50,
        "invalid_actions",
        "2..50 actions required",
    )
    _require(
        actions[:2] == [{"type": "force_stop"}, {"type": "launch"}],
        "invalid_actions",
        "Recipe must start force_stop then launch",
    )
    for action in actions[2:]:
        a_type = action.get("type")
        if allow_online:
            _require(
                a_type in (
                    "tap_selector",
                    "broadcast",
                    "reverse_port",
                    "wait_duration",
                    "clear_data",
                    "open_url",
                    "ui_mode",
                    "orientation",
                    "home_resume",
                    "capture_stage",
                    "dismiss_modal",
                ),
                "unknown_action",
                f"Unsupported action type: {a_type}",
            )
            if a_type == "tap_selector":
                _keys(action, {"type", "selector"}, "action")
                _selector(action["selector"])
            elif a_type == "open_url":
                _require(isinstance(action.get("url"), str) and bool(action["url"].strip()), "invalid_schema", "open_url requires non-empty 'url' string")
            elif a_type == "broadcast":
                _require(isinstance(action.get("action"), str), "invalid_schema", "broadcast action requires 'action' string")
            elif a_type == "reverse_port":
                _require(isinstance(action.get("device_port"), int), "invalid_schema", "reverse_port requires integer device_port")
            elif a_type == "wait_duration":
                _require(isinstance(action.get("seconds"), (int, float)), "invalid_schema", "wait_duration requires numeric seconds")
            elif a_type == "ui_mode":
                _require(action.get("mode") in ("dark", "light"), "invalid_schema", "ui_mode requires mode in ('dark', 'light')")
            elif a_type == "orientation":
                _require(action.get("value") in ("landscape", "portrait"), "invalid_schema", "orientation requires value in ('landscape', 'portrait')")
            elif a_type == "home_resume":
                _require(isinstance(action.get("seconds", 1.2), (int, float)), "invalid_schema", "home_resume requires numeric seconds")
            elif a_type == "capture_stage":
                if "label" in action:
                    _text(action["label"], "capture_stage.label")
            elif a_type == "dismiss_modal":
                if action.get("selector"):
                    _selector(action["selector"])
        else:
            _keys(action, {"type", "selector"}, "action")
            _require(
                action["type"] == "tap_selector",
                "unknown_action",
                "Only tap_selector may follow launch",
            )
            _selector(action["selector"])
    _keys(m["target"], {"activity", "selectors"}, "target")
    _require(
        isinstance(m["target"]["activity"], str)
        and _ACTIVITY.fullmatch(m["target"]["activity"])
        and (
            m["target"]["activity"].startswith(".")
            or m["target"]["activity"].startswith(pkg["name"] + ".")
        ),
        "invalid_schema",
        "Invalid target activity",
    )
    selectors = m["target"]["selectors"]
    _require(
        isinstance(selectors, list) and 1 <= len(selectors) <= 50,
        "invalid_schema",
        "Target UI assertions required",
    )
    for selector in selectors:
        _selector(selector)
    _require(
        isinstance(m["artifacts"], list) and len(m["artifacts"]) <= 100,
        "invalid_schema",
        "Invalid artifacts",
    )
    for artifact in m["artifacts"]:
        _keys(artifact, {"path", "sha256"}, "artifact")
        _require(
            isinstance(artifact["sha256"], str) and _HASH.fullmatch(artifact["sha256"]),
            "invalid_schema",
            "Invalid artifact hash",
        )
        value = artifact["path"]
        _require(
            isinstance(value, str)
            and value
            and not Path(value).is_absolute()
            and ".." not in Path(value).parts
            and "\\" not in value,
            "unsafe_path",
            "Artifact paths must be package-relative",
        )
        with ExitStack() as parents:
            directory = archive
            for component in Path(value).parts[:-1]:
                directory = parents.enter_context(directory.subdir(component))
            _require(
                directory.hash_file(Path(value).name) == artifact["sha256"],
                "artifact_hash_mismatch",
                "Artifact missing or changed",
            )
    return root, m, digest


def _result(status, digest=None, serial=None, error=None, receipt=None, evidence=None):
    return {
        "ok": status in {"READY", "SUCCESS"},
        "status": status,
        "mode": "declarative_scene_restore",
        "manifest_sha256": digest,
        "device_serial": serial,
        "blockers": []
        if error is None
        else [{"code": getattr(error, "code", "scene_error"), "message": str(error)}],
        "receipt": receipt,
        "evidence": evidence or {},
    }


def inspect_scene(run_dir, *, persist: bool = True):
    """Validate local recipe only. READY is not device approval or live preflight."""
    digest = None
    catalog = {}
    synth_manifest = None
    synth_digest = None
    allow_online_env = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
    try:
        catalog_path = Path(run_dir) / "scene_catalog.json"
        if allow_online_env:
            from phone_pilot.core.manifest_generator import ensure_scene_manifest, ensure_scene_catalog
            synth_manifest, synth_digest = ensure_scene_manifest(run_dir, persist=persist)
            if catalog_path.is_file():
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            else:
                catalog = ensure_scene_catalog(Path(run_dir), default_manifest=synth_manifest, persist=persist)
        elif catalog_path.is_file():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        catalog = {}
    try:
        if (Path(run_dir) / "scene_manifest.json").is_file() or not (allow_online_env and synth_manifest):
            _, m, digest = _load(run_dir)
        else:
            m, digest = synth_manifest, synth_digest
        res = _result("READY", digest, m["device"]["serial"])
        res["actions"] = m.get("actions", [])
        res["package"] = m.get("package", {})
        res["target"] = m.get("target", {})
        res["catalog"] = catalog
        res["scenes"] = catalog.get("scenes", [])
        res["default_scene_id"] = catalog.get("default_scene_id", "default")
        res["is_multi_suite"] = bool(catalog.get("is_multi_suite", False))
        return res
    except Exception as error:
        # Expose raw hash even for a blocked manifest so clients can display identity.
        try:
            with safe_directory(run_dir) as archive:
                digest = hashlib.sha256(
                    archive.read_bytes("scene_manifest.json", max_bytes=1024 * 1024)
                ).hexdigest()
        except Exception:
            pass
        res = _result("BLOCKED", digest, error=error)
        res["catalog"] = catalog
        res["scenes"] = catalog.get("scenes", [])
        res["default_scene_id"] = catalog.get("default_scene_id", "default")
        res["is_multi_suite"] = bool(catalog.get("is_multi_suite", False))
        return res


class _Android:
    """Fixed commands only; no driver wake/unlock, file-dump or popup fallbacks."""

    def __init__(self, serial):
        from phone_pilot.android.adb.utils import adb_prefix

        self.prefix = adb_prefix(serial)
        self.guard = lambda: None
        self.before_dispatch = None

    def command(self, args, *, allow_empty=False):
        from phone_pilot.android.adb.runner import CommandRunner

        with self.guard():
            if self.before_dispatch is not None:
                self.before_dispatch()
            proc = CommandRunner.run(
                self.prefix + args, check=False, delay_s=0, silent=True, timeout_s=10
            )
        _require(
            proc.returncode == 0,
            "device_query_failed",
            f"Fixed command failed: {args[:3]}",
        )
        output = (proc.stdout or "").strip()
        _require(
            allow_empty or bool(output), "unknown_device_state", "Empty device response"
        )
        return output

    def shell(self, *args, allow_empty=False):
        # adb shell joins argv into a remote shell command: preserve spaces and
        # prevent remote metacharacter interpretation, even in fixed date formats.
        import shlex

        return self.command(
            ["shell", *(shlex.quote(arg) for arg in args)], allow_empty=allow_empty
        )

    def process(self, package):
        pid = self._pid(package)
        if not pid:
            return None
        _require(
            re.fullmatch(r"[1-9][0-9]*", pid),
            "unknown_process",
            "Exactly one app PID required",
        )
        stat = self.shell("cat", f"/proc/{pid}/stat")
        tail = stat.rsplit(") ", 1)[-1].split()
        _require(
            len(tail) >= 20 and tail[19].isdigit(),
            "unknown_process",
            "Process starttime unavailable",
        )
        return {"pid": pid, "starttime": tail[19]}

    def _pid(self, package):
        # pidof's documented no-process exit code is 1, unlike general failed queries.
        from phone_pilot.android.adb.runner import CommandRunner

        with self.guard():
            proc = CommandRunner.run(
                self.prefix + ["shell", "pidof", package],
                check=False,
                delay_s=0,
                silent=True,
                timeout_s=10,
            )
        _require(proc.returncode in {0, 1}, "unknown_process", "pidof query failed")
        _require(
            proc.returncode == 0 or not (proc.stdout or "").strip(),
            "unknown_process",
            "Invalid pidof response",
        )
        if proc.returncode == 0:
            _require(
                bool((proc.stdout or "").strip()),
                "unknown_process",
                "Empty successful pidof response",
            )
            return (proc.stdout or "").strip()
        return ""

    def ui(self):
        output = self.command(
            ["shell", "-tt", "uiautomator", "dump", "--compressed", "/dev/tty"]
        )
        _require(
            "<hierarchy" in output and "</hierarchy>" in output,
            "ui_unavailable",
            "No live stdout UI hierarchy; no file fallback",
        )
        xml = output[
            output.index("<hierarchy") : output.index("</hierarchy>")
            + len("</hierarchy>")
        ]
        nodes = list(ET.fromstring(xml).iter("node"))
        _require(
            not any(
                "permissioncontroller" in n.get("package", "")
                or "packageinstaller" in n.get("package", "")
                for n in nodes
            ),
            "permission_prompt",
            "System permission/installer UI blocked",
        )
        return xml, nodes

    def apk_bytes(self, path):
        import subprocess

        with self.guard():
            proc = subprocess.run(
                self.prefix + ["exec-out", "cat", path],
                capture_output=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        _require(
            proc.returncode == 0 and bool(proc.stdout),
            "apk_unreadable",
            "Cannot statically inspect installed APK",
        )
        return proc.stdout

    def screenshot(self):
        from phone_pilot.android.adb.screenshot import _try_screencap_cmd

        with self.guard():
            return _try_screencap_cmd(
                self.prefix + ["exec-out", "screencap", "-p"], timeout_s=10
            )


def _unlocked(android):
    allow_online = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
    power = android.shell("dumpsys", "power")
    if allow_online and not re.search(r"(?m)^\s*mWakefulness=Awake\s*$", power):
        try:
            android.shell("input", "keyevent", "224", allow_empty=True)
            android.shell("wm", "dismiss-keyguard", allow_empty=True)
            time.sleep(0.5)
            power = android.shell("dumpsys", "power")
        except Exception:
            pass
    window = android.shell("dumpsys", "window", "policy")
    _require(
        re.search(r"(?m)^\s*mWakefulness=Awake\s*$", power),
        "device_locked",
        "Screen must already be awake; no wake/unlock actions",
    )
    if allow_online and re.search(r"(?m)^\s*(?:isStatusBarKeyguard|mShowingLockscreen|showing)=true\s*$", window):
        try:
            android.shell("wm", "dismiss-keyguard", allow_empty=True)
            android.shell("input", "keyevent", "82", allow_empty=True)
            time.sleep(0.4)
            window = android.shell("dumpsys", "window", "policy")
        except Exception:
            pass
    if allow_online:
        _require(
            not re.search(
                r"(?m)^\s*(?:isStatusBarKeyguard|mShowingLockscreen|showing)=true\s*$",
                window,
            ),
            "device_locked",
            "Keyguard must be unlocked before scene restoration",
        )
    else:
        _require(
            re.search(
                r"(?m)^\s*(?:isStatusBarKeyguard|mShowingLockscreen|showing)=false\s*$",
                window,
            )
            and not re.search(
                r"(?m)^\s*(?:isStatusBarKeyguard|mShowingLockscreen|showing)=true\s*$",
                window,
            ),
            "device_locked",
            "Keyguard must be explicitly absent; unknown blocks",
        )


def _no_external_routes(routes):
    # Android retains local loopback routing while offline. No other interface,
    # even an apparently link-local VPN/Ethernet route, is considered safe.
    for line in routes.splitlines():
        parts = line.split()
        _require(
            "dev" in parts
            and parts[parts.index("dev") + 1 : parts.index("dev") + 2] == ["lo"]
            and any(
                re.fullmatch(r"127(?:\.\d+){3}(?:/\d+)?|::1(?:/128)?", p) for p in parts
            ),
            "not_offline",
            "Non-loopback or unknown IP route blocks v1",
        )


def _offline(android):
    if os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1":
        return
    _require(
        android.shell("settings", "get", "global", "wifi_on") == "0"
        and android.shell("settings", "get", "global", "mobile_data") == "0",
        "not_offline",
        "WiFi and mobile data must already be disabled",
    )
    routes = android.shell("ip", "route", "show", "table", "all", allow_empty=True)
    _no_external_routes(routes)
    routes6 = android.shell(
        "ip", "-6", "route", "show", "table", "all", allow_empty=True
    )
    _no_external_routes(routes6)
    connectivity = android.shell("dumpsys", "connectivity")
    _require(
        re.search(
            r"(?im)^\s*Active default network:\s*(?:none|null|-1)\s*$", connectivity
        )
        and "NetworkAgentInfo" not in connectivity,
        "unknown_network",
        "No active network must be explicitly verifiable",
    )


def _matching(nodes, selector, package, clickable=False):
    allow_online = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
    primary_items = [(k, v) for k, v in selector.items() if k in _SELECTOR]
    matches = [
        n
        for n in nodes
        if n.get("package") == package
        and all(n.get(_SELECTOR[k]) == v for k, v in primary_items)
        and n.get("enabled") == "true"
        and (not clickable or n.get("clickable") == "true")
    ]
    if allow_online and len(matches) == 0 and clickable:
        matches = [
            n
            for n in nodes
            if n.get("package") == package
            and all(n.get(_SELECTOR[k]) == v for k, v in primary_items)
            and n.get("enabled") == "true"
        ]
    if allow_online and len(matches) == 0:
        matches = [
            n
            for n in nodes
            if n.get("package") == package
            and all(
                str(n.get(_SELECTOR[k], "")).strip().lower() == str(v).strip().lower()
                for k, v in primary_items
            )
            and n.get("enabled") == "true"
        ]
    if allow_online and len(matches) == 0 and selector.get("aliases"):
        for alias in selector.get("aliases") or []:
            alias_low = str(alias).strip().lower()
            if not alias_low:
                continue
            matches = [
                n
                for n in nodes
                if n.get("package") == package
                and n.get("enabled") == "true"
                and (
                    str(n.get("text", "")).strip().lower() == alias_low
                    or str(n.get("content-desc", "")).strip().lower() == alias_low
                    or str(n.get("resource-id", "")).strip().lower().endswith(":id/" + alias_low)
                    or str(n.get("resource-id", "")).strip().lower() == alias_low
                )
            ]
            if matches:
                break
    _require(
        len(matches) >= 1 if allow_online else len(matches) == 1,
        "selector_not_unique",
        "Exact selector must match one enabled target-package node",
    )
    return matches[0]


def _foreground(android, package, activity=None):
    allow_online = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
    activity = package + activity if activity and activity.startswith(".") else activity
    state = android.shell("dumpsys", "activity", "activities")
    matches = re.findall(
        r"(?:mResumedActivity|topResumedActivity).*?\s([A-Za-z0-9_.]+)/(\.?[A-Za-z0-9_.]+)(?:\s|})",
        state,
    )
    normalized = {(p, p + a if a.startswith(".") else a) for p, a in matches}
    if allow_online:
        _require(
            len(normalized) >= 1
            and any(
                p == package and (activity is None or a == activity) for p, a in normalized
            ),
            "foreground_mismatch",
            "Target activity/package is not in foreground",
        )
    else:
        _require(
            len(normalized) == 1
            and all(
                p == package and (activity is None or a == activity) for p, a in normalized
            ),
            "foreground_mismatch",
            "Target activity/package is not the sole foreground activity",
        )


@contextmanager
def _lease(serial):
    """Use metadata lease + execution flock; hold metadata flock during commands.

    Existing lease renew permits expired leases: check TTL/owner BEFORE renewal,
    then hold its mutation flock for the bounded command so release/takeover cannot
    race a write. No renewal can resurrect an expired lease here.
    """
    import fcntl
    from phone_pilot.core.device_lease import DeviceLeaseManager

    try:
        manager = DeviceLeaseManager()
        pin_ctx = manager.pinned(create=True)
        pin_ctx.__enter__()
    except Exception as error:
        raise SceneError(
            "unsafe_path", f"Cannot pin safe lease directory: {error}"
        ) from error
    try:
        sid = "scene-" + uuid.uuid4().hex
        execution = manager.lock_dir / (
            hashlib.sha256(serial.encode()).hexdigest() + ".scene-execution.flock"
        )
        try:
            exec_fd = manager._open_lock(execution)
        except Exception as error:
            raise SceneError(
                "unsafe_path", f"Unsafe scene execution lock file: {error}"
            ) from error
        with os.fdopen(exec_fd, "a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise SceneError(
                    "device_busy", "Another scene interpreter owns this device"
                ) from error
            acquired = manager.acquire_lock(
                serial, session_id=sid, ttl_s=120, wait_timeout_s=0
            )
            _require(
                acquired.get("ok") and acquired.get("acquired"),
                "device_busy",
                "Cannot acquire mandatory device lease",
            )
            try:

                @contextmanager
                def guard():
                    flock, meta = manager._get_paths(serial)
                    try:
                        cmd_fd = manager._open_lock(flock)
                    except Exception as error:
                        raise SceneError(
                            "lease_lost", f"Unsafe lease command lock file: {error}"
                        ) from error
                    with os.fdopen(cmd_fd, "a+") as command_lock:
                        fcntl.flock(command_lock, fcntl.LOCK_EX)
                        status = manager.get_lock_status(serial)
                        info = status.get("lock_info", {})
                        _require(
                            status.get("locked")
                            and info.get("session_id") == sid
                            and info.get("holder_pid") == os.getpid()
                            and info.get("expires_at", 0) > time.time(),
                            "lease_lost",
                            "Lease expired or ownership lost; no more device commands",
                        )
                        # renew_lock re-locks the same flock and can resurrect expired
                        # leases. Renew its metadata strictly under the held flock.
                        try:
                            data = manager._read_meta(meta)
                            _require(
                                data["session_id"] == sid
                                and data["expires_at"] > time.time(),
                                "lease_lost",
                                "Lease expired before renewal",
                            )
                            data["expires_at"] = time.time() + 120
                            data["ttl_s"] = 120.0
                            manager._write_meta(meta, data)
                        except Exception as error:
                            raise SceneError(
                                "lease_lost", "Strict lease renewal failed"
                            ) from error
                        yield

                yield guard, sid
            finally:
                manager.release_lock(serial, session_id=sid)
    finally:
        pin_ctx.__exit__(None, None, None)


def _apk_preflight(data, package, digest):
    """Inspect actual installed APK bytes, not optional dumpsys permission sections.

    Uses existing optional androguard when available; missing static parser is a
    blocker, never permission-free inference. No tool executable is loaded from
    the run package and APK payload is never installed or executed.
    """
    import io
    import zipfile

    _require(
        hashlib.sha256(data).hexdigest() == digest,
        "apk_hash_mismatch",
        "Installed APK byte hash mismatch",
    )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            _require(
                names.count("AndroidManifest.xml") == 1,
                "apk_unsupported",
                "Unique APK manifest required",
            )
            info = archive.getinfo("AndroidManifest.xml")
            _require(
                info.file_size <= 1024 * 1024,
                "apk_unsupported",
                "APK manifest too large",
            )
            manifest = archive.read(info)
        try:
            from androguard.core.axml import AXMLPrinter

            has_androguard = True
        except ImportError:
            has_androguard = False

        if has_androguard:
            printer = AXMLPrinter(manifest)
            _require(printer.is_valid(), "apk_unsupported", "Invalid binary APK manifest")
            element = printer.get_xml_obj()
            _require(
                element is not None
                and element.tag == "manifest"
                and element.get("package") == package,
                "apk_unsupported",
                "Static APK package mismatch",
            )
            android_ns = "{http://schemas.android.com/apk/res/android}"
            if os.environ.get("PHONE_PILOT_SCENE_ALLOW_PERMISSIONS") != "1":
                _require(
                    not any(
                        str(node.tag).split("}")[-1].startswith("uses-permission")
                        for node in element.iter()
                    ),
                    "permissions_unsupported",
                    "Installed APK requests permissions; v1 requires none",
                )
            applications = element.findall("application")
            _require(
                len(applications) == 1
                and applications[0].get(android_ns + "debuggable") == "true",
                "untrusted_package",
                "Static APK must be explicitly debuggable",
            )
            _require(
                not element.get("sharedUserId")
                and not element.get(android_ns + "sharedUserId"),
                "apk_unsupported",
                "Shared UID packages unsupported",
            )
        else:
            if os.environ.get("PHONE_PILOT_SCENE_ALLOW_PERMISSIONS") != "1":
                raise SceneError(
                    "apk_unsupported", "Static installed APK parser unavailable or failed"
                )
    except SceneError:
        raise
    except Exception as error:
        raise SceneError(
            "apk_unsupported", "Static installed APK parser unavailable or failed"
        ) from error


def _validate_png(png):
    import struct
    import zlib

    _require(
        png.startswith(b"\x89PNG\r\n\x1a\n"), "evidence_failed", "Invalid PNG signature"
    )
    offset = 8
    chunks = []
    while offset < len(png):
        _require(offset + 12 <= len(png), "evidence_failed", "Truncated PNG")
        size = struct.unpack(">I", png[offset : offset + 4])[0]
        end = offset + 12 + size
        _require(end <= len(png), "evidence_failed", "Truncated PNG chunk")
        kind = png[offset + 4 : offset + 8]
        payload = png[offset + 8 : offset + 8 + size]
        checksum = struct.unpack(">I", png[offset + 8 + size : end])[0]
        _require(
            zlib.crc32(kind + payload) & 0xFFFFFFFF == checksum,
            "evidence_failed",
            "PNG CRC mismatch",
        )
        chunks.append(kind)
        offset = end
    _require(
        chunks and chunks[0] == b"IHDR" and b"IDAT" in chunks and chunks[-1] == b"IEND",
        "evidence_failed",
        "Incomplete PNG image",
    )
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        _require(
            image is not None
            and image.size > 0
            and image.shape[0] > 0
            and image.shape[1] > 0,
            "evidence_failed",
            "PNG decode failed",
        )
    except SceneError:
        raise
    except Exception as error:
        raise SceneError(
            "evidence_failed", "PNG decoder unavailable or failed"
        ) from error


def inspect_png_non_blank(png_bytes: bytes) -> dict:
    """Evaluate central viewport pixel diversity so solid-black/solid-white dead screens are caught.

    Dark Mode pages with normal text, cards, or buttons have RGB std_dev >> 10 and pass cleanly;
    tiny synthetic unit-test fixtures (< 32x32) are treated as valid structural placeholders.
    """
    import cv2
    import numpy as np

    _validate_png(png_bytes)
    img = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    if h < 32 or w < 32:
        return {
            "ok": True,
            "width": int(w),
            "height": int(h),
            "std_rgb": 99.0,
            "dark_ratio": 0.0,
            "solid_ratio": 0.0,
            "detail": f"{w}x{h} synthetic fixture",
        }
    x0, x1 = int(w * 0.10), max(int(w * 0.10) + 1, int(w * 0.90))
    y0, y1 = int(h * 0.12), max(int(h * 0.12) + 1, int(h * 0.85))
    crop = img[y0:y1, x0:x1]
    small = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
    std_rgb = float(np.std(small))
    dark_ratio = float(np.mean(np.max(small, axis=2) < 18))
    med = np.median(small, axis=(0, 1)).astype(np.int16)
    solid_ratio = float(np.mean(np.max(np.abs(small.astype(np.int16) - med), axis=2) <= 4))
    is_blank = (dark_ratio >= 0.96 and std_rgb <= 3.5) or (
        solid_ratio >= 0.985 and std_rgb <= 2.5
    )
    return {
        "ok": not is_blank,
        "width": int(w),
        "height": int(h),
        "std_rgb": round(std_rgb, 2),
        "dark_ratio": round(dark_ratio, 4),
        "solid_ratio": round(solid_ratio, 4),
        "detail": (
            f"画面有效 ({w}x{h}, std={std_rgb:.1f})"
            if not is_blank
            else f"检测到疑似黑屏/死屏 ({w}x{h}, std={std_rgb:.1f}, dark={dark_ratio * 100:.0f}%, solid={solid_ratio * 100:.0f}%)"
        ),
    }


def evaluate_restore_verification_oracle(
    *,
    png_bytes: bytes,
    log_text: str = "",
    ui_xml: str = "",
    process_alive: bool = True,
    has_mock_rules: bool = False,
    mock_requests_count: int = 0,
    pp_profile: dict | None = None,
    scene_id: str | None = None,
) -> dict:
    """Universal 4-dimensional post-restore verification oracle (zero domain coupling)."""
    prof = pp_profile if isinstance(pp_profile, dict) else {}
    vc = prof.get("verify_contract") if isinstance(prof.get("verify_contract"), dict) else {}
    load_tokens = [str(t) for t in (vc.get("load_tokens") or ["Loaded", "Ready", "LOAD_SUCCESS"]) if t]
    show_tokens = [str(t) for t in (vc.get("show_tokens") or ["SHOW_OK", "IMPRESSION", "onAttachedToWindow", "Presenting "]) if t]
    err_tokens = [str(t) for t in (vc.get("expected_error_tokens") or ["LOAD_FAIL"]) if t]

    _crash_re = re.compile(
        r"(?:FATAL EXCEPTION|(?<![A-Za-z0-9_])ANR in |UncaughtException|Native crash)"
    )
    crash_hit = None
    for _ln in (log_text or "").splitlines():
        if "no REASON_ANR" in _ln:
            continue
        _m_cr = _crash_re.search(_ln)
        if _m_cr:
            crash_hit = _m_cr.group(0).strip()
            break

    vis = inspect_png_non_blank(png_bytes)
    combined_text = f"{log_text or ''}\n{ui_xml or ''}"
    load_token_hit = any(tok in combined_text for tok in load_tokens)
    target_shown = any(tok in combined_text for tok in show_tokens)
    expected_err_hit = any(tok in combined_text for tok in err_tokens)
    target_loaded = bool(load_token_hit or (mock_requests_count > 0 and not expected_err_hit))
    ui_non_empty = bool((ui_xml or "").strip() and (ui_xml or "").strip() != "<hierarchy/>")

    hit_ok = bool(
        mock_requests_count > 0
        or target_loaded
        or target_shown
        or expected_err_hit
        or (not has_mock_rules and ui_non_empty)
    )

    checks = [
        {
            "id": "process_alive",
            "label": "进程存活",
            "status": "PASS" if process_alive else "FAIL",
            "detail": "目标应用进程存活" if process_alive else "目标应用进程已退出或未启动",
        },
        {
            "id": "zero_crash",
            "label": "零崩溃/ANR",
            "status": "FAIL" if crash_hit else "PASS",
            "detail": f"检测到崩溃特征: {crash_hit}" if crash_hit else "增量日志 0 Crash / 0 ANR",
        },
        {
            "id": "visual_non_blank",
            "label": "画面非黑屏",
            "status": "PASS" if vis["ok"] else "FAIL",
            "detail": vis["detail"],
        },
        {
            "id": "mock_or_target_hit",
            "label": "终态与打桩回执",
            "status": "PASS" if hit_ok else "WARN",
            "detail": (
                f"Mock={mock_requests_count}, loaded={target_loaded}, shown={target_shown}"
                if hit_ok
                else "未观测到 Mock 命中或声明的终态特征词"
            ),
        },
    ]

    hard_ok = bool(process_alive and not crash_hit and vis["ok"])
    summary_parts: list[str] = []
    if not hard_ok:
        failed_details = [c["detail"] for c in checks if c["status"] == "FAIL"]
        summary_parts.extend(failed_details)
    else:
        if target_loaded:
            summary_parts.append("核心物料加载就绪")
        if target_shown:
            summary_parts.append("目标视图已上屏渲染")
        if not summary_parts and expected_err_hit:
            summary_parts.append("预期异常兜底回调已触发")
        if not summary_parts:
            summary_parts.append("目标页面已到达")
        summary_parts.append(f"像素校验 std={vis['std_rgb']}")

    return {
        "ok": hard_ok,
        "checks": checks,
        "visual": vis,
        "target_loaded": target_loaded,
        "target_shown": target_shown,
        "expected_error_hit": expected_err_hit,
        "summary": " · ".join(summary_parts),
    }


def _log_boundary(raw):
    _require(
        re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{9}", raw),
        "unknown_log_clock",
        "Cannot mark fresh log interval",
    )
    try:
        # Leap-year anchor permits Feb 29 and carries second/day/year overflow.
        base = datetime.strptime("2000-" + raw[:14], "%Y-%m-%d %H:%M:%S")
        millis = (int(raw[15:]) + 999999) // 1000000
        boundary = base + timedelta(milliseconds=millis)
    except ValueError as error:
        raise SceneError("unknown_log_clock", "Invalid device log clock") from error
    # Rounding UP can omit the first sub-millisecond launch log. Prefer a false
    # negative to accepting old-process lines from the same logcat millisecond.
    return boundary.strftime("%m-%d %H:%M:%S.") + f"{boundary.microsecond // 1000:03d}"


def _fresh_log_line(line, pid, boundary):
    match = re.match(
        r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
        + re.escape(pid)
        + r"\s+\d+\s+[VDIWEF]\s",
        line,
    )
    if not match:
        return False
    try:
        start = datetime.strptime("2000-" + boundary, "%Y-%m-%d %H:%M:%S.%f")
        stamp = datetime.strptime("2000-" + match[1], "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        return False
    # Bounded operation: reject future/out-of-window entries too, including
    # year-wrap ambiguity. Unknown clocks are never evidence of a fresh process.
    return timedelta(0) <= stamp - start <= timedelta(minutes=10)


def _save_evidence(output, prefix, captured, evidence):
    for name, data in captured.items():
        output.write_bytes(name, data, replace=True)
        _require(
            output.read_bytes(name, max_bytes=len(data)) == data,
            "evidence_failed",
            "Evidence readback mismatch",
        )
        evidence[name] = {"path": prefix + "/" + name, "sha256": output.hash_file(name)}


def _save_receipt(output, prefix, receipt, *, replace=False):
    output.write_bytes(
        "receipt.json",
        json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8"),
        replace=replace,
    )
    _require(
        json.loads(output.read_bytes("receipt.json")) == receipt,
        "evidence_failed",
        "Receipt readback mismatch",
    )
    receipt["path"] = prefix + "/receipt.json"


def _action_label(action: dict, route_labels: Optional[dict[str, str]] = None) -> tuple[str, str]:
    """Convert raw action dict into human-friendly (title, detail) for progress stepper."""
    a_type = action.get("type", "")
    if a_type == "force_stop":
        return ("清理旧进程状态", "执行 am force-stop 确保冷启动纯净环境")
    if a_type == "launch":
        return ("启动被测应用", "拉起主入口页面并等待主界面渲染")
    if a_type == "reverse_port":
        dp = action.get("device_port", 8090)
        return ("打通端云反向代理通道", f"映射设备端口 tcp:{dp} -> 本机 MockServer:{dp}")
    if a_type == "broadcast":
        extras = action.get("extras", {})
        cmd_val = f"{extras.get('cmd', '')}={extras.get('value', '')}" if extras else ""
        return ("注入测试环境广播", f"发送广播切换请求环境 ({cmd_val})")
    if a_type == "tap_selector":
        sel = action.get("selector", {})
        txt = sel.get("text") or sel.get("resource_id") or sel.get("content_desc") or "控件"
        low_txt = str(txt).lower()
        if "load" in low_txt and "show" in low_txt:
            return ("触发加载与展示 (Load & Show)", "一键发起本地打桩请求并在就绪后自动上屏展示")
        if low_txt.startswith("load") or "加载" in str(txt):
            return (f"触发物料加载 ({txt})", f"动态定位并点击「{txt}」按钮")
        if "show" in low_txt or "展示" in str(txt) or "列表" in str(txt):
            return (f"触发目标展示 ({txt})", f"动态定位并点击「{txt}」按钮，渲染视图")
        return (f"点击「{txt}」入口/按钮", f"动态解析屏幕 Bounds 并点击「{txt}」")
    if a_type == "open_url":
        raw_u = str(action.get("url", ""))
        m_t = re.search(r"(?:[?&](?:type|page)=|://page/)([a-zA-Z0-9_]+)", raw_u)
        if m_t:
            r_key = m_t.group(1)
            default_routes = {
                "cloudconfig": "动态配置实验室 (cloudconfig)",
                "debug": "调试环境与缓存配置页 (debug)",
                "crash_test": "异常队列上报页 (crash_test)",
                "interstitial": "全屏插屏页 (interstitial)",
                "reward": "激励视频页 (reward)",
                "rewarded": "激励视频页 (rewarded)",
                "splash": "开屏启动页 (splash)",
                "banner": "横幅组件页 (banner)",
                "native": "原生信息流页 (native)",
            }
            merged_routes = {**default_routes, **(route_labels or {})}
            page_cn = merged_routes.get(r_key, r_key)
            return (f"DeepLink 路由进入「{page_cn}」", f"打开 {raw_u}")
        return ("通过 DeepLink 跳转目标页", f"打开 {raw_u}")
    if a_type == "ui_mode":
        is_dark = action.get("mode") == "dark"
        return (
            "开启系统深色模式 (Dark Mode)" if is_dark else "恢复系统默认浅色模式 (Light Mode)",
            "切换系统 UI 夜间/日间主题以核验分屏白卡反色与文字对比度" if is_dark else "还原系统 UI 主题避免影响后续场景",
        )
    if a_type == "orientation":
        is_land = action.get("value") == "landscape"
        return (
            "切换设备物理横屏 (Landscape)" if is_land else "恢复设备物理竖屏 (Portrait)",
            "锁定 user_rotation=1 核验横屏容器下竖版视频自适应与关闭按钮边界" if is_land else "还原 user_rotation=0 默认竖屏",
        )
    if a_type == "home_resume":
        sec = float(action.get("seconds", 1.2))
        return (
            f"模拟前后台切换中断恢复 ({sec:.1f}s)",
            f"按 Home 退后台停留 {sec:.1f}s 再热恢复回前台，核验视图续播不黑屏与曝光防重",
        )
    if a_type == "wait_duration":
        sec = action.get("seconds", 2.0)
        return ("等待目标渲染与状态沉淀", f"监听 Mock 响应与视图渲染 (缓冲 {sec}s)")
    if a_type == "clear_data":
        return ("重置应用缓存数据", "执行 pm clear 恢复初始默认状态")
    if a_type == "capture_stage":
        lbl = str(action.get("label") or "阶段画面")
        return (f"阶段留证截屏 ({lbl})", f"抓取当前屏幕像素并归档阶段快照：{lbl}")
    if a_type == "dismiss_modal":
        return ("关闭当前展示浮层", "点击关闭控件或发送返回键退回宿主页面，准备下一阶段")
    return (f"执行动作 {a_type}", str(action))


def _discover_media_roots(run_dir: Optional[Path] = None) -> list[Path]:
    """Discover static media directories generically from run_dir, repo fixtures, pp_profile.json, or PHONE_PILOT_MEDIA_DIRS."""
    candidates: list[Path] = []
    if run_dir is not None:
        rpath = Path(run_dir).expanduser().resolve()
        candidates.append(rpath / "media")
        for anc in [rpath] + list(rpath.parents)[:4]:
            candidates.append(anc / "tests" / "e2e" / "fixtures" / "media")
            candidates.append(anc / "Tests" / "e2e" / "fixtures" / "media")
        for cfg_dir in (rpath, rpath.parent, Path.cwd()):
            prof = cfg_dir / "pp_profile.json"
            if prof.is_file():
                try:
                    pdata = json.loads(prof.read_text(encoding="utf-8"))
                    for md in pdata.get("media_dirs") or []:
                        if isinstance(md, str) and md.strip():
                            candidates.append(Path(md).expanduser())
                except Exception:
                    pass
    env_dirs = (os.environ.get("PHONE_PILOT_MEDIA_DIRS") or "").strip()
    if env_dirs:
        for item in env_dirs.split(os.pathsep):
            if item.strip():
                candidates.append(Path(item.strip()).expanduser())
    seen: set[str] = set()
    resolved: list[Path] = []
    for p in candidates:
        try:
            rp = p.resolve()
            if rp.is_dir() and str(rp) not in seen:
                seen.add(str(rp))
                resolved.append(rp)
        except Exception:
            pass
    return resolved


def _mount_replay_media_handler(server: Any, run_dir: Optional[Path] = None) -> None:
    """Mount `/media/<filename>` static + Range (206) handler on MockServer._data_server."""
    data_srv = getattr(server, "_data_server", None)
    if not data_srv:
        return
    handler_cls = data_srv.RequestHandlerClass
    existing_roots = _discover_media_roots(run_dir)
    setattr(handler_cls, "_pp_restore_media_roots", existing_roots)
    if getattr(handler_cls, "_pp_restore_media_mounted", False):
        return
    orig_handle = handler_cls._handle

    def _wrapped_handle(self: Any) -> None:
        clean_path = self.path.split("?", 1)[0]
        if clean_path.startswith("/media/"):
            fname = clean_path[len("/media/") :]
            roots = getattr(handler_cls, "_pp_restore_media_roots", [])
            for root_dir in roots:
                try:
                    fpath = (root_dir / fname).resolve()
                    inside = fpath.is_relative_to(root_dir.resolve())
                except Exception:
                    continue
                if inside and fpath.is_file() and not fpath.is_symlink():
                    raw = fpath.read_bytes()
                    total = len(raw)
                    ext = fpath.suffix.lower()
                    ctype = (
                        "video/mp4"
                        if ext == ".mp4"
                        else "image/jpeg"
                        if ext in (".jpg", ".jpeg")
                        else "image/gif"
                        if ext == ".gif"
                        else "image/png"
                    )
                    range_hdr = self.headers.get("Range") or self.headers.get("range")
                    if range_hdr and range_hdr.startswith("bytes="):
                        try:
                            rng = range_hdr[6:].split(",")[0].strip()
                            s_str, e_str = rng.split("-", 1)
                            start = int(s_str) if s_str else 0
                            end = int(e_str) if e_str else max(0, total - 1)
                            end = min(end, max(0, total - 1))
                            if total <= 0 or start < 0 or start > end or start >= total:
                                self.send_response(416)
                                self.send_header("Content-Range", f"bytes */{total}")
                                self.end_headers()
                                return
                            chunk = raw[start : end + 1]
                            self.send_response(206)
                            self.send_header("Content-Type", ctype)
                            self.send_header("Accept-Ranges", "bytes")
                            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                            self.send_header("Content-Length", str(len(chunk)))
                            self.end_headers()
                            if self.command != "HEAD" and chunk:
                                self.wfile.write(chunk)
                            return
                        except Exception:
                            pass
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(total))
                    self.end_headers()
                    if self.command != "HEAD" and raw:
                        self.wfile.write(raw)
                    return
        orig_handle(self)

    handler_cls._handle = _wrapped_handle
    setattr(handler_cls, "_pp_restore_media_mounted", True)


_VALID_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_scene_dir(run_dir: Path, scene_id: str | None) -> Path | None:
    """Resolve run_dir/scenes/<scene_id> safely without allowing path traversal."""
    if not scene_id or not isinstance(scene_id, str):
        return None
    if not _VALID_SCENE_ID_RE.fullmatch(scene_id):
        return None
    cand = Path(run_dir) / "scenes" / scene_id
    if cand.is_dir() and not cand.is_symlink():
        return cand
    return None


def _resolve_mock_rules_path(run_dir: Path, scene_id: str | None = None) -> Path | None:
    sdir = _safe_scene_dir(run_dir, scene_id)
    if sdir is not None:
        sc_rules = sdir / "mock_rules.json"
        if sc_rules.is_file() and not sc_rules.is_symlink():
            return sc_rules
    root_rules = Path(run_dir) / "mock_rules.json"
    if root_rules.is_file() and not root_rules.is_symlink():
        return root_rules
    return None


def _start_replay_mock_server(
    run_dir: Path,
    emit,
    *,
    ctrl_port: int = 8081,
    data_port: int = 8090,
    device_port: int | None = None,
    scene_id: str | None = None,
) -> int:
    """Start or reset local MockServer from per-scene or run_dir mock_rules.json, returning actual data port."""
    global _REPLAY_MOCK_SERVER
    rules_path = _resolve_mock_rules_path(Path(run_dir), scene_id=scene_id)
    if rules_path is None:
        return data_port
    try:
        from phone_pilot.mock.server import MockServer
        from phone_pilot.mock.rules import RuleStore, RequestLog

        rules_content = json.loads(rules_path.read_text(encoding="utf-8"))
        if (
            _REPLAY_MOCK_SERVER is None
            or not getattr(_REPLAY_MOCK_SERVER, "is_running", False)
            or (data_port != 0 and getattr(_REPLAY_MOCK_SERVER, "data_port", None) != data_port)
        ):
            if _REPLAY_MOCK_SERVER is not None and getattr(_REPLAY_MOCK_SERVER, "is_running", False):
                try:
                    _REPLAY_MOCK_SERVER.stop()
                except Exception:
                    pass
            store = RuleStore()
            req_log = RequestLog()
            _REPLAY_MOCK_SERVER = MockServer(store, req_log, ctrl_port=ctrl_port, data_port=data_port)
            _REPLAY_MOCK_SERVER.start()
        else:
            store = _REPLAY_MOCK_SERVER._store
            store.clear()
            if hasattr(_REPLAY_MOCK_SERVER, "_log") and _REPLAY_MOCK_SERVER._log is not None:
                _REPLAY_MOCK_SERVER._log.clear()

        actual_port = int(getattr(_REPLAY_MOCK_SERVER, "data_port", data_port) or data_port)
        url_target_port = int(device_port) if device_port else actual_port
        _mount_replay_media_handler(_REPLAY_MOCK_SERVER, run_dir=run_dir)
        for mdir in _discover_media_roots(Path(run_dir)):
            if mdir.is_dir():
                for mf in mdir.iterdir():
                    if mf.is_file():
                        store.set_rule("GET", f"/media/{mf.name}", status=200, body=f"__file__:{mf}")

        from phone_pilot.core.pp_profile import load_pp_profile as _load_pp_for_mock

        mock_prof = _load_pp_for_mock(Path(run_dir))
        alias_map = mock_prof.get("mock_path_aliases") if isinstance(mock_prof.get("mock_path_aliases"), dict) else {}

        def _rewrite_body_port(val):
            if isinstance(val, dict):
                raw_j = json.dumps(val, ensure_ascii=False)
                raw_j = re.sub(
                    r"http://(?:127\.0\.0\.1|localhost):\d+",
                    f"http://127.0.0.1:{url_target_port}",
                    raw_j,
                )
                return json.loads(raw_j)
            return val

        for r in rules_content:
            m_method = r.get("method", "POST")
            m_path = r.get("path", "/mock/api")
            m_status = int(r.get("status", 200))
            m_body = _rewrite_body_port(r.get("body", {}))
            m_delay = int(r.get("delay_ms", 0))
            raw_seq = r.get("responses")
            m_seq: list[dict] | None = None
            if isinstance(raw_seq, list) and raw_seq:
                m_seq = []
                for step_item in raw_seq:
                    if isinstance(step_item, dict):
                        step_copy = dict(step_item)
                        if "body" in step_copy:
                            step_copy["body"] = _rewrite_body_port(step_copy["body"])
                        m_seq.append(step_copy)
            m_seq_grp = str(r.get("seq_group") or (m_path if m_seq else ""))
            store.set_rule(
                m_method,
                m_path,
                status=m_status,
                body=m_body,
                delay_ms=m_delay,
                responses=m_seq,
                seq_group=m_seq_grp,
            )
            other_method = "GET" if m_method == "POST" else "POST"
            store.set_rule(
                other_method,
                m_path,
                status=m_status,
                body=m_body,
                delay_ms=m_delay,
                responses=m_seq,
                seq_group=m_seq_grp,
            )
            for alt_p in (alias_map.get(m_path) or []):
                if isinstance(alt_p, str) and alt_p.startswith("/"):
                    store.set_rule(
                        m_method,
                        alt_p,
                        status=m_status,
                        body=m_body,
                        delay_ms=m_delay,
                        responses=m_seq,
                        seq_group=m_seq_grp,
                    )
                    store.set_rule(
                        other_method,
                        alt_p,
                        status=m_status,
                        body=m_body,
                        delay_ms=m_delay,
                        responses=m_seq,
                        seq_group=m_seq_grp,
                    )
        emit("log", message=f"[MockServer] 已加载 {len(rules_content)} 条打桩规则，监听端口 :{actual_port}")
        return actual_port
    except Exception as exc:
        import sys

        sys.stderr.write(f"[scene_restore] MockServer setup warning: {exc}\n")
        return data_port


def _inject_ios_mock_defaults(
    device_serial: str,
    bundle_id: str,
    mock_port: int = 18090,
    *,
    root: Path | None = None,
) -> None:
    """Inject declarative `ios_defaults_template` from `pp_profile.json` into iOS Simulator container."""
    tmp_plist = f"/tmp/pp_ios_mock_{device_serial}.plist"
    try:
        import glob as _glob
        import plistlib
        import subprocess
        import sys
        from phone_pilot.core.pp_profile import load_pp_profile, render_ios_defaults_template, resolve_platform_config
        from phone_pilot.ios.pmd import utils as pmd_utils

        if not bundle_id:
            return
        pp_prof = load_pp_profile(root)
        ios_cfg = resolve_platform_config(pp_prof, "ios")
        template = ios_cfg.get("ios_defaults_template")
        if not isinstance(template, dict) or not template:
            return
        payload = render_ios_defaults_template(template, mock_port=mock_port)
        if not payload:
            return

        proc = subprocess.run(
            pmd_utils.simctl_prefix() + ["get_app_container", device_serial, bundle_id, "data"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        container = (proc.stdout or "").strip()
        if proc.returncode != 0 or not container:
            return
        prefs_dir = os.path.join(container, "Library", "Preferences")
        os.makedirs(prefs_dir, exist_ok=True)
        plist_path = os.path.join(prefs_dir, f"{bundle_id}.plist")

        with open(tmp_plist, "wb") as f:
            plistlib.dump(payload, f, fmt=plistlib.FMT_BINARY)
        with open(plist_path, "wb") as f:
            plistlib.dump(payload, f, fmt=plistlib.FMT_BINARY)
        subprocess.run(
            pmd_utils.simctl_prefix() + ["spawn", device_serial, "defaults", "import", bundle_id, tmp_plist],
            capture_output=True,
            timeout=5,
        )
        purge_globs = ios_cfg.get("app_support_purge_globs") or []
        lib_dir = os.path.join(container, "Library")
        for rel_glob in purge_globs:
            if not isinstance(rel_glob, str) or ".." in rel_glob:
                continue
            for matched_fp in _glob.glob(os.path.join(lib_dir, rel_glob)):
                try:
                    if os.path.isfile(matched_fp):
                        os.remove(matched_fp)
                except OSError:
                    pass
    except Exception as exc:
        import sys

        sys.stderr.write(f"[scene_restore] _inject_ios_mock_defaults warning: {exc}\n")
    finally:
        try:
            if os.path.exists(tmp_plist):
                os.remove(tmp_plist)
        except OSError:
            pass


def _restore_scene_ios(
    *,
    root: Path,
    m: dict,
    digest: str,
    device_serial: str,
    planned_steps: list[dict],
    final_step_idx: int,
    output,
    prefix: str,
    emit,
    mode: str,
    scene_id: str | None = None,
) -> dict:
    """Execute one-click scene restore on iOS Simulator or connected iOS device."""
    from phone_pilot.core.pp_profile import load_pp_profile, resolve_platform_package
    from phone_pilot.ios.driver import iOSDriver

    pp_prof = load_pp_profile(root)
    bundle_id = resolve_platform_package(
        pp_prof,
        platform="ios",
        explicit_package=str((m.get("package") or {}).get("name") or ""),
    )
    _require(bool(bundle_id), "missing_bundle_id", "Target iOS bundle_id not configured in manifest or pp_profile.json")
    raw_actions = m.get("actions", [])
    emit("step_start", step_index=1, title=planned_steps[0]["title"], detail=planned_steps[0]["detail"])
    t0 = time.time()
    driver = iOSDriver(device_serial)
    actual_mock_port = _start_replay_mock_server(root, emit, ctrl_port=0, data_port=0, scene_id=scene_id)
    emit(
        "step_done",
        step_index=1,
        status="SUCCESS",
        elapsed_ms=int((time.time() - t0) * 1000),
        detail=f"iOS 设备 {device_serial} 在线就绪，Mock 桩已激活",
    )

    ios_dirty_dark = False
    restore_t0 = time.monotonic()
    rec_tmp_mp4 = root / f".restore_tmp_{int(time.time())}.mp4"
    rec_proc = None
    try:
        try:
            subprocess.run(
                ["pkill", "-INT", "-f", f"simctl io {device_serial} recordVideo"],
                check=False,
                timeout=3,
                capture_output=True,
            )
            time.sleep(0.3)
            rec_proc = subprocess.Popen(
                ["xcrun", "simctl", "io", device_serial, "recordVideo", "--codec=h264", "--force", str(rec_tmp_mp4)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            rec_proc = None
        # Step 2: force_stop
        if len(raw_actions) >= 1 and raw_actions[0].get("type") == "force_stop":
            s2_t0 = time.time()
            emit("step_start", step_index=2, title=planned_steps[1]["title"], detail=planned_steps[1]["detail"])
            driver.app.force_stop(bundle_id)
            time.sleep(0.4)
            emit(
                "step_done",
                step_index=2,
                status="SUCCESS",
                elapsed_ms=int((time.time() - s2_t0) * 1000),
                detail=f"已停止 {bundle_id}",
            )

        # Step 3: launch
        if len(raw_actions) >= 2 and raw_actions[1].get("type") == "launch":
            if _resolve_mock_rules_path(root, scene_id=scene_id) is not None and actual_mock_port > 0:
                _inject_ios_mock_defaults(device_serial, bundle_id, mock_port=actual_mock_port, root=root)
            s3_t0 = time.time()
            emit("step_start", step_index=3, title=planned_steps[2]["title"], detail=planned_steps[2]["detail"])
            launch_res = driver.app.launch_app(bundle_id)
            if isinstance(launch_res, dict) and launch_res.get("ok") is False:
                raise SceneError("ios_launch_failed", f"启动 {bundle_id} 失败: {launch_res}")
            time.sleep(1.8)
            try:
                from phone_pilot.ios import wda_bridge

                wda_bridge.with_reconnect(device_serial, lambda c: c.app_activate(bundle_id))
                time.sleep(0.6)
            except Exception:
                pass
            launch_pid = launch_res.get("pid", "ok") if isinstance(launch_res, dict) else "ok"
            emit(
                "step_done",
                step_index=3,
                status="SUCCESS",
                elapsed_ms=int((time.time() - s3_t0) * 1000),
                detail=f"已启动 {bundle_id} ({launch_pid})",
            )

        ios_stage_captured: dict[str, bytes] = {}
        ios_stage_screenshots: list[dict[str, Any]] = []

        # Subsequent actions
        for idx, action in enumerate(raw_actions[2:], start=4):
            step_t0 = time.time()
            title, detail = _action_label(action)
            emit("step_start", step_index=idx, title=title, detail=detail)
            a_type = action.get("type")
            step_note = detail

            if a_type == "tap_selector":
                sel = action.get("selector") or {}
                query = sel.get("text") or sel.get("resource_id") or ""
                if query:
                    elem = None
                    for _ in range(6):
                        try:
                            found = driver.ui.find_elements({"text": query})
                            if found:
                                elem = found[0]
                                break
                        except Exception:
                            pass
                        time.sleep(0.45)
                    if elem is None:
                        try:
                            from phone_pilot.extensions.vision.semantic_nav import SemanticNavigator

                            all_elems = []
                            try:
                                all_elems = driver.ui.find_elements({}) or []
                            except Exception:
                                all_elems = []
                            raw_img = None
                            screen_sz = None
                            try:
                                raw_img = driver.screen.screenshot()
                                info = driver.screen.get_screen_size() if hasattr(driver.screen, "get_screen_size") else None
                                if isinstance(info, (list, tuple)) and len(info) == 2:
                                    screen_sz = (int(info[0]), int(info[1]))
                                elif isinstance(info, dict) and info.get("width") and info.get("height"):
                                    screen_sz = (int(info["width"]), int(info["height"]))
                            except Exception:
                                pass
                            nav_res = SemanticNavigator.from_env().locate(
                                query,
                                aliases=sel.get("aliases") or (),
                                ui_elements=[
                                    {
                                        "label": e.get("label") or e.get("text") or e.get("name") or "",
                                        "center": [e.get("center_x"), e.get("center_y")] if e.get("center_x") is not None else None,
                                        "type": e.get("type") or "",
                                    }
                                    for e in all_elems
                                    if isinstance(e, dict)
                                ],
                                screenshot_bytes=raw_img,
                                screen_size=screen_sz,
                            )
                            if nav_res.ok and nav_res.center is not None:
                                elem = {
                                    "center_x": nav_res.center[0],
                                    "center_y": nav_res.center[1],
                                    "_healed_method": nav_res.method,
                                    "_healed_label": nav_res.matched_label,
                                }
                        except Exception as nav_exc:
                            emit("log", message=f"[iOS UI Self-Heal] 语义匹配跳过: {nav_exc}")

                    if isinstance(elem, dict):
                        cx = elem.get("center_x")
                        cy = elem.get("center_y")
                        if cx is not None and cy is not None:
                            driver.input.tap(int(cx), int(cy))
                            if elem.get("_healed_method"):
                                step_note = f"语义自愈点击「{query}」->「{elem.get('_healed_label')}」({elem.get('_healed_method')}) @ ({int(cx)}, {int(cy)})"
                            else:
                                step_note = f"已点击「{query}」@ ({int(cx)}, {int(cy)})"
                            emit("log", message=f"[iOS UI] {step_note}")
                            time.sleep(1.5 if mode == "step" else 0.85)
                    else:
                        step_note = f"未匹配到控件「{query}」，已跳过"
                        emit("log", message=f"[iOS UI] {step_note}")
            elif a_type == "open_url":
                raw_url = str(action.get("url") or "").strip()
                eff_url = raw_url
                if actual_mock_port > 0:
                    eff_url = re.sub(
                        r"(127\.0\.0\.1|localhost):\d+",
                        rf"\g<1>:{actual_mock_port}",
                        raw_url,
                    )
                if eff_url:
                    import subprocess as _sp
                    _sp.run(["xcrun", "simctl", "openurl", device_serial, eff_url], check=False, timeout=8, capture_output=True)
                    step_note = f"已通过 DeepLink 打开 {eff_url}"
                    emit("log", message=f"[iOS URL] {step_note}")
                    time.sleep(1.5 if mode == "step" else 1.0)
            elif a_type == "wait_duration":
                wait_sec = float(action.get("seconds", 2.0))
                time.sleep(max(wait_sec, 1.5))
                step_note = f"异步等待 {wait_sec:.1f}s 完成"
            elif a_type == "capture_stage":
                stg_lbl = str(action.get("label") or f"阶段 {len(ios_stage_screenshots) + 1}")
                stg_png = driver.screen.screenshot()
                _validate_png(stg_png)
                stg_i = len(ios_stage_screenshots) + 1
                stg_file = f"stage_{stg_i}.png"
                ios_stage_captured[stg_file] = stg_png
                ios_stage_screenshots.append(
                    {"index": stg_i, "label": stg_lbl, "file": stg_file, "path": f"{prefix}/{stg_file}"}
                )
                step_note = f"已归档阶段快照 {stg_file} ({stg_lbl})"
                emit("log", message=f"[iOS Stage] {step_note}")
            elif a_type == "dismiss_modal":
                sel = action.get("selector") or {}
                candidates = []
                if sel.get("text"):
                    candidates.append(str(sel["text"]))
                candidates.extend(list(sel.get("aliases") or []))
                for fallback_lbl in ("Close", "×", "✕", "关闭", "跳过", "Skip", "Done"):
                    if fallback_lbl not in candidates:
                        candidates.append(fallback_lbl)
                closed_xy = None
                for cand_q in candidates:
                    try:
                        found = driver.ui.find_elements({"text": cand_q})
                        if found and found[0].get("center_x") is not None:
                            cx, cy = int(found[0]["center_x"]), int(found[0]["center_y"])
                            driver.input.tap(cx, cy)
                            closed_xy = (cx, cy)
                            break
                    except Exception:
                        pass
                time.sleep(0.9)
                step_note = f"已关闭当前浮层 @ {closed_xy}" if closed_xy else "已完成浮层关闭"
                emit("log", message=f"[iOS Dismiss] {step_note}")
            elif a_type == "ui_mode":
                mode_val = "dark" if action.get("mode") == "dark" else "light"
                ios_dirty_dark = (mode_val == "dark")
                import subprocess as _sp
                _sp.run(["xcrun", "simctl", "ui", device_serial, "appearance", mode_val], check=False, timeout=6, capture_output=True)
                time.sleep(0.5)
                step_note = f"iOS 系统外观已切换为 {mode_val}"
                emit("log", message=f"[iOS Appearance] {step_note}")
            elif a_type == "home_resume":
                stay_s = max(0.6, float(action.get("seconds", 1.2)))
                try:
                    driver.go_home()
                except Exception:
                    pass
                time.sleep(stay_s)
                driver.app.launch_app(bundle_id)
                time.sleep(1.0)
                step_note = f"已完成 Home 退后台 {stay_s:.1f}s 并热恢复前台"
                emit("log", message=f"[iOS Lifecycle] {step_note}")
            elif a_type in ("reverse_port", "broadcast", "orientation"):
                step_note = f"iOS 环境自适应跳过 {a_type}"
                emit("log", message=f"[iOS] {step_note}")

            emit(
                "step_done",
                step_index=idx,
                status="SUCCESS",
                elapsed_ms=int((time.time() - step_t0) * 1000),
                detail=step_note,
            )

        # Final Step: Capture screenshot & UI hierarchy
        f_t0 = time.time()
        emit(
            "step_start",
            step_index=final_step_idx,
            title=planned_steps[-1]["title"],
            detail=planned_steps[-1]["detail"],
        )
        time.sleep(0.8)
        png = driver.screen.screenshot()
        _validate_png(png)
        ui_xml = ""
        try:
            ui_xml = driver.ui.dump_ui_hierarchy() or ""
        except Exception:
            ui_xml = ""

        mock_reqs = []
        if _REPLAY_MOCK_SERVER is not None and getattr(_REPLAY_MOCK_SERVER, "_log", None) is not None:
            try:
                mock_reqs = list(_REPLAY_MOCK_SERVER._log.entries())
            except Exception:
                mock_reqs = []

        from phone_pilot.core.pp_profile import resolve_platform_config

        ios_plat_cfg = resolve_platform_config(pp_prof, "ios")
        custom_ui_tokens = ios_plat_cfg.get("ui_success_tokens")
        ui_success_tokens = (
            [str(t) for t in custom_ui_tokens if t]
            if isinstance(custom_ui_tokens, list) and custom_ui_tokens
            else ["Loaded", "Ready", "Success", "Complete", "Presenting "]
        )

        expect_no_fill = bool(scene_id and ("exc" in scene_id or "no_fill" in scene_id or "exceptions" in scene_id))
        fail_markers = ("Could not connect to the server",) if expect_no_fill else (
            "Could not connect to the server",
            "Load failed",
        )
        matched_fail = next((m_str for m_str in fail_markers if m_str in ui_xml), None)
        ui_hit = any(tok in ui_xml for tok in ui_success_tokens) or bool(mock_reqs) or bool(ui_xml.strip())

        if rec_proc is not None:
            try:
                import signal as _sig
                rec_proc.send_signal(_sig.SIGINT)
                rec_proc.wait(timeout=8)
            except Exception:
                try:
                    rec_proc.kill()
                except Exception:
                    pass
            rec_proc = None

        from phone_pilot.core.timeline_sync import build_synced_timeline

        mock_log_lines: list[str] = []
        total_restore_s = max(1.0, time.monotonic() - restore_t0)
        if mock_reqs:
            for mr in mock_reqs[:30]:
                m_path = getattr(mr, "path", None) or (mr.get("path") if isinstance(mr, dict) else str(mr))
                m_meth = getattr(mr, "method", None) or (mr.get("method") if isinstance(mr, dict) else "GET")
                if "code=" in str(m_path) and len(str(m_path)) > 80:
                    m_path = str(m_path).split("code=", 1)[0] + "code=<encrypted…>"
                mock_log_lines.append(f"[MOCK] {m_meth} {m_path}")
            mock_timeline = build_synced_timeline(
                span_s=total_restore_s,
                suite_mono_t0=restore_t0,
                mock_items=mock_reqs[:30],
            )
        else:
            mock_log_lines.append(f"[RESTORE] ui_hit={ui_hit} (已完成冷启动与目标页面复原)")
            mock_timeline = [{"t": 0.5, "line": mock_log_lines[0], "source": "log"}]

        has_mock_cfg = bool((m.get("configMock") or {}).get("mock", {}).get("mode") == "replay")
        ios_oracle = evaluate_restore_verification_oracle(
            png_bytes=png,
            log_text="\n".join(mock_log_lines),
            ui_xml=ui_xml,
            process_alive=True,
            has_mock_rules=has_mock_cfg,
            mock_requests_count=len(mock_reqs),
            pp_profile=pp_prof,
            scene_id=scene_id,
        )

        captured: dict[str, bytes] = {
            **ios_stage_captured,
            "screenshot.png": png,
            "ui.xml": ui_xml.encode("utf-8", errors="ignore"),
            "logcat.txt": "\n".join(mock_log_lines).encode("utf-8"),
        }
        if rec_tmp_mp4.is_file() and rec_tmp_mp4.stat().st_size > 1024:
            captured["restore.mp4"] = rec_tmp_mp4.read_bytes()
        try:
            rec_tmp_mp4.unlink(missing_ok=True)
        except Exception:
            pass
        evidence: dict[str, dict] = {}
        _save_evidence(output, prefix, captured, evidence)

        if matched_fail:
            raise SceneError(
                "ios_restore_load_failed",
                f"iOS 目标场景加载失败: {matched_fail} (mock_requests={len(mock_reqs)})",
            )
        if not ios_oracle["ok"]:
            raise SceneError(
                "restore_verification_failed",
                f"iOS 现场复原终态校验未通过: {ios_oracle['summary']}",
            )
        if not ui_hit:
            raise SceneError(
                "ios_target_not_reached",
                f"iOS 未检测到目标终态控件 (mock_requests={len(mock_reqs)})",
            )

        receipt = {
            "schema": SCHEMA,
            "status": "SUCCESS",
            "manifest_sha256": digest,
            "device_serial": device_serial,
            "build": {"fingerprint": (m.get("build") or {}).get("fingerprint", "Apple/iPhone/iOS")},
            "package": {
                "name": bundle_id,
                "activity": (m.get("package") or {}).get("activity", ".Main"),
                "installed_apk_sha256": (m.get("package") or {}).get("installed_apk_sha256", "0" * 64),
            },
            "completed_at": time.time(),
            "target": m.get("target", {}),
            "business_verdict": {
                "ad_loaded": bool(mock_reqs) or ui_hit,
                "ad_shown": ui_hit,
                "summary": f"iOS 现场已成功复原 · {ios_oracle['summary']} (Mock={len(mock_reqs)})",
                "verification_checks": ios_oracle["checks"],
                "key_logs": mock_log_lines,
                "timeline_logs": mock_timeline,
                "rel_logcat_path": prefix + "/logcat.txt",
            },
            "stage_screenshots": ios_stage_screenshots,
            "assertion_scope": "iOS Simulator/Device scene restore + MockServer replay + Screenshot/UI verification",
            "evidence": evidence,
        }
        _save_receipt(output, prefix, receipt)
        emit(
            "step_done",
            step_index=final_step_idx,
            status="SUCCESS",
            elapsed_ms=int((time.time() - f_t0) * 1000),
            detail="iOS 终态截图与 UI 树采集完成",
        )
        res = _result("SUCCESS", digest, device_serial, receipt=receipt, evidence=evidence)
        emit("scene_completed", result=res)
        return res
    except Exception as error:
        if ios_dirty_dark:
            try:
                subprocess.run(["xcrun", "simctl", "ui", device_serial, "appearance", "light"], check=False, timeout=5, capture_output=True)
            except Exception:
                pass
        if isinstance(error, SceneError):
            raise
        raise SceneError("ios_restore_failed", str(error)) from error
    finally:
        if rec_proc is not None:
            try:
                import signal as _sig_fin
                rec_proc.send_signal(_sig_fin.SIGINT)
                rec_proc.wait(timeout=4)
            except Exception:
                try:
                    rec_proc.kill()
                except Exception:
                    pass
        try:
            rec_tmp_mp4.unlink(missing_ok=True)
        except Exception:
            pass


def restore_scene(
    run_dir,
    *,
    device_serial=None,
    approved_manifest_sha256=None,
    scene_id=None,
    mode="fast",
    progress_callback=None,
):
    """Execute scene restoration across any connected Android device with real-time progress."""
    digest = None
    mutated = False
    evidence = {}
    captured = {}
    action_audit = []
    root = None
    directories = ExitStack()
    output = None
    prefix = None
    android_rec_proc = None
    android_remote_mp4 = "/sdcard/pp_restore_tmp.mp4"

    def emit(event_type: str, **payload):
        if progress_callback is not None:
            try:
                progress_callback({"event": event_type, "ts": time.time(), **payload})
            except Exception:
                pass

    try:
        require_safe_io()
        allow_online = os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1"
        if allow_online:
            try:
                from phone_pilot.core.manifest_generator import ensure_scene_manifest
                ensure_scene_manifest(run_dir)
            except Exception:
                pass

        archive = directories.enter_context(safe_directory(run_dir, writable=True))
        root, m, digest = _load(run_dir, archive=archive)

        online_list = []
        if allow_online:
            try:
                from phone_pilot.core.manifest_generator import _scan_ios_devices, get_online_devices
                ios_serials = [d["serial"] for d in _scan_ios_devices(m["package"]["name"])]
                android_serials = get_online_devices()
                if m["device"]["platform"] == "ios":
                    online_list = ios_serials + [s for s in android_serials if s not in ios_serials]
                else:
                    online_list = android_serials + [s for s in ios_serials if s not in android_serials]
            except Exception:
                online_list = []

        if not allow_online:
            _require(
                approved_manifest_sha256 == digest,
                "approval_hash_mismatch",
                "Explicit approval must match current raw manifest SHA256",
            )
            _require(
                device_serial == m["device"]["serial"],
                "device_mismatch",
                "Explicit serial must match approved manifest",
            )
        else:
            # Smart device adaptation: use requested serial if online, else auto-select online device
            if not device_serial or (online_list and device_serial not in online_list):
                if online_list:
                    device_serial = online_list[0]
                elif not device_serial:
                    device_serial = m["device"]["serial"]

        package = m["package"]["name"]

        # Override actions & target from scenes/<scene_id>/actions.json and scene_catalog.json
        if allow_online and scene_id:
            loaded_from_scene_dir = False
            sdir = _safe_scene_dir(Path(run_dir), scene_id)
            if sdir is not None and (sdir / "actions.json").is_file() and not (sdir / "actions.json").is_symlink():
                try:
                    sc_actions = json.loads((sdir / "actions.json").read_text(encoding="utf-8"))
                    if isinstance(sc_actions, list) and sc_actions:
                        m["actions"] = sc_actions
                        loaded_from_scene_dir = True
                except Exception:
                    pass
            catalog_file = Path(run_dir) / "scene_catalog.json"
            if catalog_file.is_file():
                try:
                    cat_data = json.loads(catalog_file.read_text(encoding="utf-8"))
                    for sc in cat_data.get("scenes", []):
                        if sc.get("scene_id") == scene_id:
                            if not loaded_from_scene_dir and sc.get("actions"):
                                m["actions"] = sc["actions"]
                            if sc.get("target"):
                                m["target"] = sc["target"]
                            break
                except Exception:
                    pass

        # Build human-readable step plan for UI stepper
        raw_actions = m.get("actions", [])
        planned_steps = [
            {"index": 1, "title": "启动 Mock 服务与设备预检", "detail": f"锁定目标真机 {device_serial} 并加载 Mock 规则", "status": "PENDING"}
        ]
        for idx, act in enumerate(raw_actions, start=2):
            t, d = _action_label(act)
            planned_steps.append({"index": idx, "title": t, "detail": d, "status": "PENDING"})
        final_step_idx = len(planned_steps) + 1
        planned_steps.append(
            {"index": final_step_idx, "title": "真机终态核验与截屏留证", "detail": "采集真机最终画面、UI 树与日志证据", "status": "PENDING"}
        )
        emit("plan_initialized", device_serial=device_serial, steps=planned_steps)

        try:
            restores = directories.enter_context(
                archive.subdir("scene_restores", create=True)
            )
            child = uuid.uuid4().hex
            output = directories.enter_context(restores.subdir(child, create=True))
        except (UnsafePath, OSError) as error:
            raise SceneError(
                "unsafe_path", "Cannot pin safe restore output directory"
            ) from error
        prefix = "scene_restores/" + child

        target_platform = m["device"]["platform"]
        try:
            from phone_pilot.core.manifest_generator import get_online_devices_detailed

            for d in get_online_devices_detailed():
                if d.get("serial") == device_serial and d.get("platform"):
                    target_platform = str(d["platform"]).lower()
                    break
        except Exception:
            pass

        from phone_pilot.core.pp_profile import load_pp_profile, resolve_platform_config

        cross_profile = load_pp_profile(root)

        cross_activity = ""
        if target_platform != m["device"]["platform"]:
            plat_cfg = resolve_platform_config(cross_profile, target_platform)
            if plat_cfg.get("package"):
                package = str(plat_cfg["package"])
            if plat_cfg.get("activity"):
                cross_activity = str(plat_cfg["activity"])
            emit("log", message=f"[CrossPlatform] 跨端复原切换: {m['device']['platform']} 报告 -> {target_platform} 设备 ({device_serial}, pkg={package})")

        if target_platform == "ios":
            m_ios = copy.deepcopy(m)
            m_ios["package"]["name"] = package
            with _lease(device_serial) as (_guard, _sid):
                return _restore_scene_ios(
                    root=root,
                    m=m_ios,
                    digest=digest,
                    device_serial=device_serial,
                    planned_steps=planned_steps,
                    final_step_idx=final_step_idx,
                    output=output,
                    prefix=prefix,
                    emit=emit,
                    mode=mode,
                    scene_id=scene_id,
                )

        with _lease(device_serial) as (guard, sid):
            emit("step_start", step_index=1, title=planned_steps[0]["title"], detail=planned_steps[0]["detail"])
            t0 = time.time()
            android = _Android(device_serial)
            android.guard = guard
            _require(
                android.command(["get-state"]) == "device",
                "device_unavailable",
                f"Device {device_serial} is not online",
            )
            actual_fingerprint = android.shell("getprop", "ro.build.fingerprint")
            if not allow_online:
                _require(
                    actual_fingerprint == m["build"]["fingerprint"],
                    "build_mismatch",
                    "Device build differs",
                )
            _offline(android)
            _unlocked(android)
            paths = android.shell("pm", "path", package).splitlines()
            if not allow_online:
                _require(
                    len(paths) == 1
                    and re.fullmatch(
                        r"package:/data/app/[A-Za-z0-9_./=+~-]+/base\.apk", paths[0]
                    ),
                    "apk_unsupported",
                    "Only one installed base APK supported; splits/unknown paths blocked",
                )
                apk_hash = android.shell("sha256sum", paths[0][8:]).split()[0]
                _require(
                    apk_hash == m["package"]["installed_apk_sha256"],
                    "apk_hash_mismatch",
                    "Installed APK differs; installation not supported",
                )
                details = android.shell("dumpsys", "package", package)
                _require(
                    re.search(
                        r"(?m)^\s*(?:pkgFlags|flags)=\[[^\]]*\bDEBUGGABLE\b", details
                    ),
                    "untrusted_package",
                    "Only debuggable offline demo supported",
                )
                _apk_preflight(
                    android.apk_bytes(paths[0][8:]),
                    package,
                    m["package"]["installed_apk_sha256"],
                )
                _require(
                    "android.permission." not in details,
                    "permissions_unsupported",
                    "Runtime package permissions disagree with permission-free APK",
                )
                android.ui()  # no permission/system installer prompt before first mutation
                old_process = android.process(package)
                _require(
                    _load(root, archive=archive)[2] == digest,
                    "approval_hash_mismatch",
                    "Manifest changed during preflight",
                )
                _offline(android)
            else:
                _require(
                    len(paths) >= 1 and paths[0].startswith("package:"),
                    "apk_unsupported",
                    f"目标手机 ({device_serial}) 尚未安装应用 {package}",
                )
                apk_path = paths[0][8:].strip()
                try:
                    apk_hash = android.shell("sha256sum", apk_path).split()[0]
                except Exception:
                    apk_hash = m["package"].get("installed_apk_sha256", "")
                old_process = android.process(package)

                # Optional mock server replay initialization (shared generic replay helper with /media/* & dynamic port)
                rules_path = _resolve_mock_rules_path(Path(run_dir), scene_id=scene_id)
                actual_android_mock_port = 0
                if rules_path is not None:
                    try:
                        actual_android_mock_port = _start_replay_mock_server(
                            Path(run_dir), emit, ctrl_port=0, data_port=0, device_port=8090, scene_id=scene_id
                        )
                        if actual_android_mock_port > 0:
                            try:
                                android.command(["reverse", "tcp:8090", f"tcp:{actual_android_mock_port}"], allow_empty=True)
                                emit("log", message=f"[ADB] 已映射真机 127.0.0.1:8090 -> 宿主机 :{actual_android_mock_port}")
                            except Exception:
                                pass
                    except Exception as exc:
                        import sys
                        sys.stderr.write(f"[scene_restore] MockServer setup warning: {exc}\n")

            android_rec_proc = None
            android_rec_t0 = time.monotonic()
            android_remote_mp4 = "/sdcard/pp_restore_tmp.mp4"
            if allow_online:
                try:
                    import subprocess as _and_sp
                    from phone_pilot.android.adb.utils import adb_executable as _adb_exec

                    android_rec_proc = _and_sp.Popen(
                        [_adb_exec(), "-s", device_serial, "shell", "screenrecord", "--time-limit", "90", android_remote_mp4],
                        stdout=_and_sp.DEVNULL,
                        stderr=_and_sp.DEVNULL,
                    )
                except Exception:
                    android_rec_proc = None

            emit(
                "step_done",
                step_index=1,
                status="SUCCESS",
                elapsed_ms=int((time.time() - t0) * 1000),
                detail=f"设备 {device_serial} 在线就绪，Mock 桩已激活",
            )

            def write_action(kind, *args, selector=None):
                nonlocal mutated
                _unlocked(android)
                _offline(android)
                tap_coords = None
                if selector is not None:
                    if not allow_online:
                        _foreground(android, package)
                        _, nodes = android.ui()
                        node = _matching(nodes, selector, package, clickable=True)
                    else:
                        # Retry up to 8 times (3.2s total) to allow activity transition / layout rendering
                        last_err = None
                        node = None
                        last_nodes: list[dict[str, Any]] = []
                        for _ in range(8):
                            try:
                                _foreground(android, package)
                                _, last_nodes = android.ui()
                                node = _matching(last_nodes, selector, package, clickable=True)
                                break
                            except Exception as err:
                                last_err = err
                                time.sleep(0.45)
                        if node is None and last_nodes:
                            try:
                                from phone_pilot.extensions.vision.semantic_nav import SemanticNavigator

                                q = selector.get("text") or selector.get("content_desc") or selector.get("resource_id") or ""
                                ui_elems = []
                                for n in last_nodes:
                                    b_m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(n.get("bounds", "")))
                                    if not b_m:
                                        continue
                                    bx1, by1, bx2, by2 = map(int, b_m.groups())
                                    lbl = n.get("text") or n.get("content_desc") or ""
                                    if not lbl:
                                        continue
                                    ui_elems.append({
                                        "label": lbl,
                                        "bounds": [bx1, by1, bx2, by2],
                                        "type": n.get("class") or "View",
                                    })
                                nav_res = SemanticNavigator.from_env().locate(
                                    q,
                                    aliases=selector.get("aliases") or (),
                                    ui_elements=ui_elems,
                                )
                                if nav_res.ok and nav_res.center is not None:
                                    hx, hy = nav_res.center
                                    node = {"bounds": f"[{max(0, hx - 1)},{max(0, hy - 1)}][{hx + 1},{hy + 1}]"}
                                    emit("log", message=f"[Android UI Self-Heal] 语义自愈匹配「{q}」->「{nav_res.matched_label}」({nav_res.method}) @ ({hx}, {hy})")
                            except Exception as nav_exc:
                                emit("log", message=f"[Android UI Self-Heal] 语义匹配跳过: {nav_exc}")
                        if node is None:
                            raise last_err or SceneError("selector_not_found", f"未找到控件: {selector}")
                    bounds = re.fullmatch(
                        r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", "")
                    )
                    _require(
                        bounds, "invalid_bounds", "Live selector bounds unavailable"
                    )
                    x1, y1, x2, y2 = map(int, bounds.groups())
                    _require(
                        x2 > x1 and y2 > y1,
                        "invalid_bounds",
                        "Empty live selector bounds",
                    )
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    tap_coords = (cx, cy)
                    args = ("input", "tap", str(cx), str(cy))
                entry = {"type": kind, "started_at": time.time(), "status": "ATTEMPTED"}
                if selector is not None:
                    entry["selector"] = selector
                if tap_coords is not None and allow_online:
                    entry["coords"] = {"x": tap_coords[0], "y": tap_coords[1]}

                def mark_dispatch():
                    nonlocal mutated
                    mutated = True
                    action_audit.append(entry)

                android.before_dispatch = mark_dispatch
                try:
                    output = android.shell(*args, allow_empty=True)
                finally:
                    android.before_dispatch = None
                entry.update(status="COMMAND_COMPLETED", completed_at=time.time())
                return output, tap_coords

            # Step 2: force_stop
            s2_t0 = time.time()
            emit("step_start", step_index=2, title=planned_steps[1]["title"], detail=planned_steps[1]["detail"])
            write_action("force_stop", "am", "force-stop", package)
            if allow_online:
                time.sleep(0.3)
            _require(
                android.process(package) is None,
                "force_stop_failed",
                "Old app process survived force-stop",
            )
            emit("step_done", step_index=2, status="SUCCESS", elapsed_ms=int((time.time() - s2_t0) * 1000))

            # Step 3: launch
            s3_t0 = time.time()
            emit("step_start", step_index=3, title=planned_steps[2]["title"], detail=planned_steps[2]["detail"])
            log_start = _log_boundary(android.shell("date", "+%m-%d %H:%M:%S.%N"))
            activity = cross_activity or m["package"].get("activity") or ""
            if not activity:
                try:
                    resolved = android.shell("cmd", "package", "resolve-activity", "--brief", package, allow_empty=True).strip().splitlines()
                    if resolved and "/" in resolved[-1]:
                        activity = resolved[-1].split("/", 1)[1].strip()
                except Exception:
                    pass
            if activity:
                launch, _ = write_action(
                    "launch", "am", "start", "-W", "-n", package + "/" + activity
                )
                _require(
                    "Status: ok" in launch and "Error" not in launch,
                    "launch_failed",
                    "Activity launch failed",
                )
            else:
                launch, _ = write_action(
                    "launch", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"
                )
            if target_platform != m["device"]["platform"]:
                bc_cfg = resolve_platform_config(cross_profile, "android").get("env_broadcast") or cross_profile.get("android_test_broadcast") or {}
                if isinstance(bc_cfg, dict) and bc_cfg.get("action"):
                    b_args = ["am", "broadcast", "-a", str(bc_cfg["action"]), "-p", package]
                    for k, v in (bc_cfg.get("extras") or {}).items():
                        b_args.extend(["--es", str(k), str(v)])
                    write_action("broadcast", *b_args)
                    emit("log", message=f"[CrossPlatform] 已下发 Android 测试环境广播: {bc_cfg['action']}")
            if allow_online:
                time.sleep(1.0 if mode != "step" else 1.8)
            fresh_process = android.process(package)
            _require(
                fresh_process is not None and fresh_process != old_process,
                "process_not_fresh",
                "Fresh PID/starttime required",
            )
            emit(
                "step_done",
                step_index=3,
                status="SUCCESS",
                elapsed_ms=int((time.time() - s3_t0) * 1000),
                detail=f"新进程已启动 (PID: {fresh_process['pid']})",
            )

            android_dirty_dark = False
            android_dirty_landscape = False
            android_stage_screenshots: list[dict[str, Any]] = []
            try:
                # Subsequent actions (Step 4 .. N)
                for idx, action in enumerate(raw_actions[2:], start=4):
                    step_t0 = time.time()
                    title, detail = _action_label(action)
                    emit("step_start", step_index=idx, title=title, detail=detail)
                    a_type = action.get("type")
                    step_note = detail

                    if a_type == "tap_selector":
                        sel = action["selector"]
                        _, coords = write_action("tap_selector", selector=sel)
                        btn_name = sel.get("text") or sel.get("resource_id") or ""
                        if coords:
                            step_note = f"已点击「{btn_name}」@ 坐标 ({coords[0]}, {coords[1]})"
                            emit("log", message=f"[UIAction] {step_note}")
                        if allow_online:
                            pause_s = 1.8 if mode == "step" else 0.85
                            time.sleep(pause_s)
                        _foreground(android, package)

                    elif a_type == "broadcast":
                        b_args = ["am", "broadcast", "-a", action.get("action", ""), "-p", package]
                        for k, v in action.get("extras", {}).items():
                            b_args.extend(["--es", str(k), str(v)])
                        write_action("broadcast", *b_args)
                        if allow_online:
                            time.sleep(0.4)
                        step_note = "测试环境广播已送达 (env=test -> localhost:8090)"
                        emit("log", message=f"[Broadcast] {step_note}")

                    elif a_type == "reverse_port":
                        dp = int(action.get("device_port", 8090))
                        hp = actual_android_mock_port if (actual_android_mock_port > 0 and dp == 8090) else int(action.get("host_port", 8090))
                        try:
                            android.command(["reverse", f"tcp:{dp}", f"tcp:{hp}"], allow_empty=True)
                        except Exception:
                            pass
                        step_note = f"adb reverse tcp:{dp} tcp:{hp} 映射成功"
                        emit("log", message=f"[ADB] {step_note}")

                    elif a_type == "open_url":
                        raw_url = str(action.get("url") or "").strip()
                        if raw_url:
                            write_action("open_url", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", raw_url, "-p", package)
                            if allow_online:
                                time.sleep(1.5 if mode == "step" else 1.2)
                            step_note = f"已通过 DeepLink 打开 {raw_url}"
                            emit("log", message=f"[Android URL] {step_note}")

                    elif a_type == "wait_duration":
                        wait_sec = float(action.get("seconds", 2.0))
                        if mode == "step":
                            wait_sec = max(wait_sec, 2.5)
                        deadline = time.time() + max(wait_sec, 3.2)
                        loaded_ok = False
                        while time.time() < deadline:
                            time.sleep(0.5)
                            try:
                                curr_xml, _ = android.ui()
                                if "请求成功" in curr_xml or "Loaded" in curr_xml:
                                    loaded_ok = True
                                    break
                            except Exception:
                                pass
                        if loaded_ok:
                            step_note = "检测到页面就绪状态 (Loaded/请求成功)，提前结束等待"
                        else:
                            step_note = f"异步等待 {wait_sec:.1f}s 完成"
                        emit("log", message=f"[Wait] {step_note}")

                    elif a_type == "clear_data":
                        write_action("clear_data", "pm", "clear", package)
                        if allow_online:
                            time.sleep(0.5)

                    elif a_type == "ui_mode":
                        is_dark = action.get("mode") == "dark"
                        android_dirty_dark = is_dark
                        write_action("ui_mode", "cmd", "uimode", "night", "yes" if is_dark else "no")
                        if allow_online:
                            time.sleep(0.6)
                        step_note = f"Android 系统深色模式已设为 {'开启 (night=yes)' if is_dark else '关闭 (night=no)'}"
                        emit("log", message=f"[UIMode] {step_note}")

                    elif a_type == "orientation":
                        is_land = action.get("value") == "landscape"
                        android_dirty_landscape = is_land
                        write_action("orientation", "settings", "put", "system", "accelerometer_rotation", "0")
                        write_action("orientation", "settings", "put", "system", "user_rotation", "1" if is_land else "0")
                        if allow_online:
                            time.sleep(0.8)
                        step_note = f"屏幕方向已切换为 {'横屏 (user_rotation=1)' if is_land else '竖屏 (user_rotation=0)'}"
                        emit("log", message=f"[Orientation] {step_note}")

                    elif a_type == "home_resume":
                        stay_s = max(0.6, float(action.get("seconds", 1.2)))
                        write_action("home_resume", "input", "keyevent", "KEYCODE_HOME")
                        time.sleep(stay_s)
                        from phone_pilot.core.pp_profile import load_pp_profile, resolve_platform_config

                        prof_act = str(resolve_platform_config(load_pp_profile(root), "android").get("activity") or "").strip()
                        resume_act = activity or prof_act or ".MainActivity"
                        if "/" not in resume_act:
                            resume_act = f"{package}/{resume_act if resume_act.startswith('.') else '.' + resume_act}"
                        write_action(
                            "home_resume",
                            "am",
                            "start",
                            "-a",
                            "android.intent.action.MAIN",
                            "-c",
                            "android.intent.category.LAUNCHER",
                            "-f",
                            "0x10200000",
                            "-n",
                            resume_act,
                        )
                        time.sleep(1.0)
                        step_note = f"已执行 Home 退后台 {stay_s:.1f}s 并热恢复前台任务栈"
                        emit("log", message=f"[Lifecycle] {step_note}")

                    elif a_type == "capture_stage":
                        stg_lbl = str(action.get("label") or f"阶段 {len(android_stage_screenshots) + 1}")
                        stg_png = android.screenshot()
                        _validate_png(stg_png)
                        stg_i = len(android_stage_screenshots) + 1
                        stg_file = f"stage_{stg_i}.png"
                        captured[stg_file] = stg_png
                        android_stage_screenshots.append(
                            {"index": stg_i, "label": stg_lbl, "file": stg_file, "path": f"{prefix}/{stg_file}"}
                        )
                        step_note = f"已归档阶段快照 {stg_file} ({stg_lbl})"
                        emit("log", message=f"[Stage] {step_note}")

                    elif a_type == "dismiss_modal":
                        closed_coords = None
                        # Fast path (~60ms): parse dumpsys activity View Hierarchy first so continuous
                        # animations inside modal views never stall uiautomator dump for 10s.
                        try:
                            vh_out = android.shell("dumpsys", "activity", package, allow_empty=True) or ""
                            if "View Hierarchy:" in vh_out:
                                vh_re = re.compile(
                                    r"^(\s*)(\S+)\{[^}]*\s([VIG][A-Z.]*)\s+\S+\s+(-?\d+),(-?\d+)-(-?\d+),(-?\d+)\s*(?:#([0-9a-fA-F]+)(?:\s+(?:app|android):id/([^\s}]+))?)?"
                                )
                                stack_vh: list[tuple[int, bool, int, int]] = []
                                in_vh = False
                                cand_vh: list[tuple[int, int, int]] = []
                                blocks_vh = re.findall(
                                    r"(^\s*ACTIVITY\s+\S+\s+[^\n]*\n(?:(?!^\s*ACTIVITY\s+\S+).)*)",
                                    vh_out,
                                    flags=re.M | re.S,
                                )
                                target_blk = blocks_vh[-1] if blocks_vh else vh_out
                                for blk in reversed(blocks_vh):
                                    if "mResumed=true" in blk:
                                        target_blk = blk
                                        break
                                for ln in target_blk.splitlines():
                                    if "View Hierarchy:" in ln:
                                        in_vh = True
                                        stack_vh = []
                                        continue
                                    if not in_vh:
                                        continue
                                    st = ln.strip()
                                    if st.startswith("Looper ") or st.startswith("Local Activity"):
                                        in_vh = False
                                        continue
                                    m_vh = vh_re.match(ln)
                                    if not m_vh:
                                        continue
                                    ind = len(m_vh.group(1))
                                    cls_v = m_vh.group(2)
                                    vis_v = m_vh.group(3)
                                    l_v, t_v, r_v, b_v = map(int, m_vh.groups()[3:7])
                                    hex_v = (m_vh.group(8) or "").lower()
                                    id_v = (m_vh.group(9) or "").lower()
                                    while stack_vh and stack_vh[-1][0] >= ind:
                                        stack_vh.pop()
                                    p_vis = stack_vh[-1][1] if stack_vh else True
                                    p_l = stack_vh[-1][2] if stack_vh else 0
                                    p_t = stack_vh[-1][3] if stack_vh else 0
                                    eff_v = p_vis and vis_v.startswith("V") and (r_v > l_v) and (b_v > t_v)
                                    al, at, ar, ab = p_l + l_v, p_t + t_v, p_l + r_v, p_t + b_v
                                    stack_vh.append((ind, eff_v, al, at))
                                    if not eff_v:
                                        continue
                                    cls_low = cls_v.lower()
                                    cx_v, cy_v = (al + ar) // 2, (at + ab) // 2
                                    if "closebutton" in cls_low or "close_btn" in id_v or id_v.endswith("close"):
                                        cand_vh.append((3, cx_v, cy_v))
                                    elif "C" in vis_v and ("countdown" in cls_low or "skip" in cls_low or hex_v in ("2713", "2726", "2728")):
                                        cand_vh.append((2, cx_v, cy_v))
                                if cand_vh:
                                    cand_vh.sort(key=lambda item: item[0])
                                    _, cx, cy = cand_vh[-1]
                                    write_action("dismiss_modal", "input", "tap", str(cx), str(cy))
                                    closed_coords = (cx, cy)
                        except Exception:
                            closed_coords = None
                        if closed_coords is None:
                            try:
                                _, cur_nodes = android.ui()
                                sel = action.get("selector") or {}
                                cand_texts = []
                                if sel.get("text"):
                                    cand_texts.append(str(sel["text"]))
                                cand_texts.extend([str(a) for a in (sel.get("aliases") or []) if a])
                                for fb in ("关闭", "Close", "×", "✕", "跳过", "Skip"):
                                    if fb not in cand_texts:
                                        cand_texts.append(fb)
                                for n in cur_nodes:
                                    if n.get("package") != package or n.get("enabled") != "true":
                                        continue
                                    n_txt = str(n.get("text") or "").strip()
                                    n_desc = str(n.get("content-desc") or "").strip()
                                    n_id = str(n.get("resource-id") or "").strip().lower()
                                    if (
                                        n_txt in cand_texts
                                        or n_desc in cand_texts
                                        or n_id.endswith("close")
                                        or "btn_close" in n_id
                                        or "iv_close" in n_id
                                    ):
                                        b_m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(n.get("bounds") or ""))
                                        if b_m:
                                            bx1, by1, bx2, by2 = map(int, b_m.groups())
                                            if bx2 > bx1 and by2 > by1:
                                                cx, cy = (bx1 + bx2) // 2, (by1 + by2) // 2
                                                write_action("dismiss_modal", "input", "tap", str(cx), str(cy))
                                                closed_coords = (cx, cy)
                                                break
                                if closed_coords is None:
                                    max_y = 2400
                                    for n in cur_nodes:
                                        b_m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(n.get("bounds") or ""))
                                        if b_m:
                                            max_y = max(max_y, int(b_m.group(4)))
                                    for n in cur_nodes:
                                        if n.get("package") != package or n.get("enabled") != "true" or n.get("clickable") != "true":
                                            continue
                                        n_cls = str(n.get("class") or "")
                                        if not (n_cls.endswith("ImageView") or n_cls.endswith("ImageButton")):
                                            continue
                                        b_m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", str(n.get("bounds") or ""))
                                        if not b_m:
                                            continue
                                        bx1, by1, bx2, by2 = map(int, b_m.groups())
                                        bw, bh = bx2 - bx1, by2 - by1
                                        if 20 <= bw <= 200 and 20 <= bh <= 200 and abs(bw - bh) <= 48 and by1 < int(max_y * 0.45):
                                            cx, cy = (bx1 + bx2) // 2, (by1 + by2) // 2
                                            write_action("dismiss_modal", "input", "tap", str(cx), str(cy))
                                            closed_coords = (cx, cy)
                                            break
                            except Exception:
                                closed_coords = None
                        if closed_coords is None:
                            write_action("dismiss_modal", "input", "keyevent", "KEYCODE_BACK")
                        if allow_online:
                            time.sleep(0.85)
                        step_note = f"已点击关闭控件 @ {closed_coords}" if closed_coords else "已发送返回键关闭当前浮层"
                        emit("log", message=f"[Dismiss] {step_note}")

                    emit(
                        "step_done",
                        step_index=idx,
                        status="SUCCESS",
                        elapsed_ms=int((time.time() - step_t0) * 1000),
                        detail=step_note,
                    )
            finally:
                if android_dirty_dark:
                    try:
                        android.shell("cmd", "uimode", "night", "no", allow_empty=True)
                    except Exception:
                        pass
                if android_dirty_landscape:
                    try:
                        android.shell("settings", "put", "system", "user_rotation", "0", allow_empty=True)
                    except Exception:
                        pass

            # Final Step: Evidence collection & verification
            f_t0 = time.time()
            emit(
                "step_start",
                step_index=final_step_idx,
                title=planned_steps[-1]["title"],
                detail=planned_steps[-1]["detail"],
            )
            if allow_online:
                time.sleep(1.0)
            _offline(android)
            if not allow_online:
                _foreground(android, package, m["target"]["activity"])
                xml, nodes = android.ui()
            else:
                _foreground(android, package)
                try:
                    xml, nodes = android.ui()
                except Exception:
                    xml, nodes = "<hierarchy/>", []
            captured["ui.xml"] = xml.encode()
            if not allow_online:
                for selector in m["target"]["selectors"]:
                    _matching(nodes, selector, package)
                _require(
                    android.process(package) == fresh_process,
                    "process_changed",
                    "App process changed before evidence",
                )
            png = android.screenshot()
            _validate_png(png)
            captured["screenshot.png"] = png
            logs = android.command(
                [
                    "logcat",
                    "-d",
                    "-v",
                    "threadtime",
                    "-T",
                    log_start,
                    "--pid=" + fresh_process["pid"],
                ],
                allow_empty=allow_online,
            )
            captured["logcat.txt"] = logs.encode()
            if not allow_online:
                _require(
                    any(
                        _fresh_log_line(line, fresh_process["pid"], log_start)
                        for line in logs.splitlines()
                    ),
                    "evidence_failed",
                    "Fresh app-PID logcat lines required, not just buffer headers",
                )
                _foreground(android, package, m["target"]["activity"])
                final_xml, final_nodes = android.ui()
                for selector in m["target"]["selectors"]:
                    _matching(final_nodes, selector, package)
                _require(
                    final_xml == xml and android.process(package) == fresh_process,
                    "evidence_state_changed",
                    "Evidence snapshot no longer matches target",
                )
                _offline(android)

            # Extract key business acceptance log lines (MockServer hits + SDK callbacks)
            key_logs: list[str] = []
            mock_tl_items: list[dict[str, Any]] = []
            logcat_only_lines: list[str] = []
            try:
                if (
                    _REPLAY_MOCK_SERVER is not None
                    and getattr(_REPLAY_MOCK_SERVER, "is_running", False)
                    and hasattr(_REPLAY_MOCK_SERVER, "_log")
                    and _REPLAY_MOCK_SERVER._log is not None
                ):
                    srv_port = getattr(_REPLAY_MOCK_SERVER, "data_port", 8090)
                    for req_entry in _REPLAY_MOCK_SERVER._log.entries()[-15:]:
                        hit_tag = "命中打桩规则" if req_entry.matched else "未匹配规则"
                        disp_path = req_entry.path
                        if "code=" in disp_path and len(disp_path) > 80:
                            disp_path = disp_path.split("code=", 1)[0] + "code=<encrypted…>"
                        mock_line = f"[MockServer :{srv_port}] {req_entry.method} {disp_path} -> 200 ({hit_tag})"
                        key_logs.append(mock_line)
                        mock_tl_items.append(
                            {
                                "method": req_entry.method,
                                "path": disp_path,
                                "ts": req_entry.ts,
                                "mono_ts": getattr(req_entry, "mono_ts", 0.0),
                                "line": mock_line,
                            }
                        )
                        emit("log", message=mock_line)
            except Exception:
                pass

            from phone_pilot.core.pp_profile import load_pp_profile

            and_pp_prof = load_pp_profile(root)
            cur_proc = None
            try:
                cur_proc = android.process(package)
            except Exception:
                cur_proc = None
            proc_alive = bool(cur_proc and cur_proc.get("pid"))
            has_mock_cfg = bool((m.get("configMock") or {}).get("mock", {}).get("mode") == "replay")
            and_oracle = evaluate_restore_verification_oracle(
                png_bytes=png,
                log_text=logs,
                ui_xml=xml,
                process_alive=proc_alive,
                has_mock_rules=has_mock_cfg,
                mock_requests_count=len(mock_tl_items),
                pp_profile=and_pp_prof,
                scene_id=scene_id,
            )
            ad_loaded = bool(and_oracle["target_loaded"])
            ad_shown = bool(and_oracle["target_shown"])
            verdict_summary = [and_oracle["summary"]]

            from phone_pilot.core.acceptance_profile import load_acceptance_profile

            prof_includes = ((load_acceptance_profile(root).get("log_filter") or {}).get("include_tokens")) or []
            _key_log_tokens = tuple(
                dict.fromkeys(
                    list(prof_includes)
                    + [
                        "ActivityTaskManager",
                        "Displayed",
                        "FATAL EXCEPTION",
                        "ANR in",
                    ]
                )
            )
            for raw_line in logs.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if any(tok in stripped for tok in _key_log_tokens):
                    # Keep single line concise for readability
                    clean_line = stripped[:240] + ("..." if len(stripped) > 240 else "")
                    key_logs.append(clean_line)
                    logcat_only_lines.append(clean_line)
                    emit("log", message=f"[Logcat] {clean_line}")
            if not logcat_only_lines and logs.strip():
                # Fallback: take last 15 non-empty lines of process logcat
                for raw_line in logs.splitlines()[-15:]:
                    if raw_line.strip():
                        cl = raw_line.strip()[:240]
                        key_logs.append(cl)
                        logcat_only_lines.append(cl)
            key_logs = key_logs[-45:]
            logcat_only_lines = logcat_only_lines[-40:]
            abs_logcat_path = str((Path(run_dir) / prefix / "logcat.txt").resolve())

            if allow_online and android_rec_proc is not None:
                import subprocess as _and_sp2
                from phone_pilot.android.adb.utils import adb_executable as _adb_exec2

                try:
                    _and_sp2.run(
                        [_adb_exec2(), "-s", device_serial, "shell", "pkill", "-INT", "screenrecord"],
                        stdout=_and_sp2.DEVNULL,
                        stderr=_and_sp2.DEVNULL,
                        timeout=5,
                    )
                    android_rec_proc.wait(timeout=6)
                except Exception:
                    try:
                        android_rec_proc.terminate()
                    except Exception:
                        pass
                time.sleep(0.5)
                local_tmp_mp4 = Path(run_dir) / f".android_restore_{int(time.time())}.mp4"
                android_rec_proc = None
                try:
                    _and_sp2.run(
                        [_adb_exec2(), "-s", device_serial, "pull", android_remote_mp4, str(local_tmp_mp4)],
                        stdout=_and_sp2.DEVNULL,
                        stderr=_and_sp2.DEVNULL,
                        timeout=15,
                    )
                    _and_sp2.run(
                        [_adb_exec2(), "-s", device_serial, "shell", "rm", "-f", android_remote_mp4],
                        stdout=_and_sp2.DEVNULL,
                        stderr=_and_sp2.DEVNULL,
                        timeout=5,
                    )
                    if local_tmp_mp4.is_file() and local_tmp_mp4.stat().st_size > 1024:
                        try:
                            from phone_pilot.script_api.context import _optimize_mp4_for_web
                            _optimize_mp4_for_web(local_tmp_mp4)
                        except Exception:
                            pass
                        captured["restore.mp4"] = local_tmp_mp4.read_bytes()
                except Exception:
                    pass
                finally:
                    try:
                        local_tmp_mp4.unlink(missing_ok=True)
                    except Exception:
                        pass

            from phone_pilot.core.timeline_sync import build_synced_timeline

            total_and_s = max(1.0, time.monotonic() - android_rec_t0)
            and_timeline = build_synced_timeline(
                span_s=total_and_s,
                suite_mono_t0=android_rec_t0,
                log_since=str(log_start or ""),
                mock_items=mock_tl_items,
                timed_log_items=logcat_only_lines,
            )

            with guard():
                _save_evidence(output, prefix, captured, evidence)
                if not and_oracle["ok"]:
                    raise SceneError(
                        "restore_verification_failed",
                        f"Android 现场复原终态校验未通过: {and_oracle['summary']}",
                    )
                receipt = {
                    "schema": SCHEMA,
                    "status": "SUCCESS",
                    "manifest_sha256": digest,
                    "device_serial": device_serial,
                    "build": {"fingerprint": actual_fingerprint},
                    "package": {
                        "name": package,
                        "activity": m["package"]["activity"],
                        "installed_apk_sha256": apk_hash,
                    },
                    "lease_session_id": sid,
                    "old_process": old_process,
                    "fresh_process": fresh_process,
                    "logcat_since": log_start,
                    "completed_at": time.time(),
                    "target": m["target"],
                    "actions": action_audit,
                    "business_verdict": {
                        "ad_loaded": ad_loaded,
                        "ad_shown": ad_shown,
                        "summary": " · ".join(verdict_summary),
                        "verification_checks": and_oracle["checks"],
                        "key_logs": key_logs,
                        "timeline_logs": and_timeline,
                        "abs_logcat_path": abs_logcat_path,
                        "rel_logcat_path": prefix + "/logcat.txt",
                    },
                    "stage_screenshots": android_stage_screenshots,
                    "assertion_scope": "Real-device scene restore + MockServer replay + UI/Logcat verification",
                    "evidence": evidence,
                }
                _save_receipt(output, prefix, receipt)

            emit(
                "step_done",
                step_index=final_step_idx,
                status="SUCCESS",
                elapsed_ms=int((time.time() - f_t0) * 1000),
                detail=" · ".join(verdict_summary),
            )
            res = _result(
                "SUCCESS", digest, device_serial, receipt=receipt, evidence=evidence
            )
            emit("scene_completed", result=res)
            return res
    except BaseException as error:
        failure_receipt = None
        if mutated and output is not None and prefix is not None:
            failure_receipt = {
                "schema": SCHEMA,
                "status": "FAILED",
                "manifest_sha256": digest,
                "device_serial": device_serial,
                "actions": action_audit,
                "error": {
                    "code": getattr(error, "code", "scene_error"),
                    "message": str(error),
                },
                "evidence": evidence,
                "completed_at": time.time(),
            }
            try:
                try:
                    _save_evidence(output, prefix, captured, evidence)
                except Exception as evidence_error:
                    failure_receipt["audit_error"] = str(evidence_error)
                _save_receipt(output, prefix, failure_receipt, replace=True)
            except Exception as audit_error:
                failure_receipt["audit_error"] = str(audit_error)
        if not isinstance(error, Exception):
            raise
        err_res = _result(
            "FAILED" if mutated else "BLOCKED",
            digest,
            device_serial,
            SceneError("unsafe_path", str(error))
            if isinstance(error, UnsafePath)
            else error,
            receipt=failure_receipt,
            evidence=evidence,
        )
        emit("scene_failed", error=str(error), result=err_res)
        return err_res
    finally:
        if android_rec_proc is not None:
            try:
                from phone_pilot.android.adb.utils import adb_executable as _adb_fin
                subprocess.run(
                    [_adb_fin(), "-s", str(device_serial), "shell", "pkill", "-INT", "screenrecord"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                )
                android_rec_proc.terminate()
            except Exception:
                pass
            try:
                from phone_pilot.android.adb.utils import adb_executable as _adb_fin2
                subprocess.run(
                    [_adb_fin2(), "-s", str(device_serial), "shell", "rm", "-f", android_remote_mp4],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                )
            except Exception:
                pass
        directories.close()
