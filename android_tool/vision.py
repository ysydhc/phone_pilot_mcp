#!/usr/bin/env python3
"""
OpenCV-based vision helpers for Android automation.

Features:
- Capture a device screenshot (PNG bytes) and decode to OpenCV image
- Template matching (cv2.matchTemplate) with optional multi-scale search
- Return match boxes / center points for tapping

This is intended for "image button" automation when UIAutomator tree cannot expose text/desc.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, asdict
from typing import Optional
import cv2
import numpy as np

from android_tool.recordings_store import ensure_abs, now_dirname, safe_name
from android_tool.screenshot import take_screenshot_png_bytes


def _require_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "OpenCV not available. Please install dependency: opencv-python-headless. "
            f"Import error: {e}"
        ) from e
    return cv2, np


@dataclass
class MatchBox:
    x: int
    y: int
    w: int
    h: int
    score: float
    scale: float

    @property
    def center(self) -> tuple[int, int]:
        return int(self.x + self.w / 2), int(self.y + self.h / 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        cx, cy = self.center
        d["center_x"] = cx
        d["center_y"] = cy
        return d


@dataclass
class Box:
    """
    Generic box with optional label for annotation.
    Coordinates are full-image pixels: x,y,w,h.
    """

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


def _decode_png_to_bgr(png: bytes):
    cv2, np = _require_cv2()
    buf = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("failed to decode screenshot PNG via cv2.imdecode")
    return img


def _encode_bgr_to_png_bytes(img_bgr) -> bytes:
    cv2, _ = _require_cv2()
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError("failed to encode image via cv2.imencode(.png)")
    return bytes(buf)


def _black_screen_stats(screen_bgr) -> dict:
    """
    Detect "almost black" screenshots which commonly happen when:
    - device is locked / keyguard showing
    - app uses FLAG_SECURE / DRM protected content
    - transient rendering issue (very short moment)
    """
    cv2, _ = _require_cv2()
    gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    # ratio of pixels below a low threshold
    dark_ratio = float((gray < 10).mean())
    return {"mean_gray": mean, "dark_ratio": dark_ratio}


def _is_mostly_black(screen_bgr, *, mean_thresh: float = 6.0, ratio_thresh: float = 0.985) -> tuple[bool, dict]:
    st = _black_screen_stats(screen_bgr)
    return (st["mean_gray"] <= float(mean_thresh) and st["dark_ratio"] >= float(ratio_thresh)), st


def _load_image_bgr(path: pathlib.Path):
    cv2, _ = _require_cv2()
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"failed to read image: {path}")
    return img


def _crop_roi(img, roi: Optional[tuple[int, int, int, int]]):
    # roi: (x, y, w, h)
    if not roi:
        return img, (0, 0)
    x, y, w, h = roi
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    return img[y : y + h, x : x + w], (x, y)


def _match_template_shape(
    *,
    screen_bgr,
    tmpl_bgr,
    threshold: float,
    roi: Optional[tuple[int, int, int, int]],
    scales: Optional[list[float]],
    max_results: int,
) -> tuple[list["MatchBox"], dict]:
    """
    基于形状的模板匹配，对背景颜色完全不敏感。
    Shape-based template matching, completely insensitive to background color.
    
    原理：
    1. 使用自适应阈值二值化提取形状
    2. 在二值图上做多尺度模板匹配
    3. 验证匹配位置确实包含非空内容
    """
    cv2, _ = _require_cv2()
    
    screen_roi, (ox, oy) = _crop_roi(screen_bgr, roi)
    
    # 转灰度
    screen_gray = cv2.cvtColor(screen_roi, cv2.COLOR_BGR2GRAY)
    tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
    
    # 自适应阈值二值化（提取形状）
    screen_bin = cv2.adaptiveThreshold(
        screen_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5
    )
    tmpl_bin_orig = cv2.adaptiveThreshold(
        tmpl_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5
    )
    
    # 检查模板是否有足够的形状内容
    tmpl_content_ratio = np.sum(tmpl_bin_orig > 0) / tmpl_bin_orig.size
    if tmpl_content_ratio < 0.01:
        return [], {"method": "shape", "error": "template_no_content"}
    
    # 多尺度匹配
    scale_list = scales or [1.0]
    sh_, sw_ = screen_bin.shape[:2]
    candidates = []
    
    for sf in scale_list:
        th_orig, tw_orig = tmpl_bin_orig.shape[:2]
        th_ = max(1, int(th_orig * sf))
        tw_ = max(1, int(tw_orig * sf))
        
        if th_ > sh_ or tw_ > sw_:
            continue
        
        if sf != 1.0:
            tmpl_bin = cv2.resize(tmpl_bin_orig, (tw_, th_), interpolation=cv2.INTER_NEAREST)
        else:
            tmpl_bin = tmpl_bin_orig
        
        result = cv2.matchTemplate(screen_bin, tmpl_bin, cv2.TM_CCOEFF_NORMED)
        result_work = result.copy()
        
        for _ in range(max(1, int(max_results))):
            _, max_val, _, max_loc = cv2.minMaxLoc(result_work)
            
            if max_val < threshold:
                break
            
            x, y = int(max_loc[0]), int(max_loc[1])
            
            # 验证：检查匹配位置是否有实际内容（非空白区域）
            match_region = screen_bin[y:y+th_, x:x+tw_]
            content_ratio = np.sum(match_region > 0) / match_region.size
            
            # 只接受有足够内容的匹配
            if content_ratio >= tmpl_content_ratio * 0.3:
                candidates.append(MatchBox(
                    x=ox + x, y=oy + y, w=int(tw_), h=int(th_),
                    score=float(max_val), scale=float(sf)
                ))
            
            # Mask around peak
            x0 = max(0, x - int(tw_ * 0.5))
            y0 = max(0, y - int(th_ * 0.5))
            x1 = min(result_work.shape[1], x + int(tw_ * 0.5))
            y1 = min(result_work.shape[0], y + int(th_ * 0.5))
            result_work[y0:y1, x0:x1] = -1.0
    
    # 按分数排序
    candidates.sort(key=lambda b: float(b.score), reverse=True)
    candidates = candidates[:max(1, int(max_results))]
    
    return candidates, {"method": "shape", "threshold": threshold, "roi": roi, "scales": scale_list}


def _match_template_auto(
    *,
    screen_bgr,
    tmpl_bgr,
    threshold: float,
    grayscale: bool,
    roi: Optional[tuple[int, int, int, int]],
    scales: Optional[list[float]],
    max_results: int,
) -> tuple[list["MatchBox"], dict]:
    """
    自动尝试多种匹配方法，选择最佳结果。
    Auto mode: try multiple methods and pick the best result.
    
    策略:
    1. 先尝试 ccoeff_normed（标准阈值）
    2. 如果没有高分匹配，尝试多尺度 ccoeff_normed（适应轻微大小差异）
    3. 如果还没有，尝试 edge + 多尺度（对颜色不敏感）
    4. 最后尝试 shape 匹配（二值化形状匹配）
    """
    cv2, _ = _require_cv2()
    
    # 1. 标准 ccoeff_normed
    matches, meta = _match_template(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=threshold,
        grayscale=grayscale,
        roi=roi,
        scales=scales or [1.0],
        method="ccoeff_normed",
        max_results=max_results,
        use_edge_fallback=False,
    )
    
    if matches and matches[0].score >= threshold:
        meta["auto_method_used"] = "ccoeff_normed"
        return matches, meta
    
    # 2. 多尺度 ccoeff_normed（适应模板大小不完全匹配的情况）
    multi_scales = [0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25]
    lower_threshold = threshold * 0.5  # 降低阈值到 50%
    
    matches, meta = _match_template(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=lower_threshold,
        grayscale=grayscale,
        roi=roi,
        scales=multi_scales,
        method="ccoeff_normed",
        max_results=max_results,
        use_edge_fallback=False,
    )
    
    if matches and matches[0].score >= lower_threshold:
        meta["auto_method_used"] = "ccoeff_normed_multiscale"
        meta["note"] = f"使用多尺度匹配，阈值降低到 {lower_threshold:.2f}"
        return matches, meta
    
    # 3. 边缘检测 + 多尺度（对背景颜色完全不敏感）
    edge_matches, edge_meta = _match_template(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=lower_threshold * 0.6,  # 边缘匹配阈值更低
        grayscale=grayscale,
        roi=roi,
        scales=multi_scales,
        method="edge",
        max_results=max_results,
        use_edge_fallback=False,
    )
    
    if edge_matches:
        edge_meta["auto_method_used"] = "edge_multiscale"
        return edge_matches, edge_meta
    
    # 4. 形状匹配（二值化后匹配）
    shape_matches, shape_meta = _match_template_shape(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=lower_threshold * 0.8,
        roi=roi,
        scales=multi_scales,
        max_results=max_results,
    )
    
    if shape_matches:
        shape_meta["auto_method_used"] = "shape"
        return shape_matches, shape_meta
    
    # 没有找到任何匹配
    return [], {"method": "auto", "auto_method_used": "none", "threshold": threshold, "roi": roi}


def _match_template(
    *,
    screen_bgr,
    tmpl_bgr,
    threshold: float,
    grayscale: bool,
    roi: Optional[tuple[int, int, int, int]],
    scales: Optional[list[float]],
    method: str,
    max_results: int,
    use_edge_fallback: bool = True,
) -> tuple[list[MatchBox], dict]:
    """
    Core template matching logic. Returns (matches, meta).
    - matches: list of MatchBox in *full-screen* coordinates.
    - meta: {method, scales, threshold, grayscale, roi}
    
    支持的方法:
    - ccoeff_normed: 归一化相关系数（默认，对背景颜色敏感）
    - ccorr_normed: 归一化互相关（对背景颜色更稳健）
    - sqdiff_normed: 归一化平方差
    - edge: 边缘检测后匹配（对颜色完全不敏感）
    - auto: 自动尝试多种方法，选择最佳结果
    """
    cv2, _ = _require_cv2()

    screen_roi, (ox, oy) = _crop_roi(screen_bgr, roi)
    if grayscale:
        screen = cv2.cvtColor(screen_roi, cv2.COLOR_BGR2GRAY)
        tmpl0 = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
    else:
        screen = screen_roi
        tmpl0 = tmpl_bgr

    m = (method or "ccoeff_normed").strip().lower()
    
    # 方法映射
    method_map = {
        "sqdiff_normed": (cv2.TM_SQDIFF_NORMED, True),
        "ccorr_normed": (cv2.TM_CCORR_NORMED, False),
        "ccoeff_normed": (cv2.TM_CCOEFF_NORMED, False),
    }
    
    if m == "edge":
        # 边缘检测匹配
        screen_edge = cv2.Canny(screen, 50, 150)
        tmpl_edge = cv2.Canny(tmpl0, 50, 150)
        cv_method = cv2.TM_CCOEFF_NORMED
        better_is_lower = False
        screen = screen_edge
        tmpl0 = tmpl_edge
        m = "edge"
    elif m == "auto":
        # 自动模式：尝试多种方法
        return _match_template_auto(
            screen_bgr=screen_bgr,
            tmpl_bgr=tmpl_bgr,
            threshold=threshold,
            grayscale=grayscale,
            roi=roi,
            scales=scales,
            max_results=max_results,
        )
    elif m in method_map:
        cv_method, better_is_lower = method_map[m]
    else:
        cv_method = cv2.TM_CCOEFF_NORMED
        better_is_lower = False
        m = "ccoeff_normed"

    # Normalize scales
    scale_list = scales or [1.0]
    scale_list2: list[float] = []
    for s in scale_list:
        try:
            sf = float(s)
        except Exception:
            continue
        if sf <= 0:
            continue
        scale_list2.append(float(f"{sf:.4f}"))
    if not scale_list2:
        scale_list2 = [1.0]

    candidates: list[MatchBox] = []
    for sf in scale_list2:
        if sf == 1.0:
            tmpl = tmpl0
        else:
            tw = max(1, int(round(tmpl0.shape[1] * sf)))
            th = max(1, int(round(tmpl0.shape[0] * sf)))
            tmpl = cv2.resize(tmpl0, (tw, th), interpolation=cv2.INTER_AREA if sf < 1.0 else cv2.INTER_LINEAR)

        th_, tw_ = tmpl.shape[:2]
        sh_, sw_ = screen.shape[:2]
        if th_ > sh_ or tw_ > sw_:
            continue

        res = cv2.matchTemplate(screen, tmpl, cv_method)
        res_work = res.copy()
        for _ in range(max(1, int(max_results))):
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res_work)
            if better_is_lower:
                score0 = float(min_val)
                loc = min_loc
                passed = score0 <= (1.0 - threshold)
                out_score = 1.0 - score0
            else:
                score0 = float(max_val)
                loc = max_loc
                passed = score0 >= threshold
                out_score = score0
            if not passed:
                break

            x, y = int(loc[0]), int(loc[1])
            candidates.append(MatchBox(x=ox + x, y=oy + y, w=int(tw_), h=int(th_), score=float(out_score), scale=float(sf)))

            # Mask around peak (lightweight NMS-ish)
            x0 = max(0, x - int(tw_ * 0.5))
            y0 = max(0, y - int(th_ * 0.5))
            x1 = min(res_work.shape[1], x + int(tw_ * 0.5))
            y1 = min(res_work.shape[0], y + int(th_ * 0.5))
            res_work[y0:y1, x0:x1] = (1.0 if better_is_lower else -1.0)

    candidates.sort(key=lambda b: float(b.score), reverse=True)
    candidates = candidates[: max(1, int(max_results))]

    return candidates, {"method": m, "scales": scale_list2, "roi": roi, "grayscale": bool(grayscale), "threshold": float(threshold)}


def annotate_matches_on_screen(
    *,
    device_serial: Optional[str],
    template_path: str,
    threshold: float = 0.85,
    grayscale: bool = True,
    roi: Optional[tuple[int, int, int, int]] = None,
    scales: Optional[list[float]] = None,
    method: str = "ccoeff_normed",
    max_results: int = 10,
    top_k: Optional[int] = None,
) -> dict:
    """
    一次截图 + 模板匹配，返回带编号标注的 PNG（bytes），用于人工选择匹配框。
    Capture once, run template matching, and return annotated PNG bytes for selection.

    用法/Usage:
    - annotate_matches_on_screen(device_serial, template_path, threshold=0.8, top_k=5)

    返回/Returns:
    - ok
    - matches: list of match boxes (x,y,w,h,score,scale,center_x/center_y)
    - annotated_png: raw PNG bytes (no base64)
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    try:
        thr = float(threshold)
    except Exception:
        thr = 0.85
    thr = max(0.0, min(1.0, thr))

    cv2, _ = _require_cv2()
    tmpl_path = pathlib.Path(template_path).expanduser().resolve()
    tmpl_bgr = _load_image_bgr(tmpl_path)

    png = take_screenshot_png_bytes(device_serial)
    screen_bgr = _decode_png_to_bgr(png)
    is_black, stats = _is_mostly_black(screen_bgr)
    if is_black:
        return {
            "ok": False,
            "device_serial": device_serial,
            "error": "screenshot_black",
            "stats": stats,
            "note": "截图几乎全黑；可能是锁屏/安全页面(FLAG_SECURE)/渲染瞬间。建议先检查 keyguard 状态。",
        }

    matches, meta = _match_template(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=thr,
        grayscale=bool(grayscale),
        roi=roi,
        scales=scales,
        method=method,
        max_results=int(max_results),
    )

    draw_n = len(matches)
    if top_k is not None:
        try:
            draw_n = max(0, min(draw_n, int(top_k)))
        except Exception:
            draw_n = draw_n

    vis = screen_bgr.copy()
    # Draw boxes + index labels
    for i, b in enumerate(matches[:draw_n]):
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.w), int(b.y + b.h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 200, 255), 3)  # orange-ish
        label = f"#{i} {b.score:.3f}"
        # Put text above box if possible
        ty = y1 - 10 if y1 - 10 > 20 else (y2 + 25)
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA)

    annotated_png = _encode_bgr_to_png_bytes(vis)
    return {
        "ok": True,
        "device_serial": device_serial,
        "template_path": str(tmpl_path),
        **meta,
        "count": len(matches),
        "matches": [m.to_dict() for m in matches],
        "annotated_png": annotated_png,
        "stats": stats,
    }


def _find_icon_with_features_on_bgr(icon_bgr, screen_bgr, ratio_thresh=0.7, min_inliers: int = 0):
    """
    使用特征匹配（SIFT）在截图中定位图标（内部工具）。
    SIFT-based feature matching on in-memory images (internal helper).
    """
    # 2. 转换为灰度图
    if icon_bgr is None or screen_bgr is None:
        return False, None, None
    gray_icon = cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2GRAY)
    gray_screen = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

    # 3. 初始化特征检测器（这里以SIFT为例）
    sift = cv2.SIFT_create()

    # 4. 检测关键点并计算描述子
    kp_icon, des_icon = sift.detectAndCompute(gray_icon, None)
    kp_screen, des_screen = sift.detectAndCompute(gray_screen, None)

    if des_icon is None or des_screen is None:
        return False, None, None

    # 5. 特征匹配：使用FLANN匹配器（适合SIFT）
    # 配置FLANN参数
    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)

    # 进行KNN匹配，k=2为每个关键点找两个最近邻
    matches = flann.knnMatch(des_icon, des_screen, k=2)

    # 6. 应用Lowe's比率测试筛选优质匹配
    good_matches = []
    matches_mask = [[0, 0] for _ in range(len(matches))]  # 用于绘制
    for i, (m, n) in enumerate(matches):
        if m.distance < ratio_thresh * n.distance:
            good_matches.append(m)
            matches_mask[i] = [1, 0]  # 标记为优质匹配

    # 7. 估算几何变换（单应性矩阵）
    if len(good_matches) > 4:  # 至少需要4个点来计算单应性矩阵
        # 提取匹配点的坐标
        src_pts = np.float32([kp_icon[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp_screen[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

        # 使用RANSAC算法稳健地估算单应性矩阵，可以排除异常匹配点
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
        if H is not None:
            # 使用mask进一步筛选出内点（inliers）
            inlier_mask = mask.ravel().tolist()
            if min_inliers and isinstance(inlier_mask, list):
                if sum(1 for x in inlier_mask if x) < int(min_inliers):
                    return False, None, inlier_mask
            return True, H, inlier_mask
        else:
            return False, None, None
    else:
        return False, None, None


def _annotate_boxes_on_image_bytes(
    *,
    img_bgr,
    boxes: list[Box],
) -> bytes:
    """
    Draw labeled rectangles on an in-memory BGR image and return PNG bytes.
    """
    cv2, _ = _require_cv2()
    vis = img_bgr.copy()
    for i, b in enumerate(boxes):
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.w), int(b.y + b.h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 200, 255), 3)
        label = (b.label or f"#{i}").strip()
        ty = y1 - 10 if y1 - 10 > 20 else (y2 + 25)
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(vis, label, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2, cv2.LINE_AA)
    return _encode_bgr_to_png_bytes(vis)


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
    cv2, _ = _require_cv2()
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
    png = _annotate_boxes_on_image_bytes(img_bgr=img, boxes=out_boxes)
    return {"ok": True, "image_path": str(p), "count": len(out_boxes), "boxes": [b.to_dict() for b in out_boxes], "annotated_png": png}


def find_template_on_screen_with_fallback(
    device_serial: Optional[str],
    *,
    template_path: str,
    threshold: float = 0.85,
    grayscale: bool = True,
    roi: Optional[tuple[int, int, int, int]] = None,
    scales: Optional[list[float]] = None,
    method: str = "ccoeff_normed",
    max_results: int = 5,
    feature_enabled: bool = True,
    feature_ratio_thresh: float = 0.7,
    feature_min_inliers: int = 0,
    debug_annotate: bool = False,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
) -> dict:
    """
    统一找图入口：先特征匹配(SIFT)，失败后再模板匹配。
    Unified entry: feature match (SIFT) first, then template matching.

    用法/Usage:
    - find_template_on_screen_with_fallback(device_serial, template_path, threshold=0.85, roi=[x,y,w,h])

    返回/Returns:
    - ok
    - matches: list of match boxes (x,y,w,h,score,scale,center_x/center_y)
    - method_used: "sift_features" or "template"
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    tmpl_path = pathlib.Path(template_path).expanduser().resolve()
    if not tmpl_path.exists():
        return {"ok": False, "error": "template_not_found", "template_path": str(tmpl_path)}

    png = take_screenshot_png_bytes(device_serial)
    screen_bgr = _decode_png_to_bgr(png)
    is_black, stats = _is_mostly_black(screen_bgr)
    if is_black:
        return {
            "ok": False,
            "device_serial": device_serial,
            "error": "screenshot_black",
            "stats": stats,
            "note": "截图几乎全黑；可能是锁屏/安全页面(FLAG_SECURE)/渲染瞬间。",
        }

    tmpl_bgr = _load_image_bgr(tmpl_path)

    # ROI crop (screen-side) for shared screenshot
    if roi is not None:
        try:
            rx, ry, rw, rh = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])
            screen_roi = screen_bgr[ry : ry + rh, rx : rx + rw]
            ox, oy = rx, ry
        except Exception:
            screen_roi = screen_bgr
            ox, oy = 0, 0
    else:
        screen_roi = screen_bgr
        ox, oy = 0, 0

    # 1) Feature matching (SIFT) first
    sift_debug = None
    ok, H, inlier_mask = False, None, None
    if feature_enabled:
        try:
            ok, H, inlier_mask = _find_icon_with_features_on_bgr(
                tmpl_bgr,
                screen_roi,
                ratio_thresh=float(feature_ratio_thresh),
                min_inliers=int(feature_min_inliers),
            )
            sift_debug = {
                "ok": bool(ok),
                "inliers": len(inlier_mask or []),
                "ratio_thresh": float(feature_ratio_thresh),
                "min_inliers": int(feature_min_inliers),
            }
        except Exception as e:
            ok, H, inlier_mask = False, None, None
            sift_debug = {"ok": False, "error": str(e)}
    else:
        sift_debug = {"ok": False, "disabled": True}
    annotated_path = None
    if ok and H is not None:
        th, tw = tmpl_bgr.shape[:2]
        corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
        proj = cv2.perspectiveTransform(corners, H)
        xs = proj[:, 0, 0]
        ys = proj[:, 0, 1]
        x1 = max(0, int(xs.min()))
        y1 = max(0, int(ys.min()))
        x2 = min(screen_roi.shape[1] - 1, int(xs.max()))
        y2 = min(screen_roi.shape[0] - 1, int(ys.max()))
        if x2 > x1 and y2 > y1:
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            cx = ox + x1 + w / 2.0
            cy = oy + y1 + h / 2.0
            match = {
                "x": int(ox + x1),
                "y": int(oy + y1),
                "w": int(w),
                "h": int(h),
                "center_x": int(round(cx)),
                "center_y": int(round(cy)),
                "score": 1.0,
                "scale": 1.0,
            }
            if debug_annotate:
                try:
                    out_root = ensure_abs(out_dir)
                    ts = now_dirname()
                    safe = safe_name(name or tmpl_path.stem or "find_image")
                    box = Box(x=int(ox + x1), y=int(oy + y1), w=int(w), h=int(h), label="sift")
                    png = _annotate_boxes_on_image_bytes(img_bgr=screen_bgr, boxes=[box])
                    out_path = out_root / "find_image_debug" / f"{ts}_{safe}_sift.png"
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(png)
                    annotated_path = str(out_path)
                except Exception as e:
                    sift_debug["annotate_error"] = str(e)
            return {
                "ok": True,
                "device_serial": device_serial,
                "template_path": str(tmpl_path),
                "count": 1,
                "matches": [match],
                "method_used": "sift_features",
                "debug": {"sift": sift_debug},
                "annotated_path": annotated_path,
            }

    # 2) Template matching fallback (shared screenshot)
    try:
        thr = float(threshold)
    except Exception:
        thr = 0.85
    thr = max(0.0, min(1.0, thr))
    candidates, meta = _match_template(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=thr,
        grayscale=bool(grayscale),
        roi=roi,
        scales=scales,
        method=method,
        max_results=int(max_results),
    )
    result = {
        "ok": True,
        "device_serial": device_serial,
        "template_path": str(tmpl_path),
        **meta,
        "count": len(candidates),
        "matches": [c.to_dict() for c in candidates],
        "method_used": "template",
        "debug": {"sift": sift_debug},
    }
    if debug_annotate:
        try:
            out_root = ensure_abs(out_dir)
            ts = now_dirname()
            safe = safe_name(name or tmpl_path.stem or "find_image")
            boxes = []
            for c in candidates:
                boxes.append(Box(x=int(c.x), y=int(c.y), w=int(c.w), h=int(c.h), label=f"{c.score:.3f}"))
            if boxes:
                png = _annotate_boxes_on_image_bytes(img_bgr=screen_bgr, boxes=boxes)
                out_path = out_root / "find_image_debug" / f"{ts}_{safe}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(png)
                annotated_path = str(out_path)
        except Exception as e:
            result["debug_annotate_error"] = str(e)
    if annotated_path:
        result["annotated_path"] = annotated_path
    return result


