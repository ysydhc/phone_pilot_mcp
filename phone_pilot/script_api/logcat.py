"""日志查找 API / Logcat find API.

logcat_find, logcat_wait_for — 设备日志搜索与等待。
Device log searching and waiting functions.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .context import ScriptContext
from ._helpers import _auto_log_print


def _merge_logcat_lines(raw: str) -> list[str]:
    """按 logcat PID+Tag 前缀合并属于同一逻辑消息的连续多行。

    Android logcat 格式示例::

        02-09 15:13:46.062 14459 15444 D Adm-Tag: first line
        02-09 15:13:46.062 14459 15444 D Adm-Tag: second line (same logical msg)

    相邻行如果 PID、TID、Tag 完全一致且时间戳相同/递增极小，则合并为一条。
    """
    lines = raw.splitlines()
    if not lines:
        return []

    # logcat line pattern: date time PID TID level Tag: message
    _LOG_RE = re.compile(
        r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+(\d+)\s+(\d+)\s+\w\s+([\w\-./]+):\s"
    )

    merged: list[str] = []
    buf: list[str] = []
    prev_key: Optional[tuple] = None

    for line in lines:
        m = _LOG_RE.match(line)
        if m:
            key = (m.group(1), m.group(2), m.group(3))  # (PID, TID, Tag)
            if key == prev_key and buf:
                # Same logical message – append
                buf.append(line)
            else:
                # New message — flush previous
                if buf:
                    merged.append("\n".join(buf))
                buf = [line]
                prev_key = key
        else:
            # Continuation line without logcat header – belongs to previous
            if buf:
                buf.append(line)
            else:
                merged.append(line)

    if buf:
        merged.append("\n".join(buf))
    return merged


def logcat_find(
    ctx: ScriptContext,
    pattern: str,
    *,
    lines: int = 5000,
    regex: bool = False,
    merge_multiline: bool = False,
) -> dict:
    """在设备日志中查找 pattern。
    Find pattern in device log (logcat).

    读取最近 lines 行日志，按字符串包含或正则匹配查找。
    Reads recent log lines and searches by substring or regex.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        pattern: 查找字符串或正则模式 / Search string or regex pattern
        lines: 读取的日志行数 / Number of log lines to read
        regex: 是否将 pattern 视为正则 / Treat pattern as regex
        merge_multiline: 是否合并多行日志消息 / Merge multiline log messages

    Returns / 返回值:
        dict: {"ok": bool, "count": int, "pattern": str, "hits": list[str]}
    """
    content = ctx.driver.read_log(lines=int(lines))
    ok = bool(content)

    if merge_multiline:
        search_lines = _merge_logcat_lines(content)
    else:
        search_lines = content.splitlines()

    if regex:
        pat = re.compile(pattern)
        hits = [line for line in search_lines if pat.search(line)]
    else:
        hits = [line for line in search_lines if pattern in line]
    return {"ok": ok, "count": len(hits), "pattern": pattern, "hits": hits}


def logcat_wait_for(
    ctx: ScriptContext,
    *patterns: str,
    any_of: Optional[list[str]] = None,
    timeout_s: float = 30,
    interval_s: float = 2,
    lines: int = 5000,
    clear_before: bool = False,
    merge_multiline: bool = True,
) -> dict:
    """轮询设备日志，等待指定模式出现。
    Poll device log until pattern(s) appear.

    *patterns 必须**全部**命中；any_of 只需命中**一个**。
    All *patterns must match; any_of requires at least one match.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        *patterns: 所有必须命中的字符串 / Required strings (all must match)
        any_of: 至少命中一个的列表，None 则不检查 / Optional list, any one match
        timeout_s: 最长等待秒数 / Max wait seconds
        interval_s: 轮询间隔秒数 / Poll interval seconds
        lines: 每次读取的日志行数 / Log lines per read
        clear_before: 轮询前是否清空日志 / Clear log before polling
        merge_multiline: 合并多行日志 / Merge multiline messages

    Returns / 返回值:
        dict: {"ok": bool, "hits": {pattern: [lines]}, "missing": [str]}
    """
    if clear_before:
        try:
            ctx.driver.clear_log()
        except Exception:
            pass
        time.sleep(0.5)

    any_of_list = list(any_of or [])
    all_keys = list(patterns)
    found: dict[str, list[str]] = {}
    any_of_hit: Optional[str] = None

    deadline = time.monotonic() + max(0, float(timeout_s))

    while time.monotonic() < deadline:
        time.sleep(max(0.5, float(interval_s)))

        content = ctx.driver.read_log(lines=int(lines))
        if not content:
            continue

        if merge_multiline:
            search_lines = _merge_logcat_lines(content)
        else:
            search_lines = content.splitlines()

        # Check required patterns
        for p in all_keys:
            if p not in found:
                hits = [ln for ln in search_lines if p in ln]
                if hits:
                    found[p] = hits
                    _auto_log_print(f"  >> logcat 发现 '{p}' ({len(hits)} 条)")

        # Check any_of
        if any_of_list and not any_of_hit:
            for p in any_of_list:
                hits = [ln for ln in search_lines if p in ln]
                if hits:
                    any_of_hit = p
                    found[p] = hits
                    _auto_log_print(f"  >> logcat 发现 '{p}' ({len(hits)} 条)")
                    break

        # All satisfied?
        all_found = all(p in found for p in all_keys)
        any_satisfied = (not any_of_list) or (any_of_hit is not None)
        if all_found and any_satisfied:
            return {"ok": True, "hits": found, "missing": []}

        remaining = max(0, deadline - time.monotonic())
        _auto_log_print(
            f"  ... logcat 等待中 ({remaining:.0f}s)"
            f" required={sum(1 for p in all_keys if p in found)}/{len(all_keys)}"
            + (f" any_of={'found' if any_of_hit else 'waiting'}" if any_of_list else "")
        )

    # Timeout — report missing
    missing = [p for p in all_keys if p not in found]
    if any_of_list and not any_of_hit:
        missing.append(f"any_of({', '.join(any_of_list)})")
    return {"ok": False, "hits": found, "missing": missing}
