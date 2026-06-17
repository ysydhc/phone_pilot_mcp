#!/usr/bin/env python3
"""Decompose where the ~4s UI dump time goes (measurement only).

Splits dump_ui_nodes into its raw pieces so we can tell whether the cost is:
  - the `uiautomator dump` command itself (device-side, unavoidable-ish)
  - the /dev/tty vs file fallback path
  - the `cat` read-back
  - XML parsing in Python
  - keep_screen_alive / _get_screen_timeout side calls

Run from repo root:
    PYTHONPATH=. python scripts/bench_dump.py [--serial S] [--repeat N]
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


def _report() -> None:
    print("\n" + "=" * 66)
    print(f"{'segment':<36}{'mean':>9}{'min':>9}{'max':>9}  ms")
    print("-" * 66)
    for k, v in _results.items():
        print(f"{k:<36}{statistics.mean(v):>9.1f}{min(v):>9.1f}{max(v):>9.1f}")
    print("=" * 66)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None)
    ap.add_argument("--repeat", type=int, default=5)
    ns = ap.parse_args(argv)

    from phone_pilot.android.adb.utils import ensure_adb_env, adb_executable, adb_prefix
    from phone_pilot.android.ui.automator import (
        dump_ui_xml,
        parse_uiautomator_nodes,
        keep_screen_alive,
        _get_screen_timeout,
    )

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
    print(f"[bench] serial={serial}")
    pre = adb_prefix(serial)

    def raw(cmd, timeout=8.0):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    # --- A. bare `uiautomator dump --compressed /dev/tty` (the fast path) ---
    for _ in range(ns.repeat):
        with timed("A_dump_compressed_tty"):
            raw(pre + ["shell", "uiautomator", "dump", "--compressed", "/dev/tty"])

    # --- B. bare `uiautomator dump` (no compressed) to file ---
    for _ in range(ns.repeat):
        with timed("B_dump_nocompress_file"):
            raw(pre + ["shell", "uiautomator", "dump"])

    # --- C. cat read-back of the dumped file ---
    for _ in range(ns.repeat):
        with timed("C_cat_window_dump"):
            raw(pre + ["shell", "cat", "/sdcard/window_dump.xml"])

    # --- D. _get_screen_timeout (a settings query, called every dump) ---
    for _ in range(ns.repeat):
        with timed("D_get_screen_timeout"):
            try:
                _get_screen_timeout(serial)
            except Exception:
                pass

    # --- E. keep_screen_alive (may send WAKEUP) ---
    for _ in range(ns.repeat):
        with timed("E_keep_screen_alive"):
            keep_screen_alive(serial)

    # --- F. full dump_ui_xml (what the driver actually calls) ---
    xml = ""
    for _ in range(ns.repeat):
        with timed("F_dump_ui_xml(full)"):
            res = dump_ui_xml(serial)
            xml = res.get("xml", "")
    print(f"[bench] dump method={res.get('method')} xml_len={len(xml)}")

    # --- G. XML parse only (Python-side) ---
    if xml:
        for _ in range(ns.repeat):
            with timed("G_parse_xml_nodes"):
                nodes = parse_uiautomator_nodes(xml)
        print(f"[bench] parsed nodes={len(nodes)}")

    _report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
