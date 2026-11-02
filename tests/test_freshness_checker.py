"""Unit tests for phone_pilot.core.guard.freshness_checker."""

from __future__ import annotations

import os
import pathlib
import zipfile

import pytest

from phone_pilot.core.guard import (
    FreshnessChecker,
    FreshnessReport,
    check_freshness,
    check_process_freshness,
    check_symbol_present,
    get_binary_mtime,
    scan_latest_source_mtime,
)


def _set_mtime(path: pathlib.Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def test_fresh_source_and_binary(tmp_path: pathlib.Path) -> None:
    """Case 1: bin_mtime >= src_mtime passes freshness check."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "MainActivity.kt"
    src_file.write_text("class MainActivity {}\n", encoding="utf-8")
    _set_mtime(src_file, 1000.0)

    # Ignored directory with newer file should not poison src_mtime
    ignored_dir = src_dir / "build"
    ignored_dir.mkdir()
    ignored_file = ignored_dir / "Generated.kt"
    ignored_file.write_text("// generated\n", encoding="utf-8")
    _set_mtime(ignored_file, 5000.0)

    apk_path = tmp_path / "app-debug.apk"
    apk_path.write_bytes(b"fake-apk-binary-payload")
    _set_mtime(apk_path, 1200.0)

    report = check_freshness(
        source_dir=src_dir,
        binary_path=apk_path,
        pid_mtime=1300.0,
        install_mtime=1250.0,
        pid=4321,
    )
    assert isinstance(report, FreshnessReport)
    assert report.ok is True
    assert report.reason == "fresh"
    assert report.src_mtime == 1000.0
    assert report.bin_mtime == 1200.0
    assert report.pid_mtime == 1300.0
    assert report.install_mtime == 1250.0
    assert report.pid == 4321

    d = report.to_dict()
    assert d["ok"] is True
    assert d["reason"] == "fresh"
    assert d["pid"] == 4321
    assert str(src_file) in d["details"]["latest_source_file"]


def test_stale_binary_blocked(tmp_path: pathlib.Path) -> None:
    """Case 2: src_mtime > bin_mtime is blocked with stale_binary reason."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "SplashAdView.m"
    src_file.write_text("@implementation SplashAdView @end\n", encoding="utf-8")
    _set_mtime(src_file, 2000.0)

    bin_path = tmp_path / "AppBinary"
    bin_path.write_bytes(b"\xcf\xfa\xed\xfeMachO")
    _set_mtime(bin_path, 1500.0)

    report = check_freshness(source_dir=src_dir, binary_path=bin_path)
    assert report.ok is False
    assert "stale_binary" in report.reason
    assert report.src_mtime == 2000.0
    assert report.bin_mtime == 1500.0


def test_app_bundle_directory_mtime(tmp_path: pathlib.Path) -> None:
    """Case 3: .app/.framework bundle directory resolves max inner file mtime."""
    app_bundle = tmp_path / "DemoApp.app"
    app_bundle.mkdir()
    info_plist = app_bundle / "Info.plist"
    info_plist.write_text("<plist></plist>", encoding="utf-8")
    _set_mtime(info_plist, 1100.0)

    nested_dir = app_bundle / "Frameworks"
    nested_dir.mkdir()
    inner_bin = nested_dir / "AdSDK"
    inner_bin.write_bytes(b"mach-o-inner-executable-symbol_v2")
    _set_mtime(inner_bin, 1850.0)

    assert get_binary_mtime(app_bundle) == 1850.0

    with pytest.raises(FileNotFoundError):
        get_binary_mtime(tmp_path / "NonExistent.app")

    with pytest.raises(FileNotFoundError):
        scan_latest_source_mtime(tmp_path / "missing_src")


def test_stale_process_blocked(tmp_path: pathlib.Path) -> None:
    """Case 4: running process started before binary/install timestamp is blocked."""
    ok_fresh, reason_fresh = check_process_freshness(
        pid_mtime=2000.0, install_mtime=1999.5, tolerance_s=1.0
    )
    assert ok_fresh is True
    assert reason_fresh == "fresh_process"

    ok_stale, reason_stale = check_process_freshness(
        pid_mtime=1000.0, install_mtime=1500.0, tolerance_s=0.5
    )
    assert ok_stale is False
    assert "stale_process" in reason_stale

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "App.swift"
    src_file.write_text("struct App {}\n", encoding="utf-8")
    _set_mtime(src_file, 1000.0)

    bin_path = tmp_path / "App.ipa"
    bin_path.write_bytes(b"binary-content")
    _set_mtime(bin_path, 1500.0)

    report = check_freshness(
        source_dir=src_dir,
        binary_path=bin_path,
        pid_mtime=1200.0,
        install_mtime=1500.0,
        pid=9988,
    )
    assert report.ok is False
    assert "stale_process" in report.reason
    assert report.pid == 9988


def test_symbol_present_in_binary_and_apk(tmp_path: pathlib.Path) -> None:
    """Case 5: symbol check works on raw binaries, .app bundles, and .apk/.zip archives."""
    # 5a. APK (zip containing classes.dex)
    apk_path = tmp_path / "release.apk"
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        zf.writestr(
            "classes.dex",
            b"dex\n035\x00Lcom/baidu/mobads/SplashClickFixV2;\x00",
        )
    _set_mtime(apk_path, 3000.0)

    assert check_symbol_present(apk_path, "SplashClickFixV2") is True
    assert check_symbol_present(apk_path, "NonExistentSymbolXYZ") is False

    # 5b. .app directory bundle
    app_dir = tmp_path / "Runner.app"
    app_dir.mkdir()
    exec_file = app_dir / "Runner"
    exec_file.write_bytes(b"\x00\x00_OBJC_CLASS_$_BDAdShakeGuard\x00\x00")
    assert check_symbol_present(app_dir, "BDAdShakeGuard") is True
    assert check_symbol_present(app_dir, "MissingObjCSymbol") is False

    # 5c. Full pipeline withFreshnessChecker wrapper
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    f = src_dir / "Fix.java"
    f.write_text("class SplashClickFixV2 {}", encoding="utf-8")
    _set_mtime(f, 2500.0)

    checker = FreshnessChecker(
        source_dir=src_dir,
        binary_path=apk_path,
        package_name="com.baidu.demo",
    )
    ok_report = checker.verify(symbol_name="SplashClickFixV2", pid_mtime=3100.0)
    assert ok_report.ok is True
    assert ok_report.symbol_present is True
    assert ok_report.details["package_name"] == "com.baidu.demo"

    fail_report = checker.verify(symbol_name="MissingFeatureFlag99", pid_mtime=3100.0)
    assert fail_report.ok is False
    assert fail_report.symbol_present is False
    assert "missing_symbol" in fail_report.reason


class TestFreshnessChecker:
    """Class-based anchor matching TDD spec table."""

    def test_fresh_source_and_binary(self, tmp_path: pathlib.Path) -> None:
        test_fresh_source_and_binary(tmp_path)

    def test_stale_binary_blocked(self, tmp_path: pathlib.Path) -> None:
        test_stale_binary_blocked(tmp_path)

    def test_app_bundle_directory_mtime(self, tmp_path: pathlib.Path) -> None:
        test_app_bundle_directory_mtime(tmp_path)

    def test_stale_process_blocked(self, tmp_path: pathlib.Path) -> None:
        test_stale_process_blocked(tmp_path)

    def test_symbol_present_in_binary_and_apk(self, tmp_path: pathlib.Path) -> None:
        test_symbol_present_in_binary_and_apk(tmp_path)
