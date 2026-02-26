"""
OCR extension - Text recognition capabilities.

Contains:
- tesseract.py: Tesseract OCR integration (CLI-based)

All functions operate on bytes, with no platform dependencies.
Requires tesseract CLI to be installed on host.
"""

from phone_pilot.extensions.ocr.tesseract import (
    ocr_image,
    find_text,
    ocr_and_find,
    OCRBox,
)

__all__ = [
    "ocr_image",
    "find_text",
    "ocr_and_find",
    "OCRBox",
]
