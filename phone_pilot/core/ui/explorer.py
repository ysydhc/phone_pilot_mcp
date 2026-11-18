"""AppExplorer — 启发式自动化应用页面探索与脑图拓扑建图引擎。

提供一键探索（One-Click App Exploration）能力：
1. 自动抓取当前或启动目标应用，以当前主页作为根节点。
2. 启发式识别可交互组件（Tab、按钮、列表项），自动过滤破坏性敏感动作（如退出/删除/支付等）。
3. 动态触发点击转移并捕捉前后页面状态（Activity/VC、关键标题与元素），自动建立 UIKnowledgeGraph。
4. 结合拓扑回溯（Back 键或定向自愈导航）支持多分支页面深度遍历。
5. 一键输出标准化页面知识图谱、Mermaid 有向图与交互式脑图数据结构。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from phone_pilot.core.log import _log
from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph
from phone_pilot.core.ui.navigator import UINavigator


DEFAULT_DESTRUCTIVE_BLACKLIST: tuple[str, ...] = (
    "退出",
    "注销",
    "登出",
    "解绑",
    "删除",
    "清空",
    "清除数据",
    "卸载",
    "销户",
    "支付",
    "充值",
    "购买",
    "立即付款",
    "格式化",
    "重置",
    "Logout",
    "Log out",
    "Sign out",
    "Delete",
    "Clear",
    "Uninstall",
    "Pay",
    "Purchase",
    "Reset",
    "Format",
    "切换夜间模式",
    "切换日间模式",
    "深色模式",
)


@dataclass
class ExploreStep:
    from_node_id: str
    action: dict[str, Any]
    to_node_id: str
    is_new_page: bool
    timestamp: float = field(default_factory=time.time)


class AppExplorer:
    """启发式自动化应用页面探索引擎。"""

    def __init__(
        self,
        driver: Any = None,
        graph: Optional[UIKnowledgeGraph] = None,
        *,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        action_executor: Callable[[dict[str, Any]], bool] | None = None,
        back_executor: Callable[[], bool] | None = None,
        destructive_blacklist: Sequence[str] | None = None,
        target_package: Optional[str] = None,
        lock_package: bool = True,
    ) -> None:
        self.driver = driver
        self.graph = graph if graph is not None else UIKnowledgeGraph()
        self.state_provider = state_provider
        self.action_executor = action_executor
        self.back_executor = back_executor
        self.blacklist = tuple(destructive_blacklist) if destructive_blacklist is not None else DEFAULT_DESTRUCTIVE_BLACKLIST
        self.target_package: Optional[str] = target_package.strip() if target_package else None
        self.lock_package: bool = lock_package
        self.history: list[ExploreStep] = []
        self._cached_screen_size: tuple[int, int] = (0, 0)
        self._fragment_probe_supported: dict[str, bool] = {}
        self._current_scroll_page: int = 0
        self._scroll_identity: tuple[Any, ...] | None = None
        self._scroll_offsets: dict[tuple[Any, ...], int] = {}
        self._viewport_offsets: dict[tuple[Any, ...], int] = {}
        self._deadline: float | None = None

    @staticmethod
    def _extract_package(state: dict[str, Any]) -> str:
        """从页面状态字典中提取当前包名 / Bundle ID。"""
        if not isinstance(state, dict):
            return ""
        act = state.get("activity")
        if isinstance(act, dict):
            pkg = act.get("package") or act.get("bundle_id")
            if isinstance(pkg, str) and pkg.strip():
                return pkg.strip()
            act_str = str(act.get("activity") or "").strip()
        elif isinstance(act, str):
            act_str = act.strip()
        else:
            act_str = ""

        for key in ("package", "bundle_id", "app_id"):
            val = state.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        if "/" in act_str:
            prefix = act_str.split("/", 1)[0].strip()
            if "." in prefix:
                return prefix
        return ""

    def _is_within_target_package(self, state: dict[str, Any], locked_pkg: Optional[str]) -> bool:
        """检查当前页面状态是否处于目标包名锁范围内。"""
        if not self.lock_package or not locked_pkg:
            return True
        curr_pkg = self._extract_package(state)
        if not curr_pkg:
            # 纯离线状态提供器可不含包名；真实设备无法确认前台归属时拒绝动作。
            return self.driver is None
        return curr_pkg == locked_pkg

    @staticmethod
    def _is_login_wall_state(state: dict[str, Any]) -> bool:
        """识别页面是否为登录拦截页/半屏登录弹窗（记录节点入图，但不再向登录表单内部深挖点击）。"""
        act_str = UIKnowledgeGraph._extract_activity_str(state).lower()
        if "login" in act_str or "signin" in act_str:
            return True
        login_markers = (
            "请输入手机号",
            "请输入密码",
            "验证码登录",
            "验证登录",
            "账号密码登录",
            "登录注册解锁",
            "未注册绑定的手机号",
            "一键登录",
            "登录后即可",
        )
        for el in state.get("elements") or []:
            if not isinstance(el, dict):
                continue
            txt = str(el.get("text") or el.get("label") or el.get("content_desc") or "")
            if any(m in txt for m in login_markers):
                return True
        for txt in state.get("all_texts") or []:
            if isinstance(txt, str) and any(m in txt for m in login_markers):
                return True
        return False

    def _force_restart_target_package(self, locked_pkg: str, step_delay_s: float = 0.8) -> bool:
        """当陷入无法通过 Back 退出的弹窗/死胡同时，强制重启目标应用回到初始首页。"""
        if not locked_pkg or self.driver is None or self._budget_exhausted():
            return False
        app_drv = getattr(self.driver, "app", None)
        if app_drv is not None:
            stop_fn = getattr(app_drv, "force_stop", None)
            if callable(stop_fn):
                try:
                    stop_fn(locked_pkg)
                    time.sleep(0.5)
                except Exception as exc:
                    _log(f"[AppExplorer] force_stop {locked_pkg} failed: {exc}")
        ok = self._relaunch_target_package(locked_pkg)
        if ok and step_delay_s > 0:
            time.sleep(max(2.5, step_delay_s * 2))
            for _ in range(3):
                if self._budget_exhausted():
                    break
                st = self.get_current_state(enrich_system=False)
                if len(st.get("elements") or []) >= 6:
                    break
                time.sleep(1.8)
        return ok

    def _relaunch_target_package(self, locked_pkg: str) -> bool:
        """当逃逸出目标包名时，重新拉起目标应用回前台。"""
        if not locked_pkg or self.driver is None or self._budget_exhausted():
            return False
        app_drv = getattr(self.driver, "app", None)
        if app_drv is not None:
            for method_name in ("launch_app", "launch", "start"):
                fn = getattr(app_drv, method_name, None)
                if callable(fn):
                    try:
                        if method_name == "launch_app":
                            result = fn(locked_pkg, None)
                        else:
                            result = fn(locked_pkg)
                        if self._action_succeeded(result):
                            return True
                    except Exception as exc:
                        _log(f"[AppExplorer] relaunch {locked_pkg} via {method_name} failed: {exc}")
        return False

    def _ensure_in_target_package(self, locked_pkg: Optional[str], step_delay_s: float = 0.8) -> dict[str, Any]:
        """确保设备前台处于目标包名内；若逃逸则先尝试 Back，仍逃逸则重拉应用。"""
        st = self.get_current_state()
        if self._is_within_target_package(st, locked_pkg):
            return st

        curr_pkg = self._extract_package(st)
        _log(f"[AppExplorer] Package lock triggered: escaped to '{curr_pkg}', target='{locked_pkg}'. Recovering...")
        self.execute_back()
        if step_delay_s > 0:
            time.sleep(step_delay_s)
        st = self.get_current_state()
        if self._is_within_target_package(st, locked_pkg):
            return st

        if locked_pkg and self._relaunch_target_package(locked_pkg):
            if step_delay_s > 0:
                time.sleep(max(1.0, step_delay_s))
            st = self.get_current_state()
        return st

    def _get_device_serial(self) -> str:
        """从 driver 提取设备序列号（仅真机/模拟器环境有效）。"""
        if self.driver is None:
            return ""
        for attr in ("device_serial", "_device_serial", "serial"):
            val = getattr(self.driver, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for sub in ("input", "ui", "screen", "app"):
            sub_drv = getattr(self.driver, sub, None)
            if sub_drv is not None:
                for attr in ("_device_serial", "device_serial", "serial"):
                    val = getattr(sub_drv, attr, None)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
        return ""

    def _run_adb_shell(self, shell_cmd: str, timeout_s: float = 2.0) -> str:
        """执行轻量级 ADB shell 灰盒探针命令（失败或超时静默返回空串）。"""
        if self.driver is None or self._budget_exhausted():
            return ""
        import subprocess

        # 灰盒探针只允许绑定明确 Android 设备，不能落到默认 ADB 设备。
        if getattr(self.driver, "platform", None) != "android":
            return ""
        serial = self._get_device_serial()
        if not serial:
            return ""
        cmd = ["adb", "-s", serial, "shell", shell_cmd]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s, check=False
            )
            return proc.stdout or ""
        except Exception:
            return ""

    def extract_package_routes(self, package_name: str) -> dict[str, Any]:
        """灰盒静态路由探针：通过 dumpsys package 提取应用的 Activity 清单与 DeepLink Schemes。"""
        if not package_name or self.driver is None:
            return {}
        import re

        import shlex

        out = self._run_adb_shell(f"dumpsys package {shlex.quote(package_name)}", timeout_s=3.0)
        if not out:
            return {}

        activities: set[str] = set()
        schemes: set[str] = set()
        pkg_escaped = re.escape(package_name)
        for m in re.finditer(rf"({pkg_escaped}/[A-Za-z0-9_.$]+)", out):
            act_comp = m.group(1)
            cls_part = act_comp.split("/", 1)[1]
            if cls_part.startswith("."):
                cls_part = f"{package_name}{cls_part}"
            if "Activity" in cls_part or "Launcher" in cls_part:
                activities.add(cls_part)

        for m in re.finditer(r'Scheme:\s*"([A-Za-z0-9_.+-]+)"', out):
            sch = m.group(1).strip()
            if sch and sch not in ("http", "https", "file", "content"):
                schemes.add(sch)

        route_info = {
            "package": package_name,
            "declared_activities_count": len(activities),
            "declared_activities": sorted(activities)[:80],
            "deeplink_schemes": sorted(schemes)[:30],
        }
        self.graph.metadata["package_routes"] = route_info
        return route_info

    def _query_active_fragment(self) -> str:
        """灰盒运行时栈探针：从 dumpsys activity top 提取当前处于活跃态的业务 Fragment 名称。"""
        if self.driver is None:
            return ""
        import re

        out = self._run_adb_shell(
            "dumpsys activity top | grep -E '#[0-9]+: [A-Za-z0-9_.$]+\\{' | tail -n 12",
            timeout_s=1.2,
        )
        if not out:
            return ""
        ignore_frags = {
            "ReportFragment",
            "SupportRequestManagerFragment",
            "RequestManagerFragment",
            "NavHostFragment",
            "InvisibleFragment",
            "PermissionFragment",
            "LifecycleFragment",
        }
        found: list[str] = []
        for m in re.finditer(r"#[0-9]+:\s+([A-Za-z0-9_.$]+)\{", out):
            f_name = m.group(1).split(".")[-1]
            if f_name and f_name not in ignore_frags:
                found.append(f_name)
        return found[-1] if found else ""

    def teleport_to_activity(self, package_name: str, activity_name: str, step_delay_s: float = 0.8) -> bool:
        """灰盒瞬移：尝试通过 am start 直接拉起目标 Activity。"""
        if not package_name or not activity_name or self.driver is None or self._budget_exhausted():
            return False
        if "MainActivity" in activity_name:
            return False
        import shlex

        comp = activity_name if "/" in activity_name else f"{package_name}/{activity_name}"
        if comp.split("/", 1)[0] != package_name:
            return False
        out = self._run_adb_shell(f"am start -W -n {shlex.quote(comp)}", timeout_s=3.0)
        if "Status: ok" in out and "Error" not in out and "Exception" not in out:
            if step_delay_s > 0:
                time.sleep(step_delay_s)
            return True
        return False

    def get_current_state(self, *, enrich_system: bool = True) -> dict[str, Any]:
        """获取当前页面状态字典（缓存屏幕尺寸并按需注入系统级活跃 Fragment 栈信息）。"""
        state: dict[str, Any] = {}
        if self.state_provider is not None:
            res = self.state_provider()
            state = res if isinstance(res, dict) else {}
        elif self.driver is not None and hasattr(self.driver, "get_page_state") and callable(self.driver.get_page_state):
            res = self.driver.get_page_state()
            state = res if isinstance(res, dict) else {}
        elif self.driver is not None and hasattr(self.driver, "ui"):
            try:
                from phone_pilot.mcp._page_state_tools import _collect_page_elements

                act_info = self.driver.ui.get_current_activity()
                w, h = self._cached_screen_size
                if w <= 0 or h <= 0:
                    try:
                        w, h = self.driver.screen.get_screen_size()
                        if w > 0 and h > 0:
                            self._cached_screen_size = (w, h)
                    except Exception:
                        w, h = 0, 0
                elements, scrollables, all_texts = _collect_page_elements(
                    self.driver, include_invisible=False
                )
                state = {
                    "ok": True,
                    "activity": act_info,
                    "screen": {"width": w, "height": h},
                    "elements": elements,
                    "element_count": len(elements),
                    "scrollable_areas": scrollables,
                    "all_texts": all_texts,
                }
            except Exception as exc:
                _log(f"[AppExplorer] collect page state failed: {exc}")
                state = {"ok": False, "elements": []}
        else:
            return {"ok": False, "elements": []}

        if enrich_system and state.get("ok", True) and self.driver is not None:
            act_str = self.graph._extract_activity_str(state)
            pkg = self._extract_package(state) or "default"
            if "MainActivity" in act_str and self._fragment_probe_supported.get(pkg, True):
                frag = self._query_active_fragment()
                if frag:
                    state["active_fragment"] = frag
                else:
                    self._fragment_probe_supported[pkg] = False
        return state

    def stitch_scrollable_state(
        self,
        base_state: dict[str, Any],
        *,
        max_scroll_pages: int = 2,
        step_delay_s: float = 0.8,
    ) -> dict[str, Any]:
        """探测性滚动视口拼接（Scroll-and-Stitch）：对可纵向滚动的核心功能/配置页自动下滑采样并合成多屏控件全集。"""
        if max_scroll_pages <= 1 or self.driver is None or not hasattr(self.driver, "input") or self._is_login_wall_state(base_state):
            return base_state

        screen_info = base_state.get("screen") or {}
        w = int(screen_info.get("width") or 0) if isinstance(screen_info, dict) else 0
        h = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0
        if w <= 0 or h <= 0:
            return base_state

        raw_els = base_state.get("elements") or []
        # 通用几何与结构特征判定：区分“无限内容瀑布流（Infinite Feed）”与“有界功能菜单/配置页（Bounded Menu）”
        # 若页面包含顶栏多频道标签列（>=3 个横向频道 Tab）、社交动态点赞/转发栏，或双列大尺寸媒体卡片瀑布流，跳过滚屏拼接
        top_tab_count = 0
        has_feed_interaction_bar = False
        has_ecommerce_header = False
        header_texts: set[str] = set()
        for e in raw_els:
            if not isinstance(e, dict):
                continue
            txt = str(e.get("text") or e.get("label") or e.get("content_desc") or "").strip()
            rid = str(e.get("resource_id") or e.get("identifier") or "").lower().split("/")[-1]
            bds = e.get("bounds")
            if any(k in rid for k in ("card_like", "card_comment", "card_repost", "like_count", "comment_count")):
                has_feed_interaction_bar = True
            if any(k in txt for k in ("播放视频", "未点赞", "点赞数", "原声")):
                has_feed_interaction_bar = True
            if isinstance(bds, (list, tuple)) and len(bds) == 4:
                try:
                    cy = (int(bds[1]) + int(bds[3])) // 2
                    if 0 < cy <= h * 0.24 and ("标签" in txt or "tab" in rid):
                        top_tab_count += 1
                    if 0 < cy <= h * 0.28 and txt:
                        header_texts.add(txt)
                except Exception:
                    pass
        if "购物车" in header_texts and ("我的订单" in header_texts or "订单" in header_texts):
            has_ecommerce_header = True
        is_infinite_feed = top_tab_count >= 3 or has_feed_interaction_bar or has_ecommerce_header
        if is_infinite_feed:
            return base_state

        # 判断是否存在显著的纵向可滚动区域
        scrollables = base_state.get("scrollable_areas") or []
        has_vert_scroll = any(
            isinstance(s, dict)
            and str(s.get("direction") or "").lower() == "vertical"
            and isinstance(s.get("bounds"), (list, tuple))
            and len(s["bounds"]) == 4
            and (int(s["bounds"][3]) - int(s["bounds"][1])) >= h * 0.40
            for s in scrollables
        )
        if not has_vert_scroll:
            has_vert_scroll = any(
                isinstance(el, dict)
                and any(
                    k in str(el.get("resource_id") or "").lower()
                    for k in ("recycle", "recycler", "scroll_view", "scrollview", "list_view")
                )
                for el in raw_els
            )
        if not has_vert_scroll:
            return base_state

        if not self._is_within_target_package(base_state, self.target_package):
            return base_state
        self._sync_scroll_state(base_state)
        # 采样入口也必须读取实际前台，不能用缓存页面授权swipe。
        if not self._same_scroll_page(base_state, self.get_current_state(enrich_system=True)):
            self._current_scroll_page = 0
            return base_state
        merged_elements: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for el in raw_els:
            if not isinstance(el, dict):
                continue
            el_copy = dict(el)
            el_copy.setdefault("scroll_page", 0)
            merged_elements.append(el_copy)
            txt = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
            rid = str(el.get("resource_id") or el.get("identifier") or "").strip()
            if txt or rid:
                seen_keys.add(f"{txt}|{rid}")

        actual_scrolls = 0
        cx = w // 2
        sy1, sy2 = int(h * 0.76), int(h * 0.26)

        for pg in range(1, max_scroll_pages):
            if self._budget_exhausted():
                break
            try:
                if not self._action_succeeded(self.driver.input.swipe(cx, sy1, cx, sy2, duration_ms=420)):
                    break
                actual_scrolls += 1
                time.sleep(max(0.55, step_delay_s * 0.75))
                next_st = self.get_current_state(enrich_system=True)
                if not self._same_scroll_page(base_state, next_st):
                    self._current_scroll_page = 0
                    self._scroll_identity = None
                    return base_state
                added_on_page = 0
                for el in next_st.get("elements") or []:
                    if not isinstance(el, dict):
                        continue
                    txt = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
                    rid = str(el.get("resource_id") or el.get("identifier") or "").strip()
                    if not txt and not rid:
                        continue
                    k = f"{txt}|{rid}"
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    el_copy = dict(el)
                    el_copy["scroll_page"] = pg
                    merged_elements.append(el_copy)
                    added_on_page += 1
                if added_on_page == 0:
                    break
            except Exception as exc:
                _log(f"[AppExplorer] stitch scroll page {pg} failed: {exc}")
                break

        # 滚回顶部视口（Viewport 0），显式失败时保留已知剩余偏移。
        self._current_scroll_page = actual_scrolls
        for _ in range(actual_scrolls):
            if self._budget_exhausted():
                break
            try:
                if not self._same_scroll_page(base_state, self.get_current_state(enrich_system=True)):
                    self._current_scroll_page = 0
                    self._scroll_identity = None
                    return base_state
                if self._budget_exhausted() or not self._action_succeeded(self.driver.input.swipe(cx, int(h * 0.26), cx, int(h * 0.80), duration_ms=380)):
                    break
                self._current_scroll_page -= 1
                time.sleep(0.45)
            except Exception:
                break

        stitched_state = dict(base_state)
        stitched_state["elements"] = merged_elements
        stitched_state["element_count"] = len(merged_elements)
        stitched_state["scroll_pages_stitched"] = 1 + actual_scrolls
        return stitched_state

    def _budget_exhausted(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    @staticmethod
    def _action_succeeded(result: Any) -> bool:
        """兼容驱动 dict 与旧无返回值动作，不吞掉显式失败。"""
        return bool(result.get("ok", False)) if isinstance(result, dict) else result is not False

    def _viewport_identity(self, state: dict[str, Any]) -> tuple[Any, ...]:
        """滚动偏移只属于同一包、页面、Fragment/host与稳定页标题。"""
        return (self._extract_package(state), state.get("node_id"),
                self.graph._extract_activity_str(state),
                self.graph._extract_active_fragment(state), state.get("host_node_id"),
                tuple(sorted(str(el.get("text") or el.get("label") or el.get("content_desc") or "")
                             for el in state.get("elements") or [] if isinstance(el, dict)
                             and any(key in str(el.get("resource_id") or el.get("identifier") or "").lower()
                                     for key in ("toolbar_title", "page_title", "navigation_title")))))

    def _sync_scroll_state(self, state: dict[str, Any]) -> None:
        identity = self._viewport_identity(state)
        same_page = self._scroll_identity is not None and identity[:-1] == self._scroll_identity[:-1] and (
            not identity[-1] or not self._scroll_identity[-1] or identity[-1] == self._scroll_identity[-1])
        if self._scroll_identity is not None and not same_page:
            self._scroll_offsets[self._scroll_identity] = self._current_scroll_page
            self._current_scroll_page = self._scroll_offsets.get(identity, 0)
        self._scroll_identity = identity

    def _viewport_key(self, state: dict[str, Any]) -> tuple[Any, ...]:
        controls = tuple(sorted(str(el.get("resource_id") or el.get("identifier") or el.get("text") or el.get("label") or "")
                                for el in state.get("elements") or [] if isinstance(el, dict)))
        return (*self._viewport_identity(state), controls)

    def _finish_tap(self, ok: bool, before: dict[str, Any] | None) -> bool:
        if ok and before is not None:
            self._viewport_offsets[self._viewport_key(before)] = self._current_scroll_page
            after = self.get_current_state(enrich_system=True)
            def controls(st: dict[str, Any]) -> set[tuple[str, str]]:
                return {(str(el.get("resource_id") or el.get("identifier") or ""),
                         "" if el.get("resource_id") or el.get("identifier") else
                         str(el.get("text") or el.get("label") or el.get("content_desc") or ""))
                        for el in st.get("elements") or [] if isinstance(el, dict)}
            if not self._same_scroll_page(before, after):
                self._sync_scroll_state(after)
            elif controls(before) != controls(after) and not before.get("node_id"):
                # 同Activity无身份元数据时保守隔离子页；保留父视口供Back恢复。
                self._scroll_offsets[self._viewport_identity(before)] = self._current_scroll_page
                self._current_scroll_page = 0
                self._scroll_identity = self._viewport_identity(after)
        return ok

    def _same_scroll_page(self, before: dict[str, Any], after: dict[str, Any]) -> bool:
        if not after.get("ok", True) or not self._is_within_target_package(after, self.target_package):
            return False
        # 即使未启用包锁，拼接也不能跨包或在包名丢失时猜测。
        if self._extract_package(before) != self._extract_package(after):
            return False
        if self.driver is not None and not self._extract_package(before) and not self.graph._extract_activity_str(before):
            return False
        before_id, after_id = self._viewport_identity(before), self._viewport_identity(after)
        return before_id[:-1] == after_id[:-1] and (not before_id[-1] or not after_id[-1] or before_id[-1] == after_id[-1])

    def execute_action(self, action: dict[str, Any], *, live_state: dict[str, Any] | None = None,
                       require_live_locator: bool = False) -> bool:
        """执行单一动作（支持有状态视口追踪、跨滚屏视口元素的自动定位与动态点击）。"""
        if self._budget_exhausted():
            return False
        criteria = action.get("trigger_criteria") if isinstance(action.get("trigger_criteria"), dict) else {}
        for value in (action.get("text"), action.get("resource_id"), criteria.get("text"), criteria.get("resource_id")):
            if value and self._is_destructive(str(value)):
                return False
        action_type = str(action.get("action_type") or action.get("type") or "tap")
        if live_state is None and (self.state_provider is not None or self.target_package or require_live_locator):
            live_state = self.get_current_state(enrich_system=True)
        if live_state is not None:
            if not live_state.get("ok", True) or not self._is_within_target_package(live_state, self.target_package):
                return False
            previous_identity = self._scroll_identity
            self._sync_scroll_state(live_state)
            known_offset = self._viewport_offsets.get(self._viewport_key(live_state))
            # 缓存只服务页面返回；不能覆盖同页刚观测到的部分滚动进度。
            if known_offset is not None and previous_identity is not None and (
                previous_identity[:-1] != self._scroll_identity[:-1] or self._current_scroll_page == 0):
                self._current_scroll_page = known_offset
        if self.action_executor is not None:
            if require_live_locator and action_type == "tap":
                locator = {**criteria, **{k: action[k] for k in
                           ("text", "label", "resource_id", "identifier", "content_desc", "name") if k in action}}
                matched = UINavigator._find_element_for_criteria(locator, live_state or {})
                if matched is None or matched.get("enabled") is False:
                    return False
                action = {**action, "bounds": matched.get("bounds"),
                          "trigger_criteria": {**criteria, "bounds": matched.get("bounds")}}
            if self._budget_exhausted():
                return False
            return self._finish_tap(self._action_succeeded(self.action_executor(action)), live_state) if action_type == "tap" else self._action_succeeded(self.action_executor(action))

        if action_type == "back":
            return self.execute_back()
        crit = action.get("trigger_criteria") if isinstance(action.get("trigger_criteria"), dict) else {}
        bounds = action.get("bounds") or crit.get("bounds")
        scroll_page = int(action.get("scroll_page") or crit.get("scroll_page") or 0)

        if action_type == "tap":
            label = str(action.get("text") or crit.get("text") or "")
            resource_id = str(action.get("resource_id") or crit.get("resource_id") or "")
            if self._is_destructive(label) or self._is_destructive(resource_id):
                return False
            if (scroll_page > 0 or self._current_scroll_page > 0) and self.driver is not None and hasattr(self.driver, "input"):
                try:
                    w, h = self._cached_screen_size
                    if w <= 0 or h <= 0:
                        w, h = self.driver.screen.get_screen_size()
                        self._cached_screen_size = (w, h)
                    if w <= 0 or h <= 0:
                        return False
                    if w > 0 and h > 0:
                        if scroll_page > self._current_scroll_page:
                            for _ in range(scroll_page - self._current_scroll_page):
                                if live_state is not None and not self._same_scroll_page(live_state, self.get_current_state(enrich_system=True)):
                                    self._current_scroll_page = 0
                                    return False
                                if self._budget_exhausted() or not self._action_succeeded(self.driver.input.swipe(w // 2, int(h * 0.76), w // 2, int(h * 0.26), duration_ms=420)):
                                    return False
                                self._current_scroll_page += 1
                                time.sleep(0.55)
                        elif scroll_page < self._current_scroll_page:
                            for _ in range(self._current_scroll_page - scroll_page):
                                if live_state is not None and not self._same_scroll_page(live_state, self.get_current_state(enrich_system=True)):
                                    self._current_scroll_page = 0
                                    return False
                                if self._budget_exhausted() or not self._action_succeeded(self.driver.input.swipe(w // 2, int(h * 0.26), w // 2, int(h * 0.80), duration_ms=380)):
                                    return False
                                self._current_scroll_page -= 1
                                time.sleep(0.45)

                        if scroll_page > 0:
                            # 在当前滚动视口上动态重定位目标元素坐标（若页面返回后滚动位置被重置，自动补滑一次）
                            for attempt in range(2):
                                live_st = self.get_current_state(enrich_system=True)
                                if live_state is not None and not self._same_scroll_page(live_state, live_st):
                                    self._current_scroll_page = 0
                                    return False
                                found_bounds = None
                                locator = {**crit, **{k: action[k] for k in
                                           ("text", "label", "resource_id", "identifier", "content_desc", "name") if k in action}}
                                matched = UINavigator._find_element_for_criteria(locator, live_st)
                                if matched is not None and matched.get("enabled") is not False:
                                    found_bounds = matched.get("bounds")
                                if found_bounds is not None:
                                    bounds = found_bounds
                                    break
                                if attempt == 0:
                                    if live_state is not None and not self._same_scroll_page(live_state, self.get_current_state(enrich_system=True)):
                                        self._current_scroll_page = 0
                                        return False
                                    if self._budget_exhausted() or not self._action_succeeded(self.driver.input.swipe(w // 2, int(h * 0.76), w // 2, int(h * 0.26), duration_ms=420)):
                                        return False
                                    self._current_scroll_page += 1
                                    time.sleep(0.55)
                            if found_bounds is None:
                                return False
                except Exception as exc:
                    _log(f"[AppExplorer] pre-tap scroll_page={scroll_page} failed: {exc}")
                    return False

            if live_state is not None:
                fresh = self.get_current_state(enrich_system=True)
                if not self._same_scroll_page(live_state, fresh):
                    self._current_scroll_page = 0
                    return False
                if require_live_locator or scroll_page > 0:
                    locator = {**crit, **{k: action[k] for k in
                               ("text", "label", "resource_id", "identifier", "content_desc", "name") if k in action}}
                    matched = UINavigator._find_element_for_criteria(locator, fresh)
                    if matched is None or matched.get("enabled") is False:
                        return False
                    bounds = matched.get("bounds")
                    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                        return False
            x = None if require_live_locator else action.get("x")
            y = action.get("y")
            if x is None or y is None or scroll_page > 0:
                if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                    x = (bounds[0] + bounds[2]) // 2
                    y = (bounds[1] + bounds[3]) // 2
            if x is not None and y is not None and self.driver is not None and hasattr(self.driver, "input"):
                try:
                    if self._budget_exhausted():
                        return False
                    return self._finish_tap(self._action_succeeded(self.driver.input.tap(int(x), int(y))), fresh if live_state is not None else None)
                except Exception as exc:
                    _log(f"[AppExplorer] tap ({x}, {y}) failed: {exc}")
                    return False

        return False

    def execute_back(self) -> bool:
        """执行返回上一级页面动作。"""
        if self._budget_exhausted():
            return False
        if self.back_executor is not None:
            return self._action_succeeded(self.back_executor())

        if self.driver is not None and hasattr(self.driver, "go_back"):
            try:
                return self._action_succeeded(self.driver.go_back())
            except Exception as exc:
                _log(f"[AppExplorer] go_back failed: {exc}")
                return False
        return False

    def _try_ui_close_or_back(self, state: dict[str, Any]) -> bool:
        """当系统物理 Back 键被页面拦截（如全屏登录墙、定制弹窗或侧边栏）时，自动点击左上角返回/关闭控件或显式关闭按钮。"""
        if not isinstance(state, dict):
            return False
        if not self._is_within_target_package(state, self.target_package):
            return False
        els = state.get("elements") or []
        screen_info = state.get("screen") or {}
        sw = int(screen_info.get("width") or 0) if isinstance(screen_info, dict) else 0
        sh = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0

        close_texts = ("关闭", "[关闭]", "[关闭，按钮]", "返回", "[返回]", "[返回，按钮]", "转到上一层级", "取消", "以后再说", "暂不")
        close_ids = ("close", "iv_close", "btn_close", "back", "iv_back", "btn_back", "cancel", "dismiss", "toolbar_close")
        for el in els:
            if not isinstance(el, dict):
                continue
            t = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
            rid = str(el.get("resource_id") or el.get("identifier") or "").lower().split("/")[-1]
            b = el.get("bounds")
            if self._is_destructive(t) or self._is_destructive(rid) or el.get("enabled") is False:
                continue
            if not isinstance(b, (list, tuple)) or len(b) != 4:
                continue
            if (
                t in close_texts
                or any(t.startswith(ct) for ct in ("关闭", "[关闭", "返回", "[返回", "转到上一层级"))
                or rid in close_ids
            ):
                return self.execute_action({"action_type": "tap", "text": t or rid, "bounds": b})

        # 通用几何兜底：仅在非主导航页上，寻找屏幕左上角导航栏区域的无文本小型可点击返回/关闭图标（如无标签的 '×' 或 '<' 视图，严禁误点 '[侧边栏]' 等具名入口）
        ntype, _ = self.graph._infer_semantic_metadata(state)
        if sw > 0 and sh > 0 and ntype != "sub_tab":
            for el in els:
                if not isinstance(el, dict) or el.get("clickable") is False:
                    continue
                t = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
                rid = str(el.get("resource_id") or el.get("identifier") or "")
                if t or self._is_destructive(rid) or el.get("enabled") is False:
                    continue
                b = el.get("bounds")
                if not isinstance(b, (list, tuple)) or len(b) != 4:
                    continue
                try:
                    x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                    bw, bh = x2 - x1, y2 - y1
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    if 0 < cx <= sw * 0.16 and sh * 0.03 <= cy <= sh * 0.14 and 0 < bw <= sw * 0.18 and 0 < bh <= sh * 0.08:
                        return self.execute_action({"action_type": "tap", "text": "top_left_close", "bounds": b})
                except Exception:
                    pass
        return False

    def _is_destructive(self, text: str) -> bool:
        """检查文本是否命中破坏性动作黑名单。"""
        clean = text.strip().lower()
        if not clean:
            return False
        for bl in self.blacklist:
            if bl.lower() in clean:
                return True
        return False

    def filter_interactive_candidates(
        self,
        state: dict[str, Any],
        max_per_page: int = 100,
        *,
        depth: int = 0,
        allow_bottom_tabs: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        """从页面状态中基于通用几何与结构特征筛选候选交互元素（含容器过滤、父子空间去重、结构等价簇归并、多列宫格配额与导航优先排序）。"""
        import re

        raw_elements = state.get("elements") or []
        screen_info = state.get("screen") or {}
        screen_w = int(screen_info.get("width") or 0) if isinstance(screen_info, dict) else 0
        screen_h = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0
        screen_area = screen_w * screen_h

        container_id_patterns = (
            "scroll_container",
            "coordinator",
            "recyclerview",
            "recycler_view",
            "recycle",
            "pager",
            "viewpager",
            "tabs",
            "tab_layout",
            "rl_top",
            "head_layout",
            "action_bar_title",
            "toolbar_title",
            "state_view",
            "statetextview",
            "tv_log",
            "log_scroll",
            "subtitle_click_container",
        )
        readonly_meta_patterns = (
            "watched",
            "liked",
            "text_corner",
            "tvarea",
            "nickname",
            "upper_title",
            "upper_subtitle",
            "count",
            "cover_left_text",
            "support_text",
            "play_time",
            "duration",
            "timestamp",
            "subtitle",
        )
        # 页面内状态开关及系统硬件权限入口（仅记录于节点组件清单中，探索时不主动点击以避免翻转主题/静音状态或拉起系统相机权限窗）
        state_toggle_patterns = (
            "day_night",
            "night_mode",
            "dark_mode",
            "_scan",
            "qr_scan",
            "button_layout",
            "mute",
            "card_like",
            "frame_like",
            "frame_dislike",
            "ivfeedback",
        )
        generic_bottom_tab_names = ("首页", "朋友", "关注", "动态", "消息", "我的", "我", "发现", "商城", "会员购", "Home", "Me", "Profile")

        raw_candidates: list[dict[str, Any]] = []
        seen_identifiers: set[str] = set()
        sampled_feed_res_ids: dict[str, int] = {}
        sampled_top_tabs: int = 0
        small_grid_count: int = 0
        cluster_representatives: dict[str, dict[str, Any]] = {}

        top_header_texts: set[str] = set()
        side_drawer_mask_x1: int = 0
        for e in raw_elements:
            if not isinstance(e, dict):
                continue
            t = str(e.get("text") or e.get("label") or e.get("content_desc") or "").strip()
            b = e.get("bounds")
            if isinstance(b, (list, tuple)) and len(b) == 4 and screen_h > 0:
                try:
                    bx1, by1, _bx2, by2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                    if t and (by1 + by2) // 2 <= screen_h * 0.28:
                        top_header_texts.add(t)
                    if t in ("[关闭，按钮]", "关闭", "[关闭]") and screen_w > 0 and bx1 >= screen_w * 0.65 and (by2 - by1) >= screen_h * 0.70:
                        side_drawer_mask_x1 = bx1
                except Exception:
                    pass
        is_ecommerce_feed = "购物车" in top_header_texts and (
            "我的订单" in top_header_texts or "订单" in top_header_texts
        )
        if allow_bottom_tabs is None:
            allow_bottom_tabs = (depth == 0)

        def _match_structural_cluster(short_rid: str, cy_val: int, bw_val: int, is_tab_el: bool) -> Optional[str]:
            if not short_rid or is_tab_el:
                return None
            # 1. 顶部个人名片头部簇 (User Profile Header Card): 头像、昵称、会员标、等级、空间跳转入口
            if screen_h > 0 and cy_val <= screen_h * 0.42:
                if any(
                    k in short_rid
                    for k in (
                        "nick",
                        "avatar",
                        "go_to_space",
                        "user_space",
                        "rank",
                        "vip_label",
                        "following_dec",
                        "following_layout",
                        "edit_info",
                    )
                ):
                    return "profile_header"
                # 2. 顶部社交关注/粉丝计数簇 (Social Counter Strip)
                if any(
                    k in short_rid
                    for k in ("attention_dec", "attention_layout", "fans_dec", "fans_layout", "follower", "friend_count")
                ):
                    return "social_counters"
                # 3. 横向头像快拍/常看作者栏簇 (Horizontal Story / Creator Strip)
                if (
                    screen_w > 0
                    and bw_val <= screen_w * 0.28
                    and any(k in short_rid for k in ("user_name", "_name", "badge_title"))
                    and short_rid != "card_user_name"
                ):
                    return "horizontal_creator_strip"
            # 4. 中部动态/视频流卡片内部子组件簇 (Feed Card Sub-elements)
            if screen_h > 0 and (screen_h * 0.20 < cy_val < screen_h * 0.88):
                if any(k in short_rid for k in ("card_user_name", "author_layout")) or (
                    short_rid.endswith("avatar") and cy_val > screen_h * 0.35
                ):
                    return "feed_card_author"
                if any(
                    k in short_rid
                    for k in ("card_living", "tv_region", "text_time", "card_comment", "live_title", "video_title")
                ):
                    return "feed_card_detail"
                if any(k in short_rid for k in ("card_repost", "forward")):
                    return "feed_card_repost"
            return None

        for idx, el in enumerate(raw_elements):
            if not isinstance(el, dict):
                continue

            el_type = str(el.get("type") or "").strip().lower()
            if el_type in ("scroll_container", "list", "pager"):
                continue

            text = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
            res_id = str(el.get("resource_id") or el.get("identifier") or "").strip()
            clickable = el.get("clickable")
            bounds = el.get("bounds")
            scroll_page = int(el.get("scroll_page") or 0)

            if not text and not res_id:
                continue

            if self._is_destructive(text) or self._is_destructive(res_id):
                continue

            # 排除超长文本（通常是正文/文章而不是按钮）
            if len(text) > 20:
                continue

            res_id_lower = res_id.lower()
            short_res_id = res_id_lower.split("/")[-1]
            if any(cid in res_id_lower for cid in container_id_patterns):
                continue
            if any(stid in res_id_lower for stid in state_toggle_patterns):
                continue
            if (
                text in ("扫一扫", "[扫一扫]", "夜间模式", "日间模式", "深色模式", "[播放视频，按钮]", "[暂停视频，按钮]", "[进度条]")
                or any(k in text for k in ("播放视频", "暂停视频", "进度条", "未点赞", "已点赞"))
                or text.startswith("[未选中，收藏")
                or text.startswith("[已选中，收藏")
                or text.startswith("[音乐，")
            ):
                continue

            cx, cy, bw, bh = -1, -1, 0, 0
            if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                try:
                    x1, y1, x2, y2 = int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])
                    bw = max(0, x2 - x1)
                    bh = max(0, y2 - y1)
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                except Exception:
                    pass

            # 若当前处于左侧抽屉（Side Drawer）展开态，过滤右侧半透明关闭蒙层及其下方被遮挡的背景组件
            if side_drawer_mask_x1 > 0 and cx >= side_drawer_mask_x1:
                continue

            # 过滤全屏背景画布/播放器蒙层（面积占比超过屏幕 45% 的元素均属于背景容器而非导航控件）
            if screen_area > 0 and (bw * bh) > screen_area * 0.45:
                continue

            # 过滤纯展示型非可点击计数/版本号/角标/分栏静态小标题
            if clickable is False:
                if re.match(r"^[\d\.\+\-\:\s/万亿kKwW%vV¥￥$]+$", text):
                    continue
                if any(fmid in res_id_lower for fmid in readonly_meta_patterns):
                    continue
                if short_res_id == "button_text":
                    continue
                if short_res_id in ("title", "header", "section_title") and screen_w > 0 and bw < screen_w * 0.35:
                    continue
                if text in ("最常访问", "最近常看", "为你推荐", "热门推荐"):
                    continue

            # 电商商品瀑布流页面仅探索顶部功能区（搜索/购物车/订单），避免陷入海量商品卡片
            if is_ecommerce_feed and screen_h > 0 and cy > screen_h * 0.25:
                continue

            # 无文本的纯容器若面积过大、宽度占满横栏或属于无语义通用 View 容器，跳过（避免横栏/卡片外层容器吞并内部独立按钮）
            if not text:
                if clickable is False:
                    continue
                if screen_area > 0 and (bw * bh) > screen_area * 0.15:
                    continue
                if screen_w > 0 and bw >= screen_w * 0.30:
                    continue
                if el_type == "view" and not any(
                    k in short_res_id
                    for k in ("close", "back", "cancel", "search", "more", "menu", "avatar", "icon", "btn", "button", "tab")
                ):
                    continue

            # 通用几何识别导航 Tab（顶栏/底栏标签）
            clean_b_txt = self.graph._clean_ui_label(text)
            # 过滤信息流/视频流中部的作者关注状态开关按钮（区别于顶栏/底栏的“关注”频道 Tab）
            if clean_b_txt in ("关注", "+关注", "已关注") and screen_h > 0 and (0.25 * screen_h < cy < 0.85 * screen_h):
                continue
            is_selected = bool(el.get("selected") is True or "已选中" in text)
            is_bottom_tab = bool(
                screen_h > 0
                and cy >= screen_h * 0.88
                and (
                    "标签" in text
                    or "tab" in res_id_lower
                    or bool(re.search(r",\d+之\d+", text))
                    or clean_b_txt in generic_bottom_tab_names
                    or any(k in text for k in ("发布", "拍摄", "相机"))
                )
            )
            is_geom_top_tab = bool(
                screen_w > 0
                and screen_h > 0
                and 0 < cy <= screen_h * 0.22
                and 0 < bw <= screen_w * 0.22
                and bh <= screen_h * 0.08
                and clean_b_txt in ("直播", "团购", "同城", "关注", "商城", "推荐", "热门", "发现", "视频", "频道")
            )
            is_tab = bool("标签" in text or "tab" in res_id_lower or is_bottom_tab or is_geom_top_tab)

            # 瀑布流重复卡片采样：在屏幕中部区域且宽度小于 55% 屏宽的同 resource_id 瀑布流元素，最多采样 1 个代表项
            if (
                screen_w > 0
                and screen_h > 0
                and res_id
                and not is_tab
                and short_res_id not in ("title", "name", "text", "label", "icon", "content")
                and not any(k in short_res_id for k in ("menu_", "setting_", "nav_", "tab_", "btn_"))
                and (0.18 * screen_h < cy < 0.88 * screen_h)
                and (0 < bw < screen_w * 0.55)
            ):
                cnt = sampled_feed_res_ids.get(res_id_lower, 0)
                if cnt >= 1:
                    continue
                sampled_feed_res_ids[res_id_lower] = cnt + 1

            ident = f"{text}|{res_id}"
            if ident in seen_identifiers:
                continue
            seen_identifiers.add(ident)

            # 通用结构等价簇归并（Structural Action Equivalence Clustering）：
            matched_cluster = _match_structural_cluster(short_res_id, cy, bw, is_tab)
            if matched_cluster:
                existing_rep = cluster_representatives.get(matched_cluster)
                if existing_rep is not None:
                    eq_label = text or short_res_id
                    eq_list = existing_rep["trigger_criteria"].setdefault("equivalent_triggers", [])
                    if eq_label and eq_label not in eq_list:
                        eq_list.append(eq_label)
                    continue

            # 空间中心点与父子包含去重（仅在同一视口屏 scroll_page 内比较，解决外层容器与内层文本重合连点两次的问题）
            merged_with_existing = False
            if cx >= 0 and cy >= 0 and isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                x1, y1, x2, y2 = int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])
                for prev in raw_candidates:
                    if int(prev.get("scroll_page", 0) or 0) != scroll_page:
                        continue
                    pcx, pcy = prev.get("_cx", -1), prev.get("_cy", -1)
                    pb = prev.get("bounds")
                    center_close = pcx >= 0 and pcy >= 0 and abs(cx - pcx) <= 36 and abs(cy - pcy) <= 45
                    box_contained = False
                    if isinstance(pb, (list, tuple)) and len(pb) == 4:
                        px1, py1, px2, py2 = int(pb[0]), int(pb[1]), int(pb[2]), int(pb[3])
                        max_box_area = max((x2 - x1) * (y2 - y1), (px2 - px1) * (py2 - py1))
                        centers_near = pcx >= 0 and pcy >= 0 and abs(cx - pcx) <= 75 and abs(cy - pcy) <= 60
                        if centers_near and (screen_area == 0 or max_box_area < screen_area * 0.15):
                            curr_in_prev = x1 >= px1 - 6 and y1 >= py1 - 6 and x2 <= px2 + 6 and y2 <= py2 + 6
                            prev_in_curr = px1 >= x1 - 6 and py1 >= y1 - 6 and px2 <= x2 + 6 and py2 <= y2 + 6
                            box_contained = curr_in_prev or prev_in_curr

                    if center_close or box_contained:
                        if is_selected:
                            prev["_selected"] = True
                        if is_tab:
                            prev["is_tab"] = True
                        if is_bottom_tab:
                            prev["is_bottom_tab"] = True
                            prev["_tier"] = 0
                        if res_id and not prev.get("resource_id"):
                            prev["resource_id"] = res_id
                            prev["trigger_criteria"]["resource_id"] = res_id
                        if text and "search_text" not in res_id_lower:
                            if not prev.get("text") or (prev["text"].startswith("[") and not text.startswith("[")) or "search_text" in str(prev.get("resource_id", "")).lower():
                                prev["text"] = text
                                prev["trigger_criteria"]["text"] = text
                                if res_id:
                                    prev["resource_id"] = res_id
                                    prev["trigger_criteria"]["resource_id"] = res_id
                        merged_with_existing = True
                        break
            if merged_with_existing:
                continue

            # 计算通用几何探索优先级 Tier（数值越小越优先）：
            # Tier 0: 底部主导航栏 Tab
            # Tier 1: 顶部功能入口与频道 Tab
            # Tier 2: 单列全宽菜单项 (bw >= 0.55*W)、核心按钮与前 10 个多列宫格项
            # Tier 3: 中部局部卡片
            # Tier 4: 多列宫格 (>10) 长尾溢出项（排在单列全宽菜单项之后）
            is_small_grid_cell = bool(
                screen_w > 0
                and screen_h > 0
                and not is_tab
                and (0 < bw <= screen_w * 0.28)
                and cy > screen_h * 0.35
            )
            if is_small_grid_cell:
                small_grid_count += 1

            tier = 2
            if screen_h > 0 and cy >= 0:
                if is_bottom_tab:
                    tier = 0
                elif cy <= screen_h * 0.18 or is_tab:
                    tier = 1
                elif is_small_grid_cell and small_grid_count > 10:
                    tier = 4
                elif clickable is True or (screen_w > 0 and bw >= screen_w * 0.55) or el_type in ("button", "toggle"):
                    tier = 2
                else:
                    tier = 3

            cand_obj: dict[str, Any] = {
                "action_type": "tap",
                "text": text,
                "resource_id": res_id,
                "bounds": bounds,
                "is_tab": is_tab,
                "is_bottom_tab": is_bottom_tab,
                "scroll_page": scroll_page,
                "_selected": is_selected,
                "_cx": cx,
                "_cy": cy,
                "_tier": tier,
                "_idx": idx,
                "trigger_criteria": {
                    "text": text,
                    "resource_id": res_id,
                    "bounds": bounds,
                    "scroll_page": scroll_page,
                },
            }
            if matched_cluster:
                cand_obj["trigger_criteria"]["equivalence_cluster"] = matched_cluster
                cluster_representatives[matched_cluster] = cand_obj
            raw_candidates.append(cand_obj)

        # 父子包含合并完成后，统一过滤底栏/顶栏已选中 Tab、底栏拍摄加号与底栏无文本空容器
        post_candidates: list[dict[str, Any]] = []
        for item in raw_candidates:
            c_txt = str(item.get("text") or "").strip()
            c_rid = str(item.get("resource_id") or "").lower()
            c_cy = int(item.get("_cy", -1))
            c_sel = bool(item.get("_selected") is True)
            c_clean = self.graph._clean_ui_label(c_txt)
            is_b_tab = bool(item.get("is_bottom_tab"))
            is_t_tab = bool(item.get("is_tab"))

            if screen_h > 0 and c_cy >= screen_h * 0.89 and (not is_b_tab or not c_txt):
                continue
            if is_b_tab:
                if not allow_bottom_tabs:
                    continue
                if c_sel or c_clean in ("首页", "Home") or ",1)" in c_txt or "之1," in c_txt:
                    continue
                if any(k in c_txt for k in ("发布", "拍摄", "相机")) or any(k in c_rid for k in ("publish", "shoot", "camera")):
                    continue
            is_top_ch = bool(is_t_tab and not is_b_tab and screen_h > 0 and 0 < c_cy <= screen_h * 0.24)
            if is_top_ch:
                if c_sel or c_clean in ("推荐", "首页", "全部"):
                    continue
                max_top_tabs = 3 if depth == 0 else 1
                if sampled_top_tabs >= max_top_tabs:
                    continue
                sampled_top_tabs += 1
            post_candidates.append(item)

        raw_candidates = post_candidates

        if screen_h > 0:
            raw_candidates.sort(
                key=lambda item: (
                    1 if item.get("_tier", 2) >= 4 else 0,
                    int(item.get("scroll_page", 0) or 0),
                    item.get("_tier", 2),
                    item.get("_idx", 0),
                )
            )

        candidates: list[dict[str, Any]] = []
        for item in raw_candidates[:max_per_page]:
            clean_item = {k: v for k, v in item.items() if not k.startswith("_")}
            candidates.append(clean_item)

        return candidates

    def explore(
        self,
        max_depth: int = 3,
        max_pages: int = 15,
        max_branches_per_page: int = 100,
        step_delay_s: float = 0.8,
        timeout_s: float = 120.0,
        *,
        target_package: Optional[str] = None,
        lock_package: Optional[bool] = None,
        mermaid_mode: str = "detailed",
        enable_scroll_stitch: bool = True,
        max_scroll_pages: int = 2,
        inspect_package_routes: bool = True,
        allow_bottom_tabs: Optional[bool] = None,
    ) -> dict[str, Any]:
        """对当前应用开展启发式 BFS 自动漫游并构建知识图谱与脑图。"""
        start_time = time.time()
        self.history.clear()
        self._deadline = None
        self._current_scroll_page = 0
        self._scroll_identity = None
        self._scroll_offsets.clear()
        self._viewport_offsets.clear()
        if max_pages <= 0 or max_depth <= 0 or timeout_s <= 0 or max_branches_per_page < 0:
            return {"ok": False, "error": "invalid_exploration_budget"}
        self._deadline = time.monotonic() + timeout_s

        if lock_package is not None:
            self.lock_package = lock_package
        if target_package:
            self.target_package = target_package.strip()

        init_state = self.get_current_state()
        if not init_state or not init_state.get("ok", True):
            return {
                "ok": False,
                "error": "failed_to_get_initial_page_state",
                "nodes_count": len(self.graph.nodes),
                "edges_count": len(self.graph.edges),
            }

        locked_pkg: Optional[str] = self.target_package
        if self.lock_package and not locked_pkg:
            inferred_pkg = self._extract_package(init_state)
            if inferred_pkg:
                locked_pkg = inferred_pkg
                self.target_package = inferred_pkg
        if self.lock_package and not locked_pkg and self.driver is not None:
            return {"ok": False, "error": "target_package_unknown"}

        if not self._is_within_target_package(init_state, locked_pkg):
            init_state = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
            if not init_state.get("ok", True) or not self._is_within_target_package(init_state, locked_pkg):
                return {"ok": False, "error": "target_package_unavailable"}

        if inspect_package_routes and locked_pkg:
            self.extract_package_routes(locked_pkg)

        if enable_scroll_stitch and max_scroll_pages > 1 and not self._is_login_wall_state(init_state):
            init_state = self.stitch_scrollable_state(
                init_state, max_scroll_pages=max_scroll_pages, step_delay_s=step_delay_s
            )

        root_node = self.graph._resolve_or_infer_node(init_state)
        visited_nodes: set[str] = {root_node.node_id}

        nav_action_executor = None
        if self.driver is not None or self.action_executor is not None:
            def _nav_exec(edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
                if not self._is_within_target_package(state, locked_pkg):
                    return {"ok": False, "error": "outside_target_package"}
                fresh_state = self.get_current_state(enrich_system=True)
                if not self._is_within_target_package(fresh_state, locked_pkg):
                    return {"ok": False, "error": "outside_target_package"}
                crit = edge.trigger_criteria or {}
                act_dict = {
                    "action_type": edge.action_type,
                    "trigger_criteria": crit,
                    **crit,
                }
                if edge.action_type == "back":
                    ok = self.execute_back()
                else:
                    ok = self.execute_action(act_dict, live_state=fresh_state, require_live_locator=True)
                return {"ok": ok}
            nav_action_executor = _nav_exec

        last_known_state: list[Optional[dict[str, Any]]] = [init_state]

        def _replay_edges(edges_to_play: list[UIEdge]) -> bool:
            for edge in edges_to_play:
                if not self._is_within_target_package(self.get_current_state(), locked_pkg):
                    return False
                crit = edge.trigger_criteria or {}
                if not self.execute_action({
                    "action_type": edge.action_type,
                    "trigger_criteria": crit,
                    **crit,
                }, require_live_locator=True):
                    return False
                if step_delay_s > 0:
                    time.sleep(step_delay_s)
            st = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
            last_known_state[0] = st
            m = self.graph.match_node(st)
            return bool(m is not None and m.node_id == target_id_Holder[0])

        target_id_Holder = [""]

        def _tap_element_by_keywords(st: dict[str, Any], keywords: tuple[str, ...], *, bottom_only: bool = False) -> bool:
            screen_info = st.get("screen") or {}
            sh = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0
            els = st.get("elements") or []
            for kw in keywords:
                for el in els:
                    if not isinstance(el, dict):
                        continue
                    t = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
                    b = el.get("bounds")
                    if not t or not isinstance(b, (list, tuple)) or len(b) != 4:
                        continue
                    clean_t = self.graph._clean_ui_label(t)
                    rid = str(el.get("resource_id") or el.get("identifier") or "")
                    if clean_t != kw or self._is_destructive(t) or self._is_destructive(rid) or el.get("enabled") is False:
                        continue
                    if sh > 0:
                        cy = (int(b[1]) + int(b[3])) // 2
                        if bottom_only and cy < sh * 0.85:
                            continue
                        if not bottom_only and (sh * 0.26 < cy < sh * 0.85):
                            continue
                    if self.execute_action({
                        "action_type": "tap",
                        "text": t,
                        "resource_id": rid,
                        "bounds": b,
                        "trigger_criteria": {"text": t, "resource_id": rid, "bounds": b},
                    }):
                        if step_delay_s > 0:
                            time.sleep(step_delay_s)
                        return True
            return False

        def _infer_root_tab_keywords(st: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
            import re

            els = st.get("elements") or []
            screen_info = st.get("screen") or {}
            sh = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0
            bottom_tabs: list[tuple[int, str, bool]] = []
            top_tabs: list[tuple[int, str, bool]] = []
            for el in els:
                if not isinstance(el, dict):
                    continue
                t = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
                rid = str(el.get("resource_id") or "").lower()
                b = el.get("bounds")
                if not t or not isinstance(b, (list, tuple)) or len(b) != 4:
                    continue
                cx = (int(b[0]) + int(b[2])) // 2
                cy = (int(b[1]) + int(b[3])) // 2
                is_sel = bool(el.get("selected") is True or "已选中" in t)
                clean_t = self.graph._clean_ui_label(t)
                if sh > 0 and cy >= sh * 0.88 and (
                    "标签" in t
                    or "tab" in rid
                    or re.search(r",\d+之\d+", t)
                    or clean_t in ("首页", "朋友", "关注", "动态", "消息", "我的", "我", "发现", "商城", "会员购", "Home", "Me", "Profile")
                ):
                    if clean_t:
                        bottom_tabs.append((cx, clean_t, is_sel))
                elif sh > 0 and 0 < cy <= sh * 0.24 and (
                    "标签" in t
                    or "tab" in rid
                    or clean_t in ("直播", "团购", "同城", "关注", "商城", "推荐", "热门", "发现", "视频", "频道")
                ):
                    if clean_t:
                        top_tabs.append((cx, clean_t, is_sel))

            bottom_kw: tuple[str, ...] = ("首页", "Home")
            _, init_sem = self.graph._infer_semantic_metadata(st)
            if init_sem == "个人中心" and bottom_tabs:
                bottom_tabs.sort(key=lambda x: x[0])
                bottom_kw = (bottom_tabs[-1][1], "我的", "Profile", "Me")
            elif bottom_tabs:
                sel_b = [x[1] for x in bottom_tabs if x[2] and x[1]]
                if sel_b:
                    bottom_kw = (sel_b[0], "首页", "Home")
                else:
                    bottom_tabs.sort(key=lambda x: x[0])
                    bottom_kw = (bottom_tabs[0][1], "首页", "Home")

            top_kw: tuple[str, ...] = ("推荐", "全部", "综合") + bottom_kw
            if top_tabs:
                sel_t = [x[1] for x in top_tabs if x[2] and x[1]]
                if sel_t:
                    top_kw = (sel_t[0], "推荐") + bottom_kw
            return bottom_kw, top_kw

        root_bottom_tab_keywords, root_top_tab_keywords = _infer_root_tab_keywords(init_state)

        def _recover_to_node(target_id: str, path: list[UIEdge]) -> bool:
            target_id_Holder[0] = target_id
            cached_st = last_known_state[0]
            if cached_st is not None and self._is_within_target_package(cached_st, locked_pkg):
                cached_node = self.graph.match_node(cached_st)
                if cached_node is not None and cached_node.node_id == target_id:
                    return True

            curr_st = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
            last_known_state[0] = curr_st
            if not self._is_within_target_package(curr_st, locked_pkg):
                return False
            c_node = self.graph.match_node(curr_st)
            if c_node is not None and c_node.node_id == target_id:
                return True

            # 若当前已在根节点且有通往 target_id 的路径，直接重放路径（不按 Back 避免退出主页）
            if c_node is not None and c_node.node_id == root_node.node_id and path:
                if _replay_edges(path):
                    return True
                curr_st = self.get_current_state()
                last_known_state[0] = curr_st

            # 尝试使用图导航器直接跳转（适用于同 Activity 底栏 Tab 互切）
            nav = UINavigator(
                self.graph,
                driver=self.driver,
                state_provider=self.state_provider,
                action_executor=nav_action_executor,
                action_guard=lambda st: not self._budget_exhausted() and self._is_within_target_package(st, locked_pkg),
            )
            nav_res = nav.navigate_to(target_id, current_state=curr_st, max_hops=4)
            if nav_res.get("ok"):
                last_known_state[0] = self.get_current_state()
                return True

            # 若在同一主 Activity 下且有 path（如从其他 Tab 切往“关注”或“我的”），优先尝试直接重放 path
            root_act = self.graph._extract_activity_str(init_state)
            curr_act = self.graph._extract_activity_str(curr_st)
            if path and root_act and curr_act == root_act:
                if _replay_edges(path):
                    return True
                curr_st = self.get_current_state()
                last_known_state[0] = curr_st
            elif target_id == root_node.node_id and root_act and curr_act == root_act:
                if _tap_element_by_keywords(curr_st, root_bottom_tab_keywords, bottom_only=True):
                    curr_st = self.get_current_state()
                    last_known_state[0] = curr_st
                    c_node = self.graph.match_node(curr_st)
                    if c_node is not None and c_node.node_id == target_id:
                        return True

            # 尝试最多 2 次 Back 或左上角 UI 关闭按钮退出子 Activity、强制登录墙、半屏弹窗或软键盘
            for attempt_idx in range(2):
                self.execute_back()
                if step_delay_s > 0:
                    time.sleep(step_delay_s)
                curr_st = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                last_known_state[0] = curr_st
                c_node = self.graph.match_node(curr_st)
                if c_node is not None and c_node.node_id == target_id:
                    return True
                if c_node is not None and c_node.node_id == root_node.node_id and path:
                    if _replay_edges(path):
                        return True
                if attempt_idx == 1 and self._try_ui_close_or_back(curr_st):
                    if step_delay_s > 0:
                        time.sleep(step_delay_s)
                    curr_st = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                    last_known_state[0] = curr_st
                    c_node = self.graph.match_node(curr_st)
                    if c_node is not None and c_node.node_id == target_id:
                        return True
                    if c_node is not None and c_node.node_id == root_node.node_id and path:
                        if _replay_edges(path):
                            return True

            # 灰盒 ADB Teleport：若目标节点是独立非主页 Activity，尝试通过 am start -W -n 一步瞬移恢复
            target_node_obj = self.graph.get_node(target_id)
            if (
                locked_pkg
                and target_node_obj is not None
                and target_node_obj.activity_or_vc
                and "MainActivity" not in target_node_obj.activity_or_vc
            ):
                if self.teleport_to_activity(locked_pkg, target_node_obj.activity_or_vc, step_delay_s=step_delay_s):
                    curr_st = self.get_current_state()
                    last_known_state[0] = curr_st
                    c_node = self.graph.match_node(curr_st)
                    if c_node is not None and c_node.node_id == target_id:
                        return True

            # 兜底：强制重启目标应用回到根首页再重放 path
            if locked_pkg and self._force_restart_target_package(locked_pkg, step_delay_s=step_delay_s):
                curr_st = self.get_current_state()
                last_known_state[0] = curr_st
                c_node = self.graph.match_node(curr_st)
                if c_node is not None and c_node.node_id == target_id:
                    return True
                if target_id == root_node.node_id:
                    _tap_element_by_keywords(curr_st, root_bottom_tab_keywords, bottom_only=True)
                    curr_st = self.get_current_state()
                    last_known_state[0] = curr_st
                    c_node = self.graph.match_node(curr_st)
                    if c_node is not None and c_node.node_id == target_id:
                        return True
                if path and _replay_edges(path):
                    return True
            return False

        # 队列元素: (node_id, depth, path_from_root)
        queue: deque[tuple[str, int, list[UIEdge]]] = deque([(root_node.node_id, 0, [])])
        explored_transitions: set[tuple[str, str, str]] = set()
        stitched_node_states: dict[str, dict[str, Any]] = {root_node.node_id: init_state}

        while queue and not self._budget_exhausted() and len(self.graph.nodes) < max_pages:
            current_node_id, depth, path_edges = queue.popleft()

            if depth >= max_depth:
                continue
            current_state = stitched_node_states.get(current_node_id)
            if current_state is not None and self._is_login_wall_state(current_state):
                continue

            # 若当前端侧页面不是 current_node_id，尝试恢复
            if not _recover_to_node(current_node_id, path_edges):
                continue

            if current_node_id in stitched_node_states:
                curr_state = stitched_node_states[current_node_id]
            else:
                curr_state = last_known_state[0] or self.get_current_state()
                if enable_scroll_stitch and max_scroll_pages > 1:
                    curr_state = self.stitch_scrollable_state(
                        curr_state, max_scroll_pages=max_scroll_pages, step_delay_s=step_delay_s
                    )
                    self.graph._resolve_or_infer_node(curr_state)
                stitched_node_states[current_node_id] = curr_state

            if self._is_login_wall_state(curr_state):
                continue
            candidates = self.filter_interactive_candidates(
                curr_state,
                max_per_page=max_branches_per_page,
                depth=depth,
                allow_bottom_tabs=(
                    allow_bottom_tabs
                    if allow_bottom_tabs is not None
                    else (current_node_id == root_node.node_id)
                ),
            )

            for cand in candidates:
                if len(self.graph.nodes) >= max_pages or self._budget_exhausted():
                    break

                cand_key = (current_node_id, str(cand.get("text")), str(cand.get("resource_id")))
                if cand_key in explored_transitions:
                    continue
                explored_transitions.add(cand_key)

                # 执行动作
                act_ok = self.execute_action(cand)
                if not act_ok:
                    continue

                if step_delay_s > 0:
                    time.sleep(step_delay_s)

                next_state = self.get_current_state()
                # 过渡骨架态重试：若点击后页面正在加载（有效元素极少），额外等待一次避免生成空壳节点
                if step_delay_s > 0 and len(next_state.get("elements") or []) <= 2:
                    time.sleep(max(0.8, step_delay_s))
                    next_state = self.get_current_state()
                last_known_state[0] = next_state

                # 目标包名锁检查：若跳出了目标应用（如跳到系统桌面/设置），立即拉回并跳过外部节点入图
                if not self._is_within_target_package(next_state, locked_pkg):
                    last_known_state[0] = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                    if not _recover_to_node(current_node_id, path_edges):
                        break
                    continue

                next_node = self.graph._resolve_or_infer_node(next_state)
                if next_node.node_id != current_node_id and next_node.node_type in ("modal", "sub_tab") and not next_node.host_node_id:
                    next_node.host_node_id = current_node_id

                is_new = next_node.node_id not in self.graph.nodes or next_node.node_id not in visited_nodes
                # 记录转移边（自动触发入边语义传播）
                src, edge, dst = self.graph.record_transition(
                    curr_state, cand, next_state, from_node_id=current_node_id, to_node_id=next_node.node_id, success=True
                )

                self.history.append(
                    ExploreStep(
                        from_node_id=current_node_id,
                        action=cand,
                        to_node_id=next_node.node_id,
                        is_new_page=is_new,
                    )
                )

                # 若跳转到了不同页面，将新页面入队并尝试返回（模态浮层 Modal 默认不递归展开子队列，避免遮罩污染主图）
                if next_node.node_id != current_node_id:
                    curr_act = self.graph._extract_activity_str(curr_state)
                    next_act = self.graph._extract_activity_str(next_state)
                    same_act_tab_switch = bool(cand.get("is_tab") and curr_act and curr_act == next_act)
                    # 平行主导航底栏 Tab 切换属于同层状态迁移 (delta_depth = 0)，纵向页面跳转深度 +1
                    is_peer_bottom_tab = bool(cand.get("is_bottom_tab") and same_act_tab_switch)
                    next_depth = depth if is_peer_bottom_tab else (depth + 1)

                    is_login_wall = self._is_login_wall_state(next_state)
                    is_modal = next_node.node_type == "modal"
                    if next_node.node_id not in visited_nodes and next_depth < max_depth and not is_login_wall and not is_modal:
                        visited_nodes.add(next_node.node_id)
                        queue.append((next_node.node_id, next_depth, path_edges + [edge]))
                    elif is_login_wall or is_modal:
                        visited_nodes.add(next_node.node_id)

                    if not same_act_tab_switch:
                        # 跨 Activity 压栈跳转或模态弹窗：通过系统 Back 返回父页面（若首次 Back 仅收起软键盘，自动补按第二次 Back）
                        back_ok = self.execute_back()
                        used_ui_close = False
                        if step_delay_s > 0:
                            time.sleep(step_delay_s)

                        back_state = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                        last_known_state[0] = back_state
                        back_node = self.graph.match_node(back_state)
                        if back_node is not None and back_node.node_id == next_node.node_id:
                            back_ok = self.execute_back()
                            if step_delay_s > 0:
                                time.sleep(step_delay_s)
                            back_state = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                            last_known_state[0] = back_state
                            back_node = self.graph.match_node(back_state)
                        if back_node is not None and back_node.node_id == next_node.node_id:
                            if self._try_ui_close_or_back(back_state):
                                used_ui_close = True
                                if step_delay_s > 0:
                                    time.sleep(step_delay_s)
                                back_state = self._ensure_in_target_package(locked_pkg, step_delay_s=step_delay_s)
                                last_known_state[0] = back_state
                                back_node = self.graph.match_node(back_state)

                        # 记录逆向回退边（仅当真正退回到了不同于 next_node 的节点时记录）
                        if back_ok and not used_ui_close and back_node is not None and back_node.node_id != next_node.node_id:
                            self.graph.record_transition(
                                next_state,
                                {"action_type": "back", "trigger_criteria": {"key": "BACK"}},
                                back_state,
                                from_node_id=next_node.node_id,
                                to_node_id=back_node.node_id,
                                success=True,
                            )
                        if back_node is None or back_node.node_id != current_node_id:
                            if not _recover_to_node(current_node_id, path_edges):
                                break
                    else:
                        # 同 Activity 下的 Tab 切换：不按系统 Back（避免直接退出应用），直接切回原 Tab
                        if current_node_id == root_node.node_id:
                            if cand.get("is_bottom_tab"):
                                _tap_element_by_keywords(next_state, root_bottom_tab_keywords, bottom_only=True)
                            else:
                                _tap_element_by_keywords(next_state, root_top_tab_keywords, bottom_only=False)
                        elif path_edges:
                            _replay_edges([path_edges[-1]])
                        curr_state_now = self.get_current_state()
                        last_known_state[0] = curr_state_now
                        m_node = self.graph.match_node(curr_state_now)
                        if m_node is None or m_node.node_id != current_node_id:
                            if not _recover_to_node(current_node_id, path_edges):
                                break

        elapsed = round(time.time() - start_time, 2)
        id_map = self.graph.consolidate_equivalent_nodes()
        canonical_root_id = id_map.get(root_node.node_id, root_node.node_id)
        mermaid_compact = self.graph.to_mermaid(root_node_id=canonical_root_id, mode="compact")
        mermaid_detailed = self.graph.to_mermaid(root_node_id=canonical_root_id, mode="detailed")
        mermaid_code = (
            mermaid_detailed
            if str(mermaid_mode).strip().lower() in ("detailed", "detail", "full", "verbose")
            else mermaid_compact
        )
        mindmap_data = self.graph.to_mindmap_dict(root_node_id=canonical_root_id)
        self._deadline = None

        return {
            "ok": True,
            "root_node_id": canonical_root_id,
            "target_package": locked_pkg or "",
            "lock_package": self.lock_package,
            "nodes_count": len(self.graph.nodes),
            "edges_count": len(self.graph.edges),
            "explored_steps": len(self.history),
            "elapsed_seconds": elapsed,
            "package_routes": self.graph.metadata.get("package_routes", {}),
            "mermaid": mermaid_code,
            "mermaid_compact": mermaid_compact,
            "mermaid_detailed": mermaid_detailed,
            "mindmap": mindmap_data,
            "graph": self.graph.export_graph_json(),
        }
