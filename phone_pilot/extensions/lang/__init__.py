"""Language utilities (detection/translation)."""

from phone_pilot.extensions.lang.translate import (
    detect_language,
    translate_text,
    translate_candidates,
    get_synonyms,
    record_synonym,
)

__all__ = [
    "detect_language",
    "translate_text",
    "translate_candidates",
    "get_synonyms",
    "record_synonym",
]
