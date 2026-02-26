#!/usr/bin/env python3
"""
Interactive recorder:
- Starts `adb shell getevent -lt` and records your touches.
- When you press Enter, converts the captured touches into a Monkey script.
- Optionally pushes the script and executes Monkey for quick replay.

Designed to reuse the existing `covert_touch` converter and `MonkeyRunner`.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import List, Optional

from phone_pilot.android.touch.convert import parse_getevent, to_monkey_script
from phone_pilot.android.touch.monkey import MonkeyRunner
import phone_pilot.android.adb.utils as _adb


def adb_prefix(device: Optional[str]) -> List[str]:
    """Backward-compatible wrapper. Prefer: adb_utils.adb_prefix()."""
    return _adb.adb_prefix(device)


def wait_for_device(device: Optional[str]) -> None:
    """Backward-compatible wrapper. Prefer: adb_utils.wait_for_device()."""
    return _adb.wait_for_device(device)


def push_script(device: Optional[str], local: pathlib.Path, remote: str) -> None:
    """Backward-compatible wrapper. Prefer: adb_utils.push_script()."""
    return _adb.push_script(device, str(local), remote)


@dataclasses.dataclass
class GeteventRecording:
    device: Optional[str]
    raw_log: pathlib.Path
    proc: subprocess.Popen[str]
    pump_thread: threading.Thread
    stop_event: threading.Event
    started_at: float


def start_getevent_recording(device: Optional[str], raw_log: pathlib.Path) -> GeteventRecording:
    """Start `adb shell getevent -lt` and pump stdout to raw_log in a background thread."""
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    cmd = adb_prefix(device) + ["shell", "getevent", "-lt"]
    # IMPORTANT: stderr for logs so MCP stdio transport isn't corrupted.
    print(f"[recorder] 开始录制触摸事件: {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stop_event = threading.Event()

    def pump_stdout() -> None:
        """Continuously write adb getevent output to raw log file."""
        with open(raw_log, "w", encoding="utf-8") as f:
            assert proc.stdout is not None
            for line in proc.stdout:
                f.write(line)
                if stop_event.is_set():
                    break

    pump_thread = threading.Thread(target=pump_stdout, daemon=True)
    pump_thread.start()
    return GeteventRecording(
        device=device,
        raw_log=raw_log,
        proc=proc,
        pump_thread=pump_thread,
        stop_event=stop_event,
        started_at=time.time(),
    )


def start_getevent_recording_detached(
    device: Optional[str],
    raw_log: pathlib.Path,
    *,
    err_log: Optional[pathlib.Path] = None,
) -> int:
    """
    Start `adb shell getevent -lt` writing directly to raw_log (no pump thread).

    This is safer for short-lived MCP processes because the adb process can
    continue recording even if the MCP server process exits.

    Returns: local adb process pid.
    """
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    if err_log is None:
        err_log = raw_log.parent / "getevent.err"
    err_log.parent.mkdir(parents=True, exist_ok=True)

    cmd = adb_prefix(device) + ["shell", "getevent", "-lt"]
    print(f"[recorder] 开始录制触摸事件(detached): {' '.join(cmd)}", file=sys.stderr)

    out_f = open(raw_log, "w", encoding="utf-8")
    err_f = open(err_log, "w", encoding="utf-8")
    # Detach from the parent session so it survives client/server lifetime.
    popen_kwargs = dict(stdout=out_f, stderr=err_f, text=True)
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
    return int(proc.pid)


def stop_getevent_recording_pid(pid: int, *, timeout_s: float = 3.0) -> None:
    """
    Stop a detached recording by local adb pid (best-effort).
    """
    if pid <= 0:
        return
    try:
        os.kill(pid, 15)  # SIGTERM
    except Exception:
        return
    # Best-effort wait by polling.
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            os.kill(pid, 0)
        except Exception:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, 9)  # SIGKILL
    except Exception:
        return


def stop_getevent_recording(recording: GeteventRecording, *, timeout_s: float = 3.0) -> None:
    """Stop a recording started by start_getevent_recording()."""
    recording.stop_event.set()
    recording.proc.terminate()
    try:
        recording.proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        recording.proc.kill()
    recording.pump_thread.join(timeout=timeout_s)
    if recording.proc.stderr:
        err = recording.proc.stderr.read()
        if err:
            print(err, file=sys.stderr, end="")


def record_getevent(device: Optional[str], raw_log: pathlib.Path) -> None:
    """Interactive: start `adb shell getevent -lt` and write output to raw_log until user stops."""
    recording = start_getevent_recording(device, raw_log)

    try:
        input("[recorder] 请在手机上操作，完成后按回车结束录制...\n")
    except KeyboardInterrupt:
        print("\n[recorder] 收到中断信号，准备停止录制", file=sys.stderr)
    finally:
        stop_getevent_recording(recording)

    print(f"[recorder] 已保存原始事件到 {raw_log}", file=sys.stderr)


def convert_to_monkey(raw_log: pathlib.Path, monkey_out: pathlib.Path, keep_device: Optional[str], slot: int) -> None:
    """Convert raw getevent log to Monkey script file."""
    with open(raw_log, "r", encoding="utf-8") as f:
        actions = list(parse_getevent(f, keep_device, slot))
    lines = to_monkey_script(actions)
    monkey_out.parent.mkdir(parents=True, exist_ok=True)
    monkey_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[recorder] 已生成 Monkey 脚本: {monkey_out}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for touch recording."""
    p = argparse.ArgumentParser(description="Record touches and convert to Monkey script.")
    p.add_argument("--device", "-s", help="ADB 设备序列号，可选")
    p.add_argument("--raw-log", default="getevent.log", help="保存 getevent 原始日志的路径 (默认: getevent.log)")
    p.add_argument("--monkey-out", default="auto.monkey", help="输出 Monkey 脚本路径 (默认: auto.monkey)")
    p.add_argument("--keep-device", help="只保留包含该子串的 input 设备名，例如 /dev/input/event2")
    p.add_argument("--slot", type=int, default=0, help="触摸槽 (ABS_MT_SLOT) 默认 0")
    p.add_argument("--run-monkey", action="store_true", help="生成后立即推送并执行 Monkey")
    p.add_argument("--package", help="Monkey -p 的包名 (仅 run-monkey 时使用)")
    p.add_argument("--remote-script", default="/data/local/tmp/auto.mks", help="设备上保存脚本的路径")
    p.add_argument("--throttle", type=int, default=80, help="Monkey throttle 毫秒，默认 80")
    p.add_argument("--seed", type=int, default=1234, help="Monkey 随机种子，默认 1234")
    p.add_argument("--count", type=int, default=1, help="Monkey 次数，默认 1")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for interactive touch recorder."""
    args = build_parser().parse_args(argv)

    raw_log = pathlib.Path(args.raw_log).expanduser().resolve()
    monkey_out = pathlib.Path(args.monkey_out).expanduser().resolve()

    wait_for_device(args.device)
    record_getevent(args.device, raw_log)
    convert_to_monkey(raw_log, monkey_out, args.keep_device, args.slot)

    if args.run_monkey:
        push_script(args.device, monkey_out, args.remote_script)
        MonkeyRunner.run_monkey(
            args.device,
            args.package,
            args.remote_script,
            args.throttle,
            args.seed,
            args.count,
        )

    print("[recorder] 完成", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

