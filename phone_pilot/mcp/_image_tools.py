"""Image match, OCR, and screenshot comparison MCP tools for phone_pilot."""

from __future__ import annotations

import asyncio
import pathlib

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_skills
from phone_pilot.core.resource import resolve_or_cache_path


# =============================================================================
# Image Tools
# =============================================================================


def _phone_find_image_sync(
    device_serial: str,
    template_path: str,
    platform: str,
    threshold: float,
    grayscale: bool,
    method: str,
    max_results: int,
) -> dict:
    """Sync body for phone_find_image (run in thread to avoid blocking event loop)."""
    if not (0.0 <= threshold <= 1.0):
        return {"ok": False, "error": f"threshold must be 0.0-1.0, got {threshold}"}
    try:
        try:
            template_path = resolve_or_cache_path(template_path)
        except Exception as e:
            return {
                "ok": False,
                "error": "resource_not_found",
                "detail": str(e),
                "template_path": template_path,
            }
        skills = get_skills(device_serial, platform)
        result = skills.find_image_on_screen(
            template_path,
            threshold=threshold,
            grayscale=grayscale,
            method=method,
            max_results=max_results,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["template_path"] = template_path
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_find_image(
    device_serial: str,
    template_path: str,
    platform: str = "android",
    threshold: float = 0.85,
    grayscale: bool = True,
    method: str = "auto",
    max_results: int = 5,
) -> dict:
    """在屏幕上通过图像匹配查找模板位置。
    Find template image on screen via template matching.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        template_path: 模板图片路径，支持 @res:key 引用 / Template path or @res:key
        platform: 平台类型 / Platform type
        threshold: 匹配阈值 0.0-1.0，默认 0.85 / Match threshold, default 0.85
        grayscale: 是否转灰度，默认 True / Convert to grayscale, default True
        method: 匹配方法 auto/ccoeff_normed/edge 等 / Matching method
        max_results: 最大返回结果数，默认 5 / Max matches returned, default 5

    Returns / 返回值:
        dict: {"ok": True, "count", "matches": [{x, y, w, h, score, center_x, center_y}]}
        或 {"ok": False, "error": str}；resource_not_found 表示模板路径无效

    Notes / 特殊逻辑:
        - template_path 可为本地路径或 @res:key（需先用 phone_res_add 缓存）
    """
    return await asyncio.to_thread(
        _phone_find_image_sync,
        device_serial,
        template_path,
        platform,
        threshold,
        grayscale,
        method,
        max_results,
    )


# =============================================================================
# OCR Tools
# =============================================================================


def _phone_ocr_find_sync(
    device_serial: str,
    query: str,
    platform: str,
    lang: str,
    exact: bool,
    case_sensitive: bool,
    limit: int,
) -> dict:
    """Sync body for phone_ocr_find (run in thread to avoid blocking event loop)."""
    try:
        skills = get_skills(device_serial, platform)
        result = skills.find_text_on_screen(
            query,
            lang=lang,
            exact=exact,
            case_sensitive=case_sensitive,
            limit=limit,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["query"] = query
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_ocr_find(
    device_serial: str,
    query: str,
    platform: str = "android",
    lang: str = "eng",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> dict:
    """使用 OCR 在屏幕上查找文本。
    Find text on screen using OCR (Tesseract).

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        query: 要搜索的文本 / Text to search for
        platform: 平台类型 / Platform type
        lang: Tesseract 语言代码，如 eng、chi_sim / Tesseract lang code
        exact: 是否精确匹配 / Require exact match
        case_sensitive: 是否区分大小写 / Case-sensitive matching
        limit: 最大返回匹配数，默认 10 / Max matches, default 10

    Returns / 返回值:
        dict: {"ok": True, "boxes_count", "matches_count", "matches": [{text, x, y, w, h, center_x, center_y}]}
        或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_ocr_find_sync,
        device_serial,
        query,
        platform,
        lang,
        exact,
        case_sensitive,
        limit,
    )


# =============================================================================
# Screenshot Comparison Tools
# =============================================================================


def _phone_compare_screenshot_sync(
    device_serial: str,
    baseline_path: str,
    platform: str,
    threshold: float,
) -> dict:
    """Sync body for phone_compare_screenshot (run in thread to avoid blocking event loop)."""
    if not (0.0 <= threshold <= 1.0):
        return {"ok": False, "error": f"threshold must be 0.0-1.0, got {threshold}"}
    try:
        try:
            baseline_path = resolve_or_cache_path(baseline_path)
        except Exception as e:
            return {
                "ok": False,
                "error": "resource_not_found",
                "detail": str(e),
                "baseline_path": baseline_path,
            }
        skills = get_skills(device_serial, platform)
        baseline_bytes = pathlib.Path(baseline_path).read_bytes()

        result = skills.compare_screenshot(
            baseline_bytes,
            threshold=threshold,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["baseline_path"] = baseline_path
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_compare_screenshot(
    device_serial: str,
    baseline_path: str,
    platform: str = "android",
    threshold: float = 0.95,
) -> dict:
    """将当前屏幕与基准截图进行相似度比较。
    Compare current screen with a baseline screenshot.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        baseline_path: 基准图片路径，支持 @res:key / Baseline path or @res:key
        platform: 平台类型 / Platform type
        threshold: 相似度阈值 0.0-1.0，默认 0.95 / Similarity threshold, default 0.95

    Returns / 返回值:
        dict: {"ok": True, "match": bool, "similarity": float, "threshold"}
        或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_compare_screenshot_sync,
        device_serial,
        baseline_path,
        platform,
        threshold,
    )
