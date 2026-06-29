"""MCP 工具 UI 缓存失效 hook。

在 MCP 工具注册前包装 ``mcp.tool``，使每个动作类工具成功执行后失效
ui_snapshot_cache，避免后续查询拿到旧 UI 层级。

与 _recorder.py 的录制 hook 独立：录制只包 RECORDABLE 工具，缓存失效
需要覆盖所有动作工具（包括 phone_tap_element 等非录制工具）。
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from phone_pilot.core import ui_snapshot_cache


# ---------------------------------------------------------------------------
# 动作类工具白名单：这些工具改变设备/UI 状态，成功后需失效 UI 缓存。
# 与 _recorder.RECORDABLE 不同：这里包含 phone_tap_element（非录制工具），
# 但不包含 phone_screenshot / phone_scroll_to_find / phone_wait_for_element
# （它们不直接改变 UI 或本身是查询/等待类）。
# ---------------------------------------------------------------------------

_ACTION_INVALIDATES_UI: frozenset[str] = frozenset(
    {
        "phone_tap",
        "phone_tap_text",
        "phone_tap_element",
        "phone_swipe",
        "phone_long_press",
        "phone_input_text",
        "phone_keyevent",
        "phone_launch_app",
        "phone_force_stop",
        "phone_go_back",
        "phone_go_home",
        "phone_clear_background",
        "phone_open_deeplink",
        "phone_launch_from_home",
        "phone_unlock",
    }
)


def _safe_invalidate_ui(tool_name: str, kwargs: dict, result: Any) -> None:
    """工具调用后按需失效 UI 缓存。

    - 非 action 工具：跳过。
    - result.ok=False：跳过（失败动作不改变 UI）。
    - 无 serial：跳过（无法定位缓存 key）。
    """
    if tool_name not in _ACTION_INVALIDATES_UI:
        return
    if not isinstance(result, dict):
        return
    if not result.get("ok", True):
        return

    serial = result.get("device_serial") or kwargs.get("device_serial")
    platform = result.get("platform") or kwargs.get("platform")

    if not serial:
        return

    try:
        ui_snapshot_cache.invalidate_device_ui(serial, platform=platform)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hook installation — wraps mcp.tool() so every registered tool gets
# post-call cache invalidation when it's an action tool.
# ---------------------------------------------------------------------------


def install_cache_invalidation_hook(mcp_server: Any) -> None:
    """包装 ``mcp_server.tool`` 使动作类工具成功后失效 UI 缓存。

    必须在工具子模块 import 之前调用（与 install_recording_hook 类似）。
    可与录制 hook 叠加使用。
    """
    original_tool = mcp_server.tool

    @functools.wraps(original_tool)
    def patched_tool(*t_args, **t_kwargs):
        decorator = original_tool(*t_args, **t_kwargs)

        def wrapping_decorator(fn):
            tool_name = getattr(fn, "__name__", "")

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*a, **kw):
                    result = await fn(*a, **kw)
                    _safe_invalidate_ui(tool_name, kw, result)
                    return result

                return decorator(async_wrapper)

            @functools.wraps(fn)
            def sync_wrapper(*a, **kw):
                result = fn(*a, **kw)
                _safe_invalidate_ui(tool_name, kw, result)
                return result

            return decorator(sync_wrapper)

        return wrapping_decorator

    mcp_server.tool = patched_tool
