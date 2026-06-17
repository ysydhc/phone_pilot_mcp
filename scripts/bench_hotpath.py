#!/usr/bin/env python3
"""Micro-benchmark for phone_pilot hot paths (measurement only — changes nothing).

Pinpoints where time goes in the common "find -> tap -> verify" loop by timing
each segment separately:

  1. import cost           — cold-import of the driver/MCP stack
  2. ensure_adb_env        — first-time adb discovery (filesystem scan)
  3. device resolution     — _resolve_device_serial (adb + hdc scan)
  4. raw adb subprocess    — bare `adb devices` with NO post-command delay
  5. CMD_DELAY overhead     — what PHONE_PILOT_CMD_DELAY_S adds per command
  6. driver construction   — AndroidDriver(serial)
  7. UI dump + parse        — driver.ui.dump_ui_nodes()
  8. page-state collect     — _collect_page_elements (dump + filter + sort)
  9. screenshot (png)       — driver.screen.screenshot() + base64
 10. tap                    — driver.input.tap()

Usage:
    python scripts/bench_hotpath.py [--serial SERIAL] [--repeat N] [--no-delay]

--no-delay sets PHONE_PILOT_CMD_DELAY_S=0 for the whole run so you can see the
floor without the artificial sleeps. Run it both ways to quantify the delay tax.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
from contextlib import contextmanager


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

_results: dict[str, list[float]] = {}


@contextmanager
def timed(label: str):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = (time.perf_counter() - t0) * 1000.0
        _results.setdefault(label, []).append(dt)


def _report() -> None:
    print("\n" + "=" * 64)
    print(f"{'segment':<34}{'mean':>8}{'min':>8}{'max':>8}  (ms)")
    print("-" * 64)
    for label, samples in _results.items():
        mean = statistics.mean(samples)
        print(f"{label:<34}{mean:>8.1f}{min(samples):>8.1f}{max(samples):>8.1f}")
    print("=" * 64)
    # Highlight the artificial delay tax if both variants were measured.
    delay = float(os.getenv("PHONE_PILOT_CMD_DELAY_S", "0.4") or 0.4)
    print(f"\nPHONE_PILOT_CMD_DELAY_S = {delay}s  (added once per adb subprocess)")


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="phone_pilot hot-path micro-benchmark")
    ap.add_argument("--serial", default=None, help="device serial (auto if omitted)")
    ap.add_argument("--repeat", type=int, default=3, help="repeat count for hot ops")
    ap.add_argument(
        "--no-delay",
        action="store_true",
        help="force PHONE_PILOT_CMD_DELAY_S=0 to see the floor",
    )
    ap.add_argument(
        "--tap",
        action="store_true",
        help="actually perform a tap at screen center (mutates device state)",
    )
    ns = ap.parse_args(argv)

    if ns.no_delay:
        os.environ["PHONE_PILOT_CMD_DELAY_S"] = "0"

    # --- 1. import cost (cold) ---
    with timed("01_import_stack"):
        from phone_pilot.android.adb.utils import (
            ensure_adb_env,
            adb_executable,
        )
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.mcp._helpers import _resolve_device_serial, get_driver

    # --- 2. ensure_adb_env (first call does the filesystem scan) ---
    with timed("02_ensure_adb_env"):
        ensure_adb_env()

    # --- 4. raw adb subprocess, no delay (isolate pure subprocess cost) ---
    adb = adb_executable()
    for _ in range(ns.repeat):
        with timed("04_raw_adb_devices(no_delay)"):
            CommandRunner.run(
                [adb, "devices"], check=False, delay_s=0, log_output=False
            )

    # --- 5. same call WITH default delay, to show the tax ---
    for _ in range(ns.repeat):
        with timed("05_raw_adb_devices(+delay)"):
            CommandRunner.run([adb, "devices"], check=False, log_output=False)

    # --- 3. device resolution (auto = adb + hdc scan) ---
    for _ in range(ns.repeat):
        with timed("03_resolve_device(auto)"):
            serial, plat, err = _resolve_device_serial(ns.serial, "auto")
    if err:
        print(f"[bench] device resolution failed: {err}")
        _report()
        return 1

    # resolution pinned to android (skips hdc scan) — compare against auto
    for _ in range(ns.repeat):
        with timed("03b_resolve_device(android)"):
            _resolve_device_serial(serial, "android")

    print(f"[bench] using serial={serial} platform={plat}")

    # --- 6. driver construction ---
    for _ in range(ns.repeat):
        with timed("06_construct_driver"):
            driver = get_driver(serial, plat)

    # --- 7. UI dump + parse ---
    for _ in range(ns.repeat):
        with timed("07_dump_ui_nodes"):
            try:
                driver.ui.dump_ui_nodes()
            except Exception as e:
                print(f"[bench] dump_ui_nodes error: {e}")
                break

    # --- 8. page-state collect (dump + filter + sort) ---
    try:
        from phone_pilot.mcp._page_state_tools import _collect_page_elements

        for _ in range(ns.repeat):
            with timed("08_collect_page_elements"):
                _collect_page_elements(driver)
    except Exception as e:
        print(f"[bench] collect_page_elements error: {e}")

    # --- 9. screenshot (png) + base64 ---
    import base64

    for _ in range(ns.repeat):
        with timed("09_screenshot_png"):
            try:
                png = driver.screen.screenshot()
            except Exception as e:
                print(f"[bench] screenshot error: {e}")
                png = b""
                break
    if png:
        with timed("09b_base64_encode"):
            base64.b64encode(png).decode("ascii")

    # --- 10. tap (optional, mutates device) ---
    if ns.tap:
        try:
            w, h = driver.screen.get_screen_size()
            for _ in range(ns.repeat):
                with timed("10_tap_center"):
                    driver.input.tap(w // 2, h // 2)
        except Exception as e:
            print(f"[bench] tap error: {e}")

    _report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
