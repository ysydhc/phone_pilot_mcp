"""Static L1 Zero-Business-Coupling Gate & `pp_profile.v2` Contract Unit Tests."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from phone_pilot.core.pp_profile import (
    PP_PROFILE_SCHEMA,
    load_pp_profile,
    render_ios_defaults_template,
    resolve_platform_package,
)
from phone_pilot.core.scene_restore import _action_label

FORBIDDEN_BUSINESS_RE = re.compile(
    r"(?:\b(?:adx|admtest|admaster|baidujp|mediago|houyi|s_vld|topon|tradplus|app_ad_rev|SHOW_CALLED)\b"
    r"|\badm_[a-z0-9_]+"
    r"|\bonAd[A-Z][A-Za-z0-9_]*"
    r"|\bdemo_(?:show|load)[a-z0-9_]*"
    r"|/sdk/(?:mock/)?bid"
    r"|cc\.admaster|com\.baidujp|admasterdemo://|admtest://|10009000)",
    re.IGNORECASE,
)


def test_phone_pilot_l1_zero_business_coupling():
    """R1-TC-01: Assert zero project/domain business coupling across all L1 source & templates."""
    pkg_root = Path(__file__).resolve().parents[1] / "phone_pilot"
    assert pkg_root.is_dir()

    violations: list[str] = []
    for ext in ("*.py", "*.j2"):
        for fp in sorted(pkg_root.rglob(ext)):
            if "__pycache__" in fp.parts:
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                m = FORBIDDEN_BUSINESS_RE.search(line)
                if m:
                    rel = fp.relative_to(pkg_root)
                    violations.append(f"{rel}:{lineno}: matched '{m.group(0)}' -> {line.strip()[:120]}")

    assert violations == [], "L1 zero-business-coupling violated:\n" + "\n".join(violations[:25])


def test_pp_profile_ancestor_fallback_and_override(tmp_path: Path):
    """R1-TC-02a: Ancestor `.recordings/pp_profile.json` applies to legacy runs, while `run_dir` overrides."""
    rec_dir = tmp_path / ".recordings"
    run_legacy = rec_dir / "runs" / "legacy_run_001"
    run_custom = rec_dir / "runs" / "custom_run_002"
    run_legacy.mkdir(parents=True)
    run_custom.mkdir(parents=True)

    ancestor_prof = {
        "schema": PP_PROFILE_SCHEMA,
        "project_name": "sample-mobile-sdk",
        "platforms": {
            "android": {"package": "com.vendor.demo.android", "activity": ".HomeActivity"},
            "ios": {"package": "com.vendor.demo.ios"},
        },
        "route_labels": {"checkout": "收银台测试页 (checkout)"},
    }
    (rec_dir / "pp_profile.json").write_text(json.dumps(ancestor_prof), encoding="utf-8")

    # Legacy run without its own pp_profile.json inherits from .recordings/pp_profile.json
    loaded_legacy = load_pp_profile(run_legacy)
    assert loaded_legacy["project_name"] == "sample-mobile-sdk"
    assert resolve_platform_package(loaded_legacy, "android") == "com.vendor.demo.android"
    assert resolve_platform_package(loaded_legacy, "ios", "com.vendor.demo.android") == "com.vendor.demo.ios"

    # Run-level pp_profile.json overrides specific fields cleanly
    (run_custom / "pp_profile.json").write_text(
        json.dumps({"platforms": {"android": {"package": "com.vendor.demo.override"}}}),
        encoding="utf-8",
    )
    loaded_custom = load_pp_profile(run_custom)
    assert resolve_platform_package(loaded_custom, "android") == "com.vendor.demo.override"
    assert resolve_platform_package(loaded_custom, "ios") == "com.vendor.demo.ios"

    title, _ = _action_label(
        {"type": "open_url", "url": "myapp://open?type=checkout"},
        route_labels=loaded_legacy.get("route_labels"),
    )
    assert "收银台测试页 (checkout)" in title


def test_render_ios_defaults_template_interpolation():
    """R1-TC-02b: Verify `{mock_host}`, `{mock_port}`, and `__UTC_NOW__` recursive rendering."""
    tpl = {
        "mock_endpoint": "{mock_host}/v1/config",
        "port_str": "{mock_port}",
        "updated_at": "__UTC_NOW__",
        "nested": [{"url": "{mock_host}/mock", "enabled": True}],
    }
    rendered = render_ios_defaults_template(tpl, mock_port=18686)
    assert rendered["mock_endpoint"] == "http://127.0.0.1:18686/v1/config"
    assert rendered["port_str"] == "18686"
    assert isinstance(rendered["updated_at"], dt.datetime)
    assert rendered["nested"][0]["url"] == "http://127.0.0.1:18686/mock"
    assert rendered["nested"][0]["enabled"] is True
