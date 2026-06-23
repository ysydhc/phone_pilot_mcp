"""Logcat MCP tools: start, stop, and search logcat/hilog capture."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _resolve_device_serial
from phone_pilot.mcp._state import _state_lock, _logcat_state


def _phone_start_logcat_sync(
    device_serial: str | None,
    output_path: str,
    tags: str,
    exclude_tags: str,
    level: str,
    process: str,
    out_dir: str,
    platform: str,
) -> dict:
    """Sync body for phone_start_logcat."""
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    # 检查是否已在采集 / Check if already capturing
    with _state_lock:
        if resolved_serial in _logcat_state:
            return {
                "ok": False,
                "error": "already_capturing",
                "device_serial": resolved_serial,
            }

    if not output_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_root = Path(out_dir).expanduser().resolve()
        output_path = str(out_root / "logcat" / f"{ts}_{resolved_serial}.log")
        out_root.joinpath("logcat").mkdir(parents=True, exist_ok=True)

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        res = driver.start_log_capture(
            output_path,
            tags=tags,
            exclude_tags=exclude_tags,
            level=level,
            process=process,
        )
        if not res.get("ok"):
            return {
                "ok": False,
                "error": res.get("error", "unknown"),
                "device_serial": resolved_serial,
            }

        with _state_lock:
            _logcat_state[resolved_serial] = output_path
        return {
            "ok": True,
            "path": output_path,
            "pid": res.get("pid"),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_start_logcat(
    device_serial: str = "",
    output_path: str = "",
    tags: str = "",
    exclude_tags: str = "",
    level: str = "",
    process: str = "",
    out_dir: str = "./.recordings",
    platform: str = "auto",
) -> dict:
    """开始后台日志采集。
    Start background logcat/hilog capture.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        output_path: 日志输出路径，空则自动生成 / Log output path, auto-generate if empty
        tags: 逗号分隔的 tag 过滤 / Comma-separated tag filters
        exclude_tags: 排除的 tag / Tags to exclude
        level: 最低日志级别 (D/I/W/E/F) / Minimum log level
        process: 按进程名或 PID 过滤 / Filter by process name or PID
        out_dir: 输出根目录 / Output root directory
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "path": str, "pid": int}
        或 {"ok": False, "error": "already_capturing"} / {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_start_logcat_sync,
        device_serial or None,
        output_path,
        tags,
        exclude_tags,
        level,
        process,
        out_dir,
        platform,
    )


def _phone_stop_logcat_sync(
    device_serial: str | None,
    output_path: str,
    platform: str,
) -> dict:
    """Sync body for phone_stop_logcat."""
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    with _state_lock:
        path = output_path or _logcat_state.pop(resolved_serial, "")
    if not path:
        return {
            "ok": False,
            "error": "no_logcat_capture",
            "device_serial": resolved_serial,
        }

    # 若通过 output_path 指定，也需从状态中移除 / Remove from state when output_path provided
    with _state_lock:
        _logcat_state.pop(resolved_serial, None)

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        result = driver.stop_log_capture(path)
        result["device_serial"] = resolved_serial
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_stop_logcat(
    device_serial: str = "",
    output_path: str = "",
    platform: str = "auto",
) -> dict:
    """停止日志采集。
    Stop logcat/hilog capture.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        output_path: 日志输出路径，空则从状态获取 / Log path, auto-retrieve from state if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "lines": int, ...}
        或 {"ok": False, "error": "no_logcat_capture"} / {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_stop_logcat_sync,
        device_serial or None,
        output_path,
        platform,
    )


def _phone_search_logcat_sync(
    device_serial: str | None,
    pattern: str,
    lines: int,
    regex: bool,
    platform: str,
) -> dict:
    """Sync body for phone_search_logcat."""
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        log_text = driver.read_log(lines=lines)

        matches: list[str] = []
        for line in log_text.splitlines():
            if regex:
                if re.search(pattern, line):
                    matches.append(line)
            else:
                if pattern in line:
                    matches.append(line)

        return {
            "ok": True,
            "matches": matches,
            "count": len(matches),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_search_logcat(
    device_serial: str = "",
    pattern: str = "",
    lines: int = 5000,
    regex: bool = False,
    platform: str = "auto",
) -> dict:
    """搜索设备日志。
    Search device logcat for pattern.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        pattern: 搜索关键词或正则 / Search keyword or regex pattern
        lines: 读取最近行数 / Number of recent lines to read
        regex: 是否使用正则匹配 / Use regex matching
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "matches": [str], "count": int}
    """
    return await asyncio.to_thread(
        _phone_search_logcat_sync,
        device_serial or None,
        pattern,
        lines,
        regex,
        platform,
    )


def _phone_logcat_since_sync(
    device_serial: str | None,
    since: str,
    pattern: str,
    regex: bool,
    platform: str,
) -> dict:
    """Sync body for phone_logcat_since."""
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)

        # No since timestamp → capture the current device time and return it,
        # so the caller can mark "now" and read logs after it later.
        mark = since
        if not mark:
            mark = driver.logcat_time() or ""

        log_text = driver.read_log(since=mark) if mark else driver.read_log()
        all_lines = log_text.splitlines()

        if not pattern:
            hits = all_lines
        elif regex:
            rx = re.compile(pattern)
            hits = [ln for ln in all_lines if rx.search(ln)]
        else:
            hits = [ln for ln in all_lines if pattern in ln]

        return {
            "ok": True,
            "since": mark,
            "pattern": pattern or None,
            "count": len(hits),
            "matches": hits,
            "line_count": len(all_lines),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_logcat_since(
    device_serial: str = "",
    since: str = "",
    pattern: str = "",
    regex: bool = False,
    platform: str = "auto",
) -> dict:
    """只读取某个设备时间点之后的日志（可选地查找 pattern）。
    Read only logs at/after a device timestamp; optionally search for a pattern.

    配合"打标→操作→筛区间"工作流：先用空 since 调用一次取得当前设备时间戳（mark），
    执行操作后再用该 mark 作为 since 读取这之后的日志，避免历史日志干扰。
    Workflow: call once with empty `since` to get the current device timestamp
    (mark), then pass that mark back as `since` after performing actions.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        since: 起点时间戳 "MM-DD HH:MM:SS.mmm"；空则返回当前时间戳作为 mark
               Start timestamp; empty returns the current device timestamp as a mark
        pattern: 可选查找模式 / Optional search pattern
        regex: 是否将 pattern 视为正则 / Treat pattern as regex
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "since": str, "count": int, "matches": [str],
               "line_count": int}
        - since 为空时，返回的 "since" 即可作为后续调用的 mark
    """
    return await asyncio.to_thread(
        _phone_logcat_since_sync,
        device_serial or None,
        since,
        pattern,
        regex,
        platform,
    )


def _phone_logcat_assert_sequence_sync(
    device_serial: str | None,
    patterns: list[str],
    tag: str,
    timeout_s: float,
    interval_s: float,
    regex: bool,
    platform: str,
) -> dict:
    """Sync body for phone_logcat_assert_sequence (subsequence match over polls)."""
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    total = len(patterns or [])
    if total == 0:
        return {
            "ok": True,
            "matched": 0,
            "total": 0,
            "stuck_at_index": None,
            "device_serial": resolved_serial,
        }

    compiled = [re.compile(p) for p in patterns] if regex else None

    def _line_matches(idx: int, line: str) -> bool:
        return bool(compiled[idx].search(line)) if regex else patterns[idx] in line

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        since = driver.logcat_time() or ""
        start = time.monotonic()
        deadline = start + max(0.0, float(timeout_s))
        best_matched = 0
        best_lines: list[str] = []

        while True:
            log_text = driver.read_log(since=since, tag_filter=tag or None)
            lines = log_text.splitlines()

            cursor = 0
            matched_lines: list[str] = []
            for line in lines:
                if cursor >= total:
                    break
                if _line_matches(cursor, line):
                    matched_lines.append(line)
                    cursor += 1
            if cursor > best_matched:
                best_matched = cursor
                best_lines = matched_lines

            if cursor >= total:
                return {
                    "ok": True,
                    "matched": total,
                    "total": total,
                    "stuck_at_index": None,
                    "stuck_pattern": None,
                    "matched_lines": matched_lines,
                    "since": since,
                    "elapsed_s": round(time.monotonic() - start, 3),
                    "device_serial": resolved_serial,
                }

            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "matched": best_matched,
                    "total": total,
                    "stuck_at_index": best_matched,
                    "stuck_pattern": patterns[best_matched]
                    if best_matched < total
                    else None,
                    "matched_lines": best_lines,
                    "since": since,
                    "elapsed_s": round(time.monotonic() - start, 3),
                    "device_serial": resolved_serial,
                }

            time.sleep(max(0.5, float(interval_s)))
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_logcat_assert_sequence(
    patterns: list[str],
    device_serial: str = "",
    tag: str = "",
    timeout_s: float = 60.0,
    interval_s: float = 2.0,
    regex: bool = True,
    platform: str = "auto",
) -> dict:
    """验证一组 pattern 在日志中按给定顺序出现（子序列匹配）。
    Assert that patterns appear in the given order in logcat (subsequence match).

    子序列语义：pattern[i] 命中后，从其之后的行继续找 pattern[i+1]，
    两个 pattern 之间允许夹杂任意其他日志。进入即记录起点时间，只验证此刻之后的日志。
    Subsequence semantics; only inspects logs after entry. Returns as soon as the
    full sequence matches, or on timeout reporting where it got stuck.

    Parameters / 参数:
        patterns: 期望按顺序出现的模式列表 / Patterns expected in order
        device_serial: 设备序列号 / Device serial
        tag: 可选 logcat tag 过滤 / Optional logcat tag filter
        timeout_s: 最长等待秒数 / Max wait seconds
        interval_s: 轮询间隔秒数 / Poll interval seconds
        regex: pattern 是否为正则（默认 True）/ Treat patterns as regex
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": bool, "matched": int, "total": int,
               "stuck_at_index": int|None, "stuck_pattern": str|None,
               "matched_lines": [str], "since": str, "elapsed_s": float}
    """
    return await asyncio.to_thread(
        _phone_logcat_assert_sequence_sync,
        device_serial or None,
        patterns,
        tag,
        timeout_s,
        interval_s,
        regex,
        platform,
    )
