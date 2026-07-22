"""广播命令总线 API / Broadcast command-bus API.

send_broadcast — ``am broadcast`` 发送 + ACK 日志闭环等待（mark→since 区间，
ADR-1/13）+ 注入加固（ADR-12）。存活探测 ``_pid_of`` 供超时细分（ADR-7）复用。
send_broadcast — fire ``am broadcast`` with optional ACK log wait (mark→since
window, ADR-1/13) and shell-injection hardening (ADR-12). The ``_pid_of``
liveness probe backs the timeout sub-classification (ADR-7).

设计文档 / Design doc: docs/design/broadcast-testcase.md
"""

from __future__ import annotations

import re
import shlex
import time
from typing import Optional

from ._helpers import _auto_log_print
from .context import ScriptContext
from .logcat import _merge_logcat_lines, logcat_mark

# ADR-12 注入加固白名单 / injection-hardening allowlists (ADR-12)
_ACTION_RE = re.compile(r"^[A-Za-z0-9._$/-]+$")  # action / package / component
_EXTRA_KEY_RE = re.compile(r"^[\w.]+$")  # extras key
_FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*$")  # flags, per token

_REDACTED = "***"


def _invalid(field: str, value: str) -> dict:
    """构造 invalid_param 失败返回 / Build an invalid_param failure dict."""
    return {
        "ok": False,
        "ack_seen": False,
        "line": None,
        "error": f"invalid_param: {field}={value!r}",
        "reason": "invalid_param",
    }


def _pid_of(ctx: ScriptContext, package: str) -> Optional[int]:
    """探测进程 pid：先 ``pidof``，失败降级 ``ps`` 解析（ADR-4）。
    Probe the process pid: try ``pidof`` first, fall back to parsing
    ``ps`` output (ADR-4, supports API<23 devices without pidof).

    多 pid 时取第一个（B7）。package 必须已通过 ADR-12 白名单校验。
    Takes the first pid when multiple are returned (B7). ``package`` must
    already have passed the ADR-12 allowlist validation.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        package: 应用包名（已校验）/ App package name (pre-validated)

    Returns / 返回值:
        进程 pid，未找到返回 None / Process pid, or None if not running.
    """
    r = ctx.driver.shell(f"pidof {shlex.quote(package)}")
    out = (r.get("stdout") or "").strip()
    if r.get("ok") and out:
        return int(out.split()[0])  # 多 pid 取第一个 / first pid wins (B7)
    # 降级 ps（API<23 无 pidof；ps 输出最后一列为进程名，精确匹配）
    # Fallback: parse `ps` (no pidof on API<23; last column is the exact name).
    r = ctx.driver.shell("ps")
    for line in (r.get("stdout") or "").splitlines():
        cols = line.split()
        if cols and cols[-1] == package:
            for c in cols[1:3]:  # PID 通常在第 2 列，厂商差异容错 / vendor-tolerant
                if c.isdigit():
                    return int(c)
    return None


def send_broadcast(
    ctx: ScriptContext,
    action: str,
    *,
    extras: Optional[dict[str, str]] = None,
    package: Optional[str] = None,
    component: Optional[str] = None,
    flags: Optional[list[str]] = None,
    wait_ack: Optional[str] = None,
    ack_timeout_s: float = 8,
    ack_tag: Optional[str] = None,
    check_alive: bool = False,
    redact_keys: Optional[list[str]] = None,
) -> dict:
    """发送 ``am broadcast`` 并可选等待 ACK 日志回执。
    Send an ``am broadcast`` and optionally wait for an ACK log line.

    注入加固（ADR-12）：action/package/component 白名单
    ``^[A-Za-z0-9._$/-]+$``，extras key ``^[\\w.]+$``，flags 逐 token
    ``^--?[A-Za-z][\\w-]*$``；校验不过 fail fast 返回 ``invalid_param``，
    不执行任何 shell。extras 全部 ``--es``（String，ADR-2），值 ``shlex.quote``。
    Injection hardening (ADR-12): action/package/component, extras keys and
    per-token flags are allowlist-validated; any violation fails fast with
    ``invalid_param`` and NO shell command is executed. All extras are sent
    as ``--es`` strings (ADR-2) with ``shlex.quote``-d values.

    定向参数（``-p``/``-n``/flags）由调用方显式传入，本函数只透传（ADR-3）——
    Android 8+ 隐式广播会被系统静默丢弃，忘传定向参数时广播无任何反应。
    Targeting parameters are caller-provided pass-through (ADR-3): Android 8+
    silently drops implicit broadcasts, so remember ``package=``/``component=``.

    ACK 闭环（ADR-1/13）：``wait_ack`` 非空时**发送前** ``logcat_mark`` 打标
    （None → ``mark_failed``，不发送，绝不静默降级），发送后
    ``read_log(since=mark)`` 区间轮询，``wait_ack`` 作**子串**匹配。
    ``ack_timeout_s=0`` 语义：只做一次读取判定，不轮询。
    ACK loop (ADR-1/13): with ``wait_ack`` set, ``logcat_mark`` is taken
    BEFORE sending (None → ``mark_failed``, nothing is sent, never a silent
    fallback); afterwards the ``since``-window is polled and ``wait_ack`` is
    matched as a substring. ``ack_timeout_s=0`` means a single read, no poll.

    超时细分（ADR-7）：已知 ``package`` 时超时后附带一次 ``_pid_of`` 存活检查，
    reason 细分 ``ack_timeout_app_dead`` / ``ack_timeout_app_alive``；
    无 ``package`` 时为 ``ack_timeout``。``check_alive=True`` 需同时传
    ``package``（否则 ``invalid_param``），预检死则不发广播（``app_not_alive``）。
    Timeout sub-classification (ADR-7): with a known ``package`` the timeout
    path runs one liveness probe (``ack_timeout_app_dead``/``_alive``);
    without it the reason is plain ``ack_timeout``. ``check_alive=True``
    requires ``package`` and blocks the send when the app is dead.

    掩码（B33）：``redact_keys`` 命中的 extras 值在返回 dict 与
    ``_auto_log_print`` 输出中替换为 ``***``；真实发送的命令串不掩码。
    Redaction (B33): values of extras keys listed in ``redact_keys`` are
    replaced with ``***`` in the returned dict and in any log output; the
    actually-executed command string is NOT masked.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        action: 广播 action / Broadcast action
        extras: 附加参数，全部作 ``--es`` String 透传（ADR-2）/ String extras
        package: ``-p`` 定向包名（调用方决定，ADR-3）/ Target package
        component: ``-n`` 定向组件 / Target component
        flags: 透传 flags，逐 token 校验（ADR-12）/ Pass-through flags
        wait_ack: ACK 子串；None 则发完即返 / ACK substring; None = fire-and-forget
        ack_timeout_s: ACK 最长等待秒数；0 = 单次读取判定 / Max ACK wait; 0 = single read
        ack_tag: logcat tag 过滤（调用方传业务 tag）/ Device-side logcat tag filter
        check_alive: 发送前对 package 做存活预检（ADR-7）/ Pre-send liveness check
        redact_keys: 这些 extras key 的值在日志/返回中掩码 / Keys to redact in logs

    Returns / 返回值:
        dict: {"ok", "ack_seen", "line", "error", "reason", "cmd"}
        - reason ∈ {"", "ack_timeout", "ack_timeout_app_alive",
          "ack_timeout_app_dead", "app_not_alive", "invalid_param",
          "mark_failed", "shell_error", "not_supported"}
        - cmd: 掩码后的命令串（证据用）/ redacted command string (evidence)
    """
    # 1. 平台判定 / platform gate
    if ctx.platform != "android":
        return {
            "ok": False,
            "ack_seen": False,
            "line": None,
            "error": "not_supported",
            "reason": "not_supported",
        }

    # 2. 参数校验（ADR-12，fail fast，不执行任何 shell）/ validation, fail fast
    if not _ACTION_RE.match(action):
        return _invalid("action", action)
    if package is not None and not _ACTION_RE.match(package):
        return _invalid("package", package)
    if component is not None and not _ACTION_RE.match(component):
        return _invalid("component", component)
    for f in flags or []:
        if not _FLAG_RE.match(f):
            return _invalid("flags", f)
    for k in extras or {}:
        if not _EXTRA_KEY_RE.match(k):
            return _invalid("extras_key", k)

    # 3. 存活预检（ADR-7）/ pre-send liveness check
    if check_alive:
        if not package:
            return _invalid("package", "required when check_alive=True")
        if _pid_of(ctx, package) is None:
            return {
                "ok": False,
                "ack_seen": False,
                "line": None,
                "error": "app_not_alive",
                "reason": "app_not_alive",
            }

    # 4. wait_ack → 发送前打标（ADR-13：None → mark_failed，不发送）
    #    mark BEFORE send; a failed mark aborts, never a silent fallback.
    mark: Optional[str] = None
    if wait_ack:
        mark = logcat_mark(ctx, tag=ack_tag)
        if mark is None:
            return {
                "ok": False,
                "ack_seen": False,
                "line": None,
                "error": "mark_failed",
                "reason": "mark_failed",
            }

    # 5. 拼命令：校验过的裸词外一律 shlex.quote / build command, quote all values
    def _build(masked: bool) -> str:
        redact = set(redact_keys or [])
        parts = ["am", "broadcast", "-a", shlex.quote(action)]
        if package:
            parts += ["-p", shlex.quote(package)]
        if component:
            parts += ["-n", shlex.quote(component)]
        parts += [shlex.quote(f) for f in (flags or [])]
        for k, v in (extras or {}).items():
            shown = _REDACTED if (masked and k in redact) else shlex.quote(v)
            parts += ["--es", k, shown]
        return " ".join(parts)

    cmd = _build(masked=False)  # 真实发送串不掩码 / real command, unmasked
    cmd_logged = _build(masked=True)  # 日志/返回用 / for logs and return dict

    # 6. 发送 / send
    _auto_log_print(f"  >> am broadcast: {cmd_logged}")
    r = ctx.driver.shell(cmd)
    if not r.get("ok"):
        return {
            "ok": False,
            "ack_seen": False,
            "line": None,
            "error": f"shell_error: {(r.get('stderr') or '').strip()}",
            "reason": "shell_error",
            "cmd": cmd_logged,
        }
    if not wait_ack:
        return {
            "ok": True,
            "ack_seen": False,
            "line": None,
            "error": "",
            "reason": "",
            "cmd": cmd_logged,
        }

    # 7. ACK 区间轮询（子串匹配；ack_timeout_s=0 = 单次读取判定）
    #    poll the since-window; substring match; timeout 0 = single read
    start = time.monotonic()
    deadline = start + max(0.0, float(ack_timeout_s))
    while True:
        content = ctx.driver.read_log(since=mark, tag_filter=ack_tag)
        for line in _merge_logcat_lines(content):
            if wait_ack in line:
                return {
                    "ok": True,
                    "ack_seen": True,
                    "line": line,
                    "error": "",
                    "reason": "",
                    "cmd": cmd_logged,
                }
        if time.monotonic() >= deadline:
            # 超时细分（ADR-7）/ sub-classify by liveness when package known
            if package:
                alive = _pid_of(ctx, package) is not None
                reason = "ack_timeout_app_alive" if alive else "ack_timeout_app_dead"
            else:
                reason = "ack_timeout"
            return {
                "ok": False,
                "ack_seen": False,
                "line": None,
                "error": "ack_timeout",
                "reason": reason,
                "cmd": cmd_logged,
            }
        _auto_log_print(
            f"  ... 等待 ACK '{wait_ack}' ({max(0, deadline - time.monotonic()):.0f}s)"
        )
        time.sleep(0.5)
