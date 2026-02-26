"""
Lightweight language detection and translation helpers.

Designed for best-effort fallback; failures must not break main flow.
"""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Iterable, Optional

from phone_pilot.extensions.cache.sqlite import get_synonyms as _get_synonyms_db
from phone_pilot.extensions.cache.sqlite import record_synonym as _record_synonym_db


DEFAULT_TARGET_LANGS: list[str] = ["zh", "en", "ja", "ko"]
_DISABLE_TRANSLATION_ENV = "PHONE_PILOT_DISABLE_TRANSLATION"


@lru_cache(maxsize=512)
def _detect_lang_cached(text: str) -> Optional[str]:
    try:
        from langdetect import detect
    except ImportError:
        # langdetect is an optional dependency: pip install phone-pilot[vision]
        return None
    try:
        return detect(text)
    except Exception:
        return None


def detect_language(text: str) -> Optional[str]:
    """Detect language code for text (best-effort)."""
    s = (text or "").strip()
    if not s:
        return None
    return _detect_lang_cached(s)


@lru_cache(maxsize=1024)
def _translate_cached(text: str, target_lang: str) -> Optional[str]:
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        # deep-translator is an optional dependency: pip install phone-pilot[vision]
        return None
    try:
        translator = GoogleTranslator(source="auto", target=str(target_lang))
        out = translator.translate(text)
        return (out or "").strip() or None
    except Exception:
        return None


def translate_text(text: str, target_lang: str) -> Optional[str]:
    """Translate text to target language (best-effort)."""
    s = (text or "").strip()
    if not s:
        return None
    if not target_lang:
        return None
    return _translate_cached(s, str(target_lang))


def translate_candidates(
    text: str,
    *,
    target_langs: Optional[Iterable[str]] = None,
) -> list[str]:
    """
    Return translation candidates in preferred order.

    Always includes the original text as the first candidate.
    """
    s = (text or "").strip()
    if not s:
        return []

    langs = list(target_langs or DEFAULT_TARGET_LANGS)
    # Always keep original query first.
    candidates: list[str] = [s]

    # Add learned synonyms from DB (best-effort).
    try:
        for syn in get_synonyms(s):
            if syn and syn not in candidates:
                candidates.append(syn)
    except Exception:
        pass

    # Avoid translating into the same language if detected.
    src = detect_language(s)
    if os.getenv(_DISABLE_TRANSLATION_ENV):
        return candidates

    for lang in langs:
        if not lang:
            continue
        if src and lang.lower() == src.lower():
            continue
        t = translate_text(s, lang)
        if t and t not in candidates:
            candidates.append(t)

    return candidates


def get_synonyms(text: str) -> list[str]:
    s = (text or "").strip()
    if not s:
        return []
    return _get_synonyms_db(s)


def record_synonym(query: str, synonym: str, *, source: str = "auto") -> None:
    _record_synonym_db(query, synonym, source=source)
