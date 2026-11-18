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
        m["device"]["platform"] == "android",
        "unsupported_platform",
        "Only Android supported",
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
    try:
        _, m, digest = _load(run_dir)
        return _result("READY", digest, m["device"]["serial"])
    except Exception as error:
        # Expose raw hash even for a blocked manifest so clients can display identity.
        try:
            with safe_directory(run_dir) as archive:
                digest = hashlib.sha256(
                    archive.read_bytes("scene_manifest.json", max_bytes=1024 * 1024)
                ).hexdigest()
        except Exception:
            pass
        return _result("BLOCKED", digest, error=error)


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
                timeout=10,
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
    power = android.shell("dumpsys", "power")
    window = android.shell("dumpsys", "window", "policy")
    _require(
        re.search(r"(?m)^\s*mWakefulness=Awake\s*$", power),
        "device_locked",
        "Screen must already be awake; no wake/unlock actions",
    )
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
    matches = [
        n
        for n in nodes
        if n.get("package") == package
        and all(n.get(_SELECTOR[k]) == v for k, v in selector.items())
        and n.get("enabled") == "true"
        and (not clickable or n.get("clickable") == "true")
    ]
    _require(
        len(matches) == 1,
        "selector_not_unique",
        "Exact selector must match one enabled target-package node",
    )
    return matches[0]


def _foreground(android, package, activity=None):
    activity = package + activity if activity and activity.startswith(".") else activity
    state = android.shell("dumpsys", "activity", "activities")
    matches = re.findall(
        r"(?:mResumedActivity|topResumedActivity).*?\s([A-Za-z0-9_.]+)/(\.?[A-Za-z0-9_.]+)(?:\s|})",
        state,
    )
    normalized = {(p, p + a if a.startswith(".") else a) for p, a in matches}
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
        from androguard.core.axml import AXMLPrinter

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


def restore_scene(run_dir, *, device_serial, approved_manifest_sha256):
    """Execute only hash-approved fixed recipe. Never install, grant or run scripts."""
    digest = None
    mutated = False
    evidence = {}
    captured = {}
    action_audit = []
    root = None
    directories = ExitStack()
    output = None
    prefix = None
    try:
        # Capability and ALL ancestor permissions must pass before even queries.
        require_safe_io()
        archive = directories.enter_context(safe_directory(run_dir, writable=True))
        root, m, digest = _load(run_dir, archive=archive)
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
        package = m["package"]["name"]
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
        # Pin the one output inode across commands AND the failure handler. Never
        # resolve/reopen host paths after mutation, even when the lease is lost.
        with _lease(device_serial) as (guard, sid):
            android = _Android(device_serial)
            android.guard = guard
            _require(
                android.command(["get-state"]) == "device",
                "device_unavailable",
                "Device not online",
            )
            _require(
                android.shell("getprop", "ro.build.fingerprint")
                == m["build"]["fingerprint"],
                "build_mismatch",
                "Device build differs",
            )
            _offline(android)
            _unlocked(android)
            paths = android.shell("pm", "path", package).splitlines()
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
            # If dumpsys does expose requested permission entries they must also
            # agree; absence is fine only because actual APK was parsed above.
            _require(
                "android.permission." not in details,
                "permissions_unsupported",
                "Runtime package permissions disagree with permission-free APK",
            )
            android.ui()  # no permission/system installer prompt before first mutation
            old_process = android.process(package)
            # Reload approved manifest/artifacts immediately before approved mutations.
            _require(
                _load(root, archive=archive)[2] == digest,
                "approval_hash_mismatch",
                "Manifest changed during preflight",
            )
            _offline(android)

            def write_action(kind, *args, selector=None):
                nonlocal mutated
                _unlocked(android)
                _offline(android)
                if selector is not None:
                    # Resolve from the last UI snapshot AFTER safety queries, with
                    # no additional device query between bounds and tap dispatch.
                    _foreground(android, package)
                    _, nodes = android.ui()
                    node = _matching(nodes, selector, package, clickable=True)
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
                    args = ("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                entry = {"type": kind, "started_at": time.time(), "status": "ATTEMPTED"}
                if selector is not None:
                    entry["selector"] = selector

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
                return output

            write_action("force_stop", "am", "force-stop", package)
            _require(
                android.process(package) is None,
                "force_stop_failed",
                "Old app process survived force-stop",
            )
            log_start = _log_boundary(android.shell("date", "+%m-%d %H:%M:%S.%N"))
            activity = m["package"]["activity"]
            launch = write_action(
                "launch", "am", "start", "-W", "-n", package + "/" + activity
            )
            _require(
                "Status: ok" in launch and "Error" not in launch,
                "launch_failed",
                "Activity launch failed",
            )
            fresh_process = android.process(package)
            _require(
                fresh_process is not None and fresh_process != old_process,
                "process_not_fresh",
                "Fresh PID/starttime required",
            )
            for action in m["actions"][2:]:
                write_action("tap_selector", selector=action["selector"])
                # Fail without auto-back/recovery if the tap leaves this package.
                _foreground(android, package)
            _offline(android)
            _foreground(android, package, m["target"]["activity"])
            xml, nodes = android.ui()
            captured["ui.xml"] = xml.encode()
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
                ]
            )
            captured["logcat.txt"] = logs.encode()
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
            with guard():
                _save_evidence(output, prefix, captured, evidence)
                receipt = {
                    "schema": SCHEMA,
                    "status": "SUCCESS",
                    "manifest_sha256": digest,
                    "device_serial": device_serial,
                    "build": m["build"],
                    "package": m["package"],
                    "lease_session_id": sid,
                    "old_process": old_process,
                    "fresh_process": fresh_process,
                    "logcat_since": log_start,
                    "completed_at": time.time(),
                    "target": m["target"],
                    "actions": action_audit,
                    "assertion_scope": "UI scene only; app logs collected, not business callback PASS",
                    "evidence": evidence,
                }
                _save_receipt(output, prefix, receipt)
            return _result(
                "SUCCESS", digest, device_serial, receipt=receipt, evidence=evidence
            )
    except BaseException as error:
        failure_receipt = None
        if mutated:
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
            # Host-only audit; after lease loss issue NO additional device query,
            # screenshot, UI dump, recovery command, or cleanup write.
            try:
                try:
                    _save_evidence(output, prefix, captured, evidence)
                except Exception as evidence_error:
                    failure_receipt["audit_error"] = str(evidence_error)
                # Receipt is still attempted if an evidence file cannot be saved.
                # Replace any partially written SUCCESS receipt in this SAME dir.
                _save_receipt(output, prefix, failure_receipt, replace=True)
            except Exception as audit_error:
                failure_receipt["audit_error"] = str(audit_error)
        if not isinstance(error, Exception):
            # Cancellation remains cancellation, but modified devices get a
            # host-only FAILED audit first. Never issue recovery/device queries.
            raise
        return _result(
            "FAILED" if mutated else "BLOCKED",
            digest,
            device_serial,
            SceneError("unsafe_path", str(error))
            if isinstance(error, UnsafePath)
            else error,
            receipt=failure_receipt,
            evidence=evidence,
        )
    finally:
        directories.close()
