"""Tests for open_deeplink shell-quoting (injection fix, B23)."""

from __future__ import annotations

from unittest.mock import patch

from phone_pilot.android.device.utils import open_deeplink


class _FakeProc:
    returncode = 0
    stdout = ""
    stderr = ""


def _run_and_capture_cmd(uri: str, package: str | None = None) -> list[str]:
    with patch(
        "phone_pilot.android.device.utils.CommandRunner.run",
        return_value=_FakeProc(),
    ) as mock_run:
        open_deeplink("fake_serial", uri, package=package)
    return mock_run.call_args[0][0]


def test_uri_with_semicolon_is_quoted():
    # B23: uri 含分号必须被 quote 包裹，不能裸露在命令串中
    cmd = _run_and_capture_cmd("admtest://open?a=1;rm -rf /sdcard")
    joined = " ".join(cmd)
    assert "'admtest://open?a=1;rm -rf /sdcard'" in joined


def test_uri_with_dollar_is_quoted():
    # B23: $() 命令替换必须被 quote
    cmd = _run_and_capture_cmd("scheme://x?v=$(reboot)")
    assert any("$(reboot)" in part and part.startswith("'") for part in cmd)


def test_plain_uri_still_works():
    # B23: 无特殊字符时 quote 后仍等于原串（shlex.quote 幂等性）
    cmd = _run_and_capture_cmd("admtest://open?type=interstitial")
    assert "admtest://open?type=interstitial" in " ".join(cmd)


def test_package_is_quoted():
    # B23: package 参数同样需要 quote
    cmd = _run_and_capture_cmd("scheme://x", package="com.a;reboot")
    assert "'com.a;reboot'" in " ".join(cmd)
