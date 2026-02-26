"""run_script, retry, scroll_to_find, ChainResult, chain / 脚本入口与执行工具.

脚本生命周期管理（run_script）、通用重试（retry）、滚动查找（scroll_to_find）、
链式查找结果（ChainResult）以及链式 API 入口（chain）。
Script lifecycle (run_script), generic retry, scroll find, chain result, and chain API entry.
"""
from __future__ import annotations

import pathlib
import sys
import time
from typing import Any, Callable, Optional, Sequence, TypeVar

from phone_pilot.core.log import _log as _runner_log

from .context import ScriptContext, RetryExhausted
from ._helpers import (
    _box_center,
    _get_screen_size,
    _input_swipe,
    _expand_ocr_langs,
    _auto_log_print,
)
from ._ocr import _ocr_find_text_boxes, _ocr_find_text_boxes_roi
from ._locate import (
    _uia_find_text_boxes,
    _airtest_find_image_boxes,
    _select_relative_box,
)

_T = TypeVar("_T")


class ChainResult(dict):
    """链式查找结果 / Chain lookup result.

    chain(ctx).find_text(...).right(...).tap() 的中间结果，支持 tap() 和方位查找。
    Intermediate result of chain(ctx).find_text(...).right(...).tap(), supports tap() and directional find.

    Attributes / 属性:
        ok (bool): 查找是否成功 / Whether lookup succeeded
        kind (str): 查找类型（text/image/relative 等）/ Lookup type
        query (str): 查询文本或路径 / Query text or path
        method (str): 查找方式（uia/ocr/airtest 等）/ Method used (uia/ocr/airtest)
        match (dict): 匹配到的 box 信息 / Matched box info
        error (str): 失败时的错误信息 / Error message on failure

    Methods / 方法:
        tap(): 点击匹配元素的中心 / Tap center of matched element
        right/left/up/down(): 在该元素右侧/左侧/上方/下方继续查找 / Find relative to this element
    """

    def __init__(
        self,
        ctx: ScriptContext,
        *,
        ok: bool,
        kind: str,
        query: Optional[str] = None,
        method: Optional[str] = None,
        box: Optional[dict] = None,
        error: Optional[str] = None,
    ):
        super().__init__(ok=bool(ok), kind=kind)
        if query is not None:
            self["query"] = query
        if method:
            self["method"] = method
        if error:
            self["error"] = error
        if box:
            self["match"] = box
        self._ctx = ctx
        self._box = box

    def _anchor_box(self) -> Optional[dict]:
        return self._box or self.get("match")

    def tap(self) -> dict:
        from .actions import tap_xy
        box = self._anchor_box()
        if not box:
            return {"ok": False, "error": "no_anchor"}
        cx, cy = _box_center(box)
        return tap_xy(self._ctx, cx, cy)

    def find(self) -> "ChainResult":
        return self

    def right(
        self,
        text: Optional[str] = None,
        *,
        texts: Optional[Sequence[str]] = None,
        image: Optional[str] = None,
        rule: str = "band",
        x_tol: int = 80,
        y_tol: int = 40,
        expand_step: int = 20,
        expand_max: int = 200,
        lang: Optional[str] = None,
        exact: bool = False,
        case_sensitive: bool = False,
        psm: int = 6,
        threshold: float = 0.8,
        grayscale: bool = True,
    ) -> "ChainResult":
        return self._relative(
            direction="right",
            text=text,
            texts=texts,
            image=image,
            rule=rule,
            x_tol=x_tol,
            y_tol=y_tol,
            expand_step=expand_step,
            expand_max=expand_max,
            lang=lang,
            exact=exact,
            case_sensitive=case_sensitive,
            psm=psm,
            threshold=threshold,
            grayscale=grayscale,
        )

    def left(self, text: Optional[str] = None, **kwargs) -> "ChainResult":
        return self._relative(direction="left", text=text, **kwargs)

    def up(self, text: Optional[str] = None, **kwargs) -> "ChainResult":
        return self._relative(direction="up", text=text, **kwargs)

    def down(self, text: Optional[str] = None, **kwargs) -> "ChainResult":
        return self._relative(direction="down", text=text, **kwargs)

    def _relative(
        self,
        *,
        direction: str,
        text: Optional[str] = None,
        texts: Optional[Sequence[str]] = None,
        image: Optional[str] = None,
        rule: str = "band",
        x_tol: int = 80,
        y_tol: int = 40,
        expand_step: int = 20,
        expand_max: int = 200,
        lang: Optional[str] = None,
        exact: bool = False,
        case_sensitive: bool = False,
        psm: int = 6,
        threshold: float = 0.8,
        grayscale: bool = True,
    ) -> "ChainResult":
        anchor = self._anchor_box()
        if not anchor:
            return ChainResult(self._ctx, ok=False, kind="relative", error="anchor_not_found")
        screen = _get_screen_size(self._ctx) or (0, 0)
        sw, sh = screen
        # Internal expansion: start from direct side, then expand by 100px.
        rule = "band"
        expand_step = 100
        if direction in ("right", "left"):
            y_tol = 0
            expand_max = int(sh) if sh else 1000
        else:
            x_tol = 0
            expand_max = int(sw) if sw else 1000
        queries = list(texts or [])
        if text:
            queries.insert(0, text)
        ocr_langs = _expand_ocr_langs(self._ctx, lang)
        if image:
            candidates = _airtest_find_image_boxes(self._ctx, path=image, threshold=threshold, grayscale=grayscale)
            method = "airtest"
            kind = "image"
        else:
            candidates = _uia_find_text_boxes(
                self._ctx,
                texts=queries,
                exact=exact,
                case_sensitive=case_sensitive,
            )
            method = "poco"
            kind = "text"
            if not candidates:
                # OCR ROI fallback first (right/left/up/down of anchor)
                screen = _get_screen_size(self._ctx)
                if screen:
                    sw, sh = screen
                    ax1 = int(anchor.get("x", 0))
                    ay1 = int(anchor.get("y", 0))
                    ax2 = int(anchor.get("x2", ax1))
                    ay2 = int(anchor.get("y2", ay1))
                    if direction == "right":
                        roi = (ax2, max(0, ay1 - y_tol), sw, min(sh, ay2 + y_tol))
                    elif direction == "left":
                        roi = (0, max(0, ay1 - y_tol), ax1, min(sh, ay2 + y_tol))
                    elif direction == "down":
                        roi = (max(0, ax1 - x_tol), ay2, min(sw, ax2 + x_tol), sh)
                    else:
                        roi = (max(0, ax1 - x_tol), 0, min(sw, ax2 + x_tol), ay1)
                    for ocr_lang in ocr_langs:
                        roi_candidates = _ocr_find_text_boxes_roi(
                            self._ctx,
                            texts=queries,
                            roi=roi,
                            lang=ocr_lang,
                            exact=exact,
                            case_sensitive=case_sensitive,
                            psm=psm,
                        )
                        if roi_candidates:
                            candidates = roi_candidates
                            method = f"ocr_roi:{ocr_lang}"
                            break
                if not candidates:
                    for ocr_lang in ocr_langs:
                        candidates = _ocr_find_text_boxes(
                            self._ctx,
                            texts=queries,
                            lang=ocr_lang,
                            exact=exact,
                            case_sensitive=case_sensitive,
                            psm=psm,
                        )
                        if candidates:
                            method = f"ocr:{ocr_lang}"
                            break
        if not candidates:
            return ChainResult(self._ctx, ok=False, kind="relative", error="relative_not_found")
        best = _select_relative_box(
            anchor=anchor,
            candidates=candidates,
            direction=direction,
            rule=str(rule or "band"),
            x_tol=int(x_tol),
            y_tol=int(y_tol),
            expand_step=int(expand_step),
            expand_max=int(expand_max),
        )
        if not best:
            return ChainResult(self._ctx, ok=False, kind="relative", error="relative_not_found")
        return ChainResult(self._ctx, ok=True, kind=kind, method=method, box=best, query=queries[0] if queries else None)


def chain(ctx: ScriptContext):
    """链式查找 API 入口 / Chain lookup API entry.

    返回链式查找器，支持 find_text/find_image 及 right/left/up/down 方位查找。
    Returns chain lookup builder: find_text/find_image + directional find (right/left/up/down).

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context

    Returns / 返回值:
        UIChain: 链式查找器，可链式调用 / Chain builder for chained calls

    Example / 示例:
        chain(ctx).find_text("确定").tap()
        chain(ctx).find_image("btn.png").right(text="下一步").tap()
    """
    from phone_pilot.core.ui.chain import chain as _chain

    return _chain(ctx)


# ---------------------------------------------------------------------------
# retry — 通用重试封装
# ---------------------------------------------------------------------------

def retry(
    fn: Callable[[], _T],
    *,
    desc: str = "",
    max_attempts: int = 3,
    interval: float = 2.0,
    ctx: Optional[ScriptContext] = None,
) -> _T:
    """通用重试封装（支持自愈）/ Generic retry wrapper with self-healing support.

    执行 fn()，失败时重试，耗尽后抛出 RetryExhausted。
    当传入 ctx 时，在重试间隙调用 SelfHealer 尝试自愈（弹窗关闭/LLM 分析等）。
    Calls fn(); retries on failure; raises RetryExhausted when exhausted.
    When ctx is provided, calls SelfHealer between retries for self-healing.

    fn() 返回 None 或 False 视为失败 / None or False from fn() = failure.

    Parameters / 参数:
        fn: 无参可调用，返回成功值或 None/False / No-arg callable, returns success or None/False
        desc: 步骤描述（日志用）/ Step description for logs
        max_attempts: 最大尝试次数 / Max attempts (default 3)
        interval: 重试间隔秒数 / Interval between attempts in seconds
        ctx: 脚本上下文（传入时启用自愈）/ Script context (enables self-healing when provided)

    Returns / 返回值:
        fn 的成功返回值 / Successful return value from fn

    Raises / 异常:
        RetryExhausted: 全部重试失败后 / When all attempts fail
    """
    last_err: Optional[Exception] = None
    label = desc or "retry"
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            result = fn()
            if result is not None and result is not False:
                if attempt > 1:
                    _auto_log_print(f"  >> {label} 第 {attempt} 次重试成功")
                return result
        except Exception as exc:
            last_err = exc
        msg = f"  >> {label} 第 {attempt}/{max_attempts} 次失败"
        if last_err:
            msg += f" ({last_err})"
        _auto_log_print(msg)
        if attempt < max_attempts:
            # 自愈检测：若 ctx 存在 → 调用 SelfHealer
            if ctx is not None:
                try:
                    from .self_healer import get_self_healer
                    healer = get_self_healer(ctx)
                    if healer:
                        error_msg = str(last_err) if last_err else "返回 None/False"
                        heal = healer.try_heal(label, desc, error_msg, retry_count=attempt)
                        if heal:
                            # 自愈完成后立即重试 fn()
                            try:
                                result = fn()
                                if result is not None and result is not False:
                                    healer.report_outcome(heal, True)
                                    _auto_log_print(f"  >> {label} 自愈后第 {attempt + 1} 次重试成功")
                                    return result
                                healer.report_outcome(heal, False)
                            except Exception as exc2:
                                healer.report_outcome(heal, False)
                                last_err = exc2
                except Exception:
                    pass  # 自愈失败不影响正常重试流程
            time.sleep(max(0, float(interval)))
    raise RetryExhausted(f"{label} — 重试 {max_attempts} 次后失败" + (f" ({last_err})" if last_err else ""))


# ---------------------------------------------------------------------------
# scroll_to_find — 滚动查找
# ---------------------------------------------------------------------------

def scroll_to_find(
    ctx: ScriptContext,
    text: str,
    *,
    direction: str = "up_down",
    max_count: int = 10,
    duration_ms: int = 1500,
    use_ocr: bool = False,
    exact: bool = False,
    anchor: Optional[Any] = None,
    settle_s: float = 1.5,
) -> Any:
    """滚动屏幕直到找到包含 text 的元素。
    Scroll until element containing text is found.

    先查当前页面，未找到再按 direction 滚动。适用于长列表查找。
    Checks current screen first, then scrolls per direction. For long list lookup.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        text: 要查找的文本 / Text to find
        direction: 滚动策略 / Scroll strategy:
            - "up"/"down"/"left"/"right" — 单方向 / Single direction
            - "up_down" — 先上后下 / Up then down
            - "down_up" — 先下后上 / Down then up
            - "left_right"/"right_left" — 水平双向 / Horizontal
        max_count: 每方向最大滚动次数 / Max scrolls per direction
        duration_ms: 单次滑动毫秒 / Swipe duration ms
        use_ocr: 是否启用 OCR / Use OCR
        exact: 精确匹配 / Exact match
        anchor: 滑动起点锚定元素（嵌套滚动区域用）/ Anchor element for swipe start
        settle_s: 滑动后等待稳定秒数 / Wait after each swipe

    Returns / 返回值:
        UIElement: 找到的元素 / Found element

    Raises / 异常:
        RetryExhausted: 全部方向滚动完仍未找到 / Not found after all scrolls
    """
    from .find import find_text

    mc = 1000000000 if max_count == -1 else max(1, int(max_count))
    d = (direction or "up_down").strip().lower()

    # ---- 解析滚动方向序列 ----
    _DIR_MAP: dict[str, list[str]] = {
        "up":          ["up"],
        "down":        ["down"],
        "left":        ["left"],
        "right":       ["right"],
        "up_down":     ["up", "down"],
        "down_up":     ["down", "up"],
        "left_right":  ["left", "right"],
        "right_left":  ["right", "left"],
    }
    phases = _DIR_MAP.get(d, ["up", "down"])

    # ---- 获取锚点坐标（用于自定义落点）----
    anchor_x: Optional[float] = None
    anchor_y: Optional[float] = None
    if anchor is not None:
        try:
            anchor_x = float(anchor.center_x)
            anchor_y = float(anchor.center_y)
        except Exception:
            pass

    # ---- Step 0: 先查当前页面 ----
    elem = find_text(ctx, text, exact=exact, use_ocr=use_ocr, retry_attempts=1, _silent_fail=True)
    if elem:
        ctx._observe("scroll_to_find", f"'{text}' 当前页面已存在 @ ({elem.center_x}, {elem.center_y})", element=elem)
        return elem

    # ---- 滑动函数 (支持锚点) ----
    def _do_swipe(swipe_dir: str) -> None:
        screen = _get_screen_size(ctx)
        if not screen:
            return
        w, h = screen
        # 默认中心点
        cx = anchor_x if anchor_x is not None else w * 0.5
        cy = anchor_y if anchor_y is not None else h * 0.5
        # 滑动距离（屏幕尺寸的 60%）
        dist_v = h * 0.6
        dist_h = w * 0.6

        if swipe_dir == "up":
            x1, y1, x2, y2 = cx, cy + dist_v / 2, cx, cy - dist_v / 2
        elif swipe_dir == "down":
            x1, y1, x2, y2 = cx, cy - dist_v / 2, cx, cy + dist_v / 2
        elif swipe_dir == "left":
            x1, y1, x2, y2 = cx + dist_h / 2, cy, cx - dist_h / 2, cy
        elif swipe_dir == "right":
            x1, y1, x2, y2 = cx - dist_h / 2, cy, cx + dist_h / 2, cy
        else:
            return
        # Clamp to screen bounds
        x1, y1 = max(10, min(int(x1), w - 10)), max(10, min(int(y1), h - 10))
        x2, y2 = max(10, min(int(x2), w - 10)), max(10, min(int(y2), h - 10))
        _input_swipe(ctx, x1, y1, x2, y2, duration_ms=duration_ms)

    # ---- 滚动阶段 ----
    total_swipes = 0
    for phase_dir in phases:
        for i in range(mc):
            _do_swipe(phase_dir)
            total_swipes += 1
            time.sleep(max(0, float(settle_s)))
            # 滑动后页面已变化，必须丢弃旧的 UI 树缓存
            ctx._ui_cache = None
            elem = find_text(ctx, text, exact=exact, use_ocr=use_ocr, retry_attempts=1, _silent_fail=True)
            if elem:
                ctx._observe("scroll_to_find", f"'{text}' 滚动 {total_swipes} 次后找到 @ ({elem.center_x}, {elem.center_y})", element=elem)
                return elem

    msg = f"scroll_to_find('{text}') — {d} 方向滚动 {total_swipes} 次未找到"
    ctx._observe("scroll_to_find", msg, status="fail")
    raise RetryExhausted(msg)


# ---------------------------------------------------------------------------
# run_script — 标准脚本入口
# ---------------------------------------------------------------------------

def run_script(fn: Callable[[], int], *, ctx: Optional[ScriptContext] = None) -> None:
    """脚本标准入口 / Standard script entry point.

    自动处理异常、退出码、traceback、RunSession 收尾。
    Handles exceptions, exit code, traceback, RunSession finalization.

    Usage / 用法::

        if __name__ == "__main__":
            run_script(main)

    若传入 ctx，脚本结束时自动 finalize RunSession、停止录屏/logcat、打印汇总。
    If ctx provided, auto-finalizes RunSession, stops recording/logcat, prints summary.

    Parameters / 参数:
        fn: 主函数，返回退出码（0=成功）/ Main function returning exit code
        ctx: 可选上下文，用于 RunSession 关联 / Optional context for RunSession

    Notes / 特殊逻辑:
        - RetryExhausted → 退出码 1
        - 成功后若 ctx.auto_dump_hprof=True 会自动 dump hprof
    """
    code = 0
    error_msg = ""

    # ---- auto_meminfo: before snapshot ----
    _meminfo_sampler = None
    if ctx is not None and ctx.auto_meminfo:
        try:
            from .actions import mem_snapshot
            mem_snapshot(ctx, "before")
        except Exception as exc:
            _runner_log(f"  [auto-meminfo] before 快照失败: {exc}")
    # ---- meminfo sampler (background thread) ----
    if ctx is not None and ctx.meminfo_interval_s > 0:
        try:
            from phone_pilot.memory_analyze.sampler import MemInfoSampler
            from ._helpers import _get_current_focus
            focus = _get_current_focus(ctx)
            pkg = focus.get("package", "") if isinstance(focus, dict) else ""
            if pkg:
                _meminfo_sampler = MemInfoSampler(ctx.device_serial, pkg, interval_s=ctx.meminfo_interval_s)
                _meminfo_sampler.start()
                _runner_log(f"  [sampler] 后台内存采样已启动 (间隔 {ctx.meminfo_interval_s}s)")
        except Exception as exc:
            _runner_log(f"  [sampler] 启动失败: {exc}")
    # ---- perfetto heapprofd ----
    _perfetto_ctx: dict = {}
    if ctx is not None and ctx.perfetto_heap_profile:
        try:
            from phone_pilot.memory_analyze.perfetto_profiler import start_heap_profile
            from ._helpers import _get_current_focus
            focus = _get_current_focus(ctx)
            pkg = focus.get("package", "") if isinstance(focus, dict) else ""
            if pkg:
                session = ctx.session
                out = str(session.meminfo_dir) if session else (ctx.out_dir or "./.recordings")
                pf = start_heap_profile(
                    ctx.device_serial or "", pkg,
                    duration_s=ctx.perfetto_duration_s,
                    out_dir=out,
                )
                if pf.get("ok"):
                    _perfetto_ctx = pf
                    _runner_log("  [perfetto] heapprofd 采集已启动")
        except Exception as exc:
            _runner_log(f"  [perfetto] 启动失败: {exc}")

    try:
        code = fn() or 0
    except RetryExhausted as exc:
        code = 1
        error_msg = str(exc)
        _runner_log(f"[FAIL] {exc}")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        raise
    except Exception as exc:
        code = 1
        error_msg = str(exc)
        _runner_log(f"[FATAL] {exc}")
        import traceback as _tb
        _tb.print_exc()
    finally:
        _finalize_run(ctx, code, error_msg, _meminfo_sampler=_meminfo_sampler, _perfetto_ctx=_perfetto_ctx)
    sys.exit(code)


def _finalize_run(
    ctx: Optional[ScriptContext],
    code: int,
    error_msg: str,
    *,
    _meminfo_sampler: Any = None,
    _perfetto_ctx: Optional[dict] = None,
) -> None:
    """Finalize RunSession + print summary banner."""
    if ctx is None:
        return

    # Stop any running screen recordings (pull to run dir)
    if ctx._recording:
        try:
            ctx.stop_record()
        except Exception:
            pass

    # Stop any running logcat captures
    if hasattr(ctx, "_logcat_processes"):
        for name in list(ctx._logcat_processes.keys()):
            try:
                ctx.stop_logcat(name)
            except Exception:
                pass

    # 保存自愈事件到 RunSession / Save healing events to RunSession
    session = ctx.session
    if session is not None:
        try:
            healer = getattr(ctx, "_self_healer", None)
            if healer:
                events = healer.healing_events
                if events:
                    event_dicts = [e.to_dict() for e in events]
                    session.save_healing_events(event_dicts)
                    _runner_log(f"  [self-heal] 已保存 {len(events)} 条自愈事件")
        except Exception:
            pass

    # ---- auto_meminfo: after snapshot + diff ----
    if ctx.auto_meminfo:
        try:
            from .actions import mem_snapshot, mem_diff, mem_check_leak
            mem_snapshot(ctx, "after")
            mem_diff(ctx, "before", "after")
            mem_check_leak(ctx)
        except Exception as exc:
            _runner_log(f"  [auto-meminfo] after/diff 失败: {exc}")

    # ---- Stop meminfo sampler ----
    if _meminfo_sampler is not None:
        try:
            import json as _json
            samples = _meminfo_sampler.stop()
            if samples:
                session = ctx.session
                if session:
                    tl_path = session.meminfo_dir / "timeline.json"
                    tl_path.write_text(_json.dumps({"samples": samples}, ensure_ascii=False, indent=2), encoding="utf-8")
                    _runner_log(f"  [sampler] 已保存 {len(samples)} 个采样点到 timeline.json")
        except Exception as exc:
            _runner_log(f"  [sampler] 停止/保存失败: {exc}")

    # ---- Stop Perfetto heapprofd ----
    if _perfetto_ctx and _perfetto_ctx.get("ok"):
        try:
            from phone_pilot.memory_analyze.perfetto_profiler import stop_and_analyze
            import json as _json
            from ._helpers import _get_current_focus
            focus = _get_current_focus(ctx)
            pkg = focus.get("package", "") if isinstance(focus, dict) else ""
            trace_file = _perfetto_ctx.get("trace_file", "")
            if trace_file and pkg:
                result = stop_and_analyze(ctx.device_serial or "", trace_file, pkg)
                session = ctx.session
                if session and result.get("ok"):
                    pf_path = session.meminfo_dir / "perfetto_analysis.json"
                    pf_path.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    _runner_log("  [perfetto] 分析完成，已保存到 perfetto_analysis.json")
        except Exception as exc:
            _runner_log(f"  [perfetto] 分析失败: {exc}")

    # 脚本成功结束 + auto_dump_hprof=True → 自动 dump hprof
    if code == 0 and ctx.auto_dump_hprof:
        try:
            from .actions import dump_hprof_snapshot
            _runner_log("[auto] dump hprof ...")
            dump_hprof_snapshot(ctx, f"{ctx.script_name or 'auto'}_final_hprof")
        except Exception as exc:
            _runner_log(f"[auto] dump hprof 失败: {exc}")

    session = ctx.session
    if session is None:
        return

    # If script failed with an error, record a terminal failure step
    if code != 0 and error_msg:
        ctx._step_counter += 1
        try:
            png_bytes: bytes | None = None
            try:
                png_bytes = ctx.driver.screen.screenshot()
            except Exception:
                pass
            session.add_step(
                ctx._step_counter, "script_error", "fail", error_msg,
                png_bytes=png_bytes,
            )
        except Exception:
            pass

    elapsed = time.time() - ctx._started_at
    steps_total = ctx._step_counter
    # Count failures from in-memory steps
    steps_failed = sum(1 for s in session._steps if s.get("status") not in ("ok", "passed"))
    steps_passed = steps_total - steps_failed

    try:
        session.finalize(
            exit_code=code,
            steps_total=steps_total,
            steps_passed=steps_passed,
            steps_failed=steps_failed,
            error_message=error_msg,
        )
    except Exception:
        return

    # 生成 HTML 可视化报告 / Generate HTML visual report
    report_path: Optional[pathlib.Path] = None
    if getattr(ctx, "auto_report", True):
        try:
            from phone_pilot.core.html_report import generate_html_report
            report_path = generate_html_report(session.run_dir)
        except Exception as exc:
            _runner_log(f"  [report] HTML 报告生成失败: {exc}")

    # Print summary
    sep = "\u2500" * 52
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    mark = "\u2713" if code == 0 else "\u2717"
    status = "PASSED" if code == 0 else "FAILED"
    _runner_log("")
    _runner_log(sep)
    _runner_log(f"  {mark} {status} | {steps_passed}/{steps_total} 步通过 | 耗时 {mins}m{secs:02d}s")
    if error_msg:
        _runner_log(f"  错误: {error_msg[:120]}")
    try:
        rel = session.run_dir
        try:
            rel = session.run_dir.relative_to(pathlib.Path.cwd())
        except ValueError:
            pass
        if report_path:
            _runner_log(f"  报告: {rel}/report.html")
        else:
            _runner_log(f"  报告: {rel}/run_meta.json")
    except Exception:
        pass
    _runner_log(sep)
