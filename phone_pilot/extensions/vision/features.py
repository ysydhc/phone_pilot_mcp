"""
Feature matching using SIFT.

Platform-agnostic: operates on bytes, no device dependencies.
"""

from __future__ import annotations

import cv2
import numpy as np

from phone_pilot.extensions.vision.template import decode_png_to_bgr


def match_features_sift(
    screen_bytes: bytes,
    template_bytes: bytes,
    *,
    ratio_thresh: float = 0.7,
    min_inliers: int = 4,
) -> dict:
    """
    SIFT feature matching on image bytes.
    
    Args:
        screen_bytes: Screenshot PNG bytes
        template_bytes: Template image PNG bytes
        ratio_thresh: Lowe's ratio test threshold
        min_inliers: Minimum inliers for valid match
        
    Returns:
        {
            "ok": bool,
            "found": bool,
            "match": {"x", "y", "w", "h", "center_x", "center_y"} or None,
            "inliers": int,
            ...
        }
    """
    try:
        screen_bgr = decode_png_to_bgr(screen_bytes)
        template_bgr = decode_png_to_bgr(template_bytes)
    except Exception as e:
        return {"ok": False, "error": "decode_failed", "detail": str(e), "found": False}

    result = _find_icon_with_features_on_bgr(
        template_bgr,
        screen_bgr,
        ratio_thresh=ratio_thresh,
        min_inliers=min_inliers,
    )
    
    return result


def _find_icon_with_features_on_bgr(
    icon_bgr,
    screen_bgr,
    ratio_thresh: float = 0.7,
    min_inliers: int = 4,
) -> dict:
    """
    SIFT-based feature matching on BGR images.
    
    Returns dict with match info.
    """
    if icon_bgr is None or screen_bgr is None:
        return {"ok": False, "found": False, "error": "invalid_images"}
    
    # Convert to grayscale
    gray_icon = cv2.cvtColor(icon_bgr, cv2.COLOR_BGR2GRAY)
    gray_screen = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)

    # Initialize SIFT
    sift = cv2.SIFT_create()

    # Detect keypoints and compute descriptors
    kp_icon, des_icon = sift.detectAndCompute(gray_icon, None)
    kp_screen, des_screen = sift.detectAndCompute(gray_screen, None)

    if des_icon is None or des_screen is None:
        return {
            "ok": True,
            "found": False,
            "error": "no_features",
            "icon_keypoints": len(kp_icon) if kp_icon else 0,
            "screen_keypoints": len(kp_screen) if kp_screen else 0,
        }

    # FLANN-based matcher
    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    flann = cv2.FlannBasedMatcher(index_params, search_params)

    # KNN matching
    matches = flann.knnMatch(des_icon, des_screen, k=2)

    # Lowe's ratio test
    good_matches = []
    for i, pair in enumerate(matches):
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio_thresh * n.distance:
            good_matches.append(m)

    # Need at least 4 points for homography
    if len(good_matches) < 4:
        return {
            "ok": True,
            "found": False,
            "good_matches": len(good_matches),
            "ratio_thresh": ratio_thresh,
        }

    # Extract matching point coordinates
    src_pts = np.float32([kp_icon[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp_screen[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

    # Compute homography with RANSAC
    H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    
    if H is None:
        return {
            "ok": True,
            "found": False,
            "error": "homography_failed",
            "good_matches": len(good_matches),
        }

    # Count inliers
    inlier_mask = mask.ravel().tolist()
    inliers = sum(1 for x in inlier_mask if x)
    
    if inliers < min_inliers:
        return {
            "ok": True,
            "found": False,
            "inliers": inliers,
            "min_inliers": min_inliers,
            "good_matches": len(good_matches),
        }

    # Project template corners to find bounding box
    th, tw = icon_bgr.shape[:2]
    corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
    proj = cv2.perspectiveTransform(corners, H)
    
    xs = proj[:, 0, 0]
    ys = proj[:, 0, 1]
    x1 = max(0, int(xs.min()))
    y1 = max(0, int(ys.min()))
    x2 = min(screen_bgr.shape[1] - 1, int(xs.max()))
    y2 = min(screen_bgr.shape[0] - 1, int(ys.max()))
    
    if x2 <= x1 or y2 <= y1:
        return {
            "ok": True,
            "found": False,
            "error": "invalid_projection",
            "inliers": inliers,
        }

    w = x2 - x1
    h = y2 - y1
    cx = x1 + w / 2.0
    cy = y1 + h / 2.0
    
    return {
        "ok": True,
        "found": True,
        "match": {
            "x": int(x1),
            "y": int(y1),
            "w": int(w),
            "h": int(h),
            "center_x": int(round(cx)),
            "center_y": int(round(cy)),
            "score": 1.0,
            "scale": 1.0,
        },
        "inliers": inliers,
        "good_matches": len(good_matches),
        "ratio_thresh": ratio_thresh,
    }
