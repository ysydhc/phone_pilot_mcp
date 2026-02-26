"""
Vision extension - Image processing capabilities.

Contains:
- template.py: Template matching (OpenCV)
- features.py: Feature matching (SIFT)
- annotate.py: Image annotation utilities
- crop.py: Image cropping utilities

All functions operate on bytes, with no platform dependencies.
"""

from phone_pilot.extensions.vision.template import (
    match_template,
    decode_png_to_bgr,
    encode_bgr_to_png_bytes,
    MatchBox,
)
from phone_pilot.extensions.vision.features import match_features_sift
from phone_pilot.extensions.vision.annotate import annotate_boxes, is_mostly_black, Box
from phone_pilot.extensions.vision.crop import crop_image_file, crop_image_bytes
from phone_pilot.extensions.vision.markup import extract_red_box_crop

__all__ = [
    # Template matching
    "match_template",
    "decode_png_to_bgr",
    "encode_bgr_to_png_bytes",
    "MatchBox",
    # Feature matching
    "match_features_sift",
    # Annotation
    "annotate_boxes",
    "is_mostly_black",
    "Box",
    # Crop
    "crop_image_file",
    "crop_image_bytes",
    # Markup
    "extract_red_box_crop",
]
