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
    """合并"没有自己时间戳头"的续行到前一条逻辑消息。

    用途：Java 异常堆栈那种"一条消息占好几行"——只有首行带 logcat 头，
    后续 ``at com.x.Y(...)`` 等续行没有头，应并入首行。
    Merges only header-less continuation lines (e.g. Java stack-trace frames)
    into the preceding message.

    Android logcat 格式示例::

        02-09 15:13:46.062 14459 15444 E Adm-Tag: java.lang.RuntimeException: boom
            at com.example.Foo.bar(Foo.java:42)        <- 续行，无头，合并
        02-09 15:13:46.063 14459 15444 D Adm-Tag: next message  <- 有头，独立一条

    关键：**每一行只要自己带完整 logcat 头，就是独立的一条消息**，即使与上一条
    PID/TID/Tag 相同也绝不合并。这样"同 tag、连续发、各自带时间戳"的密集日志
    （如广告回调）不会被错误并成一行，避免 logcat_assert_sequence 漏判。
    A line that carries its own logcat header is ALWAYS a standalone message,
    even when its PID/TID/Tag match the previous line — so dense same-tag logs
    are never collapsed.
    """
    lines = raw.splitlines()
    if not lines:
        return []

    # logcat threadtime header: date time PID TID level Tag: message
    _LOG_RE = re.compile(
        r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+\d+\s+\w\s+[\w\-./]+:\s"
    )

    merged: list[str] = []
    buf: list[str] = []

    for line in lines:
        if _LOG_RE.match(line):
            # Has its own header → a new standalone message. Flush the previous.
            if buf:
                merged.append("\n".join(buf))
            buf = [line]
        else:
            # Header-less continuation line → belongs to the previous message.
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
    clear_before: bool = False,
    tag: Optional[str] = None,
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
        clear_before: 查找前是否清空日志 buffer / Clear log buffer before reading

    Returns / 返回值:
        dict: {"ok": bool, "count": int, "pattern": str, "hits": list[str]}
    """
    if clear_before:
        try:
            ctx.driver.clear_log()
        except Exception:
            pass
        time.sleep(0.5)
    content = ctx.driver.read_log(lines=int(lines), tag_filter=tag)
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


_LOG_TS_RE = re.compile(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})")


def logcat_mark(ctx: ScriptContext, *, tag: Optional[str] = None) -> Optional[str]:
    """记录当前设备时间，作为后续 logcat_since 的起点。
    Capture the current device time as a marker for later logcat_since.

    如果指定 tag，优先读取该 tag 的最近日志并取其时间戳，更精确。
    If tag is given, reads the latest log for that tag and uses its timestamp.

    典型用法 / Typical usage::

        mark = logcat_mark(ctx)     # 操作前打标 / mark before action
        tap_play_button()
        logcat_since(ctx, mark, pattern="onAdImpression")

    Parameters / 参数:
        tag: 可选 tag 过滤，获取该 tag 最近日志的时间戳 / Optional tag for precise mark

    Returns / 返回值:
        logcat 格式时间戳字符串 "MM-DD HH:MM:SS.mmm"，失败返回 None。
    """
    try:
        if tag:
            content = ctx.driver.read_log(lines=5, tag_filter=tag)
            if content:
                lines = content.strip().splitlines()
                if lines:
                    m = _LOG_TS_RE.match(lines[-1])
                    if m:
                        return m.group(1)
        return ctx.driver.logcat_time()
    except Exception:
        return None


def logcat_since(
    ctx: ScriptContext,
    since: str,
    *,
    pattern: Optional[str] = None,
    regex: bool = False,
    merge_multiline: bool = False,
    tag: Optional[str] = None,
) -> dict:
    """只读取某个设备时间点之后的日志，可选地查找 pattern。
    Read only logs at/after a device timestamp; optionally search for pattern.

    避免历史日志干扰：配合 logcat_mark 实现"打标 → 操作 → 筛区间"。
    Avoids historical-log interference; pair with logcat_mark.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        since: 起点时间戳 "MM-DD HH:MM:SS.mmm"（来自 logcat_mark）/ Start timestamp
        pattern: 可选查找模式，None 则返回全部行 / Optional search pattern
        regex: 是否将 pattern 视为正则 / Treat pattern as regex
        merge_multiline: 是否合并多行日志消息 / Merge multiline log messages

    Returns / 返回值:
        dict: {"ok": bool, "since": str, "count": int, "pattern": str|None,
               "hits": list[str], "lines": list[str]}
        - lines: since 之后的全部行 / all lines after `since`
        - hits: 匹配 pattern 的行（pattern=None 时等于 lines）/ matched lines
    """
    content = ctx.driver.read_log(since=since, tag_filter=tag)
    ok = bool(content)

    if merge_multiline:
        all_lines = _merge_logcat_lines(content)
    else:
        all_lines = content.splitlines()

    if pattern is None:
        hits = list(all_lines)
    elif regex:
        pat = re.compile(pattern)
        hits = [line for line in all_lines if pat.search(line)]
    else:
        hits = [line for line in all_lines if pattern in line]

    return {
        "ok": ok,
        "since": since,
        "count": len(hits),
        "pattern": pattern,
        "hits": hits,
        "lines": all_lines,
    }


def logcat_assert_sequence(
    ctx: ScriptContext,
    patterns: list[str],
    *,
    tag: Optional[str] = None,
    timeout_s: float = 60,
    interval_s: float = 2,
    regex: bool = True,
    merge_multiline: bool = True,
) -> dict:
    """验证一组 pattern 在日志中按给定顺序出现（子序列匹配）。
    Assert that patterns appear in the given order in logcat (subsequence match).

    子序列语义 / Subsequence semantics:
        pattern[i] 命中后，从其**之后**的行继续找 pattern[i+1]；
        两个 pattern 之间允许夹杂任意其他日志（广告回调间必然有噪声）。
        After matching pattern[i], search for pattern[i+1] only in lines that
        follow it; arbitrary other log lines in between are allowed.

    进入函数即记录起点时间（logcat_mark），只验证此刻之后的日志，
    避免历史日志干扰。轮询直到完整序列命中（立即返回）或超时。
    Marks the current device time on entry and only inspects logs after it.
    Polls until the full sequence matches (returns immediately) or times out.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        patterns: 期望按顺序出现的模式列表 / Patterns expected in order
        tag: 可选 logcat tag 过滤（设备端）/ Optional logcat tag filter (device-side)
        timeout_s: 最长等待秒数 / Max wait seconds
        interval_s: 轮询间隔秒数 / Poll interval seconds
        regex: pattern 是否为正则（默认 True）/ Treat patterns as regex (default True)
        merge_multiline: 合并多行日志 / Merge multiline messages

    Returns / 返回值:
        dict: {
            "ok": bool,                # 是否完整有序命中 / full ordered match
            "matched": int,            # 已按序命中的数量 / patterns matched in order
            "total": int,              # 期望总数 / total expected
            "stuck_at_index": int|None,# 卡住的 pattern 下标（ok=True 时为 None）
            "stuck_pattern": str|None, # 卡住的 pattern 文本
            "matched_lines": list[str],# 各 pattern 命中的日志行 / matched log lines
            "since": str|None,         # 起点时间戳 / start timestamp
            "elapsed_s": float,
        }
    """
    total = len(patterns)
    if total == 0:
        return {
            "ok": True,
            "matched": 0,
            "total": 0,
            "stuck_at_index": None,
            "stuck_pattern": None,
            "matched_lines": [],
            "since": None,
            "elapsed_s": 0.0,
        }

    compiled = [re.compile(p) if regex else None for p in patterns]

    def _line_matches(idx: int, line: str) -> bool:
        if regex:
            return bool(compiled[idx].search(line))
        return patterns[idx] in line

    since = logcat_mark(ctx)
    start = time.monotonic()
    deadline = start + max(0.0, float(timeout_s))

    best_matched = 0
    best_lines: list[str] = []

    while True:
        content = ctx.driver.read_log(since=since, tag_filter=tag)
        lines = (
            _merge_logcat_lines(content) if merge_multiline else content.splitlines()
        )

        # Advance a cursor through the lines, matching patterns in order.
        cursor = 0
        matched = 0
        matched_lines: list[str] = []
        for line in lines:
            if cursor >= total:
                break
            if _line_matches(cursor, line):
                matched_lines.append(line)
                cursor += 1
                matched += 1
        if matched > best_matched:
            best_matched = matched
            best_lines = matched_lines

        if matched >= total:
            return {
                "ok": True,
                "matched": total,
                "total": total,
                "stuck_at_index": None,
                "stuck_pattern": None,
                "matched_lines": matched_lines,
                "since": since,
                "elapsed_s": round(time.monotonic() - start, 3),
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
            }

        _auto_log_print(
            f"  ... logcat 序列等待中 ({max(0, deadline - time.monotonic()):.0f}s)"
            f" {best_matched}/{total}"
            + (
                f" 卡在 #{best_matched}: {patterns[best_matched][:40]}"
                if best_matched < total
                else ""
            )
        )
        time.sleep(max(0.5, float(interval_s)))


def logcat_wait_for(
    ctx: ScriptContext,
    *patterns: str,
    any_of: Optional[list[str]] = None,
    timeout_s: float = 30,
    interval_s: float = 2,
    lines: int = 5000,
    clear_before: bool = False,
    merge_multiline: bool = True,
    tag: Optional[str] = None,
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

        content = ctx.driver.read_log(lines=int(lines), tag_filter=tag)
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
