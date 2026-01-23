#!/usr/bin/env python3
"""
Screenshot Diff - Screenshot comparison utilities.
截图对比 - 截图比较工具。

This module provides screenshot comparison for visual testing.
本模块提供用于视觉测试的截图比较功能。

Key functions / 关键函数:
- save_baseline: Save current screenshot as baseline / 保存当前截图作为基准
- compare_screenshots: Compare two screenshots / 比较两张截图
- assert_screenshot_match: Assert current screenshot matches baseline / 断言当前截图与基准匹配
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any, Optional

import cv2
import numpy as np

from android_tool.recordings_store import ensure_abs, iso_now, now_dirname, safe_name
from android_tool.screenshot import save_screenshot_png, take_screenshot_png_bytes


def _apply_mask(image: np.ndarray, mask_regions: list[dict]) -> np.ndarray:
    """
    Apply mask regions to image (set masked areas to black).
    将遮罩区域应用到图像（将遮罩区域设为黑色）。
    
    Args / 参数:
    - image: Input image (numpy array) / 输入图像
    - mask_regions: List of mask region dicts / 遮罩区域列表
        Each region: {"x": int, "y": int, "w": int, "h": int}
    
    Returns / 返回:
    - Masked image / 遮罩后的图像
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
            masked[y:y+h, x:x+w] = 0  # Set to black
    
    return masked


def _compute_similarity(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    Compute similarity between two images using multiple methods.
    使用多种方法计算两张图像之间的相似度。
    
    Uses a combination of:
    - Structural similarity (histogram comparison)
    - Pixel-wise comparison
    
    Args / 参数:
    - img1, img2: Images to compare (BGR format) / 要比较的图像
    
    Returns / 返回:
    - Similarity score (0.0 to 1.0) / 相似度分数
    """
    # Ensure same size
    if img1.shape != img2.shape:
        # Resize img2 to match img1
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    
    # Convert to grayscale for comparison
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if len(img1.shape) == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if len(img2.shape) == 3 else img2
    
    # Method 1: Histogram comparison
    hist1 = cv2.calcHist([gray1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([gray2], [0], None, [256], [0, 256])
    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)
    hist_similarity = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
    
    # Method 2: Pixel-wise comparison (normalized)
    diff = cv2.absdiff(gray1, gray2)
    # Calculate percentage of pixels that are similar (within threshold)
    threshold = 30  # Allow some tolerance for minor differences
    similar_pixels = np.sum(diff < threshold)
    total_pixels = diff.size
    pixel_similarity = similar_pixels / total_pixels
    
    # Method 3: Template matching score
    result = cv2.matchTemplate(gray1, gray2, cv2.TM_CCOEFF_NORMED)
    template_similarity = float(np.max(result))
    
    # Combine methods (weighted average)
    # Give more weight to pixel similarity as it's more intuitive
    combined_similarity = (
        0.3 * max(0, hist_similarity) +  # Histogram can be negative, clamp to 0
        0.4 * pixel_similarity +
        0.3 * max(0, template_similarity)
    )
    
    return min(1.0, max(0.0, combined_similarity))


def _compute_diff_image(img1: np.ndarray, img2: np.ndarray) -> np.ndarray:
    """
    Compute visual diff between two images.
    计算两张图像之间的视觉差异。
    
    Returns an image highlighting differences in red.
    返回一张用红色突出显示差异的图像。
    """
    # Ensure same size
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    
    # Convert to grayscale
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY) if len(img1.shape) == 3 else img1
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY) if len(img2.shape) == 3 else img2
    
    # Compute difference
    diff = cv2.absdiff(gray1, gray2)
    
    # Threshold to find significant differences
    _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
    
    # Create output image (copy of img1)
    output = img1.copy() if len(img1.shape) == 3 else cv2.cvtColor(img1, cv2.COLOR_GRAY2BGR)
    
    # Highlight differences in red
    output[thresh > 0] = [0, 0, 255]  # BGR red
    
    return output


def save_baseline_impl(
    device_serial: Optional[str],
    name: str,
    out_dir: str = "./recordings",
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
        List of dicts: [{"x": int, "y": int, "w": int, "h": int}, ...]
    
    Returns / 返回:
    - ok: bool
    - baseline_path: str - Path to saved baseline / 基准文件路径
    - name: str - Baseline name / 基准名称
    - mask_regions: list - Applied mask regions / 应用的遮罩区域
    
    Example / 示例:
    ```python
    # Save baseline for login screen
    save_baseline_impl(serial, name="login_screen")
    
    # Save baseline with masked dynamic areas
    save_baseline_impl(serial, name="dashboard", mask_regions=[
        {"x": 10, "y": 10, "w": 100, "h": 30},  # Ignore timestamp area
        {"x": 200, "y": 50, "w": 150, "h": 40}, # Ignore user name area
    ])
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required"}
    if not name:
        return {"ok": False, "error": "name_required"}
    
    out_root = ensure_abs(out_dir)
    baselines_dir = out_root / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    
    safe = safe_name(name)
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
    out_dir: str = "./recordings",
) -> dict:
    """
    Compare two screenshots and compute similarity.
    比较两张截图并计算相似度。
    
    Args / 参数:
    - baseline_path: Path to baseline image / 基准图像路径
    - current_path: Path to current image (or use current_bytes) / 当前图像路径
    - current_bytes: Current image as bytes (alternative to path) / 当前图像字节
    - mask_regions: Regions to mask before comparison / 比较前要遮罩的区域
    - save_diff: Save diff image / 保存差异图像
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - similarity: float - Similarity score (0.0 to 1.0) / 相似度分数
    - diff_path: str or None - Path to diff image / 差异图像路径
    """
    # Load baseline
    baseline_file = pathlib.Path(baseline_path)
    if not baseline_file.exists():
        return {"ok": False, "error": "baseline_not_found", "baseline_path": baseline_path}
    
    try:
        baseline_img = cv2.imread(str(baseline_file))
        if baseline_img is None:
            return {"ok": False, "error": "baseline_load_failed"}
    except Exception as e:
        return {"ok": False, "error": "baseline_load_failed", "detail": str(e)}
    
    # Load current image
    try:
        if current_bytes:
            nparr = np.frombuffer(current_bytes, np.uint8)
            current_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif current_path:
            current_img = cv2.imread(current_path)
        else:
            return {"ok": False, "error": "current_image_required"}
        
        if current_img is None:
            return {"ok": False, "error": "current_load_failed"}
    except Exception as e:
        return {"ok": False, "error": "current_load_failed", "detail": str(e)}
    
    # Load mask regions from metadata if available
    if mask_regions is None:
        meta_path = baseline_file.with_suffix(".json")
        if meta_path.exists():
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                mask_regions = meta.get("mask_regions", [])
            except Exception:
                mask_regions = []
        else:
            mask_regions = []
    
    # Apply masks
    baseline_masked = _apply_mask(baseline_img, mask_regions)
    current_masked = _apply_mask(current_img, mask_regions)
    
    # Compute similarity
    similarity = _compute_similarity(baseline_masked, current_masked)
    
    # Save diff image if requested
    diff_path = None
    if save_diff:
        try:
            out_root = ensure_abs(out_dir)
            diff_dir = out_root / "screenshot_diffs"
            diff_dir.mkdir(parents=True, exist_ok=True)
            
            ts = now_dirname()
            baseline_name = baseline_file.stem
            diff_path = diff_dir / f"{ts}_{baseline_name}_diff.png"
            
            diff_img = _compute_diff_image(baseline_masked, current_masked)
            cv2.imwrite(str(diff_path), diff_img)
            diff_path = str(diff_path)
        except Exception:
            diff_path = None
    
    return {
        "ok": True,
        "similarity": round(similarity, 4),
        "baseline_path": str(baseline_path),
        "current_path": current_path,
        "mask_regions": mask_regions,
        "diff_path": diff_path,
    }


def assert_screenshot_match(
    device_serial: str,
    baseline_path: str,
    similarity_threshold: float = 0.95,
    mask_regions: Optional[list[dict]] = None,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
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
    - evidence_path: str or None
    
    Example / 示例:
    ```python
    # Assert login screen matches baseline
    result = assert_screenshot_match(serial, baseline_path="recordings/baselines/login_screen.png")
    
    # Assert with lower threshold
    result = assert_screenshot_match(serial, baseline_path="path/to/baseline.png", similarity_threshold=0.90)
    
    # Assert with masked dynamic areas
    result = assert_screenshot_match(
        serial, 
        baseline_path="path/to/baseline.png",
        mask_regions=[{"x": 10, "y": 10, "w": 100, "h": 30}]
    )
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required", "passed": False}
    if not baseline_path:
        return {"ok": False, "error": "baseline_path_required", "passed": False}
    
    out_root = ensure_abs(out_dir)
    
    # Take current screenshot
    current_path = None
    if save_evidence:
        ts = now_dirname()
        safe = safe_name(name or "screenshot_match")
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
    
    # Get current screenshot bytes if not saving to file
    current_bytes = None
    if not save_evidence:
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


async def save_baseline_async(
    device_serial: Optional[str],
    name: str,
    out_dir: str = "./recordings",
    mask_regions: Optional[list[dict]] = None,
) -> dict:
    """Async wrapper for save_baseline_impl."""
    return await asyncio.to_thread(
        save_baseline_impl,
        device_serial,
        name,
        out_dir,
        mask_regions,
    )


async def assert_screenshot_match_async(
    device_serial: str,
    baseline_path: str,
    similarity_threshold: float = 0.95,
    mask_regions: Optional[list[dict]] = None,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """Async wrapper for assert_screenshot_match."""
    return await asyncio.to_thread(
        assert_screenshot_match,
        device_serial,
        baseline_path,
        similarity_threshold,
        mask_regions,
        name,
        save_evidence,
        out_dir,
    )
