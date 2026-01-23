#!/usr/bin/env python3
"""
Workflow Runner - Core workflow execution engine.
工作流执行器 - 核心工作流执行引擎

This module contains the main workflow execution logic extracted from mcp_server.py.
本模块包含从 mcp_server.py 抽取的主要工作流执行逻辑。

Key components / 关键组件:
- WorkflowExprError: Exception for expression evaluation errors / 表达式求值错误异常
- workflow_eval_expr: Safe expression evaluator for workflow conditions / 工作流条件的安全表达式求值器
- WorkflowContext: Runtime context for workflow execution / 工作流执行的运行时上下文
- run_workflow_steps: Main step execution loop / 主步骤执行循环
"""

from __future__ import annotations

import ast
import asyncio
import copy as _copy
import json
import pathlib
import time
import uuid
from typing import Any, Callable, Optional

from android_tool.adb_utils import adb_prefix
from android_tool.android_device_utils import (
    clear_background_processes,
    get_current_focus,
    get_screen_size,
    input_swipe,
    input_text,
    input_keyevent,
    open_deeplink,
    reset_to_home,
    restart_app,
    set_clipboard_text,
)
from android_tool.clicker import Clicker
from android_tool.device_lock import get_device_state, get_display_state, wake_and_unlock
from android_tool.device_store import capture_device_profile, get_device_profile_from_store, get_device_unlock_pin
from android_tool.logcat import clear_logcat, read_logcat
from android_tool.recordings_store import ensure_abs, iso_now, now_dirname, now_ts, read_json, safe_name, write_json
from android_tool.runner import CommandRunner
from android_tool.screenrecord import WorkflowScreenRecorder
from android_tool.screenshot import save_screenshot_png
from android_tool.uiautomator import UINode, dump_ui_xml, find_nodes, parse_uiautomator_nodes, pick_scrollable_bounds, ui_signature


class WorkflowExprError(ValueError):
    """
    Exception raised when workflow expression evaluation fails.
    工作流表达式求值失败时抛出的异常。
    """
    pass


def workflow_eval_expr(expr: str, env: dict[str, Any]) -> Any:
    """
    Evaluate a small, safe expression language for workflows.
    为工作流求值一个小型、安全的表达式语言。

    Supported syntax / 支持的语法:
    - Literals / 字面量: True/False/None, numbers, strings
    - Boolean ops / 布尔运算: and/or/not
    - Comparisons / 比较: == != > >= < <= in not in is is not
    - Attribute access / 属性访问: `vars.foo` == `vars["foo"]` (missing -> None)
    - Subscript access / 下标访问: `vars["foo"]`, `last["count"]`
    - Limited functions / 有限函数: `len(x)`, `get(d, "k", default)`, `has(d,"k")`, `contains(a,b)`

    Examples / 示例:
    - `last and last.ok == True`
    - `vars.need_login == True`
    - `not vars.skip and last.error != None`

    Args:
        expr: Expression string to evaluate / 要求值的表达式字符串
        env: Environment dict with variables like 'vars', 'last' / 包含 'vars', 'last' 等变量的环境字典

    Returns:
        Evaluated result / 求值结果

    Raises:
        WorkflowExprError: If expression is invalid or uses disallowed syntax
                          如果表达式无效或使用了不允许的语法
    """
    s = (expr or "").strip()
    if not s:
        raise WorkflowExprError("empty expression")

    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise WorkflowExprError(f"syntax error: {e}") from e

    allowed = (
        ast.Expression,
        ast.BoolOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Attribute,
        ast.Subscript,
        ast.Call,
        ast.keyword,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.NotEq,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        ast.Gt,
        ast.GtE,
        ast.Lt,
        ast.LtE,
    )

    for n in ast.walk(tree):
        if not isinstance(n, allowed):
            raise WorkflowExprError(f"unsupported syntax: {type(n).__name__}")

    def fn_len(x: Any) -> int:
        try:
            return len(x)  # type: ignore[arg-type]
        except Exception:
            return 0

    def fn_get(d: Any, k: Any, default: Any = None) -> Any:
        if isinstance(d, dict):
            return d.get(k, default)
        return default

    def fn_has(d: Any, k: Any) -> bool:
        if isinstance(d, dict):
            return k in d
        return False

    def fn_contains(a: Any, b: Any) -> bool:
        try:
            return b in a  # type: ignore[operator]
        except Exception:
            return False

    safe_funcs = {
        "len": fn_len,
        "get": fn_get,
        "has": fn_has,
        "contains": fn_contains,
    }

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise WorkflowExprError("only simple function calls are allowed")
            fname = node.func.id
            if fname not in safe_funcs:
                raise WorkflowExprError(f"function not allowed: {fname}")
            if node.keywords:
                raise WorkflowExprError("keyword arguments are not allowed")
            args = [ev(a) for a in node.args]
            try:
                return safe_funcs[fname](*args)
            except TypeError as e:
                raise WorkflowExprError(f"bad call to {fname}: {e}") from e
        if isinstance(node, ast.Attribute):
            base = ev(node.value)
            attr = node.attr
            if attr.startswith("__"):
                raise WorkflowExprError("dunder attribute is not allowed")
            if isinstance(base, dict):
                return base.get(attr)
            raise WorkflowExprError(f"attribute access on non-dict is not allowed: {attr}")
        if isinstance(node, ast.Subscript):
            base = ev(node.value)
            key = ev(node.slice) if isinstance(node.slice, ast.AST) else node.slice
            if not isinstance(key, (str, int)):
                raise WorkflowExprError("only string/int subscripts are allowed")
            try:
                return base[key]  # type: ignore[index]
            except Exception:
                return None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(ev(node.operand))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                for v in node.values:
                    if not bool(ev(v)):
                        return False
                return True
            if isinstance(node.op, ast.Or):
                for v in node.values:
                    if bool(ev(v)):
                        return True
                return False
            raise WorkflowExprError("unsupported boolean operator")
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                ok: bool
                if isinstance(op, ast.Eq):
                    ok = left == right
                elif isinstance(op, ast.NotEq):
                    ok = left != right
                elif isinstance(op, ast.Gt):
                    ok = left > right
                elif isinstance(op, ast.GtE):
                    ok = left >= right
                elif isinstance(op, ast.Lt):
                    ok = left < right
                elif isinstance(op, ast.LtE):
                    ok = left <= right
                elif isinstance(op, ast.In):
                    ok = left in right  # type: ignore[operator]
                elif isinstance(op, ast.NotIn):
                    ok = left not in right  # type: ignore[operator]
                elif isinstance(op, ast.Is):
                    ok = left is right
                elif isinstance(op, ast.IsNot):
                    ok = left is not right
                else:
                    raise WorkflowExprError(f"unsupported comparator: {type(op).__name__}")
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.List):
            return [ev(x) for x in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(ev(x) for x in node.elts)
        if isinstance(node, ast.Dict):
            return {ev(k): ev(v) for k, v in zip(node.keys, node.values)}
        raise WorkflowExprError(f"unsupported expression node: {type(node).__name__}")

    return ev(tree)


def write_runlog_line(path: pathlib.Path, obj: dict) -> None:
    """
    Append a JSON line to the workflow run log.
    向工作流运行日志追加一行 JSON。

    Args:
        path: Path to the run log file / 运行日志文件路径
        obj: Dict to serialize and append / 要序列化并追加的字典
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class VisionCache:
    """
    Cache for vision-related workflow optimizations.
    视觉相关工作流优化的缓存。

    Stores successful scroll directions, UI element info, and image matching thresholds
    to improve subsequent run success rates.
    存储成功的滚动方向、UI 元素信息和图像匹配阈值，以提高后续运行的成功率。
    """

    def __init__(self, cache_dir: pathlib.Path):
        """
        Initialize vision cache.
        初始化视觉缓存。

        Args:
            cache_dir: Directory to store cache file / 存储缓存文件的目录
        """
        self.cache_dir = cache_dir
        self.cache_path = cache_dir / "vision_cache.json"
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        """Load cache from disk. 从磁盘加载缓存。"""
        try:
            if self.cache_path.exists():
                data = read_json(self.cache_path)
                if isinstance(data, dict):
                    self._data = data
        except Exception:
            self._data = {}

    def save(self) -> None:
        """Save cache to disk. 保存缓存到磁盘。"""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            write_json(self.cache_path, self._data)
        except Exception:
            pass

    def clear(self) -> None:
        """Clear all cached data. 清除所有缓存数据。"""
        try:
            if self.cache_path.exists():
                write_json(self.cache_path, {})
            self._data = {}
        except Exception:
            pass

    def make_key(self, device_serial: Optional[str], raw_key: Optional[str]) -> Optional[str]:
        """
        Create a cache key from device serial and raw key.
        从设备序列号和原始键创建缓存键。

        Args:
            device_serial: Device serial number / 设备序列号
            raw_key: Raw cache key from step config / 步骤配置中的原始缓存键

        Returns:
            Combined cache key or None if raw_key is empty
            组合的缓存键，如果 raw_key 为空则返回 None
        """
        k = str(raw_key or "").strip()
        if not k:
            return None
        return f"{device_serial or 'default'}::{k}"

    def get_entry(self, cache_key: Optional[str]) -> Optional[dict]:
        """
        Get cache entry by key.
        通过键获取缓存条目。

        Args:
            cache_key: Cache key / 缓存键

        Returns:
            Cache entry dict or None / 缓存条目字典或 None
        """
        if not cache_key:
            return None
        raw = self._data.get(cache_key)
        if isinstance(raw, list):
            return {"directions": raw}
        if isinstance(raw, dict):
            return raw
        return None

    def update_entry(self, cache_key: Optional[str], updates: dict) -> None:
        """
        Update cache entry with new data.
        用新数据更新缓存条目。

        Args:
            cache_key: Cache key / 缓存键
            updates: Dict of updates to merge / 要合并的更新字典
        """
        if not cache_key:
            return
        entry = self.get_entry(cache_key) or {}
        entry.update(updates)
        self._data[cache_key] = entry
        self.save()

    def apply_cached_directions(self, dirs: list[str], cache_key: Optional[str]) -> list[str]:
        """
        Reorder directions based on cached successful direction.
        根据缓存的成功方向重新排序方向列表。

        Args:
            dirs: Original direction list / 原始方向列表
            cache_key: Cache key / 缓存键

        Returns:
            Reordered direction list / 重新排序的方向列表
        """
        if not cache_key:
            return dirs
        entry = self.get_entry(cache_key)
        if not entry:
            return dirs
        cached = entry.get("directions")
        if not isinstance(cached, list):
            return dirs
        ordered: list[str] = []
        for d in cached:
            d2 = str(d or "").strip().lower()
            if d2 in ("up", "down", "left", "right") and d2 in dirs and d2 not in ordered:
                ordered.append(d2)
        for d in dirs:
            if d not in ordered:
                ordered.append(d)
        return ordered or dirs


def normalize_directions(dirs_raw: Any) -> list[str]:
    """
    Normalize direction list for scrolling.
    规范化滚动方向列表。

    Args:
        dirs_raw: Raw direction input (list or None) / 原始方向输入（列表或 None）

    Returns:
        Normalized list of valid directions / 规范化的有效方向列表
    """
    if not isinstance(dirs_raw, list):
        return ["up", "down", "left", "right"]
    dirs2: list[str] = []
    for d in dirs_raw:
        d2 = str(d or "").strip().lower()
        if d2 in ("up", "down", "left", "right") and d2 not in dirs2:
            dirs2.append(d2)
    return dirs2 or ["up", "down", "left", "right"]


def normalize_verify(verify_raw: Any) -> list[dict]:
    """
    Normalize verify configuration to list of dicts.
    将验证配置规范化为字典列表。

    Args:
        verify_raw: Raw verify config (dict, list, or None) / 原始验证配置

    Returns:
        List of verify config dicts / 验证配置字典列表
    """
    if isinstance(verify_raw, dict):
        return [verify_raw]
    if isinstance(verify_raw, list):
        return [x for x in verify_raw if isinstance(x, dict)]
    return []


def node_at_point(nodes: list[UINode], x: int, y: int) -> Optional[UINode]:
    """
    Find the smallest UI node containing the given point.
    查找包含给定点的最小 UI 节点。

    Args:
        nodes: List of UI nodes / UI 节点列表
        x: X coordinate / X 坐标
        y: Y coordinate / Y 坐标

    Returns:
        Smallest node containing point, or None / 包含该点的最小节点，或 None
    """
    best = None
    best_area = None
    for n in nodes:
        b = n.bounds_tuple()
        if not b:
            continue
        x1, y1, x2, y2 = b
        if x1 <= x <= x2 and y1 <= y <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            if best_area is None or area < best_area:
                best_area = area
                best = n
    return best


def dump_nodes(device_serial: Optional[str]) -> list[UINode]:
    """
    Dump UI hierarchy and parse nodes.
    导出 UI 层级并解析节点。

    Args:
        device_serial: Device serial number / 设备序列号

    Returns:
        List of parsed UI nodes / 解析后的 UI 节点列表
    """
    res = dump_ui_xml(device_serial, compressed=True)
    if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
        return []
    return parse_uiautomator_nodes(str(res.get("xml") or ""))


def compact_step_result(step: dict) -> dict:
    """
    Compact step result to reduce output size.
    压缩步骤结果以减少输出大小。

    Removes redundant fields and keeps only essential information.
    移除冗余字段，只保留必要信息。

    Args:
        step: Step result dict / 步骤结果字典

    Returns:
        Compacted step dict / 压缩后的步骤字典
    """
    typ = step.get("type")
    res = step.get("result")
    if not isinstance(res, dict):
        return step

    def pick(keys: list[str]) -> dict:
        return {k: res.get(k) for k in keys if k in res}

    compact: dict
    if typ == "clear_background_processes":
        compact = pick(["ok", "mode", "run_kill_all_first", "candidates_count", "target_count", "stopped_count", "note"])
    elif typ in ("sleep", "reset_home", "set_options", "restart_app", "force_stop_app"):
        compact = pick(["ok", "home_presses", "package", "component", "deeplink"])
    elif typ in ("launch_from_home", "launch_using_profile", "ensure_launch"):
        compact = pick(["ok", "component", "adb_cmd"])
        focus = res.get("focus")
        if isinstance(focus, dict):
            compact["focus"] = {k: focus.get(k) for k in ("package", "activity") if k in focus}
    elif typ in ("find_and_tap", "find_image_and_tap"):
        compact = pick(["ok", "query", "field", "exact"])
        match = res.get("match")
        if isinstance(match, dict):
            compact["match"] = {k: match.get(k) for k in ("resource_id", "text", "content_desc", "class_name", "center_x", "center_y") if k in match}
    elif typ in ("find_on_screen", "find_image_on_screen", "find_multi_stage"):
        compact = pick(["ok", "count", "error"])
    elif typ in ("collect_artifacts",):
        compact = pick(["ok", "artifacts_dir", "focused_package", "focused_activity"])
        logcat = res.get("logcat")
        if isinstance(logcat, dict):
            compact["logcat"] = {k: logcat.get(k) for k in ("path", "package_name", "pid_used") if k in logcat}
    elif typ in ("wait_for_ui", "wait_for_focus"):
        compact = pick(["ok", "query", "field", "exact", "elapsed_s"])
    else:
        compact = pick(["ok", "error"])

    compact.pop("device_serial", None)
    step["result"] = compact
    step.pop("step_path", None)
    return step


def strip_key_recursive(obj: Any, key: str, *, keep_root: bool = False, _is_root: bool = True) -> Any:
    """
    Recursively remove a key from nested dicts/lists.
    递归地从嵌套的字典/列表中移除指定键。

    Args:
        obj: Object to process / 要处理的对象
        key: Key to remove / 要移除的键
        keep_root: Whether to keep the key at root level / 是否在根级别保留该键
        _is_root: Internal flag for recursion / 递归内部标志

    Returns:
        Processed object / 处理后的对象
    """
    if isinstance(obj, dict):
        if key in obj and not (keep_root and _is_root):
            obj.pop(key, None)
        for v in obj.values():
            strip_key_recursive(v, key, keep_root=keep_root, _is_root=False)
    elif isinstance(obj, list):
        for v in obj:
            strip_key_recursive(v, key, keep_root=keep_root, _is_root=False)
    return obj
