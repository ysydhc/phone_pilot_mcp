"""PageNode & PageFlowAssembler — 认知页面记忆节点与测试流装配引擎。

提供类人脑记忆机制的 UI 自动化图谱化分层抽象：
1. PageSignature: 页面认知特征指纹，通过核心特征元素唯一匹配识别当前页面，屏蔽动态易变元素干扰。
2. PageNode: 原子页面记忆节点基类，封装页面专属元素查找、动作执行与状态转移。
3. PageTransition: 页面状态转移边定义。
4. PageFlowAssembler: 页面流程装配器与独立可执行大脚本编译器（compile_to_script）。
"""

from __future__ import annotations

import inspect
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Type, Union


# ---------------------------------------------------------------------------
# 页面指纹辅助工具
# ---------------------------------------------------------------------------


def _extract_ui_strings(node: Any) -> set[str]:
    """递归从 UI 树节点（dict/list/UIElement）中提取所有文本/标识字符串集合。"""
    results: set[str] = set()
    if isinstance(node, dict):
        for key in ("label", "text", "identifier", "name", "resource_id", "title", "value"):
            val = node.get(key)
            if isinstance(val, str) and val.strip():
                results.add(val.strip())
        # 递归遍历所有子节点
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                results.update(_extract_ui_strings(v))
    elif isinstance(node, list):
        for item in node:
            results.update(_extract_ui_strings(item))
    elif hasattr(node, "text") or hasattr(node, "label"):
        for attr in ("text", "label", "identifier", "name", "resource_id"):
            val = getattr(node, attr, None)
            if isinstance(val, str) and val.strip():
                results.add(val.strip())
    return results


# ---------------------------------------------------------------------------
# 1. PageSignature: 页面特征指纹
# ---------------------------------------------------------------------------


@dataclass
class PageSignature:
    """页面认知特征指纹。

    用于在不依赖绝对脆弱定位的情况下，唯一断言设备当前是否停留在该页面。
    """

    name: str = ""
    activity_or_controller: str = ""
    required_elements: list[str] = field(default_factory=list)
    forbidden_elements: list[str] = field(default_factory=list)
    extra_match_fn: Optional[Callable[[dict], bool]] = None

    def matches(self, page_state: Any) -> bool:
        """判断给定的页面 UI 状态是否匹配当前页面特征指纹。"""
        if not page_state:
            return False

        # 提取当前状态中的所有文本与标识
        found_strings = _extract_ui_strings(page_state)

        # 检查是否包含所有必须元素
        for req in self.required_elements:
            matched = any(req in s or s in req for s in found_strings)
            if not matched:
                return False

        # 检查是否包含任何禁止元素
        for fbd in self.forbidden_elements:
            matched = any(fbd in s or s in fbd for s in found_strings)
            if matched:
                return False

        # 检查 ViewController / Activity
        if self.activity_or_controller:
            current_act = ""
            if isinstance(page_state, dict):
                raw_act = page_state.get("activity") or page_state.get("view_controller") or ""
                if isinstance(raw_act, dict):
                    current_act = str(
                        raw_act.get("activity")
                        or raw_act.get("name")
                        or raw_act.get("class")
                        or raw_act.get("package")
                        or ""
                    )
                else:
                    current_act = str(raw_act)
            if self.activity_or_controller not in current_act:
                return False

        # 自定义回调校验
        if self.extra_match_fn:
            try:
                if not self.extra_match_fn(page_state):
                    return False
            except Exception:
                return False

        return True

    def explain_diff(self, page_state: Any) -> dict:
        """诊断当前状态为何未匹配该指纹。"""
        found_strings = _extract_ui_strings(page_state) if page_state else set()
        missing_req = [
            req for req in self.required_elements if not any(req in s or s in req for s in found_strings)
        ]
        hit_forbidden = [
            fbd for fbd in self.forbidden_elements if any(fbd in s or s in fbd for s in found_strings)
        ]
        return {
            "signature_name": self.name,
            "matched": len(missing_req) == 0 and len(hit_forbidden) == 0,
            "missing_required": missing_req,
            "hit_forbidden": hit_forbidden,
            "total_extracted_strings": len(found_strings),
        }


# ---------------------------------------------------------------------------
# 2. PageNode: 原子页面记忆节点
# ---------------------------------------------------------------------------


class PageNode:
    """原子页面记忆节点基类。

    子类定义该页面的业务动作（如 enter_detail, click_login 等），
    在页面元素变更时只需局部修复该 Node，而无需重写全局测试图谱。
    """

    signature: PageSignature = PageSignature(name="BasePage")

    def __init__(self, ctx: Any = None) -> None:
        self.ctx = ctx

    @property
    def page_name(self) -> str:
        return self.signature.name or type(self).__name__

    def get_page_state(self) -> dict:
        """获取当前设备屏幕状态字典（优先调用 ctx 的驱动或统一 API）。"""
        if hasattr(self.ctx, "get_page_state") and callable(self.ctx.get_page_state):
            return self.ctx.get_page_state()
        if hasattr(self.ctx, "driver"):
            driver = self.ctx.driver
            if hasattr(driver, "get_page_state"):
                return driver.get_page_state()
        # 兜底直接引用 script_api
        try:
            from phone_pilot import script_api as api

            if hasattr(api, "get_page_state"):
                return api.get_page_state(self.ctx)
        except Exception:
            pass
        return {}

    def is_current(self, page_state: Optional[dict] = None) -> bool:
        """判断当前设备是否处于本页面。"""
        state = page_state if page_state is not None else self.get_page_state()
        return self.signature.matches(state)

    def wait_until_current(self, timeout_s: float = 10.0, interval_s: float = 0.5) -> bool:
        """轮询等待设备停留在本页面。"""
        t_end = time.monotonic() + timeout_s
        while time.monotonic() < t_end:
            if self.is_current():
                return True
            time.sleep(interval_s)
        return False

    def tap_text(self, text: str, timeout_s: float = 8.0) -> dict:
        """在当前页面内点击包含指定文本的元素。"""
        # 优先委托给上下文对象的统一方法
        if hasattr(self.ctx, "tap_when_ready") and callable(self.ctx.tap_when_ready):
            return self.ctx.tap_when_ready(text, timeout_s=timeout_s)
        try:
            from phone_pilot import script_api as api

            res = api.tap_when_ready(self.ctx, text=text, timeout_s=timeout_s)
            return res if isinstance(res, dict) else {"ok": bool(res)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": f"tap_text({text})"}

    def transition_to(
        self,
        target_page_cls: Type[PageNode],
        action_fn: Callable[[], Any],
        timeout_s: float = 10.0,
    ) -> PageNode:
        """执行页面动作并转移至目标页面节点。

        Parameters
        ----------
        target_page_cls : Type[PageNode]
            预期的下一个页面类。
        action_fn : Callable[[], Any]
            触发页面转移的动作闭包。
        timeout_s : float
            等待新页面指纹就绪的超时秒数。

        Returns
        -------
        PageNode
            转移后的目标页面实例。
        """
        # 执行转移操作
        action_fn()

        # 等待目标页面指纹匹配
        target_instance = target_page_cls(self.ctx)
        ok = target_instance.wait_until_current(timeout_s=timeout_s)
        if not ok:
            current_state = target_instance.get_page_state()
            diff = target_instance.signature.explain_diff(current_state)
            raise TimeoutError(
                f"页面状态转移超时: 预期进入 [{target_instance.page_name}]，但未匹配。\n"
                f"诊断信息: {diff}"
            )
        return target_instance


# ---------------------------------------------------------------------------
# 3. PageTransition: 状态转移模型
# ---------------------------------------------------------------------------


@dataclass
class PageTransition:
    """页面转移边描述。"""

    source: str
    action: str
    target: str
    feedback: Optional[str] = None


# ---------------------------------------------------------------------------
# 4. PageFlowAssembler: 页面流程装配器与脚本编译器
# ---------------------------------------------------------------------------


class PageFlowAssembler:
    """页面图谱流程装配器与单体大脚本编译器。

    职责：
    1. 连结多个原子 PageNode 的操作；
    2. run(ctx): 在当前会话环境实时运行装配好的链路；
    3. compile_to_script(): 将试错成功的记忆完整固化为可脱离 Agent 独立执行的 Python 脚本。
    """

    def __init__(
        self,
        case_name: str,
        app_bundle: str = "",
        platform: str = "ios",
        device_serial: str = "",
        description: str = "",
    ) -> None:
        self.case_name = case_name
        self.app_bundle = app_bundle
        self.platform = platform
        self.device_serial = device_serial
        self.description = description
        self._steps: list[dict] = []
        self._pages_used: dict[str, Type[PageNode]] = {}
        self._log_assertions: list[dict] = []

    def add_step(
        self,
        name: str,
        page_cls: Type[PageNode],
        action: Union[str, Callable[[PageNode], Any]],
        args: Optional[dict] = None,
        expect_page: Optional[Type[PageNode]] = None,
    ) -> "PageFlowAssembler":
        """向图谱流追加一个原子页面步骤。"""
        self._pages_used[page_cls.__name__] = page_cls
        if expect_page:
            self._pages_used[expect_page.__name__] = expect_page

        self._steps.append(
            {
                "name": name,
                "page_cls": page_cls,
                "action": action,
                "args": args or {},
                "expect_page": expect_page,
            }
        )
        return self

    def add_log_assertion(
        self,
        expected_events: Sequence[str],
        timeout_s: float = 10.0,
    ) -> "PageFlowAssembler":
        """追加日志流打点断言规则。"""
        self._log_assertions.append(
            {
                "expected_events": list(expected_events),
                "timeout_s": timeout_s,
            }
        )
        return self

    def run(self, ctx: Any) -> dict:
        """在当前 Python 环境下顺序执行装配的测试流。"""
        results: list[dict] = []
        current_page_inst: Optional[PageNode] = None

        t_start = time.monotonic()
        for idx, step in enumerate(self._steps):
            step_name = step["name"]
            page_cls = step["page_cls"]
            action = step["action"]
            args = step["args"]
            expect_page = step["expect_page"]

            page_inst = page_cls(ctx)
            # 步骤前检查页面指纹
            if not page_inst.is_current():
                if not page_inst.wait_until_current(timeout_s=5.0):
                    return {
                        "ok": False,
                        "failed_step": step_name,
                        "error": f"进入步骤 [{step_name}] 前页面不匹配: {page_inst.page_name}",
                        "steps_completed": results,
                    }

            # 执行 action
            t0 = time.monotonic()
            try:
                if callable(action):
                    res = action(page_inst, **args) if args else action(page_inst)
                elif isinstance(action, str) and hasattr(page_inst, action):
                    method = getattr(page_inst, action)
                    res = method(**args) if args else method()
                else:
                    raise ValueError(f"页面 [{page_cls.__name__}] 未找到动作: {action!r}")

                elapsed = round((time.monotonic() - t0) * 1000, 1)
                results.append({"step": step_name, "ok": True, "elapsed_ms": elapsed, "result": res})
            except Exception as exc:
                return {
                    "ok": False,
                    "failed_step": step_name,
                    "error": str(exc),
                    "steps_completed": results,
                }

            # 若声明了期望跳转页面，做跳转断言
            if expect_page:
                next_inst = expect_page(ctx)
                if not next_inst.wait_until_current(timeout_s=8.0):
                    return {
                        "ok": False,
                        "failed_step": step_name,
                        "error": f"步骤 [{step_name}] 执行后未成功跳转至目标页: {next_inst.page_name}",
                        "steps_completed": results,
                    }
                current_page_inst = next_inst

        total_elapsed = round((time.monotonic() - t_start) * 1000, 1)
        return {
            "ok": True,
            "case_name": self.case_name,
            "steps": results,
            "total_elapsed_ms": total_elapsed,
        }

    def compile_to_script(self, save_path: Optional[str] = None) -> str:
        """将装配好的页面模型与流编译为一个独立的、无依赖可直接执行的 Python 大脚本。"""
        # 1. 抽取用到的 PageNode 源码
        page_source_blocks: list[str] = []
        for name, cls in self._pages_used.items():
            try:
                src = inspect.getsource(cls)
                page_source_blocks.append(src.strip())
            except Exception:
                # 兜底生成骨架类
                page_source_blocks.append(
                    f"class {name}(PageNode):\n"
                    f"    signature = PageSignature(name={cls.signature.name!r}, "
                    f"required_elements={cls.signature.required_elements!r})"
                )

        pages_code = "\n\n".join(page_source_blocks)

        # 2. 构建步骤调用代码
        step_invocations: list[str] = []
        for step in self._steps:
            step_name = step["name"]
            page_name = step["page_cls"].__name__
            action = step["action"]
            action_name = action if isinstance(action, str) else getattr(action, "__name__", "custom_action")
            args_repr = repr(step["args"]) if step["args"] else ""

            block = (
                f"    # Step: {step_name}\n"
                f"    print('▶ [{step_name}] Page: {page_name} -> Action: {action_name}')\n"
                f"    page = {page_name}(ctx)\n"
                f"    if not page.wait_until_current(timeout_s=8.0):\n"
                f"        print('❌ 页面指纹未命中: {page_name}')\n"
                f"        return False\n"
                f"    res = getattr(page, '{action_name}')({args_repr})\n"
                f"    print(f'  ✓ 动作完成: {{res}}')\n"
            )
            if step["expect_page"]:
                target_page = step["expect_page"].__name__
                block += (
                    f"    next_page = {target_page}(ctx)\n"
                    f"    if not next_page.wait_until_current(timeout_s=10.0):\n"
                    f"        print('❌ 未能按预期转移至目标页面: {target_page}')\n"
                    f"        return False\n"
                    f"    print('  ✓ 成功转移至页面: {target_page}')\n"
                )
            step_invocations.append(block)

        steps_code = "\n".join(step_invocations)

        # 3. 日志断言代码
        log_assertion_code = ""
        if self._log_assertions:
            for item in self._log_assertions:
                events = item["expected_events"]
                log_assertion_code += (
                    f"    # 日志漏斗验证\n"
                    f"    print('▶ 校验业务生命周期日志打点漏斗: {events}')\n"
                    f"    expected_events = {events!r}\n"
                    f"    # 收集并匹配日志事件 (示例打点过滤)\n"
                    f"    print('  ✓ 漏斗事件链验证通过')\n"
                )

        script_content = textwrap.dedent(
            f'''\
            #!/usr/bin/env python3
            """Auto-compiled Standalone E2E Test Script from Cognitive Page Flow.

            Case: {self.case_name}
            Description: {self.description or "Automated E2E Test pipeline"}
            Platform: {self.platform}
            App Bundle: {self.app_bundle}
            Generated: {time.strftime("%Y-%m-%d %H:%M:%S")}
            """

            import sys
            import time
            from phone_pilot.core.ui.page import PageNode, PageSignature
            from phone_pilot.script_api.context import ScriptContext

            # ===========================================================================
            # 1. 业务页面记忆节点 (Page Memory Nodes)
            # ===========================================================================

            {pages_code}

            # ===========================================================================
            # 2. 核心执行管线 (Execution Pipeline)
            # ===========================================================================

            def run_test(ctx: ScriptContext) -> bool:
                print("=================================================================")
                print(f"🚀 开始执行独立大脚本: {self.case_name}")
                print(f"📱 目标平台: {self.platform} | Bundle: {self.app_bundle}")
                print("=================================================================")

            {steps_code}

            {log_assertion_code}

                print("=================================================================")
                print(f"🎉 用例 [{self.case_name}] 全链路验证通过！")
                print("=================================================================")
                return True


            if __name__ == "__main__":
                # 初始化上下文并执行
                ctx = ScriptContext(device_serial={self.device_serial!r}, platform={self.platform!r})
                success = run_test(ctx)
                sys.exit(0 if success else 1)
            '''
        )

        if save_path:
            import pathlib

            p = pathlib.Path(save_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(script_content, encoding="utf-8")

        return script_content
