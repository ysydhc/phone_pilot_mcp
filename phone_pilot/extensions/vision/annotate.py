"""
Image annotation utilities.
图片标注工具。

Platform-agnostic: operates on bytes, no device dependencies.
平台无关：只操作图片字节，不依赖设备。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, asdict
from typing import Optional, Sequence

import cv2

from phone_pilot.extensions.vision.template import decode_png_to_bgr, encode_bgr_to_png_bytes


# ---------------------------------------------------------------------------
# 元素类型 → 颜色映射 / Element type → color mapping (BGR)
# ---------------------------------------------------------------------------
_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "button":           (0, 200, 0),      # 绿色 / green  — clickable
    "toggle":           (0, 200, 0),      # 绿色 / green  — clickable
    "radio":            (0, 200, 0),      # 绿色 / green  — clickable
    "input":            (200, 120, 0),    # 蓝色 / blue   — editable
    "text":             (160, 160, 160),  # 灰色 / gray   — static text
    "image":            (160, 160, 160),  # 灰色 / gray
    "scroll_container": (0, 160, 255),    # 橙色 / orange — scrollable
    "list":             (0, 160, 255),    # 橙色 / orange
    "pager":            (0, 160, 255),    # 橙色 / orange
    "webview":          (200, 0, 200),    # 紫色 / purple
}
_DEFAULT_COLOR: tuple[int, int, int] = (0, 200, 255)  # 黄色 / yellow — fallback


def _color_for_type(elem_type: str) -> tuple[int, int, int]:
    """Return a BGR color for a given element type."""
    return _TYPE_COLORS.get(elem_type, _DEFAULT_COLOR)


@dataclass
class Box:
    """Generic box with optional label for annotation."""
    x: int
    y: int
    w: int
    h: int
    label: Optional[str] = None

    @property
    def center(self) -> tuple[int, int]:
        return int(self.x + self.w / 2), int(self.y + self.h / 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        cx, cy = self.center
        d["center_x"] = cx
        d["center_y"] = cy
        return d


def annotate_boxes(
    image_bytes: bytes,
    boxes: list[dict],
    *,
    color: tuple[int, int, int] = (0, 200, 255),
    thickness: int = 3,
) -> bytes:
    """
    Draw labeled boxes on an image.
    在图片上绘制带标签的矩形框。
    
    Args:
        image_bytes: Image PNG bytes / 图片 PNG 字节
        boxes: List of box dicts with keys: x, y, w, h, label (optional)
        color: BGR color tuple / BGR 颜色
        thickness: Line thickness / 线条粗细
        
    Returns:
        Annotated image PNG bytes / 标注后的图片 PNG 字节
    """
    img_bgr = decode_png_to_bgr(image_bytes)
    
    out_boxes: list[Box] = []
    for i, b in enumerate(boxes or []):
        if not isinstance(b, dict):
            continue
        try:
            x = int(b.get("x", 0))
            y = int(b.get("y", 0))
            w = int(b.get("w", 0))
            h = int(b.get("h", 0))
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue
        label = b.get("label")
        if label is None:
            label = f"#{i}"
        out_boxes.append(Box(x=x, y=y, w=w, h=h, label=str(label)))
    
    annotated = _annotate_boxes_on_bgr(img_bgr, out_boxes, color=color, thickness=thickness)
    return encode_bgr_to_png_bytes(annotated)


def _annotate_boxes_on_bgr(
    img_bgr,
    boxes: list[Box],
    color: tuple[int, int, int] = (0, 200, 255),
    thickness: int = 3,
):
    """Draw labeled rectangles on an in-memory BGR image (legacy style)."""
    vis = img_bgr.copy()
    for i, b in enumerate(boxes):
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.w), int(b.y + b.h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, thickness)
        label = (b.label or f"#{i}").strip()
        ty = y1 - 10 if y1 - 10 > 20 else (y2 + 25)
        # Draw text with black outline
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)
    return vis


# ---------------------------------------------------------------------------
# 元素标注截图 / Annotate elements on screenshot
# ---------------------------------------------------------------------------

def annotate_elements_on_screenshot(
    png_bytes: bytes,
    elements: Sequence[dict],
) -> bytes:
    """在截图上标注 page_state 格式的元素列表，返回标注后的 PNG 字节。
    Overlay element annotations on a screenshot and return annotated PNG bytes.

    视觉增强 / Visual enhancements:
    - 每个元素按类型着色（绿=可点击, 蓝=输入框, 灰=文本, 橙=可滚动）
    - 半透明矩形填充提升可见性
    - 左上角编号 badge（圆角矩形 + 白字），确保编号在任何背景上清晰
    - 元素标签文字（带黑色描边）

    Args:
        png_bytes: 原始截图 PNG 字节 / Raw screenshot PNG bytes
        elements: phone_get_page_state 返回的 elements 列表 / Elements from phone_get_page_state
            每个元素需包含 / Each element requires:
            - "index": int
            - "bounds": [x1, y1, x2, y2] 或 None
            - "type": str (用于颜色选择)
            - "label": str (可选)

    Returns:
        bytes: 标注后的 PNG 字节 / Annotated PNG bytes
    """
    img_bgr = decode_png_to_bgr(png_bytes)
    # Create overlay for semi-transparent fills
    overlay = img_bgr.copy()
    vis = img_bgr.copy()

    for elem in elements:
        bounds = elem.get("bounds")
        if not bounds or len(bounds) < 4:
            continue
        x1, y1, x2, y2 = int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])
        if x2 <= x1 or y2 <= y1:
            continue

        idx = elem.get("index", 0)
        elem_type = elem.get("type", "view")
        label = elem.get("label", "")
        color = _color_for_type(elem_type)

        # 1) Semi-transparent fill (alpha=0.15)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, cv2.FILLED)

        # 2) Solid border
        cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)

        # 3) Badge: small rounded rect at top-left with index number
        badge_text = str(idx)
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.55
        (tw, th), baseline = cv2.getTextSize(badge_text, font, font_scale, 1)
        badge_w = tw + 10
        badge_h = th + 8
        bx1 = max(x1, 0)
        by1 = max(y1 - badge_h, 0)
        bx2 = bx1 + badge_w
        by2 = by1 + badge_h
        # Badge background (solid, same color)
        cv2.rectangle(vis, (bx1, by1), (bx2, by2), color, cv2.FILLED)
        # Badge text (white)
        cv2.putText(vis, badge_text, (bx1 + 5, by2 - 4), font, font_scale, (255, 255, 255), 1, cv2.LINE_AA)

        # 4) Label text (right of badge, if non-empty and short enough)
        if label:
            display_label = label[:20] + "..." if len(label) > 20 else label
            tx = bx2 + 4
            ty = by2 - 4
            # Black outline + colored text for readability
            cv2.putText(vis, display_label, (tx, ty), font, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(vis, display_label, (tx, ty), font, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

    # Blend overlay for semi-transparent fill
    alpha = 0.15
    vis = cv2.addWeighted(overlay, alpha, vis, 1 - alpha, 0)

    return encode_bgr_to_png_bytes(vis)


def annotate_boxes_on_image_file(
    *,
    image_path: str,
    boxes: list[dict],
) -> dict:
    """
    本地图片标注 box，返回标注 PNG bytes。
    Draw boxes on a local image and return annotated PNG bytes.

    用法/Usage:
    - annotate_boxes_on_image_file(image_path="x.png", boxes=[{"x":1,"y":2,"w":3,"h":4,"label":"#0"}])

    返回/Returns:
    - ok, image_path, boxes, annotated_png
    """
    p = pathlib.Path(str(image_path)).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": "image_not_found", "image_path": str(p)}
    img = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if img is None:
        return {"ok": False, "error": "failed_to_read_image", "image_path": str(p)}
    out_boxes: list[Box] = []
    for i, b in enumerate(boxes or []):
        if not isinstance(b, dict):
            continue
        try:
            x = int(b.get("x", 0))
            y = int(b.get("y", 0))
            w = int(b.get("w", 0))
            h = int(b.get("h", 0))
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue
        label = b.get("label")
        if label is None:
            # Provide a default label for convenience
            label = f"#{i}"
        out_boxes.append(Box(x=x, y=y, w=w, h=h, label=str(label)))
    annotated = _annotate_boxes_on_bgr(img, out_boxes)
    png = encode_bgr_to_png_bytes(annotated)
    return {"ok": True, "image_path": str(p), "count": len(out_boxes), "boxes": [b.to_dict() for b in out_boxes], "annotated_png": png}


def is_mostly_black(
    image_bytes: bytes,
    *,
    mean_thresh: float = 6.0,
    ratio_thresh: float = 0.985,
) -> dict:
    """
    Check if an image is mostly black (e.g., locked screen).
    
    Args:
        image_bytes: Image PNG bytes
        mean_thresh: Mean grayscale threshold
        ratio_thresh: Dark pixel ratio threshold
        
    Returns:
        {
            "is_black": bool,
            "mean_gray": float,
            "dark_ratio": float,
        }
    """
    img_bgr = decode_png_to_bgr(image_bytes)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    
    mean = float(gray.mean())
    dark_ratio = float((gray < 10).mean())
    
    is_black = (mean <= float(mean_thresh) and dark_ratio >= float(ratio_thresh))
    
    return {
        "is_black": is_black,
        "mean_gray": mean,
        "dark_ratio": dark_ratio,
    }
