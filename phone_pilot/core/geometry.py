"""平台无关的纯几何计算：视口可见性判定。

Platform-agnostic pure geometry: viewport visibility computation.

被 android/ui/wait.py（require_visible 语义）和 android/driver.py
（find_elements 结果富化）共同复用，避免两处各写一套导致语义分裂。
"""

from __future__ import annotations

from typing import Optional


def compute_visibility(
    bounds: Optional[tuple[int, int, int, int]],
    screen_size: Optional[tuple[int, int]],
) -> tuple[Optional[bool], Optional[float]]:
    """计算元素 bounds 与屏幕视口的相交可见性。

    Returns:
        (visible, visible_ratio)：
        - bounds 或 screen_size 缺失/无效（宽高 <= 0）→ (None, None)，代表"无法判定"，
          不是"确定不可见"（区分 WebView/Compose 等非 Accessibility-Tree 内容）。
        - 否则 visible = (visible_ratio > 0)，visible_ratio = 相交面积 / 元素面积，
          裁剪到 [0.0, 1.0]。
    """
    if not bounds or len(bounds) != 4:
        return None, None
    if not screen_size or len(screen_size) != 2:
        return None, None

    x1, y1, x2, y2 = bounds
    elem_w, elem_h = x2 - x1, y2 - y1
    if elem_w <= 0 or elem_h <= 0:
        return None, None

    sw, sh = screen_size
    if sw <= 0 or sh <= 0:
        return None, None

    ix1, iy1 = max(x1, 0), max(y1, 0)
    ix2, iy2 = min(x2, sw), min(y2, sh)
    inter_w, inter_h = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter_area = inter_w * inter_h
    elem_area = elem_w * elem_h

    ratio = max(0.0, min(1.0, inter_area / elem_area))
    return (ratio > 0.0), ratio
