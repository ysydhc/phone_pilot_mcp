"""构建产物新鲜度与进程看门狗 / Build Artifact Freshness & Process Watchdog.

在启动真机/模拟器执行 E2E 验证前，校验：
1. 二进制构建时间戳 (bin_mtime) 晚于源码最新修改时间 (src_mtime)
2. 运行中进程启动时间 (pid_mtime) 晚于包安装/构建时间 (install_mtime / bin_mtime)
3. 可选符号/字符串存在性静态探测 (symbol_present)
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from typing import Any, Sequence

from phone_pilot.core.log import _log

DEFAULT_SOURCE_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".java",
    ".kt",
    ".m",
    ".mm",
    ".swift",
    ".h",
    ".c",
    ".cpp",
    ".cs",
    ".xml",
    ".plist",
    ".gradle",
)

DEFAULT_IGNORE_DIRS: tuple[str, ...] = (
    ".git",
    "__pycache__",
    "build",
    "DerivedData",
    ".gradle",
    "node_modules",
    ".pytest_cache",
    ".tox",
    ".venv",
)


@dataclass
class FreshnessReport:
    """构建产物与运行进程的新鲜度核查报告 / Freshness verification report."""

    ok: bool
    src_mtime: float = 0.0
    bin_mtime: float = 0.0
    pid_mtime: float = 0.0
    install_mtime: float = 0.0
    pid: int = 0
    symbol_present: bool | None = None
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert report to a JSON-serializable dictionary."""
        return {
            "ok": self.ok,
            "src_mtime": self.src_mtime,
            "bin_mtime": self.bin_mtime,
            "pid_mtime": self.pid_mtime,
            "install_mtime": self.install_mtime,
            "pid": self.pid,
            "symbol_present": self.symbol_present,
            "reason": self.reason,
            "details": dict(self.details),
        }


def scan_latest_source_mtime(
    source_dir: str | pathlib.Path,
    extensions: Sequence[str] | None = None,
    ignore_dirs: Sequence[str] | None = None,
) -> tuple[float, str]:
    """Recursively scan source_dir for the latest modified source file.

    Returns (max_mtime, latest_file_path_str).
    Raises FileNotFoundError if source_dir does not exist.
    """
    root = pathlib.Path(source_dir)
    if not root.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    if root.is_file():
        return root.stat().st_mtime, str(root)

    ext_set = {
        e.lower() if e.startswith(".") else f".{e.lower()}"
        for e in (extensions if extensions is not None else DEFAULT_SOURCE_EXTENSIONS)
    }
    ignored = set(ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS)

    max_mtime: float = 0.0
    latest_file: str = ""
    fallback_mtime: float = 0.0
    fallback_file: str = ""

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in ignored and not d.startswith(".")
        ]
        for fname in filenames:
            if fname.startswith("."):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                mtime = os.stat(fpath).st_mtime
            except OSError:
                continue

            if mtime >= fallback_mtime:
                fallback_mtime = mtime
                fallback_file = fpath

            _, ext = os.path.splitext(fname)
            if not ext_set or ext.lower() in ext_set:
                if mtime >= max_mtime:
                    max_mtime = mtime
                    latest_file = fpath

    if latest_file:
        return max_mtime, latest_file
    return fallback_mtime, fallback_file


def get_binary_mtime(binary_path: str | pathlib.Path) -> float:
    """Return modification timestamp of a binary file or bundle directory (.app/.framework).

    Raises FileNotFoundError if binary_path does not exist.
    """
    path = pathlib.Path(binary_path)
    if not path.exists():
        raise FileNotFoundError(f"Binary artifact not found: {binary_path}")

    if path.is_file():
        return path.stat().st_mtime

    max_mtime: float = 0.0
    for dirpath, _, filenames in os.walk(path):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                mtime = os.stat(fpath).st_mtime
            except OSError:
                continue
            if mtime > max_mtime:
                max_mtime = mtime

    return max_mtime if max_mtime > 0.0 else path.stat().st_mtime


def check_process_freshness(
    pid_mtime: float,
    install_mtime: float,
    tolerance_s: float = 1.0,
) -> tuple[bool, str]:
    """Verify that the running process started at or after the binary/install timestamp."""
    if pid_mtime + tolerance_s >= install_mtime:
        return True, "fresh_process"
    return (
        False,
        f"stale_process: pid_mtime ({pid_mtime:.3f}) < install_mtime ({install_mtime:.3f})",
    )


def _file_contains_bytes(file_path: pathlib.Path, needle: bytes) -> bool:
    try:
        data = file_path.read_bytes()
        return needle in data
    except OSError:
        return False


def _zip_contains_bytes(zip_path: pathlib.Path, needle: bytes) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # Prioritize classes*.dex or Mach-O payload binaries first
            prioritized = sorted(
                names,
                key=lambda n: (
                    0 if (n.endswith(".dex") or "/Payload/" in n or n.startswith("Payload/")) else 1,
                    n,
                ),
            )
            for name in prioritized:
                try:
                    with zf.open(name, "r") as entry:
                        if needle in entry.read():
                            return True
                except Exception:
                    continue
    except (zipfile.BadZipFile, OSError):
        return False
    return False


def check_symbol_present(
    binary_path: str | pathlib.Path,
    symbol_name: str,
) -> bool:
    """Verify whether symbol_name is compiled into binary_path (.apk, .ipa, .app, Mach-O, .so, etc.).

    Raises FileNotFoundError if binary_path does not exist.
    """
    path = pathlib.Path(binary_path)
    if not path.exists():
        raise FileNotFoundError(f"Binary artifact not found: {binary_path}")

    if not symbol_name:
        return True

    needle = symbol_name.encode("utf-8")

    # Layer 1: Fast pure-Python byte / archive scan
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in {".apk", ".ipa", ".jar", ".aar"} or zipfile.is_zipfile(path):
            if _zip_contains_bytes(path, needle):
                return True
        if _file_contains_bytes(path, needle):
            return True
    elif path.is_dir():
        for dirpath, _, filenames in os.walk(path):
            for fname in filenames:
                fpath = pathlib.Path(dirpath) / fname
                if _file_contains_bytes(fpath, needle):
                    return True

    # Layer 2: Optional CLI fallback (strings / otool) for stripped/encoded tables
    if path.is_file():
        strings_bin = shutil.which("strings")
        if strings_bin:
            try:
                proc = subprocess.run(
                    [strings_bin, str(path)],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if symbol_name in proc.stdout:
                    return True
            except Exception:
                pass

    return False


def _query_driver_process_state(
    driver: Any,
    package_name: str,
) -> tuple[int, float | None, float | None]:
    """Optional helper to extract (pid, pid_mtime, install_mtime) from a driver if supported."""
    pid = 0
    pid_mtime: float | None = None
    install_mtime: float | None = None
    if driver is None or not package_name:
        return pid, pid_mtime, install_mtime

    try:
        if hasattr(driver, "get_process_info"):
            info = driver.get_process_info(package_name)
            if isinstance(info, dict):
                pid = int(info.get("pid", 0) or 0)
                if info.get("pid_mtime") is not None:
                    pid_mtime = float(info["pid_mtime"])
                if info.get("install_mtime") is not None:
                    install_mtime = float(info["install_mtime"])
                return pid, pid_mtime, install_mtime
    except Exception as exc:
        _log(f"[freshness_checker] driver.get_process_info failed: {exc}")

    # Real AndroidDriver fallback via ADB shell (pidof + /proc/<pid> stat + dumpsys package)
    serial = getattr(driver, "device_serial", None)
    platform = str(getattr(driver, "platform", "") or "").lower()
    if serial and (not platform or platform == "android"):
        adb_bin = shutil.which("adb") or "adb"
        try:
            proc_pid = subprocess.run(
                [adb_bin, "-s", str(serial), "shell", f"pidof {package_name}"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            tokens = (proc_pid.stdout or "").strip().split()
            if tokens and tokens[0].isdigit():
                pid = int(tokens[0])
                proc_stat = subprocess.run(
                    [adb_bin, "-s", str(serial), "shell", f"stat -c %Y /proc/{pid}"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                stat_str = (proc_stat.stdout or "").strip()
                if stat_str.isdigit():
                    pid_mtime = float(stat_str)
        except Exception as exc:
            _log(f"[freshness_checker] adb pid/proc query failed: {exc}")

        try:
            proc_pkg = subprocess.run(
                [adb_bin, "-s", str(serial), "shell", f"dumpsys package {package_name}"],
                capture_output=True,
                text=True,
                timeout=6.0,
                check=False,
            )
            m = re.search(
                r"lastUpdateTime=(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
                proc_pkg.stdout or "",
            )
            if m:
                dt = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                install_mtime = dt.timestamp()
        except Exception as exc:
            _log(f"[freshness_checker] adb dumpsys package query failed: {exc}")

    return pid, pid_mtime, install_mtime


def check_freshness(
    source_dir: str | pathlib.Path | None = None,
    binary_path: str | pathlib.Path | None = None,
    *,
    package_name: str = "",
    pid_mtime: float | None = None,
    install_mtime: float | None = None,
    pid: int = 0,
    symbol_name: str | None = None,
    driver: Any = None,
    tolerance_s: float = 0.0,
) -> FreshnessReport:
    """Execute the full Pre-E2E binary freshness & process watchdog pipeline.

    Pipeline stages:
    1. Source vs Binary mtime check
    2. Binary/Install vs Running Process PID start time check
    3. Optional Symbol presence check
    """
    details: dict[str, Any] = {}
    if package_name:
        details["package_name"] = package_name

    src_mtime: float = 0.0
    bin_mtime: float = 0.0

    if source_dir is not None:
        src_mtime, latest_src_file = scan_latest_source_mtime(source_dir)
        details["latest_source_file"] = latest_src_file
        details["source_dir"] = str(source_dir)

    if binary_path is not None:
        bin_mtime = get_binary_mtime(binary_path)
        details["binary_path"] = str(binary_path)

    if driver is not None and package_name:
        drv_pid, drv_pid_mtime, drv_install_mtime = _query_driver_process_state(
            driver, package_name
        )
        if not pid and drv_pid:
            pid = drv_pid
        if pid_mtime is None and drv_pid_mtime is not None:
            pid_mtime = drv_pid_mtime
        if install_mtime is None and drv_install_mtime is not None:
            install_mtime = drv_install_mtime

    eff_install_mtime = install_mtime if install_mtime is not None else bin_mtime
    eff_pid_mtime = pid_mtime if pid_mtime is not None else 0.0

    # Pre-compute Symbol Probe if requested so diagnostics always include symbol_present
    symbol_present: bool | None = None
    if symbol_name and binary_path is not None:
        symbol_present = check_symbol_present(binary_path, symbol_name)
        details["symbol_name"] = symbol_name

    # Stage 1: Source vs Binary
    if source_dir is not None and binary_path is not None:
        if bin_mtime + tolerance_s < src_mtime:
            return FreshnessReport(
                ok=False,
                src_mtime=src_mtime,
                bin_mtime=bin_mtime,
                pid_mtime=eff_pid_mtime,
                install_mtime=eff_install_mtime,
                pid=pid,
                symbol_present=symbol_present,
                reason="stale_binary: source modified after binary build",
                details=details,
            )

    # Stage 2: Binary/Install vs Process PID
    if pid_mtime is not None and eff_install_mtime > 0:
        if pid_mtime + tolerance_s < eff_install_mtime:
            return FreshnessReport(
                ok=False,
                src_mtime=src_mtime,
                bin_mtime=bin_mtime,
                pid_mtime=eff_pid_mtime,
                install_mtime=eff_install_mtime,
                pid=pid,
                symbol_present=symbol_present,
                reason="stale_process: running PID started before binary/install timestamp",
                details=details,
            )

    # Stage 3: Symbol Probe
    if symbol_name and binary_path is not None and not symbol_present:
        return FreshnessReport(
            ok=False,
            src_mtime=src_mtime,
            bin_mtime=bin_mtime,
            pid_mtime=eff_pid_mtime,
            install_mtime=eff_install_mtime,
            pid=pid,
            symbol_present=False,
            reason=f"missing_symbol: {symbol_name!r} not found in binary",
            details=details,
        )

    return FreshnessReport(
        ok=True,
        src_mtime=src_mtime,
        bin_mtime=bin_mtime,
        pid_mtime=eff_pid_mtime,
        install_mtime=eff_install_mtime,
        pid=pid,
        symbol_present=symbol_present,
        reason="fresh",
        details=details,
    )


class FreshnessChecker:
    """Configurable wrapper for Pre-E2E binary & process freshness verification."""

    def __init__(
        self,
        source_dir: str | pathlib.Path | None = None,
        binary_path: str | pathlib.Path | None = None,
        *,
        package_name: str = "",
        tolerance_s: float = 0.0,
        driver: Any = None,
    ) -> None:
        self.source_dir = source_dir
        self.binary_path = binary_path
        self.package_name = package_name
        self.tolerance_s = tolerance_s
        self.driver = driver

    def verify(
        self,
        *,
        source_dir: str | pathlib.Path | None = None,
        binary_path: str | pathlib.Path | None = None,
        package_name: str | None = None,
        pid_mtime: float | None = None,
        install_mtime: float | None = None,
        pid: int = 0,
        symbol_name: str | None = None,
        tolerance_s: float | None = None,
    ) -> FreshnessReport:
        """Run freshness check merging instance defaults with call-site overrides."""
        return check_freshness(
            source_dir=source_dir if source_dir is not None else self.source_dir,
            binary_path=binary_path if binary_path is not None else self.binary_path,
            package_name=package_name if package_name is not None else self.package_name,
            pid_mtime=pid_mtime,
            install_mtime=install_mtime,
            pid=pid,
            symbol_name=symbol_name,
            driver=self.driver,
            tolerance_s=tolerance_s if tolerance_s is not None else self.tolerance_s,
        )
