"""Universal project/runtime profile loader (`phone_pilot.pp_profile.v2`).

Provides a strictly business-agnostic contract for injecting project-level
package names, entry activities, route display labels, group labels, media
asset directories, and declarative iOS Simulator `NSUserDefaults` templates
into `phone_pilot` without hardcoding any domain/company identifiers in L1.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Optional

PP_PROFILE_SCHEMA = "phone_pilot.pp_profile.v2"

DEFAULT_PP_PROFILE: dict[str, Any] = {
    "schema": PP_PROFILE_SCHEMA,
    "project_name": "",
    "media_dirs": [],
    "platforms": {
        "android": {
            "package": "",
            "activity": "",
            "mock_port": 8090,
            "env_broadcast": None,
        },
        "ios": {
            "package": "",
            "activity": ".Main",
            "ios_defaults_template": {},
            "app_support_purge_globs": [],
        },
    },
    "route_labels": {},
    "group_labels": {
        "core": "核心流程",
        "adapter": "生态适配",
        "style": "样式渲染",
        "exc": "异常恢复",
        "fraud": "安全风控",
        "cloud_exp": "动态配置矩阵",
        "edge": "边角变体",
        "prod": "生产深水区加固",
        "stability": "稳定性门禁",
    },
    "default_mock_rules": [],
    "verify_contract": {
        "load_tokens": ["Loaded", "Ready", "LOAD_SUCCESS"],
        "show_tokens": ["SHOW_OK", "IMPRESSION", "onAttachedToWindow", "Presenting "],
        "expected_error_tokens": ["LOAD_FAIL"],
    },
}


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_pp_profile(run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Load `pp_profile.json` from `run_dir` or ancestor `.recordings/` / repo roots."""
    candidates: list[Path] = []
    env_p = os.environ.get("PHONE_PILOT_PP_PROFILE", "").strip()
    if env_p:
        candidates.append(Path(env_p).expanduser())
    if run_dir is not None:
        rd = Path(run_dir).expanduser().resolve()
        candidates.extend(
            [
                rd / "pp_profile.json",
                rd.parent / "pp_profile.json",
                rd.parent.parent / "pp_profile.json",
                rd.parent.parent.parent / "tests" / "e2e" / "pp_profile.json",
                rd.parent.parent.parent / "Tests" / "e2e" / "pp_profile.json",
            ]
        )
    cwd = Path.cwd()
    candidates.extend(
        [
            cwd / "pp_profile.json",
            cwd / ".recordings" / "pp_profile.json",
        ]
    )

    seen: set[Path] = set()
    merged = copy.deepcopy(DEFAULT_PP_PROFILE)
    # Merge ancestor fallback first if both ancestor and run_dir exist so run_dir wins
    found_chain: list[dict[str, Any]] = []
    for cand in candidates:
        try:
            rc = cand.resolve()
            if rc in seen or not rc.is_file():
                continue
            seen.add(rc)
            raw = json.loads(rc.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                found_chain.append(raw)
        except Exception:
            continue

    for item in reversed(found_chain):
        merged = _merge_dict(merged, item)
    return merged


def resolve_platform_config(profile: dict[str, Any], platform: str) -> dict[str, Any]:
    plat = (platform or "android").strip().lower()
    platforms = profile.get("platforms") if isinstance(profile, dict) else {}
    if isinstance(platforms, dict) and isinstance(platforms.get(plat), dict):
        return dict(platforms[plat])
    return {}


def resolve_platform_package(
    profile: dict[str, Any],
    platform: str = "android",
    explicit_package: str = "",
) -> str:
    pkg = (explicit_package or "").strip()
    plat = (platform or "android").strip().lower()
    p_cfg = resolve_platform_config(profile, plat)
    prof_pkg = str(p_cfg.get("package") or "").strip()
    if pkg:
        # Guard against accidentally passing an Android package name when querying iOS or vice versa
        other_plat = "android" if plat == "ios" else "ios"
        other_pkg = str(resolve_platform_config(profile, other_plat).get("package") or "").strip()
        if other_pkg and pkg == other_pkg and prof_pkg:
            return prof_pkg
        return pkg
    return prof_pkg


def render_ios_defaults_template(template: Any, *, mock_port: int) -> Any:
    """Recursively interpolate `{mock_host}`, `{mock_port}`, and `__UTC_NOW__` in plist templates."""
    mock_host = f"http://127.0.0.1:{int(mock_port)}"
    now_utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def _walk(val: Any) -> Any:
        if isinstance(val, str):
            if val == "__UTC_NOW__":
                return now_utc
            return val.replace("{mock_host}", mock_host).replace("{mock_port}", str(int(mock_port)))
        if isinstance(val, list):
            return [_walk(x) for x in val]
        if isinstance(val, dict):
            return {str(k): _walk(v) for k, v in val.items()}
        return val

    if not isinstance(template, dict) or not template:
        return {}
    return _walk(template)
