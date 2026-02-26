"""
Unified Skills API - Combines Driver and Extension capabilities.

The Skills layer provides a unified high-level API that:
- Wraps Driver methods for basic operations
- Combines Driver + Extension for advanced operations (e.g., find_image_on_screen)
- Provides intelligent fallback strategies (UIAutomator → OCR → Image matching)
"""

from __future__ import annotations

import pathlib
from typing import Optional

from phone_pilot.core.protocols import DeviceDriver
from phone_pilot.core.resource import resolve_or_cache_path
from phone_pilot.extensions.vision import template, features, annotate
from phone_pilot.extensions.ocr import tesseract
from phone_pilot.extensions.diff import screenshot as diff_module


class DeviceSkills:
    """
    Unified Skills API that combines Driver (platform-specific) 
    and Extension (platform-agnostic) capabilities.
    
    Usage:
        driver = AndroidDriver("device_serial")
        skills = DeviceSkills(driver)
        
        # Basic operations (via Driver)
        skills.tap(100, 200)
        
        # Advanced operations (Driver + Extension)
        result = skills.find_image_on_screen("template.png")
        result = skills.find_text_on_screen("Settings")
    """
    
    def __init__(self, driver: DeviceDriver):
        self.driver = driver
    
    # =========================================================================
    # Basic Operations (direct Driver calls)
    # =========================================================================
    
    def tap(self, x: int, y: int, wait_s: float = 0.15) -> dict:
        """Tap at screen coordinates."""
        return self.driver.input.tap(x, y, wait_s=wait_s)
    
    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        """Swipe from (x1, y1) to (x2, y2)."""
        return self.driver.input.swipe(x1, y1, x2, y2, duration_ms=duration_ms)
    
    def long_press(self, x: int, y: int, duration_ms: int = 500) -> dict:
        """Long press at coordinates."""
        return self.driver.input.long_press(x, y, duration_ms=duration_ms)
    
    def input_text(self, text: str, enter: bool = False) -> dict:
        """Input text into focused field."""
        return self.driver.input.input_text(text, enter=enter)
    
    def keyevent(self, keycode: str) -> dict:
        """Send a key event."""
        return self.driver.input.keyevent(keycode)
    
    def screenshot(self) -> bytes:
        """Capture current screen as PNG bytes."""
        return self.driver.screen.screenshot()
    
    def get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions."""
        return self.driver.screen.get_screen_size()
    
    def launch_app(self, package: str, activity: Optional[str] = None) -> dict:
        """Launch an application."""
        return self.driver.app.launch_app(package, activity)
    
    def force_stop(self, package: str) -> dict:
        """Force stop an application."""
        return self.driver.app.force_stop(package)
    
    def get_current_activity(self) -> dict:
        """Get current foreground activity info."""
        return self.driver.ui.get_current_activity()
    
    # =========================================================================
    # Advanced Operations (Driver + Extension)
    # =========================================================================
    
    def find_image_on_screen(
        self,
        template_path: str,
        *,
        threshold: float = 0.85,
        grayscale: bool = True,
        roi: Optional[tuple[int, int, int, int]] = None,
        scales: Optional[list[float]] = None,
        method: str = "ccoeff_normed",
        max_results: int = 5,
    ) -> dict:
        """
        Find template image on screen.
        
        Uses Driver to capture screenshot, Extension to match template.
        
        Args:
            template_path: Path to template image
            threshold: Match threshold (0.0-1.0)
            grayscale: Convert to grayscale before matching
            roi: Region of interest (x, y, w, h)
            scales: List of scales for multi-scale matching
            method: Matching method
            max_results: Maximum matches to return
            
        Returns:
            {
                "ok": bool,
                "count": int,
                "matches": [{"x", "y", "w", "h", "score", "center_x", "center_y"}],
                ...
            }
        """
        template_path = resolve_or_cache_path(template_path)
        screen_bytes = self.driver.screen.screenshot()
        template_bytes = pathlib.Path(template_path).read_bytes()
        
        return template.match_template(
            screen_bytes,
            template_bytes,
            threshold=threshold,
            grayscale=grayscale,
            roi=roi,
            scales=scales,
            method=method,
            max_results=max_results,
        )
    
    def find_image_sift(
        self,
        template_path: str,
        *,
        ratio_thresh: float = 0.7,
        min_inliers: int = 4,
    ) -> dict:
        """
        Find template image using SIFT feature matching.
        
        Better for matching when template may be rotated or scaled.
        
        Args:
            template_path: Path to template image
            ratio_thresh: Lowe's ratio test threshold
            min_inliers: Minimum inliers for valid match
            
        Returns:
            {"ok": bool, "found": bool, "match": {...} or None, ...}
        """
        template_path = resolve_or_cache_path(template_path)
        screen_bytes = self.driver.screen.screenshot()
        template_bytes = pathlib.Path(template_path).read_bytes()
        
        return features.match_features_sift(
            screen_bytes,
            template_bytes,
            ratio_thresh=ratio_thresh,
            min_inliers=min_inliers,
        )
    
    def find_text_on_screen(
        self,
        query: str,
        *,
        lang: str = "eng",
        psm: int = 6,
        exact: bool = False,
        case_sensitive: bool = False,
        limit: int = 10,
    ) -> dict:
        """
        Find text on screen using OCR.
        
        Uses Driver to capture screenshot, Extension to run OCR.
        
        Args:
            query: Text to search for
            lang: Tesseract language code
            psm: Page segmentation mode
            exact: Require exact match
            case_sensitive: Case-sensitive matching
            limit: Maximum results
            
        Returns:
            {
                "ok": bool,
                "boxes_count": int,
                "matches_count": int,
                "matches": [{"text", "x", "y", "w", "h", "center_x", "center_y"}],
            }
        """
        screen_bytes = self.driver.screen.screenshot()
        
        return tesseract.ocr_and_find(
            screen_bytes,
            query,
            lang=lang,
            psm=psm,
            exact=exact,
            case_sensitive=case_sensitive,
            limit=limit,
        )
    
    def compare_screenshot(
        self,
        baseline_bytes: bytes,
        *,
        mask_regions: Optional[list[dict]] = None,
        threshold: float = 0.95,
    ) -> dict:
        """
        Compare current screen with a baseline screenshot.
        
        Args:
            baseline_bytes: Baseline image PNG bytes
            mask_regions: Regions to ignore [{"x", "y", "w", "h"}]
            threshold: Similarity threshold
            
        Returns:
            {"ok": bool, "match": bool, "similarity": float, ...}
        """
        current_bytes = self.driver.screen.screenshot()
        
        return diff_module.compare_screenshots(
            baseline_bytes,
            current_bytes,
            mask_regions=mask_regions,
            threshold=threshold,
        )
    
    def is_screen_black(self) -> dict:
        """
        Check if screen is mostly black (e.g., locked/off).
        
        Returns:
            {"is_black": bool, "mean_gray": float, "dark_ratio": float}
        """
        screen_bytes = self.driver.screen.screenshot()
        return annotate.is_mostly_black(screen_bytes)
