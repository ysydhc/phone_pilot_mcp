from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from phone_pilot.core.ui.element import UIElement


@dataclass
class ChainStep:
    apply: Any

    def __call__(self, chain: "UIChain") -> "UIChain":
        return self.apply(chain)


@dataclass
class UIChain:
    ctx: Any
    ok: bool = True
    error: Optional[dict] = None
    last: Optional[Any] = None
    vars: dict[str, Any] = field(default_factory=dict)
    _history: list[Any] = field(default_factory=list, repr=False, compare=False)

    def _fail(self, code: str, message: str, *, detail: Any = None) -> "UIChain":
        if self.ok:
            self.ok = False
            self.error = {"code": code, "message": message, "detail": detail}
        return self

    def _set_last(self, value: Any) -> "UIChain":
        self.last = value
        return self

    def _record(self, fn: Any) -> None:
        self._history.append(fn)

    def find_text(
        self,
        text: str,
        *,
        use_ocr: bool = True,
        retry_interval_ms: int = 1000,
        retry_attempts: int = 2,
        _record_step: bool = True,
        **kwargs: Any,
    ) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(
                lambda ch: ch.find_text(
                    text,
                    use_ocr=use_ocr,
                    retry_interval_ms=retry_interval_ms,
                    retry_attempts=retry_attempts,
                    _record_step=False,
                    **kwargs,
                )
            )
        from phone_pilot import script_api as api

        elem = api.find_text(
            self.ctx,
            text,
            use_ocr=use_ocr,
            retry_interval_ms=retry_interval_ms,
            retry_attempts=retry_attempts,
            **kwargs,
        )
        if not elem:
            return self._fail("no_match", "text_not_found", detail={"query": text})
        return self._set_last(elem)

    def find_image(
        self,
        path: str,
        *,
        retry_interval_ms: int = 1000,
        retry_attempts: int = 2,
        _record_step: bool = True,
        **kwargs: Any,
    ) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(
                lambda ch: ch.find_image(
                    path,
                    retry_interval_ms=retry_interval_ms,
                    retry_attempts=retry_attempts,
                    _record_step=False,
                    **kwargs,
                )
            )
        from phone_pilot import script_api as api

        elem = api.find_image(
            self.ctx,
            path,
            retry_interval_ms=retry_interval_ms,
            retry_attempts=retry_attempts,
            **kwargs,
        )
        if not elem:
            return self._fail("no_match", "image_not_found", detail={"path": path})
        return self._set_last(elem)

    def tap(self, *, pc_x: Optional[float] = None, pc_y: Optional[float] = None, times: int = 1, interval: int = 0, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(
                lambda ch: ch.tap(pc_x=pc_x, pc_y=pc_y, times=times, interval=interval, _record_step=False)
            )
        if not isinstance(self.last, UIElement):
            return self._fail("tap_failed", "no_active_element")
        res = self.last.tap(pc_x=pc_x, pc_y=pc_y, times=times, interval=interval)
        if not res.get("ok"):
            return self._fail("tap_failed", "tap_failed", detail=res)
        self.last.meta["tap"] = res
        return self

    def right(self, text: Optional[str] = None, **kwargs: Any) -> "UIChain":
        return self._relative("right", text, **kwargs)

    def left(self, text: Optional[str] = None, **kwargs: Any) -> "UIChain":
        return self._relative("left", text, **kwargs)

    def up(self, text: Optional[str] = None, **kwargs: Any) -> "UIChain":
        return self._relative("up", text, **kwargs)

    def down(self, text: Optional[str] = None, **kwargs: Any) -> "UIChain":
        return self._relative("down", text, **kwargs)

    def _relative(self, direction: str, text: Optional[str], **kwargs: Any) -> "UIChain":
        if not self.ok:
            return self
        if kwargs.pop("_record_step", True):
            self._record(lambda ch: ch._relative(direction, text, _record_step=False, **kwargs))
        if not isinstance(self.last, UIElement):
            return self._fail("no_anchor", "no_active_element")
        retry_interval_ms = int(kwargs.pop("retry_interval_ms", 1000))
        retry_attempts = int(kwargs.pop("retry_attempts", 2))
        for attempt in range(max(1, retry_attempts)):
            target = getattr(self.last, direction)(text, **kwargs)
            if target:
                return self._set_last(target)
            if attempt < retry_attempts - 1:
                time.sleep(max(0.0, retry_interval_ms / 1000.0))
        return self._fail("no_match", "relative_not_found", detail={"direction": direction, "text": text})

    def wait(self, seconds: float, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.wait(seconds, _record_step=False))
        time.sleep(max(0.0, float(seconds)))
        return self

    def save(self, name: str, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.save(name, _record_step=False))
        if not isinstance(self.last, UIElement):
            return self._fail("save_failed", "no_active_element")
        self.vars[str(name)] = self.last
        return self

    def get(self, name: str, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.get(name, _record_step=False))
        value = self.vars.get(str(name))
        if not value:
            return self._fail("not_found", "saved_element_not_found", detail={"name": name})
        self.last = value
        return self

    def require(self, name: str) -> "UIChain":
        return self.get(name)

    def export(self, name: str) -> Optional[dict]:
        value = self.vars.get(str(name))
        if isinstance(value, UIElement):
            locator = getattr(value, "_locator", None)
            if isinstance(locator, dict):
                return {"type": "locator", "value": locator}
            return {"type": "snapshot", "value": value.to_dict()}
        return None

    def import_(self, name: str, payload: dict) -> "UIChain":
        if not self.ok:
            return self
        if not isinstance(payload, dict):
            return self._fail("import_failed", "invalid_payload")
        typ = payload.get("type")
        value = payload.get("value")
        if typ == "locator":
            from phone_pilot import script_api as api

            elem = api._ui_locate(self.ctx, value)
            if not elem:
                return self._fail("import_failed", "locator_not_found")
            self.vars[str(name)] = elem
            return self
        if typ == "snapshot":
            self.vars[str(name)] = value
            return self
        return self._fail("import_failed", "unknown_payload_type")

    def swipe_up(self, **kwargs: Any) -> "UIChain":
        return self._swipe("swipe_up", **kwargs)

    def swipe_down(self, **kwargs: Any) -> "UIChain":
        return self._swipe("swipe_down", **kwargs)

    def swipe_left(self, **kwargs: Any) -> "UIChain":
        return self._swipe("swipe_left", **kwargs)

    def swipe_right(self, **kwargs: Any) -> "UIChain":
        return self._swipe("swipe_right", **kwargs)

    def _swipe(self, name: str, **kwargs: Any) -> "UIChain":
        if not self.ok:
            return self
        if kwargs.pop("_record_step", True):
            self._record(lambda ch: ch._swipe(name, _record_step=False, **kwargs))
        from phone_pilot import script_api as api

        fn = getattr(api, name, None)
        if not fn:
            return self._fail("swipe_failed", "swipe_method_missing")
        res = fn(self.ctx, **kwargs)
        if not (isinstance(res, dict) and res.get("ok")):
            return self._fail("swipe_failed", "swipe_failed", detail=res)
        return self

    # ---- 验证断言方法（文本参数支持 re: 正则）----

    def assert_text_exists(
        self, text: str, *, use_ocr: bool = True, _record_step: bool = True,
    ) -> "UIChain":
        """断言屏幕上存在指定文本（支持 ``re:`` 正则前缀）。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_text_exists(text, use_ocr=use_ocr, _record_step=False))
        from phone_pilot.core.verify import _text_matches
        from phone_pilot import script_api as api

        if text.startswith("re:"):
            # 正则：遍历所有可见文本
            try:
                all_texts = self.ctx.driver.ui.collect_all_texts()
            except Exception:
                all_texts = []
            for t in all_texts:
                if _text_matches(text, t):
                    return self
            return self._fail("assert_failed", "text_not_found", detail={"query": text})
        else:
            elem = api.find_text(self.ctx, text, use_ocr=use_ocr, retry_attempts=1)
            if elem:
                return self._set_last(elem)
            return self._fail("assert_failed", "text_not_found", detail={"query": text})

    def assert_text_not_exists(
        self, text: str, *, use_ocr: bool = True, _record_step: bool = True,
    ) -> "UIChain":
        """断言屏幕上不存在指定文本（支持 ``re:`` 正则前缀）。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_text_not_exists(text, use_ocr=use_ocr, _record_step=False))
        from phone_pilot.core.verify import _text_matches
        from phone_pilot import script_api as api

        if text.startswith("re:"):
            try:
                all_texts = self.ctx.driver.ui.collect_all_texts()
            except Exception:
                all_texts = []
            for t in all_texts:
                if _text_matches(text, t):
                    return self._fail("assert_failed", "text_should_not_exist", detail={"query": text, "found": t})
            return self
        else:
            elem = api.find_text(self.ctx, text, use_ocr=use_ocr, retry_attempts=1)
            if elem:
                return self._fail("assert_failed", "text_should_not_exist", detail={"query": text})
            return self

    def assert_image_exists(
        self, path: str, *, threshold: float = 0.8, _record_step: bool = True,
    ) -> "UIChain":
        """断言屏幕上存在指定图片。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_image_exists(path, threshold=threshold, _record_step=False))
        from phone_pilot import script_api as api

        elem = api.find_image(self.ctx, path, threshold=threshold, retry_attempts=1)
        if elem:
            return self._set_last(elem)
        return self._fail("assert_failed", "image_not_found", detail={"path": path})

    def assert_image_not_exists(
        self, path: str, *, threshold: float = 0.8, _record_step: bool = True,
    ) -> "UIChain":
        """断言屏幕上不存在指定图片。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_image_not_exists(path, threshold=threshold, _record_step=False))
        from phone_pilot import script_api as api

        elem = api.find_image(self.ctx, path, threshold=threshold, retry_attempts=1)
        if elem:
            return self._fail("assert_failed", "image_should_not_exist", detail={"path": path})
        return self

    def assert_logcat_contains(
        self, pattern: str, *, lines: int = 5000, _record_step: bool = True,
    ) -> "UIChain":
        """断言日志中包含模式（支持 ``re:`` 正则前缀）。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_logcat_contains(pattern, lines=lines, _record_step=False))
        from phone_pilot import script_api as api

        result = api.logcat_find(self.ctx, pattern, lines=lines, regex=pattern.startswith("re:"))
        if result.get("count", 0) > 0:
            return self
        return self._fail("assert_failed", "logcat_pattern_not_found", detail={"pattern": pattern})

    def assert_logcat_not_contains(
        self, pattern: str, *, lines: int = 5000, _record_step: bool = True,
    ) -> "UIChain":
        """断言日志中不包含模式（支持 ``re:`` 正则前缀）。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_logcat_not_contains(pattern, lines=lines, _record_step=False))
        from phone_pilot import script_api as api

        result = api.logcat_find(self.ctx, pattern, lines=lines, regex=pattern.startswith("re:"))
        if result.get("count", 0) == 0:
            return self
        return self._fail("assert_failed", "logcat_pattern_found", detail={"pattern": pattern, "count": result.get("count", 0)})

    def assert_memory_no_growth(
        self, package: str, *, threshold_mb: float = 50, _record_step: bool = True,
    ) -> "UIChain":
        """断言内存增长不超过阈值。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.assert_memory_no_growth(package, threshold_mb=threshold_mb, _record_step=False))
        try:
            result = self.ctx.driver.dump_memory_profile(package, name="chain_mem_check")
            pss = result.get("total_pss_mb", 0) or result.get("pss_mb", 0)
            if pss <= threshold_mb:
                return self
            return self._fail(
                "assert_failed", "memory_exceeds_threshold",
                detail={"pss_mb": pss, "threshold_mb": threshold_mb},
            )
        except Exception as exc:
            return self._fail("assert_failed", f"memory_check_failed: {exc}")

    def checkpoint(self, name: str, _record_step: bool = True) -> "UIChain":
        """保存当前屏幕状态作为检查点。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.checkpoint(name, _record_step=False))
        from phone_pilot.core.checkpoint import save_checkpoint

        result = save_checkpoint(self.ctx.driver, name)
        if not result.get("ok"):
            return self._fail("checkpoint_failed", "save_checkpoint_failed", detail=result)
        self.vars[f"checkpoint:{name}"] = result
        return self

    def screenshot_save(self, name: str, _record_step: bool = True) -> "UIChain":
        """保存当前截图到指定名称。"""
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.screenshot_save(name, _record_step=False))
        from phone_pilot import script_api as api

        result = api.screenshot(self.ctx, name)
        if not result.get("ok"):
            return self._fail("screenshot_failed", "save_screenshot_failed", detail=result)
        self.vars[f"screenshot:{name}"] = result
        return self

    # ---- 结果输出 ----

    def done(self) -> dict:
        last = self.last
        if isinstance(last, UIElement):
            last = last.to_dict()
        vars_out: dict[str, Any] = {}
        for key, value in self.vars.items():
            if isinstance(value, UIElement):
                vars_out[key] = value.to_dict()
            else:
                vars_out[key] = value
        return {"ok": bool(self.ok), "error": self.error, "vars": vars_out, "last": last}

    def unlock_device(self, *, pin: Optional[str] = None, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.unlock_device(pin=pin, _record_step=False))
        from phone_pilot import script_api as api

        res = api.unlock_device(self.ctx, pin=pin)
        if not (isinstance(res, dict) and res.get("ok", True)):
            return self._fail("unlock_failed", "unlock_device_failed", detail=res)
        self.last = res
        return self

    def clear_background(self, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.clear_background(_record_step=False))
        from phone_pilot import script_api as api

        res = api.clear_background(self.ctx)
        if not (isinstance(res, dict) and res.get("ok", True)):
            return self._fail("clear_background_failed", "clear_background_failed", detail=res)
        self.last = res
        return self

    def restart_app_pkg(self, package: str, _record_step: bool = True) -> "UIChain":
        if not self.ok:
            return self
        if _record_step:
            self._record(lambda ch: ch.restart_app_pkg(package, _record_step=False))
        from phone_pilot import script_api as api

        res = api.restart_app_pkg(self.ctx, package=package)
        if not (isinstance(res, dict) and res.get("ok", True)):
            return self._fail("restart_failed", "restart_app_pkg_failed", detail=res)
        self.last = res
        return self

    def repeat(self, step_or_times=None, *, max: int = 3, block: Any = None) -> "UIChain":
        if not self.ok:
            return self
        if isinstance(step_or_times, int):
            times = int(step_or_times)
            if times < 1:
                times = 1
            if times <= 1:
                return self
            times = times - 1
            last_chain = None
            for _ in range(times):
                temp = UIChain(ctx=self.ctx)
                for fn in self._history:
                    temp = fn(temp)
                    if not temp.ok:
                        self.ok = False
                        self.error = temp.error
                        return self
                last_chain = temp
            if last_chain is not None:
                self.last = last_chain.last
                self.vars.update(last_chain.vars)
            return self

        step = step_or_times
        if isinstance(step, UIChain):
            history = list(step._history)
            step = ChainStep(lambda ch: _run_history(ch, history))
        elif callable(step):
            step = ChainStep(step)
        elif step is None:
            return self._fail("repeat_failed", "invalid_repeat_step")

        cond = None
        if isinstance(block, UIChain):
            cond_history = list(block._history)
            cond = ChainStep(lambda ch: _run_history(ch, cond_history))
        elif callable(block):
            cond = ChainStep(block)
        elif block is not None:
            return self._fail("repeat_failed", "invalid_repeat_block")

        last_err = None
        limit = int(max)
        if limit < 1:
            limit = 1
        for _ in range(limit):
            temp = UIChain(ctx=self.ctx)
            temp = step(temp)
            if not temp.ok:
                last_err = temp.error
                continue
            if cond is not None:
                temp = cond(temp)
                if not temp.ok:
                    last_err = temp.error
                    continue
            if temp.last is not None:
                self.last = temp.last
                self.vars.update(temp.vars)
                return self
            last_err = temp.error
        return self._fail("repeat_failed", "repeat_exhausted", detail=last_err)


def _run_history(chain: UIChain, history: list[Any]) -> UIChain:
    for fn in history:
        chain = fn(chain)
        if not chain.ok:
            return chain
    return chain


def chain(ctx: Any) -> UIChain:
    return UIChain(ctx=ctx)


def find_text(text: str, **kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.find_text(text, **kwargs))


def find_image(path: str, **kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.find_image(path, **kwargs))


def swipe_up(**kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.swipe_up(**kwargs))

def swipe_down(**kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.swipe_down(**kwargs))

def swipe_left(**kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.swipe_left(**kwargs))

def swipe_right(**kwargs: Any) -> ChainStep:
    return ChainStep(lambda ch: ch.swipe_right(**kwargs))   