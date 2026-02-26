"""
Template matching using OpenCV.

Platform-agnostic: operates on bytes, no device dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

from phone_pilot.core.storage import recordings_root


def _require_cv2():
    """Ensure OpenCV is available."""
    try:
        import cv2  # noqa: F811
        import numpy as np  # noqa: F811
    except Exception as e:
        raise RuntimeError(
            "OpenCV not available. Please install dependency: opencv-python-headless. "
            f"Import error: {e}"
        ) from e
    return cv2, np


@dataclass
class MatchBox:
    """Template match result box."""
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


def decode_png_to_bgr(png_bytes: bytes):
    """Decode PNG bytes to BGR numpy array."""
    cv2, np = _require_cv2()
    buf = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("failed to decode PNG via cv2.imdecode")
    return img


def encode_bgr_to_png_bytes(img_bgr) -> bytes:
    """Encode BGR numpy array to PNG bytes."""
    cv2, _ = _require_cv2()
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError("failed to encode image via cv2.imencode(.png)")
    return bytes(buf)


def _crop_roi(img, roi: Optional[tuple[int, int, int, int]]):
    """Crop image to ROI. Returns (cropped_img, (offset_x, offset_y))."""
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
) -> tuple[list[MatchBox], dict]:
    """
    Shape-based template matching using adaptive thresholding.
    Completely insensitive to background color.
    """
    cv2, np = _require_cv2()
    
    screen_roi, (ox, oy) = _crop_roi(screen_bgr, roi)
    
    screen_gray = cv2.cvtColor(screen_roi, cv2.COLOR_BGR2GRAY)
    tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
    
    screen_bin = cv2.adaptiveThreshold(
        screen_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5
    )
    tmpl_bin_orig = cv2.adaptiveThreshold(
        tmpl_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5
    )
    
    tmpl_content_ratio = np.sum(tmpl_bin_orig > 0) / tmpl_bin_orig.size
    if tmpl_content_ratio < 0.01:
        return [], {"method": "shape", "error": "template_no_content"}
    
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
            
            match_region = screen_bin[y:y+th_, x:x+tw_]
            content_ratio = np.sum(match_region > 0) / match_region.size
            
            if content_ratio >= tmpl_content_ratio * 0.3:
                candidates.append(MatchBox(
                    x=ox + x, y=oy + y, w=int(tw_), h=int(th_),
                    score=float(max_val), scale=float(sf)
                ))
            
            x0 = max(0, x - int(tw_ * 0.5))
            y0 = max(0, y - int(th_ * 0.5))
            x1 = min(result_work.shape[1], x + int(tw_ * 0.5))
            y1 = min(result_work.shape[0], y + int(th_ * 0.5))
            result_work[y0:y1, x0:x1] = -1.0
    
    candidates.sort(key=lambda b: float(b.score), reverse=True)
    candidates = candidates[:max(1, int(max_results))]
    
    return candidates, {"method": "shape", "threshold": threshold, "roi": roi, "scales": scale_list}


def _match_template_core(
    *,
    screen_bgr,
    tmpl_bgr,
    threshold: float,
    grayscale: bool,
    roi: Optional[tuple[int, int, int, int]],
    scales: Optional[list[float]],
    method: str,
    max_results: int,
) -> tuple[list[MatchBox], dict]:
    """
    Core template matching logic.
    
    Methods:
    - ccoeff_normed: Normalized cross-correlation coefficient (default)
    - ccorr_normed: Normalized cross-correlation
    - sqdiff_normed: Normalized squared difference
    - edge: Edge detection before matching
    - auto: Try multiple methods
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
    
    method_map = {
        "sqdiff_normed": (cv2.TM_SQDIFF_NORMED, True),
        "ccorr_normed": (cv2.TM_CCORR_NORMED, False),
        "ccoeff_normed": (cv2.TM_CCOEFF_NORMED, False),
    }
    
    if m == "edge":
        screen_edge = cv2.Canny(screen, 50, 150)
        tmpl_edge = cv2.Canny(tmpl0, 50, 150)
        cv_method = cv2.TM_CCOEFF_NORMED
        better_is_lower = False
        screen = screen_edge
        tmpl0 = tmpl_edge
        m = "edge"
    elif m == "auto":
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

            x0 = max(0, x - int(tw_ * 0.5))
            y0 = max(0, y - int(th_ * 0.5))
            x1 = min(res_work.shape[1], x + int(tw_ * 0.5))
            y1 = min(res_work.shape[0], y + int(th_ * 0.5))
            res_work[y0:y1, x0:x1] = (1.0 if better_is_lower else -1.0)

    candidates.sort(key=lambda b: float(b.score), reverse=True)
    candidates = candidates[: max(1, int(max_results))]

    return candidates, {"method": m, "scales": scale_list2, "roi": roi, "grayscale": bool(grayscale), "threshold": float(threshold)}


def _match_template_auto(
    *,
    screen_bgr,
    tmpl_bgr,
    threshold: float,
    grayscale: bool,
    roi: Optional[tuple[int, int, int, int]],
    scales: Optional[list[float]],
    max_results: int,
) -> tuple[list[MatchBox], dict]:
    """
    Auto mode: try multiple methods and pick the best result.
    """
    cv2, _ = _require_cv2()
    
    # 1. Standard ccoeff_normed
    matches, meta = _match_template_core(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=threshold,
        grayscale=grayscale,
        roi=roi,
        scales=scales or [1.0],
        method="ccoeff_normed",
        max_results=max_results,
    )
    
    if matches and matches[0].score >= threshold:
        meta["auto_method_used"] = "ccoeff_normed"
        return matches, meta
    
    # 2. Multi-scale ccoeff_normed
    multi_scales = [0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25]
    lower_threshold = threshold * 0.5
    
    matches, meta = _match_template_core(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=lower_threshold,
        grayscale=grayscale,
        roi=roi,
        scales=multi_scales,
        method="ccoeff_normed",
        max_results=max_results,
    )
    
    if matches and matches[0].score >= lower_threshold:
        meta["auto_method_used"] = "ccoeff_normed_multiscale"
        return matches, meta
    
    # 3. Edge detection + multi-scale
    edge_matches, edge_meta = _match_template_core(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=lower_threshold * 0.6,
        grayscale=grayscale,
        roi=roi,
        scales=multi_scales,
        method="edge",
        max_results=max_results,
    )
    
    if edge_matches:
        edge_meta["auto_method_used"] = "edge_multiscale"
        return edge_matches, edge_meta
    
    # 4. Shape matching
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
    
    return [], {"method": "auto", "auto_method_used": "none", "threshold": threshold, "roi": roi}


def match_template(
    screen_bytes: bytes,
    template_bytes: bytes,
    *,
    threshold: float = 0.85,
    grayscale: bool = True,
    roi: Optional[tuple[int, int, int, int]] = None,
    scales: Optional[list[float]] = None,
    method: str = "ccoeff_normed",
    max_results: int = 5,
) -> dict:
    """
    Template matching on image bytes.
    
    Args:
        screen_bytes: Screenshot PNG bytes
        template_bytes: Template image PNG bytes
        threshold: Match threshold (0.0-1.0)
        grayscale: Convert to grayscale before matching
        roi: Region of interest (x, y, w, h)
        scales: List of scales to try for multi-scale matching
        method: Matching method (ccoeff_normed, ccorr_normed, sqdiff_normed, edge, auto)
        max_results: Maximum number of matches to return
        
    Returns:
        {
            "ok": bool,
            "count": int,
            "matches": [{"x", "y", "w", "h", "score", "scale", "center_x", "center_y"}],
            "method": str,
            ...
        }
    """
    try:
        thr = float(threshold)
        thr = max(0.0, min(1.0, thr))
    except Exception:
        thr = 0.85

    try:
        screen_bgr = decode_png_to_bgr(screen_bytes)
        tmpl_bgr = decode_png_to_bgr(template_bytes)
    except Exception as e:
        return {"ok": False, "error": "decode_failed", "detail": str(e)}

    matches, meta = _match_template_core(
        screen_bgr=screen_bgr,
        tmpl_bgr=tmpl_bgr,
        threshold=thr,
        grayscale=bool(grayscale),
        roi=roi,
        scales=scales,
        method=method,
        max_results=int(max_results),
    )

    return {
        "ok": True,
        **meta,
        "count": len(matches),
        "matches": [m.to_dict() for m in matches],
    }


# -----------------------------------------------------------------------------
# Feature matching (SIFT)
# -----------------------------------------------------------------------------

def _find_icon_with_features_on_bgr(
    tmpl_bgr,
    screen_bgr,
    ratio_thresh: float = 0.7,
    min_inliers: int = 0,
) -> tuple[bool, Optional[np.ndarray], Optional[np.ndarray], int]:
    """
    Feature-based icon matching using SIFT.
    Returns (ok, homography_matrix, inlier_mask).
    """
    cv2, np = _require_cv2()
    
    try:
        sift = cv2.SIFT_create()
    except Exception:
        return False, None, None, 0
    
    tmpl_gray = cv2.cvtColor(tmpl_bgr, cv2.COLOR_BGR2GRAY)
    screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
    
    kp1, des1 = sift.detectAndCompute(tmpl_gray, None)
    kp2, des2 = sift.detectAndCompute(screen_gray, None)
    
    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return False, None, None, 0
    
    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)
    
    matches = flann.knnMatch(des1, des2, k=2)
    
    good = []
    for m_n in matches:
        if len(m_n) == 2:
            m, n = m_n
            if m.distance < ratio_thresh * n.distance:
                good.append(m)
    
    if len(good) < max(4, min_inliers):
        return False, None, None, 0
    
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    
    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    
    if H is None or mask is None:
        return False, None, None, 0
    
    inlier_count = int(mask.sum())
    if inlier_count < max(4, min_inliers):
        return False, None, None, 0
    
    return True, H, mask, len(good)


def _bbox_from_homography(tmpl_shape, H) -> Optional[tuple[int, int, int, int]]:
    cv2, np = _require_cv2()
    if H is None:
        return None
    h_t, w_t = tmpl_shape[:2]
    corners = np.float32([[0, 0], [w_t, 0], [w_t, h_t], [0, h_t]]).reshape(-1, 1, 2)
    try:
        projected = cv2.perspectiveTransform(corners, H)
    except Exception:
        return None
    xs = projected[:, 0, 0]
    ys = projected[:, 0, 1]
    if xs.size == 0 or ys.size == 0:
        return None
    x0, y0 = float(xs.min()), float(ys.min())
    x1, y1 = float(xs.max()), float(ys.max())
    if x1 <= x0 or y1 <= y0:
        return None
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def match_template_with_features(
    screen_bytes: bytes,
    template_bytes: bytes,
    *,
    threshold: float = 0.85,
    grayscale: bool = True,
    roi: Optional[tuple[int, int, int, int]] = None,
    scales: Optional[list[float]] = None,
    method: str = "ccoeff_normed",
    max_results: int = 5,
    feature_enabled: bool = True,
    feature_ratio_thresh: float = 0.7,
    feature_min_inliers: int = 0,
    feature_score_thresh: float = 0.6,
) -> dict:
    """
    Feature match (SIFT) first, then template matching on bytes.
    """
    try:
        screen_bgr = decode_png_to_bgr(screen_bytes)
        tmpl_bgr = decode_png_to_bgr(template_bytes)
    except Exception as e:
        return {"ok": False, "error": "decode_failed", "detail": str(e)}

    if feature_enabled:
        ok, H, inlier_mask, good_count = _find_icon_with_features_on_bgr(
            tmpl_bgr,
            screen_bgr,
            ratio_thresh=float(feature_ratio_thresh),
            min_inliers=int(feature_min_inliers),
        )
        if ok and H is not None and inlier_mask is not None:
            bbox = _bbox_from_homography(tmpl_bgr.shape, H)
            if bbox:
                inlier_count = int(inlier_mask.sum())
                score = float(inlier_count) / max(1, int(good_count))
                x, y, w, h = bbox
                if score >= float(feature_score_thresh):
                    return {
                        "ok": True,
                        "method": "features",
                        "count": 1,
                        "matches": [
                            {
                                "x": int(x),
                                "y": int(y),
                                "w": int(w),
                                "h": int(h),
                                "score": score,
                                "scale": 1.0,
                                "center_x": int(x + w / 2),
                                "center_y": int(y + h / 2),
                            }
                        ],
                        "feature_ratio_thresh": float(feature_ratio_thresh),
                        "feature_min_inliers": int(feature_min_inliers),
                        "feature_score_thresh": float(feature_score_thresh),
                    }

    return match_template(
        screen_bytes,
        template_bytes,
        threshold=threshold,
        grayscale=grayscale,
        roi=roi,
        scales=scales,
        method=method,
        max_results=max_results,
    )


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
    feature_score_thresh: float = 0.6,
    debug_annotate: bool = False,
    out_dir: str = "./.recordings",
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
    import pathlib
    import datetime
    import re
    
    cv2, np = _require_cv2()
    
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    tmpl_path = pathlib.Path(template_path).expanduser().resolve()
    if not tmpl_path.exists():
        return {"ok": False, "error": "template_not_found", "template_path": str(tmpl_path)}

    # Import screenshot function
    from phone_pilot.android.adb.screenshot import take_screenshot_png_bytes
    from phone_pilot.extensions.vision.annotate import is_mostly_black, annotate_boxes
    
    png = take_screenshot_png_bytes(device_serial)
    screen_bgr = decode_png_to_bgr(png)
    
    # Check for black screen
    black_check = is_mostly_black(png)
    if black_check.get("is_black"):
        return {
            "ok": False,
            "device_serial": device_serial,
            "error": "screenshot_black",
            "stats": black_check,
            "note": "截图几乎全黑；可能是锁屏/安全页面(FLAG_SECURE)/渲染瞬间。",
        }

    # Load template
    tmpl_bgr = cv2.imread(str(tmpl_path), cv2.IMREAD_COLOR)
    if tmpl_bgr is None:
        return {"ok": False, "error": "failed_to_load_template", "template_path": str(tmpl_path)}

    # ROI crop (screen-side) for shared screenshot
    ox, oy = 0, 0
    if roi is not None:
        try:
            rx, ry, rw, rh = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])
            screen_roi = screen_bgr[ry : ry + rh, rx : rx + rw]
            ox, oy = rx, ry
        except Exception:
            screen_roi = screen_bgr
    else:
        screen_roi = screen_bgr

    # Helper functions
    def now_dirname():
        return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def safe_name(s):
        return re.sub(r'[^\w\-_.]', '_', str(s))[:50]
    
    def ensure_abs(p):
        if p in (None, "./.recordings", ".recordings"):
            return recordings_root(None)
        return pathlib.Path(p).expanduser().resolve()

    # 1) Feature matching (SIFT) first
    sift_debug = None
    ok, H, inlier_mask, good_count = False, None, None, 0
    feature_score = None
    if feature_enabled:
        try:
            ok, H, inlier_mask, good_count = _find_icon_with_features_on_bgr(
                tmpl_bgr,
                screen_roi,
                ratio_thresh=float(feature_ratio_thresh),
                min_inliers=int(feature_min_inliers),
            )
            inliers_val = int((inlier_mask.sum() if inlier_mask is not None else 0))
            feature_score = float(inliers_val) / max(1, int(good_count or 0)) if ok else None
            if ok and feature_score is not None and feature_score < float(feature_score_thresh):
                ok, H, inlier_mask = False, None, None
            sift_debug = {
                "ok": bool(ok),
                "inliers": int(inliers_val),
                "good": int(good_count),
                "score": feature_score,
                "score_thresh": float(feature_score_thresh),
                "ratio_thresh": float(feature_ratio_thresh),
                "min_inliers": int(feature_min_inliers),
            }
        except Exception as e:
            ok, H, inlier_mask, good_count = False, None, None, 0
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
                    box_dict = {"x": int(ox + x1), "y": int(oy + y1), "w": int(w), "h": int(h), "label": "sift"}
                    ann_png = annotate_boxes(png, [box_dict])
                    out_path = out_root / "find_image_debug" / f"{ts}_{safe}_sift.png"
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(ann_png)
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
    
    candidates, meta = _match_template_core(
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
                boxes.append({"x": int(c.x), "y": int(c.y), "w": int(c.w), "h": int(c.h), "label": f"{c.score:.3f}"})
            if boxes:
                ann_png = annotate_boxes(png, boxes)
                out_path = out_root / "find_image_debug" / f"{ts}_{safe}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(ann_png)
                annotated_path = str(out_path)
        except Exception as e:
            result["debug_annotate_error"] = str(e)
    if annotated_path:
        result["annotated_path"] = annotated_path
    return result
