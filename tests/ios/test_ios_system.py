"""
Tests for phone_pilot.ios.system — simctl-backed system ops (clipboard, install,
uninstall, execute_shell, device listing). Subprocess mocked.
"""

import subprocess
from unittest.mock import patch

from phone_pilot.ios import system


SERIAL = "00008030-001234560A80802E"


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["mock"], returncode, stdout, stderr)


def _sp():
    return patch.object(system.pmd_utils, "simctl_prefix", return_value=["simctl"])


class TestClipboard:
    def test_get_clipboard(self):
        with (
            _sp(),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(stdout="hello clip")
            ),
        ):
            r = system.get_clipboard(SERIAL)
        assert r["ok"] is True
        assert r["text"] == "hello clip"

    def test_set_clipboard(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["input"] = kw.get("input_text")
            return _completed(returncode=0)

        with _sp(), patch.object(system.pmd_runner, "run", side_effect=fake_run):
            r = system.set_clipboard(SERIAL, "copythis")
        assert r["ok"] is True
        assert "pbcopy" in captured["cmd"]


class TestInstallUninstall:
    def test_install_app(self):
        with (
            _sp(),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(returncode=0)
            ),
        ):
            r = system.install_app(SERIAL, "/tmp/MyApp.app")
        assert r["ok"] is True

    def test_install_app_missing_path(self):
        r = system.install_app(SERIAL, "")
        assert r["ok"] is False

    def test_uninstall_app(self):
        with (
            _sp(),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(returncode=0)
            ),
        ):
            r = system.uninstall_app(SERIAL, "com.example.a")
        assert r["ok"] is True

    def test_install_app_real_device(self):
        with (
            patch.object(system.pmd_utils, "detect_form_factor", return_value="real"),
            patch.object(system.pmd_utils, "pmd_executable", return_value="pmd"),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(returncode=0)
            ) as mock_run,
        ):
            r = system.install_app(SERIAL, "/tmp/MyApp.ipa")
        assert r["ok"] is True
        assert r["engine"] == "pymobiledevice3"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "apps" in cmd and "install" in cmd

    def test_uninstall_app_real_device(self):
        with (
            patch.object(system.pmd_utils, "detect_form_factor", return_value="real"),
            patch.object(system.pmd_utils, "pmd_executable", return_value="pmd"),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(returncode=0)
            ) as mock_run,
        ):
            r = system.uninstall_app(SERIAL, "com.example.a")
        assert r["ok"] is True
        assert r["engine"] == "pymobiledevice3"
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "apps" in cmd and "uninstall" in cmd


class TestExecuteShell:
    def test_execute_shell_allows_whitelisted(self):
        with (
            _sp(),
            patch.object(
                system.pmd_runner,
                "run",
                return_value=_completed(stdout="Darwin", returncode=0),
            ),
        ):
            r = system.execute_shell(SERIAL, "uname -a")
        assert r["ok"] is True
        assert "Darwin" in r["stdout"]

    def test_execute_shell_blocks_non_whitelisted(self):
        r = system.execute_shell(SERIAL, "rm -rf /")
        assert r["ok"] is False
        assert "whitelist" in r["error"]


class TestListDevices:
    def test_list_ios_devices_returns_entries(self):
        payload = (
            '{"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-4": '
            '[{"udid": "%s", "name": "iPhone 16", "state": "Booted"}, '
            '{"udid": "ZZZ", "name": "iPad", "state": "Shutdown"}]}}' % SERIAL
        )
        with (
            _sp(),
            patch.object(
                system.pmd_runner, "run", return_value=_completed(stdout=payload)
            ),
        ):
            devs = system.list_ios_devices()
        # only booted devices
        assert any(d["serial"] == SERIAL and d["platform"] == "ios" for d in devs)
        assert all(d["serial"] != "ZZZ" for d in devs)
        entry = next(d for d in devs if d["serial"] == SERIAL)
        assert entry["description"] == "iPhone 16"
