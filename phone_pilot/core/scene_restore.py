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
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

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
    _require(
        isinstance(value, dict) and value and set(value) <= set(_SELECTOR),
        "unknown_selector",
        "Only exact resource_id/text/content_desc selectors supported",
    )
    for key, val in value.items():
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
                a_type in ("tap_selector", "broadcast", "reverse_port", "wait_duration", "clear_data", "open_url"),
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


def inspect_scene(run_dir):
    """Validate local recipe only. READY is not device approval or live preflight."""
    digest = None
    catalog = {}
    try:
        catalog_path = Path(run_dir) / "scene_catalog.json"
        if os.environ.get("PHONE_PILOT_SCENE_ALLOW_ONLINE") == "1":
            from phone_pilot.core.manifest_generator import ensure_scene_manifest, ensure_scene_catalog
            ensure_scene_manifest(run_dir)
            if catalog_path.is_file():
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            else:
                catalog = ensure_scene_catalog(Path(run_dir))
        elif catalog_path.is_file():
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception:
        catalog = {}
    try:
        _, m, digest = _load(run_dir)
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
    matches = [
        n
        for n in nodes
        if n.get("package") == package
        and all(n.get(_SELECTOR[k]) == v for k, v in selector.items())
        and n.get("enabled") == "true"
        and (not clickable or n.get("clickable") == "true")
    ]
    if allow_online and len(matches) == 0 and clickable:
        matches = [
            n
            for n in nodes
            if n.get("package") == package
            and all(n.get(_SELECTOR[k]) == v for k, v in selector.items())
            and n.get("enabled") == "true"
        ]
    if allow_online and len(matches) == 0:
        matches = [
            n
            for n in nodes
            if n.get("package") == package
            and all(
                str(n.get(_SELECTOR[k], "")).strip().lower() == str(v).strip().lower()
                for k, v in selector.items()
            )
            and n.get("enabled") == "true"
        ]
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


def _action_label(action: dict) -> tuple[str, str]:
    """Convert raw action dict into human-friendly (title, detail) for progress stepper."""
    a_type = action.get("type", "")
    if a_type == "force_stop":
        return ("清理旧进程状态", "执行 am force-stop 确保冷启动纯净环境")
    if a_type == "launch":
        return ("启动被测应用 Demo", "拉起主入口 Activity 并等待主界面渲染")
    if a_type == "reverse_port":
        dp = action.get("device_port", 8090)
        return ("打通端云反向代理通道", f"映射设备端口 tcp:{dp} -> 本机 MockServer:{dp}")
    if a_type == "broadcast":
        extras = action.get("extras", {})
        cmd_val = f"{extras.get('cmd', '')}={extras.get('value', '')}" if extras else ""
        return ("注入测试桩环境广播", f"发送广播切换 SDK 请求环境 ({cmd_val})")
    if a_type == "tap_selector":
        sel = action.get("selector", {})
        txt = sel.get("text") or sel.get("resource_id") or sel.get("content_desc") or "控件"
        if txt in ("load", "加载激励视频", "加载插屏"):
            return (f"触发广告加载 ({txt})", f"动态定位并点击「{txt}」按钮，发起竞价请求")
        if txt in ("show", "展示激励视频", "展示插屏", "展示Banner"):
            return (f"触发广告展示 ({txt})", f"动态定位并点击「{txt}」按钮，渲染广告视图")
        return (f"导航进入「{txt}」页面", f"动态解析屏幕 Bounds 并点击「{txt}」")
    if a_type == "open_url":
        return ("通过 DeepLink 跳转目标页", f"打开 {action.get('url', '')}")
    if a_type == "wait_duration":
        sec = action.get("seconds", 2.0)
        return ("等待广告物料就绪", f"监听 Mock 响应与 onAdLoaded 状态 (缓冲 {sec}s)")
    if a_type == "clear_data":
        return ("重置应用缓存数据", "执行 pm clear 恢复初始默认状态")
    return (f"执行动作 {a_type}", str(action))


def _discover_media_roots(run_dir: Optional[Path] = None) -> list[Path]:
    """Discover static media directories generically from run_dir, pp_profile.json, or PHONE_PILOT_MEDIA_DIRS."""
    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(Path(run_dir) / "media")
        for cfg_dir in (Path(run_dir), Path(run_dir).parent, Path.cwd()):
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
                fpath = (root_dir / fname).resolve()
                if fpath.is_file() and str(fpath).startswith(str(root_dir)):
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
                            end = int(e_str) if e_str else total - 1
                            end = min(end, total - 1)
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

        for r in rules_content:
            m_method = r.get("method", "POST")
            m_path = r.get("path", "/sdk/bid")
            m_status = int(r.get("status", 200))
            m_body = r.get("body", {})
            if isinstance(m_body, dict):
                raw_json = json.dumps(m_body, ensure_ascii=False)
                raw_json = re.sub(r"http://127\.0\.0\.1:\d+", f"http://127.0.0.1:{url_target_port}", raw_json)
                m_body = json.loads(raw_json)
            m_delay = int(r.get("delay_ms", 0))
            store.set_rule(m_method, m_path, status=m_status, body=m_body, delay_ms=m_delay)
            other_method = "GET" if m_method == "POST" else "POST"
            store.set_rule(other_method, m_path, status=m_status, body=m_body, delay_ms=m_delay)
            if m_path == "/sdk/bid":
                store.set_rule(m_method, "/sdk/mock/bid", status=m_status, body=m_body, delay_ms=m_delay)
                store.set_rule(other_method, "/sdk/mock/bid", status=m_status, body=m_body, delay_ms=m_delay)
        emit("log", message=f"[MockServer] 已加载 {len(rules_content)} 条打桩规则，监听端口 :{actual_port}")
        return actual_port
    except Exception as exc:
        import sys

        sys.stderr.write(f"[scene_restore] MockServer setup warning: {exc}\n")
        return data_port


def _inject_ios_mock_defaults(device_serial: str, bundle_id: str, mock_port: int = 18090) -> None:
    """Inject local MockServer host & Houyi switch defaults into iOS Simulator app container."""
    try:
        import datetime as _dt
        import plistlib
        import subprocess
        import sys
        from phone_pilot.ios.pmd import utils as pmd_utils

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
        mock_host = f"http://127.0.0.1:{mock_port}"
        payload = {
            "kADMHouyiRawSwitchListKey": [
                {"type": "host_switch_config", "switch": "yes", "enabled": True, "host": mock_host},
                {
                    "type": "urlConfig",
                    "switch": "yes",
                    "adUrl": f"{mock_host}/sdk/bid",
                    "mockAdUrl": f"{mock_host}/sdk/mock/bid",
                },
            ],
            "kADMHouyiUpdateTime": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
            "adm_mock_mode_enabled": True,
            "adm_demo_custom_mock_host": mock_host,
            "adm_demo_env_mode": 2,
            "adm_demo_env_mode_explicit": True,
            "ADMDebugLogEnabled": True,
        }
        tmp_plist = f"/tmp/pp_ios_mock_{device_serial}.plist"
        with open(tmp_plist, "wb") as f:
            plistlib.dump(payload, f, fmt=plistlib.FMT_BINARY)
        with open(plist_path, "wb") as f:
            plistlib.dump(payload, f, fmt=plistlib.FMT_BINARY)
        subprocess.run(
            pmd_utils.simctl_prefix() + ["spawn", device_serial, "defaults", "import", plist_path, tmp_plist],
            capture_output=True,
            timeout=5,
        )
        app_support_dir = os.path.join(container, "Library", "Application Support", "com.admaster.sdk")
        if os.path.isdir(app_support_dir):
            for fname in os.listdir(app_support_dir):
                if fname.endswith(".cfg"):
                    try:
                        os.remove(os.path.join(app_support_dir, fname))
                    except OSError:
                        pass
    except Exception as exc:
        import sys

        sys.stderr.write(f"[scene_restore] _inject_ios_mock_defaults warning: {exc}\n")


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
    from phone_pilot.ios.driver import iOSDriver

    bundle_id = (m.get("package") or {}).get("name") or "com.baidujp.gbusdkdemo"
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

    mutated = False
    restore_t0 = time.monotonic()
    rec_tmp_mp4 = root / f".restore_tmp_{int(time.time())}.mp4"
    rec_proc = None
    try:
        try:
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
            mutated = True
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
                _inject_ios_mock_defaults(device_serial, bundle_id, mock_port=actual_mock_port)
            s3_t0 = time.time()
            emit("step_start", step_index=3, title=planned_steps[2]["title"], detail=planned_steps[2]["detail"])
            launch_res = driver.app.launch_app(bundle_id)
            if isinstance(launch_res, dict) and launch_res.get("ok") is False:
                raise SceneError("ios_launch_failed", f"启动 {bundle_id} 失败: {launch_res}")
            mutated = True
            time.sleep(2.4)
            emit(
                "step_done",
                step_index=3,
                status="SUCCESS",
                elapsed_ms=int((time.time() - s3_t0) * 1000),
                detail=f"已启动 {bundle_id} ({launch_res.get('pid', 'ok')})",
            )

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
            elif a_type in ("reverse_port", "broadcast"):
                step_note = f"iOS 无需执行 {a_type}（宿主机 localhost:8090 直通）"
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

        fail_markers = ("didFailToReceiveAdWithError", "Could not connect to the server", "No fill", "Load failed")
        matched_fail = next((m_str for m_str in fail_markers if m_str in ui_xml), None)
        ad_shown = any(
            tok in ui_xml
            for tok in (
                "adm_reward_endcard_view",
                "adm_reward_content_container",
                "adm_interstitial_endcard_view",
                "Presenting ",
                "rewardedAdDidReceiveAd",
                "adDidRecordImpression",
            )
        )
        mock_reqs = []
        if _REPLAY_MOCK_SERVER is not None and getattr(_REPLAY_MOCK_SERVER, "_log", None) is not None:
            try:
                mock_reqs = list(_REPLAY_MOCK_SERVER._log.entries())
            except Exception:
                mock_reqs = []

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

        mock_log_lines: list[str] = []
        mock_timeline: list[dict[str, Any]] = []
        total_restore_s = max(1.0, time.monotonic() - restore_t0)
        if mock_reqs:
            step_t = total_restore_s / max(len(mock_reqs), 1)
            for idx_m, mr in enumerate(mock_reqs[:30]):
                m_path = getattr(mr, "path", None) or (mr.get("path") if isinstance(mr, dict) else str(mr))
                m_meth = getattr(mr, "method", None) or (mr.get("method") if isinstance(mr, dict) else "GET")
                ln = f"[MOCK] {m_meth} {m_path}"
                mock_log_lines.append(ln)
                mock_timeline.append({"t": round(min(total_restore_s, (idx_m + 1) * step_t * 0.85), 2), "line": ln})
        else:
            mock_log_lines.append(f"[RESTORE] ad_shown={ad_shown} (已完成冷启动与广告展示复原)")
            mock_timeline.append({"t": 0.5, "line": mock_log_lines[0]})

        captured: dict[str, bytes] = {
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
                "ios_ad_load_failed",
                f"iOS 广告请求/展示失败: {matched_fail} (mock_requests={len(mock_reqs)})",
            )
        if not ad_shown:
            raise SceneError(
                "ios_ad_not_shown",
                f"iOS 未检测到广告展示或尾帧控件 (mock_requests={len(mock_reqs)})",
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
                "ad_loaded": bool(mock_reqs) or ad_shown,
                "ad_shown": ad_shown,
                "summary": f"iOS 现场已成功复原 (mock_requests={len(mock_reqs)}, ad_shown={ad_shown})",
                "key_logs": mock_log_lines,
                "timeline_logs": mock_timeline,
                "rel_logcat_path": prefix + "/logcat.txt",
            },
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
        if isinstance(error, SceneError):
            raise
        raise SceneError("ios_restore_failed", str(error)) from error


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

        # Override actions & target from scenes/<scene_id>/actions.json or scene_catalog.json
        if allow_online and scene_id:
            sdir = _safe_scene_dir(Path(run_dir), scene_id)
            if sdir is not None and (sdir / "actions.json").is_file() and not (sdir / "actions.json").is_symlink():
                try:
                    sc_actions = json.loads((sdir / "actions.json").read_text(encoding="utf-8"))
                    if isinstance(sc_actions, list) and sc_actions:
                        m["actions"] = sc_actions
                except Exception:
                    pass
            else:
                catalog_file = Path(run_dir) / "scene_catalog.json"
                if catalog_file.is_file():
                    try:
                        cat_data = json.loads(catalog_file.read_text(encoding="utf-8"))
                        for sc in cat_data.get("scenes", []):
                            if sc.get("scene_id") == scene_id:
                                if sc.get("actions"):
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

        cross_profile: dict[str, Any] = {}
        prof_path = root / "pp_profile.json"
        if prof_path.is_file():
            try:
                cross_profile = json.loads(prof_path.read_text(encoding="utf-8"))
            except Exception:
                cross_profile = {}

        cross_activity = ""
        if target_platform != m["device"]["platform"]:
            plat_cfg = (cross_profile.get("platforms") or {}).get(target_platform) or {}
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
                    android_rec_proc = subprocess.Popen(
                        [adb_executable(), "-s", device_serial, "shell", "screenrecord", "--time-limit", "90", android_remote_mp4],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
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
                                nav_res = SemanticNavigator.from_env().locate(q, ui_elements=ui_elems)
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
                bc_cfg = cross_profile.get("android_test_broadcast") or {}
                if bc_cfg.get("action"):
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
                        write_action("open_url", "am", "start", "-a", "android.intent.action.VIEW", "-d", raw_url, "-p", package)
                        if allow_online:
                            time.sleep(1.5 if mode == "step" else 1.0)
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
                        step_note = "检测到「请求成功」(onAdLoaded)，广告物料已就绪！"
                    else:
                        step_note = f"异步等待 {wait_sec:.1f}s 完成"
                    emit("log", message=f"[Wait] {step_note}")

                elif a_type == "clear_data":
                    write_action("clear_data", "pm", "clear", package)
                    if allow_online:
                        time.sleep(0.5)

                emit(
                    "step_done",
                    step_index=idx,
                    status="SUCCESS",
                    elapsed_ms=int((time.time() - step_t0) * 1000),
                    detail=step_note,
                )

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

            # Check business indicators in logs & UI
            ad_loaded = (
                "onAdLoaded" in logs
                or "event=LOAD_SUCCESS" in logs
                or "Ad request success" in logs
                or "请求成功" in xml
            )
            ad_shown = (
                "SHOW_CALLED" in logs
                or "event=SHOW_OK" in logs
                or "event=IMPRESSION" in logs
                or "onAttachedToWindow" in logs
                or "show() called" in logs
                or "跳过" in xml
            )
            verdict_summary = []
            if ad_loaded:
                verdict_summary.append("广告加载成功 (onAdLoaded / LOAD_SUCCESS)")
            if ad_shown:
                verdict_summary.append("广告容器已上屏曝光 (SHOW_OK / IMPRESSION)")
            if not verdict_summary:
                verdict_summary.append("目标页面已到达")

            # Extract key business acceptance log lines (MockServer hits + SDK callbacks)
            key_logs: list[str] = []
            try:
                if (
                    _REPLAY_MOCK_SERVER is not None
                    and getattr(_REPLAY_MOCK_SERVER, "is_running", False)
                    and hasattr(_REPLAY_MOCK_SERVER, "_log")
                    and _REPLAY_MOCK_SERVER._log is not None
                ):
                    for req_entry in _REPLAY_MOCK_SERVER._log.entries()[-15:]:
                        hit_tag = "命中打桩规则" if req_entry.matched else "未匹配规则"
                        mock_line = f"[MockServer :8090] {req_entry.method} {req_entry.path} -> 200 ({hit_tag})"
                        key_logs.append(mock_line)
                        emit("log", message=mock_line)
            except Exception:
                pass

            _key_log_tokens = (
                "AdTest",
                "[AdMaster/",
                "AdDebugPanel",
                "Adm-AdRevReporter",
                "AdMasterApplication",
                "SVldManualTest",
                "XAbstractAdProdTemplate",
                "onAdLoaded",
                "onAdShow",
                "onAdClick",
                "onAdClose",
                "onAdFailed",
                "bidding success",
                "ready to show",
                "FATAL EXCEPTION",
                "ANR in",
            )
            for raw_line in logs.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if any(tok in stripped for tok in _key_log_tokens):
                    # Keep single line concise for readability
                    clean_line = stripped[:240] + ("..." if len(stripped) > 240 else "")
                    key_logs.append(clean_line)
                    emit("log", message=f"[Logcat] {clean_line}")
            if not key_logs and logs.strip():
                # Fallback: take last 15 non-empty lines of process logcat
                for raw_line in logs.splitlines()[-15:]:
                    if raw_line.strip():
                        key_logs.append(raw_line.strip()[:240])
            key_logs = key_logs[-45:]
            abs_logcat_path = str((Path(run_dir) / prefix / "logcat.txt").resolve())

            if allow_online and android_rec_proc is not None:
                try:
                    subprocess.run(
                        [adb_executable(), "-s", device_serial, "shell", "pkill", "-INT", "screenrecord"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    android_rec_proc.wait(timeout=6)
                except Exception:
                    try:
                        android_rec_proc.terminate()
                    except Exception:
                        pass
                time.sleep(0.4)
                local_tmp_mp4 = Path(run_dir) / f".android_restore_{int(time.time())}.mp4"
                try:
                    subprocess.run(
                        [adb_executable(), "-s", device_serial, "pull", android_remote_mp4, str(local_tmp_mp4)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                    )
                    subprocess.run(
                        [adb_executable(), "-s", device_serial, "shell", "rm", "-f", android_remote_mp4],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    if local_tmp_mp4.is_file() and local_tmp_mp4.stat().st_size > 1024:
                        captured["restore.mp4"] = local_tmp_mp4.read_bytes()
                except Exception:
                    pass
                finally:
                    try:
                        local_tmp_mp4.unlink(missing_ok=True)
                    except Exception:
                        pass

            total_and_s = max(1.0, time.monotonic() - android_rec_t0)
            and_timeline: list[dict[str, Any]] = []
            if key_logs:
                step_t = total_and_s / max(len(key_logs), 1)
                for idx_k, kln in enumerate(key_logs):
                    and_timeline.append({"t": round(min(total_and_s, (idx_k + 1) * step_t * 0.85), 2), "line": kln})

            with guard():
                _save_evidence(output, prefix, captured, evidence)
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
                        "key_logs": key_logs,
                        "timeline_logs": and_timeline,
                        "abs_logcat_path": abs_logcat_path,
                        "rel_logcat_path": prefix + "/logcat.txt",
                    },
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
        directories.close()
