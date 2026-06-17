#!/usr/bin/env python3
"""Compare `uiautomator dump` vs `--compressed` (measurement only).

Tests whether the --compressed flag affects device-side dump time and XML size.
--compressed tells UIAutomator to skip nodes that don't affect the layout/text,
producing a smaller tree — in theory faster to walk and serialize.

Run from repo root:
    PYTHONPATH=. python scripts/bench_compressed.py [--serial S] [--repeat N]
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import time
from contextlib import contextmanager

_results: dict[str, list[float]] = {}


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _results.setdefault(label, []).append((time.perf_counter() - t0) * 1000.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None)
    ap.add_argument("--repeat", type=int, default=6)
    ns = ap.parse_args(argv)

    from phone_pilot.android.adb.utils import ensure_adb_env, adb_executable, adb_prefix
    from phone_pilot.android.ui.automator import parse_uiautomator_nodes

    ensure_adb_env()
    adb = adb_executable()
    serial = ns.serial
    if not serial:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True).stdout
        for line in out.splitlines()[1:]:
            if "\tdevice" in line:
                serial = line.split("\t")[0]
                break
    if not serial:
        print("no device")
        return 1
    print(f"[bench] serial={serial}  repeat={ns.repeat}")
    pre = adb_prefix(serial)

    def raw(cmd, timeout=10.0):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    sizes: dict[str, int] = {}
    counts: dict[str, int] = {}

    def measure(label: str, dump_args: list[str]):
        for _ in range(ns.repeat):
            with timed(label):
                raw(pre + ["shell", "uiautomator", "dump"] + dump_args)
            # read back + parse (outside timing for size/count only on last)
        cat = raw(pre + ["shell", "cat", "/sdcard/window_dump.xml"]).stdout or ""
        idx = cat.find("<hierarchy")
        xml = cat[idx:] if idx >= 0 else ""
        sizes[label] = len(xml)
        try:
            counts[label] = len(parse_uiautomator_nodes(xml)) if xml else -1
        except Exception:
            counts[label] = -1

    # warm-up (first dump after boot can be an outlier)
    raw(pre + ["shell", "uiautomator", "dump"])

    measure("compressed_file", ["--compressed"])
    measure("nocompress_file", [])

    print("\n" + "=" * 60)
    print(f"{'variant':<22}{'mean':>8}{'min':>8}{'max':>8}  ms")
    print("-" * 60)
    for k, v in _results.items():
        print(f"{k:<22}{statistics.mean(v):>8.1f}{min(v):>8.1f}{max(v):>8.1f}")
    print("-" * 60)
    for k in _results:
        print(f"{k:<22} xml_bytes={sizes.get(k, '?'):<8} nodes={counts.get(k, '?')}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
