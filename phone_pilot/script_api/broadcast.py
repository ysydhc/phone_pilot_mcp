"""广播命令总线 API / Broadcast command-bus API.

send_broadcast — ``am broadcast`` 发送 + ACK 日志闭环等待（mark→since 区间，
ADR-1/13）+ 注入加固（ADR-12）。存活探测 ``_pid_of`` 供超时细分（ADR-7）复用。
send_broadcast — fire ``am broadcast`` with optional ACK log wait (mark→since
window, ADR-1/13) and shell-injection hardening (ADR-12). The ``_pid_of``
liveness probe backs the timeout sub-classification (ADR-7).

ensure_app_alive — pidof→ps 两级存活探测（ADR-4）+ scheme 冷启动 + 就绪日志
区间等待（ADR-1/13），超时附存活复查细分（ADR-7）。
ensure_app_alive — two-tier liveness probe (pidof→ps, ADR-4) plus scheme
cold-launch and ready-log window wait (ADR-1/13), with liveness re-check
sub-classification on timeout (ADR-7).

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


def _ensure_result(
    ok: bool,
    *,
    pid: Optional[int] = None,
    launched: bool = False,
    ready_seen: Optional[bool] = None,
    error: str = "",
    reason: str = "",
) -> dict:
    """构造 ensure_app_alive 返回 dict / Build the ensure_app_alive result dict."""
    return {
        "ok": ok,
        "pid": pid,
        "launched": launched,
        "ready_seen": ready_seen,
        "error": error,
        "reason": reason,
    }


def ensure_app_alive(
    ctx: ScriptContext,
    package: str,
    *,
    scheme_url: Optional[str] = None,
    ready_event: Optional[str] = None,
    ready_tag: Optional[str] = None,
    timeout_s: float = 15,
    force_restart: bool = False,
) -> dict:
    """确保应用进程存活：pidof→ps 两级探测，必要时 scheme 冷启动并等就绪日志。
    Ensure the app process is alive: two-tier liveness probe (pidof→ps,
    ADR-4), with optional scheme cold-launch and ready-log wait.

    存活探测（ADR-4）：先 ``pidof``，失败降级解析 ``ps``（API<23 无 pidof），
    多 pid 取第一个（B7）。``package`` 拼命令前做 ADR-12 白名单校验。
    Liveness probe (ADR-4): ``pidof`` first, falling back to ``ps`` parsing
    (no pidof below API 23); first pid wins (B7). ``package`` is
    allowlist-validated (ADR-12) before entering any shell command.

    R1 语义修订：**已存活且非 force_restart → 不重复拉起，且跳过 ready 等待**
    （``ready_seen=None`` 表示"未验证"而非"未就绪"——APP_READY 早已打过，
    mark→since 区间内永不再现，等待必然误判超时。要验证就绪必须
    ``force_restart=True``）。
    R1 semantics: an already-alive app (without ``force_restart``) is NOT
    re-launched and the ready wait is SKIPPED (``ready_seen=None`` means
    "unverified", not "not ready" — APP_READY was logged long ago and will
    never reappear inside the mark→since window, so waiting would always
    time out. Pass ``force_restart=True`` to actually verify readiness).

    冷启动：不存活且无 ``scheme_url`` → ``dead_no_launch_method``；有则
    **先** ``logcat_mark`` 打标（None → ``mark_failed``，不拉起，绝不静默
    降级，ADR-13），再经 ``driver.open_deeplink`` 拉起（含 uri 转义修复）。
    ``ready_event`` 非空时只在拉起动作**之后**的日志区间等待（ADR-1），
    作**子串**匹配；``timeout_s=0`` 语义：只做一次读取判定，不轮询。
    超时附带一次存活复查（ADR-7），细分 ``ready_timeout_app_dead``
    （拉起即崩）/ ``ready_timeout_app_alive``（慢）。
    Cold launch: dead without ``scheme_url`` → ``dead_no_launch_method``;
    otherwise ``logcat_mark`` is taken BEFORE launching (None →
    ``mark_failed``, nothing is launched, never a silent fallback, ADR-13),
    then ``driver.open_deeplink`` fires the scheme. A non-empty
    ``ready_event`` is substring-matched only inside the post-launch
    ``since``-window (ADR-1); ``timeout_s=0`` means a single read, no poll.
    On timeout one liveness re-check (ADR-7) splits
    ``ready_timeout_app_dead`` (instant crash) / ``_app_alive`` (slow).

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        package: 应用包名（ADR-12 校验）/ App package name (ADR-12 validated)
        scheme_url: 冷启动 scheme；None 且进程死 → 报错 / Cold-launch scheme URL
        ready_event: 就绪日志子串，如 ``event=APP_READY`` / Ready-log substring
        ready_tag: logcat tag 过滤（业务 tag 调用方传）/ Device-side tag filter
        timeout_s: 就绪等待秒数；0 = 单次读取判定 / Max ready wait; 0 = single read
        force_restart: 先 force_stop 再冷启动（一例一重置）/ Kill then cold-launch

    Returns / 返回值:
        dict: {"ok", "pid", "launched", "ready_seen", "error", "reason"}
        - ready_seen: True=就绪日志已见 / False=等待超时 / None=未验证（已存活
          跳过等待，或未给 ready_event）
        - reason ∈ {"", "dead_no_launch_method", "ready_timeout_app_alive",
          "ready_timeout_app_dead", "invalid_param", "mark_failed",
          "not_supported"}
    """
    # 1. 平台判定 + package 校验（ADR-12）/ platform gate + validation
    if ctx.platform != "android":
        return _ensure_result(False, error="not_supported", reason="not_supported")
    if not _ACTION_RE.match(package):
        return _ensure_result(
            False,
            error=f"invalid_param: package={package!r}",
            reason="invalid_param",
        )

    # 2. force_restart → 先杀进程（一例一重置）/ kill first when force_restart
    if force_restart:
        ctx.driver.force_stop(package)

    # 3. 存活探测（ADR-4）；已存活且非 force_restart → 不拉起且跳过等待（R1）
    #    probe; alive without force_restart → no relaunch, skip ready wait (R1)
    pid = _pid_of(ctx, package)
    if pid is not None and not force_restart:
        # ready_seen=None：区间内 APP_READY 永不再现，等待必然误判（R1 修订）
        # ready_seen=None: APP_READY never reappears in-window (R1 revision).
        return _ensure_result(True, pid=pid, launched=False, ready_seen=None)

    # 4. 不存活且无 scheme_url → 无法拉起 / dead and no launch method
    if not scheme_url:
        return _ensure_result(
            False,
            error="dead_no_launch_method",
            reason="dead_no_launch_method",
        )

    # 5. 拉起前打标（ADR-13：None → mark_failed，不拉起）→ scheme 冷启动
    #    mark BEFORE launch (ADR-13: None aborts, never a silent fallback).
    mark: Optional[str] = None
    if ready_event:
        mark = logcat_mark(ctx, tag=ready_tag)
        if mark is None:
            return _ensure_result(False, error="mark_failed", reason="mark_failed")
    _auto_log_print(f"  >> 冷启动 / cold-launch: {scheme_url}")
    ctx.driver.open_deeplink(scheme_url)  # Task 1 已修 uri 转义 / uri quoting fixed

    # 6. ready_event 区间轮询（子串匹配；timeout_s=0 = 单次读取判定）
    #    poll the since-window; substring match; timeout 0 = single read
    if ready_event:
        start = time.monotonic()
        deadline = start + max(0.0, float(timeout_s))
        while True:
            content = ctx.driver.read_log(since=mark, tag_filter=ready_tag)
            if any(ready_event in line for line in _merge_logcat_lines(content)):
                break
            if time.monotonic() >= deadline:
                # 超时细分（ADR-7）：拉起即崩 vs 慢 / crash-on-launch vs slow
                alive = _pid_of(ctx, package) is not None
                reason = (
                    "ready_timeout_app_alive" if alive else "ready_timeout_app_dead"
                )
                return _ensure_result(
                    False,
                    launched=True,
                    ready_seen=False,
                    error="ready_timeout",
                    reason=reason,
                )
            _auto_log_print(
                f"  ... 等待就绪 '{ready_event}' "
                f"({max(0, deadline - time.monotonic()):.0f}s)"
            )
            time.sleep(0.5)

    # 7. 成功路径补一次存活探测填 pid / final probe to fill pid on success
    return _ensure_result(
        True,
        pid=_pid_of(ctx, package),
        launched=True,
        ready_seen=True if ready_event else None,
    )
