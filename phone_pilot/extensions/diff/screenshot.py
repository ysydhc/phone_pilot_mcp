"""
Screenshot diff and visual regression.

Platform-agnostic: operates on bytes, no device dependencies.
"""

from __future__ import annotations

import pathlib
import datetime
import re
from typing import Optional

import cv2
import numpy as np

from phone_pilot.extensions.vision.template import decode_png_to_bgr, encode_bgr_to_png_bytes
from phone_pilot.core.storage import recordings_root


def _apply_mask(image: np.ndarray, mask_regions: list[dict]) -> np.ndarray:
    """
    Apply mask regions to image (set masked areas to black).
    
    Args:
        image: Input image (numpy array)
        mask_regions: List of mask region dicts [{"x", "y", "w", "h"}]
    
    Returns:
        Masked image
    """
    if not mask_regions:
        return image
    
    masked = image.copy()
    for region in mask_regions:
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        if w > 0 and h > 0:
            masked[y:y+h, x:x+w] = 0
    
    return masked


def _compute_similarity(img1: np.ndarray, img2: np.ndarray) -> dict:
    """
    Compute similarity between two images using multiple methods.
    
    Returns dict with individual scores and combined score.
    """
    # Ensure same size
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    
    # Convert to grayscale
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if len(img1.shape) == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if len(img2.shape) == 3 else img2
    
    # Method 1: Histogram comparison
    hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)
    hist_similarity = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
    
    # Method 2: Pixel-wise comparison
    diff = cv2.absdiff(gray1, gray2)
    threshold = 30  # Allow some tolerance
    similar_pixels = np.sum(diff < threshold)
    total_pixels = diff.size
    pixel_similarity = similar_pixels / total_pixels
    
    # Method 3: Template matching score
    result = cv2.matchTemplate(gray1, gray2, cv2.TM_CCOEFF_NORMED)
    template_similarity = float(np.max(result))
    
    # Combined score (weighted average)
    combined = (
        0.3 * max(0, hist_similarity) +
        0.4 * pixel_similarity +
        0.3 * max(0, template_similarity)
    )
    
    return {
        "histogram_similarity": round(max(0, hist_similarity), 4),
        "pixel_similarity": round(pixel_similarity, 4),
        "template_similarity": round(max(0, template_similarity), 4),
        "combined": round(min(1.0, max(0.0, combined)), 4),
    }


def _compute_diff_image(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """
    Compute visual diff between two images.
    Returns an image highlighting differences in red.
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if len(img1.shape) == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if len(img2.shape) == 3 else img2
    
    diff = cv2.absdiff(gray1, gray2)
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    
    output = img1.copy() if len(img1.shape) == 3 else cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
    output[thresh > 0] = [0, 0, 255]  # BGR red
    
    return output


def compare_screenshots(
    baseline_bytes: bytes,
    current_bytes: bytes,
    *,
    mask_regions: Optional[list[dict]] = None,
    threshold: float = 0.95,
    return_diff_image: bool = False,
) -> dict:
    """
    Compare two screenshots for visual regression.
    
    Args:
        baseline_bytes: Baseline image PNG bytes
        current_bytes: Current image PNG bytes
        mask_regions: List of regions to ignore: [{"x", "y", "w", "h"}]
        threshold: Similarity threshold (0.0-1.0)
        return_diff_image: Include diff image bytes in result
        
    Returns:
        {
            "ok": bool,
            "match": bool,  # True if similarity >= threshold
            "similarity": float,  # Combined similarity score
            "histogram_similarity": float,
            "pixel_similarity": float,
            "template_similarity": float,
            "threshold": float,
            "diff_image": bytes (optional, if return_diff_image=True),
        }
    """
    try:
        baseline_img = decode_png_to_bgr(baseline_bytes)
        current_img = decode_png_to_bgr(current_bytes)
    except Exception as e:
        return {"ok": False, "error": "decode_failed", "detail": str(e), "match": False}
    
    # Apply masks
    mask_regions = mask_regions or []
    baseline_masked = _apply_mask(baseline_img, mask_regions)
    current_masked = _apply_mask(current_img, mask_regions)
    
    # Compute similarity
    similarity_result = _compute_similarity(baseline_masked, current_masked)
    
    combined = similarity_result["combined"]
    match = combined >= threshold
    
    result = {
        "ok": True,
        "match": match,
        "similarity": combined,
        "histogram_similarity": similarity_result["histogram_similarity"],
        "pixel_similarity": similarity_result["pixel_similarity"],
        "template_similarity": similarity_result["template_similarity"],
        "threshold": threshold,
        "mask_regions": mask_regions,
    }
    
    # Add diff image if requested
    if return_diff_image:
        diff_img = _compute_diff_image(baseline_masked, current_masked)
        result["diff_image"] = encode_bgr_to_png_bytes(diff_img)
    
    return result


def compute_diff_image(
    baseline_bytes: bytes,
    current_bytes: bytes,
    *,
    mask_regions: Optional[list[dict]] = None,
) -> bytes:
    """
    Generate visual diff image between two screenshots.
    
    Args:
        baseline_bytes: Baseline image PNG bytes
        current_bytes: Current image PNG bytes
        mask_regions: List of regions to ignore
        
    Returns:
        Diff image PNG bytes with differences highlighted in red
    """
    baseline_img = decode_png_to_bgr(baseline_bytes)
    current_img = decode_png_to_bgr(current_bytes)
    
    mask_regions = mask_regions or []
    baseline_masked = _apply_mask(baseline_img, mask_regions)
    current_masked = _apply_mask(current_img, mask_regions)
    
    diff_img = _compute_diff_image(baseline_masked, current_masked)
    return encode_bgr_to_png_bytes(diff_img)


# -----------------------------------------------------------------------------
# Device-dependent functions (require device_serial)
# -----------------------------------------------------------------------------

def _ensure_abs(p):
    if p in (None, "./.recordings", ".recordings"):
        return recordings_root(None)
    return pathlib.Path(p).expanduser().resolve()


def _now_dirname():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(s):
    return re.sub(r'[^\w\-_.]', '_', str(s))[:50]


def save_baseline_impl(
    device_serial: Optional[str],
    name: str,
    out_dir: str = "./.recordings",
    mask_regions: Optional[list[dict]] = None,
) -> dict:
    """
    Save current screenshot as baseline.
    保存当前截图作为基准。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - name: Baseline name / 基准名称
    - out_dir: Output directory / 输出目录
    - mask_regions: Regions to mask (ignore in future comparisons) / 要遮罩的区域
    
    Returns / 返回:
    - ok: bool
    - baseline_path: str - Path to saved baseline / 基准文件路径
    - name: str - Baseline name / 基准名称
    - mask_regions: list - Applied mask regions / 应用的遮罩区域
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required"}
    if not name:
        return {"ok": False, "error": "name_required"}
    
    # Import here to avoid circular deps
    from phone_pilot.android.adb.screenshot import save_screenshot_png
    from phone_pilot.core.storage import iso_now
    
    out_root = _ensure_abs(out_dir)
    baselines_dir = out_root / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    
    safe = _safe_name(name)
    baseline_path = baselines_dir / f"{safe}.png"
    
    try:
        # Take screenshot
        save_screenshot_png(device_serial, baseline_path)
        
        # If mask regions provided, save a metadata file
        if mask_regions:
            import json
            meta_path = baselines_dir / f"{safe}.json"
            meta = {
                "name": name,
                "baseline_path": str(baseline_path),
                "mask_regions": mask_regions,
                "created_at": iso_now(),
                "device_serial": device_serial,
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        
        return {
            "ok": True,
            "baseline_path": str(baseline_path),
            "name": name,
            "mask_regions": mask_regions or [],
            "device_serial": device_serial,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "save_baseline_failed",
            "detail": str(e),
        }


def compare_screenshots_impl(
    baseline_path: str,
    current_path: Optional[str] = None,
    current_bytes: Optional[bytes] = None,
    mask_regions: Optional[list[dict]] = None,
    save_diff: bool = True,
    out_dir: str = "./.recordings",
) -> dict:
    """
    Compare baseline and current screenshot files/bytes.
    """
    baseline_p = pathlib.Path(baseline_path).expanduser().resolve()
    if not baseline_p.exists():
        return {"ok": False, "error": "baseline_not_found", "path": str(baseline_p)}
    
    try:
        baseline_bytes = baseline_p.read_bytes()
    except Exception as e:
        return {"ok": False, "error": "failed_to_read_baseline", "detail": str(e)}
    
    # Get current bytes
    if current_bytes is None and current_path:
        current_p = pathlib.Path(current_path).expanduser().resolve()
        if not current_p.exists():
            return {"ok": False, "error": "current_not_found", "path": str(current_p)}
        try:
            current_bytes = current_p.read_bytes()
        except Exception as e:
            return {"ok": False, "error": "failed_to_read_current", "detail": str(e)}
    
    if current_bytes is None:
        return {"ok": False, "error": "no_current_image"}
    
    # Compare
    result = compare_screenshots(
        baseline_bytes,
        current_bytes,
        mask_regions=mask_regions,
        return_diff_image=save_diff,
    )
    
    if not result.get("ok"):
        return result
    
    # Save diff if requested
    diff_path = None
    if save_diff and result.get("diff_image"):
        try:
            out_root = _ensure_abs(out_dir)
            ts = _now_dirname()
            diff_dir = out_root / "assertions"
            diff_dir.mkdir(parents=True, exist_ok=True)
            diff_path = str(diff_dir / f"{ts}_diff.png")
            pathlib.Path(diff_path).write_bytes(result["diff_image"])
            del result["diff_image"]  # Don't return bytes
        except Exception as e:
            result["diff_save_error"] = str(e)
    
    result["diff_path"] = diff_path
    return result


def assert_screenshot_match(
    device_serial: str,
    baseline_path: str,
    similarity_threshold: float = 0.95,
    mask_regions: Optional[list[dict]] = None,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./.recordings",
) -> dict:
    """
    Assert current screenshot matches baseline.
    断言当前截图与基准匹配。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - baseline_path: Path to baseline image / 基准图像路径
    - similarity_threshold: Minimum similarity to pass (0.0 to 1.0) / 通过所需的最小相似度
    - mask_regions: Regions to mask before comparison / 比较前要遮罩的区域
    - name: Assertion name / 断言名称
    - save_evidence: Save current screenshot and diff / 保存当前截图和差异图
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether similarity >= threshold / 相似度是否 >= 阈值
    - assertion_type: str
    - similarity: float - Computed similarity / 计算的相似度
    - threshold: float - Required threshold / 所需阈值
    - baseline_path: str
    - current_path: str or None
    - diff_path: str or None
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required", "passed": False}
    if not baseline_path:
        return {"ok": False, "error": "baseline_path_required", "passed": False}
    
    # Import here to avoid circular deps
    from phone_pilot.android.adb.screenshot import save_screenshot_png, take_screenshot_png_bytes
    
    out_root = _ensure_abs(out_dir)
    
    # Take current screenshot
    current_path = None
    current_bytes = None
    if save_evidence:
        ts = _now_dirname()
        safe = _safe_name(name or "screenshot_match")
        evidence_dir = out_root / "assertions"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        current_path = str(evidence_dir / f"{ts}_{safe}_current.png")
        try:
            save_screenshot_png(device_serial, current_path)
        except Exception as e:
            return {
                "ok": False,
                "error": "screenshot_failed",
                "detail": str(e),
                "passed": False,
            }
    else:
        try:
            current_bytes = take_screenshot_png_bytes(device_serial)
        except Exception as e:
            return {
                "ok": False,
                "error": "screenshot_failed",
                "detail": str(e),
                "passed": False,
            }
    
    # Compare screenshots
    compare_result = compare_screenshots_impl(
        baseline_path=baseline_path,
        current_path=current_path,
        current_bytes=current_bytes,
        mask_regions=mask_regions,
        save_diff=save_evidence,
        out_dir=out_dir,
    )
    
    if not compare_result.get("ok"):
        return {
            "ok": False,
            "passed": False,
            "assertion_type": "screenshot_match",
            "error": compare_result.get("error"),
            "detail": compare_result,
        }
    
    similarity = compare_result.get("similarity", 0.0)
    passed = similarity >= similarity_threshold
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "screenshot_match",
        "similarity": similarity,
        "threshold": similarity_threshold,
        "baseline_path": baseline_path,
        "current_path": current_path,
        "diff_path": compare_result.get("diff_path"),
        "mask_regions": mask_regions or [],
        "device_serial": device_serial,
    }
