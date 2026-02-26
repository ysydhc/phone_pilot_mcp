from __future__ import annotations

import os

DEFAULT_OCR_LANG = "eng+chi_sim"
MULTI_OCR_LANG = "eng+chi_sim+chi_tra+jpn+msa+ind+vie+ara"

_LABEL_TO_LANG: dict[str, str] = {
    "中文": "chi_sim",
    "简体中文": "chi_sim",
    "简中": "chi_sim",
    "简体": "chi_sim",
    "繁体中文": "chi_tra",
    "繁中": "chi_tra",
    "繁体": "chi_tra",
    "日文": "jpn",
    "日语": "jpn",
    "英文": "eng",
    "英语": "eng",
    "马来文": "msa",
    "马来语": "msa",
    "印度尼西亚文": "ind",
    "印尼文": "ind",
    "越南文": "vie",
    "阿拉伯文": "ara",
    "中英混合": "eng+chi_sim",
    "多语种": MULTI_OCR_LANG,
    "多语言": MULTI_OCR_LANG,
    "全语种": MULTI_OCR_LANG,
    "国际化": MULTI_OCR_LANG,
}

_LANG_TO_LABEL: dict[str, str] = {
    "chi_sim": "简体中文",
    "chi_tra": "繁体中文",
    "jpn": "日文",
    "eng": "英文",
    "msa": "马来文",
    "ind": "印度尼西亚文",
    "vie": "越南文",
    "ara": "阿拉伯文",
    "eng+chi_sim": "中英混合",
    MULTI_OCR_LANG: "多语种",
}


def get_default_ocr_lang() -> str:
    env = os.getenv("PHONE_PILOT_OCR_LANG") or os.getenv("PHONE_PILOT_OCR_LANG_DEFAULT")
    if env and env.strip():
        return env.strip()
    return DEFAULT_OCR_LANG


def resolve_ocr_lang_label(label: str | None) -> str:
    if not label:
        return get_default_ocr_lang()
    key = label.strip()
    if not key:
        return get_default_ocr_lang()
    mapped = _LABEL_TO_LANG.get(key)
    if mapped:
        return mapped
    return key


def ocr_lang_label(lang: str | None) -> str:
    if not lang:
        return ""
    return _LANG_TO_LABEL.get(str(lang), str(lang))


def normalize_ocr_lang(lang: str | None) -> str:
    if not lang:
        return get_default_ocr_lang()
    return resolve_ocr_lang_label(lang)


__all__ = [
    "DEFAULT_OCR_LANG",
    "MULTI_OCR_LANG",
    "get_default_ocr_lang",
    "resolve_ocr_lang_label",
    "ocr_lang_label",
    "normalize_ocr_lang",
]
