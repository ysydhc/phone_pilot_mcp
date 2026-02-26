"""
Diff extension - Screenshot comparison capabilities.

Contains:
- screenshot.py: Screenshot diff and visual regression

All functions operate on bytes, with no platform dependencies.
"""

from phone_pilot.extensions.diff.screenshot import (
    compare_screenshots,
    compute_diff_image,
)

__all__ = [
    "compare_screenshots",
    "compute_diff_image",
]
