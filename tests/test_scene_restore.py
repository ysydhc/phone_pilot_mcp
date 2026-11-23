"""Offline safety tests: never connect to ADB or execute historical scripts."""

from contextlib import contextmanager
import copy
import hashlib
import json
import subprocess

import pytest

from phone_pilot.core import scene_restore as scene

PACKAGE = "com.example.offlinedemo"
APK_HASH = "a" * 64
UI = '<hierarchy><node package="com.example.offlinedemo" text="Ready" resource-id="com.example.offlinedemo:id/next" content-desc="next" enabled="true" clickable="true" bounds="[20,40][80,100]" /></hierarchy>'


@pytest.fixture
def recipe(tmp_path):
    manifest = {
        "schema": scene.SCHEMA,
        "device": {"platform": "android", "serial": "offline-123"},
        "build": {"fingerprint": "test/android/debug"},
        "package": {
            "name": PACKAGE,
            "activity": ".MainActivity",
            "installed_apk_sha256": APK_HASH,
        },
        "permissions": [],
        "configMock": {"config": {"mode": "none"}, "mock": {"mode": "none"}},
        "safety": {
            "external_services": False,
            "irreversible_side_effects": False,
            "trusted_offline_demo": True,
        },
        "actions": [
            {"type": "force_stop"},
            {"type": "launch"},
            {"type": "tap_selector", "selector": {"text": "Ready"}},
        ],
        "target": {"activity": ".MainActivity", "selectors": [{"text": "Ready"}]},
        "artifacts": [],
    }

    def save(m=None):
        raw = json.dumps(manifest if m is None else m).encode()
        (tmp_path / "scene_manifest.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    save()
    return tmp_path, manifest, save


class FakeAndroid:
    def __init__(self, serial):
        self.serial = serial
        self.guard = None
        self.commands = []
        self.writes = []
        self.current = {"pid": "123", "starttime": "100"}
        self.new = {"pid": "456", "starttime": "200"}
        self.ui_xml = UI
        self.responses = {}
        self.before = None
        self.capture_failure = False

    def command(self, args, *, allow_empty=False):
        if self.before:
            self.before(args)
        with self.guard():
            if getattr(self, "before_dispatch", None) is not None:
                self.before_dispatch()
            self.commands.append(args)
            key = tuple(args)
            if key in self.responses:
                return self.responses[key]
            if args == ["get-state"]:
                return "device"
            if args[0] == "logcat":
                return f"11-17 14:00:00.100 {self.new['pid']} 456 I Demo: scene Ready"
            if args[:3] == ["shell", "am", "force-stop"]:
                self.writes.append(args)
                self.current = None
                return ""
            if args[:3] == ["shell", "am", "start"]:
                self.writes.append(args)
                self.current = self.new
                return "Status: ok"
            if args[:3] == ["shell", "input", "tap"]:
                self.writes.append(args)
                return ""
            fixed = {
                ("shell", "dumpsys", "power"): "mWakefulness=Awake",
                ("shell", "dumpsys", "window", "policy"): "isStatusBarKeyguard=false",
                ("shell", "getprop", "ro.build.fingerprint"): "test/android/debug",
                ("shell", "settings", "get", "global", "wifi_on"): "0",
                ("shell", "settings", "get", "global", "mobile_data"): "0",
                ("shell", "ip", "route", "show", "table", "all"): "",
                ("shell", "ip", "-6", "route", "show", "table", "all"): "",
                ("shell", "dumpsys", "connectivity"): "Active default network: none",
                ("shell", "pm", "path", PACKAGE): "package:/data/app/demo/base.apk",
                ("shell", "sha256sum", "/data/app/demo/base.apk"): APK_HASH
                + "  /data/app/demo/base.apk",
                (
                    "shell",
                    "dumpsys",
                    "package",
                    PACKAGE,
                ): "pkgFlags=[ DEBUGGABLE ]\nrequested permissions:\ninstall permissions:",
                ("shell", "date", "+%m-%d %H:%M:%S.%N"): "11-17 14:00:00.000000000",
                (
                    "shell",
                    "dumpsys",
                    "activity",
                    "activities",
                ): "mResumedActivity: ActivityRecord{1 u0 com.example.offlinedemo/.MainActivity t1}",
            }
            if key not in fixed:
                raise AssertionError(f"Unexpected fixed command: {args}")
            return fixed[key]

    def shell(self, *args, allow_empty=False):
        return self.command(["shell", *args], allow_empty=allow_empty)

    def process(self, package):
        with self.guard():
            return copy.deepcopy(self.current)

    def ui(self):
        with self.guard():
            nodes = list(scene.ET.fromstring(self.ui_xml).iter("node"))
            scene._require(
                not any("permissioncontroller" in n.get("package", "") for n in nodes),
                "permission_prompt",
                "Permission prompt",
            )
            return self.ui_xml, nodes

    def apk_bytes(self, path):
        with self.guard():
            return b"offline fixture APK bytes"

    def screenshot(self):
        with self.guard():
            if self.capture_failure:
                raise scene.SceneError("evidence_failed", "No screenshot")
            import cv2
            import numpy as np

            ok, data = cv2.imencode(".png", np.zeros((2, 2, 3), dtype=np.uint8))
            assert ok
            return data.tobytes()


@pytest.fixture
def device(monkeypatch):
    fake = FakeAndroid("offline-123")
    state = {"alive": True, "count": 0, "lose_at": None}

    @contextmanager
    def lease(serial):
        @contextmanager
        def guard():
            state["count"] += 1
            if state["lose_at"] == state["count"]:
                state["alive"] = False
            scene._require(state["alive"], "lease_lost", "Lost lease")
            yield

        yield guard, "fake-lease"

    monkeypatch.setattr(scene, "_apk_preflight", lambda data, package, digest: None)
    monkeypatch.setattr(scene, "_lease", lease)
    monkeypatch.setattr(scene, "_Android", lambda serial: fake)
    return fake, state


def restore(recipe):
    root, _, save = recipe
    return scene.restore_scene(
        root, device_serial="offline-123", approved_manifest_sha256=save()
    )


def test_inspect_local_ready_does_not_touch_device(recipe, monkeypatch):
    monkeypatch.setattr(
        scene, "_Android", lambda _: pytest.fail("inspect touched device")
    )
    result = scene.inspect_scene(recipe[0])
    assert result["status"] == "READY"
    assert result["receipt"] is None
    assert result["manifest_sha256"] == recipe[2]()


def test_success_fresh_process_foreground_ui_and_new_evidence(recipe, device):
    fake, _ = device
    result = restore(recipe)
    assert result["status"] == "SUCCESS", result
    assert len(fake.writes) == 3
    assert fake.writes[-1][-2:] == ["50", "70"]  # dynamic XML bounds, not recipe coords
    receipt = result["receipt"]
    assert receipt["old_process"] != receipt["fresh_process"]
    assert receipt["lease_session_id"] == "fake-lease"
    root = recipe[0]
    on_disk = json.loads((root / receipt["path"]).read_text())
    assert on_disk["status"] == "SUCCESS"
    for evidence in result["evidence"].values():
        data = (root / evidence["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == evidence["sha256"]
    assert set(result["evidence"]) == {"screenshot.png", "ui.xml", "logcat.txt"}


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda m: m.update(schema="other"), "unsupported_schema"),
        (lambda m: m.update(shell="rm -rf /"), "invalid_schema"),
        (lambda m: m["device"].update(platform="unknown_os"), "unsupported_platform"),
        (
            lambda m: m.update(
                permissions=[{"name": "android.permission.CAMERA", "granted": True}]
            ),
            "permissions_unsupported",
        ),
        (
            lambda m: m["configMock"].update(
                config={"mode": "restore", "path": "config.json"}
            ),
            "config_mock_unsupported",
        ),
        (
            lambda m: m["configMock"].update(
                mock={"mode": "external", "url": "http://host"}
            ),
            "config_mock_unsupported",
        ),
        (lambda m: m["safety"].update(external_services=True), "unsafe_side_effects"),
        (
            lambda m: m["safety"].update(irreversible_side_effects=True),
            "unsafe_side_effects",
        ),
        (
            lambda m: m["safety"].update(trusted_offline_demo=False),
            "unsafe_side_effects",
        ),
        (lambda m: m["actions"][2].update(type="python"), "unknown_action"),
        (
            lambda m: m["actions"][2].update(selector={"x": 50, "y": 70}),
            "unknown_selector",
        ),
        (lambda m: m["package"].update(name="demo;reboot"), "invalid_schema"),
        (
            lambda m: m["package"].update(activity="other.package.Activity"),
            "invalid_schema",
        ),
    ],
)
def test_invalid_manifest_blocked_without_device_writes(recipe, device, change, code):
    change(recipe[1])
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == code
    assert device[0].commands == []
    assert device[0].writes == []


@pytest.mark.parametrize(
    "path", ["../escape.py", "/tmp/script.py", "sub/../../escape", "sub\\escape"]
)
def test_artifact_path_escape_blocked(recipe, device, path):
    recipe[1]["artifacts"] = [{"path": path, "sha256": APK_HASH}]
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "unsafe_path"
    assert not device[0].commands


def test_artifact_symlink_and_hash_blocked(recipe, device, tmp_path):
    root, manifest, _ = recipe
    (root / "artifact.py").symlink_to(root / "scene_manifest.json")
    manifest["artifacts"] = [{"path": "artifact.py", "sha256": APK_HASH}]
    assert restore(recipe)["blockers"][0]["code"] == "unsafe_path"
    (root / "data.json").write_text("{}")
    manifest["artifacts"] = [{"path": "data.json", "sha256": APK_HASH}]
    assert restore(recipe)["blockers"][0]["code"] == "artifact_hash_mismatch"
    assert not device[0].commands


def test_historical_scripts_are_never_executed(recipe, device):
    (recipe[0] / "main.py").write_text("raise RuntimeError('EXECUTED')")
    (recipe[0] / "replay.sh").write_text("exit 99")
    assert restore(recipe)["status"] == "SUCCESS"


def test_approval_hash_and_serial_are_mandatory(recipe, device):
    root = recipe[0]
    result = scene.restore_scene(
        root, device_serial="offline-123", approved_manifest_sha256="b" * 64
    )
    assert result["blockers"][0]["code"] == "approval_hash_mismatch"
    result = scene.restore_scene(
        root, device_serial="other", approved_manifest_sha256=recipe[2]()
    )
    assert result["blockers"][0]["code"] == "device_mismatch"
    assert not device[0].commands


@pytest.mark.parametrize(
    "command,value,code",
    [
        (("shell", "getprop", "ro.build.fingerprint"), "different", "build_mismatch"),
        (("shell", "settings", "get", "global", "wifi_on"), "1", "not_offline"),
        (("shell", "settings", "get", "global", "mobile_data"), "null", "not_offline"),
        (
            ("shell", "ip", "route", "show", "table", "all"),
            "default dev eth0",
            "not_offline",
        ),
        (
            ("shell", "ip", "-6", "route", "show", "table", "all"),
            "default dev tun0",
            "not_offline",
        ),
        (("shell", "dumpsys", "connectivity"), "unknown format", "unknown_network"),
        (
            ("shell", "dumpsys", "connectivity"),
            "Active default network: none\nNetworkAgentInfo VPN",
            "unknown_network",
        ),
        (
            ("shell", "pm", "path", PACKAGE),
            "package:/data/app/demo/base.apk\npackage:/data/app/demo/split.apk",
            "apk_unsupported",
        ),
        (
            ("shell", "sha256sum", "/data/app/demo/base.apk"),
            "b" * 64,
            "apk_hash_mismatch",
        ),
        (
            ("shell", "dumpsys", "package", PACKAGE),
            "pkgFlags=[ SYSTEM ]\nrequested permissions:\ninstall permissions:",
            "untrusted_package",
        ),
        (
            ("shell", "dumpsys", "package", PACKAGE),
            "pkgFlags=[ DEBUGGABLE ]\nrequested permissions:\n android.permission.INTERNET\ninstall permissions:",
            "permissions_unsupported",
        ),
    ],
)
def test_preflight_blockers_never_write(recipe, device, command, value, code):
    fake, _ = device
    fake.responses[command] = value
    result = restore(recipe)
    assert result["status"] == "BLOCKED", result
    assert result["blockers"][0]["code"] == code
    assert not fake.writes


def test_permission_prompt_blocks_before_write(recipe, device):
    device[0].ui_xml = UI.replace(PACKAGE, "com.android.permissioncontroller")
    assert restore(recipe)["blockers"][0]["code"] == "permission_prompt"
    assert not device[0].writes


@pytest.mark.parametrize(
    "failure",
    [
        "same_process",
        "foreground",
        "missing_selector",
        "ambiguous_selector",
        "screenshot",
    ],
)
def test_target_failure_never_reports_success(recipe, device, failure):
    fake, _ = device
    if failure == "same_process":
        fake.new = fake.current
    elif failure == "foreground":
        fake.responses[("shell", "dumpsys", "activity", "activities")] = (
            "mResumedActivity: ActivityRecord{1 u0 other.app/.MainActivity t1}"
        )
    elif failure == "missing_selector":
        fake.ui_xml = UI.replace('text="Ready"', 'text="Missing"')
    elif failure == "ambiguous_selector":
        fake.ui_xml = UI.replace("</hierarchy>", UI[11:-12] + "</hierarchy>")
    elif failure == "screenshot":
        fake.capture_failure = True
    result = restore(recipe)
    assert result["status"] == "FAILED", result
    assert result["receipt"]["status"] == "FAILED"
    assert result["receipt"]["actions"]
    assert (recipe[0] / result["receipt"]["path"]).is_file()


def test_pid_reuse_with_new_starttime_is_fresh(recipe, device):
    device[0].new = {"pid": "123", "starttime": "200"}
    assert restore(recipe)["status"] == "SUCCESS"


def test_expired_lease_no_commands(recipe, device):
    device[1]["alive"] = False
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "lease_lost"
    assert not device[0].commands


def test_lease_lost_after_force_stop_has_no_launch_or_tap(recipe, device):
    fake, state = device

    def lose(args):
        if args[:3] == ["shell", "am", "start"]:
            state["alive"] = False

    fake.before = lose
    result = restore(recipe)
    assert result["status"] == "FAILED"
    assert result["blockers"][0]["code"] == "lease_lost"
    assert len(fake.writes) == 1


def test_manifest_changed_during_preflight_no_writes(recipe, device):
    root, _, save = recipe
    approved = save()

    def change(args):
        if args[:3] == ["shell", "dumpsys", "package"]:
            (root / "scene_manifest.json").write_text("{}")

    device[0].before = change
    result = scene.restore_scene(
        root, device_serial="offline-123", approved_manifest_sha256=approved
    )
    assert result["status"] == "BLOCKED"
    assert not device[0].writes


def test_receipt_directory_symlink_fails_no_external_write(recipe, device):
    root = recipe[0]
    (root / "scene_restores").symlink_to(root / "nonexistent")
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert not device[0].commands
    assert result["blockers"][0]["code"] == "unsafe_path"
    assert result["receipt"] is None


def test_duplicate_json_field_is_blocked(recipe):
    (recipe[0] / "scene_manifest.json").write_text('{"schema":"a","schema":"b"}')
    assert scene.inspect_scene(recipe[0])["blockers"][0]["code"] == "invalid_schema"


def test_real_lease_expiry_cannot_be_renewed_or_commanded(monkeypatch, tmp_path):
    import importlib

    device_lease = importlib.import_module("phone_pilot.core.device_lease")
    manager = device_lease.DeviceLeaseManager(tmp_path / "locks")
    monkeypatch.setattr(device_lease, "DeviceLeaseManager", lambda: manager)
    with scene._lease("offline-123") as (guard, sid):
        _, meta = manager._get_paths("offline-123")
        data = json.loads(meta.read_text())
        data["expires_at"] = 0
        meta.write_text(json.dumps(data))
        with pytest.raises(scene.SceneError, match="Lease expired"):
            with guard():
                pytest.fail("Expired lease allowed command")


def test_real_lease_renew_failure_stops_commands(monkeypatch, tmp_path):
    import importlib
    from phone_pilot.core.safe_io import SafeDirectory

    device_lease = importlib.import_module("phone_pilot.core.device_lease")
    manager = device_lease.DeviceLeaseManager(tmp_path / "locks")
    monkeypatch.setattr(device_lease, "DeviceLeaseManager", lambda: manager)
    with scene._lease("offline-123") as (guard, _):
        monkeypatch.setattr(
            SafeDirectory,
            "write_bytes",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("metadata write failed")),
        )
        with pytest.raises(scene.SceneError, match="renewal failed"):
            with guard():
                pytest.fail("Failed renewal allowed command")


def test_real_lease_rejects_untrusted_dir_and_symlink_meta_without_truncating(
    monkeypatch, tmp_path
):
    import importlib

    device_lease = importlib.import_module("phone_pilot.core.device_lease")
    shared = tmp_path / "shared_locks"
    shared.mkdir(mode=0o777)
    shared.chmod(0o777)
    try:
        with pytest.raises(Exception):
            device_lease.DeviceLeaseManager(shared)
    finally:
        shared.chmod(0o700)

    lock_dir = tmp_path / "locks"
    manager = device_lease.DeviceLeaseManager(lock_dir)
    monkeypatch.setattr(device_lease, "DeviceLeaseManager", lambda: manager)
    victim = tmp_path / "victim.json"
    victim.write_text("do_not_truncate", encoding="utf-8")
    with scene._lease("offline-123") as (guard, _):
        _, meta = manager._get_paths("offline-123")
        meta.unlink()
        meta.symlink_to(victim)
        with pytest.raises(
            scene.SceneError, match="Lease expired|renewal failed|lease_lost"
        ):
            with guard():
                pytest.fail("Symlink metadata allowed command")
    assert victim.read_text(encoding="utf-8") == "do_not_truncate"


def test_real_adapter_nonzero_query_stops(monkeypatch):
    from phone_pilot.android.adb.runner import CommandRunner

    monkeypatch.setattr(
        CommandRunner,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "failed"),
    )
    adapter = scene._Android("offline-123")

    @contextmanager
    def guard():
        yield

    adapter.guard = guard
    with pytest.raises(scene.SceneError, match="Fixed command failed"):
        adapter.shell("getprop", "ro.build.fingerprint")


def test_loopback_routes_allowed_but_ethernet_vpn_never(recipe, device):
    fake, _ = device
    fake.responses[("shell", "ip", "route", "show", "table", "all")] = (
        "local 127.0.0.1 dev lo table local"
    )
    fake.responses[("shell", "ip", "-6", "route", "show", "table", "all")] = (
        "local ::1 dev lo table local"
    )
    assert restore(recipe)["status"] == "SUCCESS"


def test_logcat_buffer_header_is_not_new_evidence(recipe, device):
    fake, _ = device
    fake.responses[
        ("logcat", "-d", "-v", "threadtime", "-T", "11-17 14:00:00.000", "--pid=456")
    ] = "--------- beginning of main"
    result = restore(recipe)
    assert result["status"] == "FAILED"
    assert result["blockers"][0]["code"] == "evidence_failed"
    assert result["receipt"]["status"] == "FAILED"


def test_real_adapter_quotes_remote_date_argument(monkeypatch):
    from phone_pilot.android.adb.runner import CommandRunner

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "11-17 14:00:00.123456789", "")

    monkeypatch.setattr(CommandRunner, "run", run)
    adapter = scene._Android("offline-123")

    @contextmanager
    def guard():
        yield

    adapter.guard = guard
    adapter.shell("date", "+%m-%d %H:%M:%S.%N")
    assert calls[0][-1] == "'+%m-%d %H:%M:%S.%N'"


def test_real_execution_lease_rejects_second_interpreter(monkeypatch, tmp_path):
    import importlib

    device_lease = importlib.import_module("phone_pilot.core.device_lease")
    manager = device_lease.DeviceLeaseManager(tmp_path / "locks")
    monkeypatch.setattr(device_lease, "DeviceLeaseManager", lambda: manager)
    with scene._lease("offline-123"):
        with pytest.raises(scene.SceneError, match="Another scene interpreter"):
            with scene._lease("offline-123"):
                pytest.fail("Concurrent interpreter acquired device")


def test_real_guard_prevents_metadata_takeover_during_command(monkeypatch, tmp_path):
    import importlib
    import fcntl

    device_lease = importlib.import_module("phone_pilot.core.device_lease")
    manager = device_lease.DeviceLeaseManager(tmp_path / "locks")
    monkeypatch.setattr(device_lease, "DeviceLeaseManager", lambda: manager)
    with scene._lease("offline-123") as (guard, sid):
        with guard():
            flock, _ = manager._get_paths("offline-123")
            with flock.open("a+") as competing:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert manager.get_lock_status("offline-123")["lock_info"]["session_id"] == sid


def test_real_ui_adapter_uses_forced_tty_without_file_fallback(monkeypatch):
    from phone_pilot.android.adb.runner import CommandRunner

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, UI, "")

    monkeypatch.setattr(CommandRunner, "run", run)
    adapter = scene._Android("offline-123")

    @contextmanager
    def guard():
        yield

    adapter.guard = guard
    assert adapter.ui()[0] == UI
    assert calls[0][-6:] == [
        "shell",
        "-tt",
        "uiautomator",
        "dump",
        "--compressed",
        "/dev/tty",
    ]
    assert len(calls) == 1


@pytest.mark.parametrize("png", [b"", b"\x89PNG\r\n\x1a\n", b"\x89PNG\r\n\x1a\nbroken"])
def test_truncated_png_is_never_valid(png):
    with pytest.raises(scene.SceneError):
        scene._validate_png(png)


def test_missing_dumpsys_permission_section_uses_static_apk(recipe, device):
    device[0].responses[("shell", "dumpsys", "package", PACKAGE)] = (
        "pkgFlags=[ DEBUGGABLE ]"
    )
    assert restore(recipe)["status"] == "SUCCESS"


@pytest.mark.parametrize(
    "permissions,debuggable,code",
    [
        (True, True, "permissions_unsupported"),
        (False, False, "untrusted_package"),
        (False, True, None),
    ],
)
def test_static_apk_preflight_parses_actual_manifest(
    monkeypatch, permissions, debuggable, code
):
    import io
    import zipfile
    from xml.etree import ElementTree as etree

    axml = pytest.importorskip(
        "androguard.core.axml", reason="install [restore] for static APK parser tests"
    )

    manifest = (
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{PACKAGE}">'
        + (
            '<uses-permission android:name="android.permission.CAMERA" />'
            if permissions
            else ""
        )
        + f'<application android:debuggable="{str(debuggable).lower()}" /></manifest>'
    )

    class Parser:
        def __init__(self, data):
            assert data == manifest.encode()

        def is_valid(self):
            return True

        def get_xml_obj(self):
            return etree.fromstring(manifest.encode())

    monkeypatch.setattr(axml, "AXMLPrinter", Parser)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("AndroidManifest.xml", manifest)
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    if code:
        with pytest.raises(scene.SceneError) as failure:
            scene._apk_preflight(data, PACKAGE, digest)
        assert failure.value.code == code
    else:
        scene._apk_preflight(data, PACKAGE, digest)


@pytest.mark.parametrize(
    "command,value",
    [
        (("shell", "dumpsys", "power"), "mWakefulness=Asleep"),
        (("shell", "dumpsys", "power"), "unknown"),
        (("shell", "dumpsys", "window", "policy"), "isStatusBarKeyguard=true"),
        (("shell", "dumpsys", "window", "policy"), "unknown"),
    ],
)
def test_keyguard_or_sleep_blocks_before_any_device_write(
    recipe, device, command, value
):
    fake, _ = device
    fake.responses[command] = value
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "device_locked"
    assert not fake.writes


def test_final_prewrite_check_blocks_without_mutation(recipe, device, monkeypatch):
    count = 0

    def check(android):
        nonlocal count
        count += 1
        if count > 1:
            raise scene.SceneError("device_locked", "locked before first write")

    monkeypatch.setattr(scene, "_unlocked", check)
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert not device[0].writes


def test_lock_after_force_stop_prevents_launch_and_audits_attempt(recipe, device):
    fake, _ = device

    def lock_screen(args):
        if fake.writes:
            fake.responses[("shell", "dumpsys", "window", "policy")] = (
                "isStatusBarKeyguard=true"
            )

    fake.before = lock_screen
    result = restore(recipe)
    assert result["status"] == "FAILED"
    assert len(fake.writes) == 1
    assert len(result["receipt"]["actions"]) == 1
    assert result["receipt"]["actions"][0]["type"] == "force_stop"
    assert (recipe[0] / result["receipt"]["path"]).is_file()


def test_windows_capability_blocked_before_lease_or_device(recipe, device, monkeypatch):
    from phone_pilot.core import safe_io

    monkeypatch.setattr(safe_io.os, "name", "nt")
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert not device[0].commands
    assert not (recipe[0] / "scene_restores").exists()


@pytest.mark.parametrize("mode", [0o770, 0o707, 0o1777])
def test_untrusted_writable_ancestor_blocks_before_device(recipe, device, mode):
    import os

    ancestor = recipe[0].parent
    original = ancestor.stat().st_mode
    if mode & 0o1000 and os.getuid() == 0:
        pytest.skip("non-root sticky negative requires non-root test user")
    try:
        ancestor.chmod(mode)
        result = restore(recipe)
        assert result["status"] == "BLOCKED", result
        assert result["blockers"][0]["code"] == "unsafe_path"
        assert not device[0].commands
    finally:
        ancestor.chmod(original)


@pytest.mark.parametrize("failed", [False, True])
def test_output_swap_during_mutation_keeps_pinned_success_and_failure_dir(
    recipe, device, tmp_path, failed
):
    root = recipe[0]
    external = tmp_path / "outside"
    external.mkdir()
    moved = tmp_path / "pinned-output"
    fake, _ = device

    def swap(args):
        if args[:3] == ["shell", "am", "force-stop"]:
            target = root / "scene_restores"
            # Verify exact test paths before moving; never touch project files.
            assert target.parent == root and moved.parent == tmp_path
            target.rename(moved)
            target.symlink_to(external, target_is_directory=True)

    fake.before = swap
    fake.capture_failure = failed
    result = restore(recipe)
    assert result["status"] == ("FAILED" if failed else "SUCCESS"), result
    assert list(external.iterdir()) == []
    children = list(moved.iterdir())
    assert len(children) == 1
    receipt = json.loads((children[0] / "receipt.json").read_bytes())
    assert receipt["status"] == result["status"]
    assert "audit_error" not in result["receipt"]


def test_success_receipt_readback_failure_replaced_in_same_directory(
    recipe, device, monkeypatch
):
    from phone_pilot.core.safe_io import SafeDirectory

    original = SafeDirectory.read_bytes
    injected = False

    def fail_once(directory, name, *args, **kwargs):
        nonlocal injected
        data = original(directory, name, *args, **kwargs)
        if name == "receipt.json" and not injected:
            injected = True
            raise OSError("receipt readback failed")
        return data

    monkeypatch.setattr(SafeDirectory, "read_bytes", fail_once)
    result = restore(recipe)
    assert result["status"] == "FAILED", result
    children = list((recipe[0] / "scene_restores").iterdir())
    assert len(children) == 1
    assert json.loads((children[0] / "receipt.json").read_bytes())["status"] == "FAILED"
    assert set(result["evidence"]) == {"ui.xml", "screenshot.png", "logcat.txt"}


def test_fresh_log_marker_after_force_stop_before_launch(recipe, device):
    fake, _ = device

    def check(args):
        if args[:2] == ["shell", "date"]:
            assert fake.current is None
            assert len(fake.writes) == 1

    fake.before = check
    assert restore(recipe)["status"] == "SUCCESS"
    marker = next(
        i for i, args in enumerate(fake.commands) if args[:2] == ["shell", "date"]
    )
    stop = next(
        i
        for i, args in enumerate(fake.commands)
        if args[:3] == ["shell", "am", "force-stop"]
    )
    launch = next(
        i
        for i, args in enumerate(fake.commands)
        if args[:3] == ["shell", "am", "start"]
    )
    assert stop < marker < launch


def test_old_pid_reuse_log_before_boundary_never_passes(recipe, device):
    fake, _ = device
    fake.new = {"pid": "123", "starttime": "200"}
    fake.responses[("shell", "date", "+%m-%d %H:%M:%S.%N")] = "11-17 14:00:00.100000001"
    fake.responses[
        ("logcat", "-d", "-v", "threadtime", "-T", "11-17 14:00:00.101", "--pid=123")
    ] = "11-17 14:00:00.100 123 456 I Demo: OLD process Ready"
    result = restore(recipe)
    assert result["status"] == "FAILED", result
    assert result["blockers"][0]["code"] == "evidence_failed"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("11-17 14:00:00.123456789", "11-17 14:00:00.124"),
        ("11-17 23:59:59.999999999", "11-18 00:00:00.000"),
        ("12-31 23:59:59.999999999", "01-01 00:00:00.000"),
    ],
)
def test_log_boundary_rounds_up_with_date_carry(raw, expected):
    assert scene._log_boundary(raw) == expected


def test_inspect_rejects_symlink_ancestor_without_resolving(recipe):
    root = recipe[0]
    alias = root.parent / "archive-alias"
    alias.symlink_to(root, target_is_directory=True)
    result = scene.inspect_scene(alias)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "unsafe_path"
    assert result["manifest_sha256"] is None


def test_inspect_oversized_manifest_bounded_without_path_read(recipe, monkeypatch):
    root = recipe[0]
    (root / "scene_manifest.json").write_bytes(b" " * (1024 * 1024 + 1))
    monkeypatch.setattr(
        scene.Path, "read_bytes", lambda _: pytest.fail("unsafe path read")
    )
    result = scene.inspect_scene(root)
    assert result["status"] == "BLOCKED"
    assert result["manifest_sha256"] is None


def test_artifacts_stream_hash_without_path_read(recipe, device, monkeypatch):
    root, manifest, _ = recipe
    (root / "sub").mkdir()
    data = b"proof" * 1000
    (root / "sub" / "proof.txt").write_bytes(data)
    manifest["artifacts"] = [
        {"path": "sub/proof.txt", "sha256": hashlib.sha256(data).hexdigest()}
    ]
    monkeypatch.setattr(
        scene.Path, "read_bytes", lambda _: pytest.fail("unsafe path read")
    )
    assert restore(recipe)["status"] == "SUCCESS"


def test_artifact_subdirectory_symlink_is_blocked(recipe, device):
    root, manifest, _ = recipe
    (root / "sub").symlink_to(root, target_is_directory=True)
    manifest["artifacts"] = [{"path": "sub/scene_manifest.json", "sha256": APK_HASH}]
    result = restore(recipe)
    assert result["status"] == "BLOCKED"
    assert result["blockers"][0]["code"] == "unsafe_path"
    assert not device[0].commands


@pytest.mark.parametrize("disappear", [False, True])
def test_tap_resolves_layout_after_final_safety_queries(recipe, device, disappear):
    fake, _ = device
    ui_queries = []
    original_ui = fake.ui

    def ui():
        ui_queries.append(len(fake.commands))
        return original_ui()

    def drift(args):
        if len(fake.writes) == 2 and args[:3] == ["shell", "dumpsys", "power"]:
            fake.ui_xml = UI.replace("[20,40][80,100]", "[100,200][200,300]")
            if disappear:
                fake.ui_xml = fake.ui_xml.replace('text="Ready"', 'text="Gone"')
        if args[:3] == ["shell", "input", "tap"]:
            # UI resolution is the last device query before this exact dispatch.
            assert ui_queries[-1] == len(fake.commands)

    fake.ui = ui
    fake.before = drift
    result = restore(recipe)
    if disappear:
        assert result["status"] == "FAILED", result
        assert result["blockers"][0]["code"] == "selector_not_unique"
        assert len(fake.writes) == 2
    else:
        assert result["status"] == "SUCCESS", result
        assert fake.writes[-1][-2:] == ["150", "250"]
        assert fake.writes[-1][-2:] != ["50", "70"]


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("after_tap", [False, True])
def test_cancellation_audits_mutation_then_propagates_without_device_queries(
    recipe, device, cancellation, after_tap
):
    fake, _ = device
    command_count = None

    def cancel(args):
        nonlocal command_count
        if (not after_tap and args[:2] == ["shell", "date"]) or (
            after_tap and len(fake.writes) == 3
        ):
            command_count = len(fake.commands)
            raise cancellation("cancel scene")

    fake.before = cancel
    with pytest.raises(cancellation, match="cancel scene"):
        restore(recipe)
    assert len(fake.commands) == command_count
    assert len(fake.writes) == (3 if after_tap else 1)
    children = list((recipe[0] / "scene_restores").iterdir())
    assert len(children) == 1
    receipt = json.loads((children[0] / "receipt.json").read_bytes())
    assert receipt["status"] == "FAILED"
    assert len(receipt["actions"]) == len(fake.writes)
    assert receipt["error"]["message"] == "cancel scene"
