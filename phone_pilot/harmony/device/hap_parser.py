#!/usr/bin/env python3
"""
Harmony Ability Package (HAP) metadata and icon parser.

Extracts bundleName, abilities, versions, and icons from HAP zip packages
(both Stage model `module.json` and legacy FA model `config.json`).
Persists package metadata and launch profiles into the device database.
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import re
from typing import BinaryIO, Optional, Union
import zipfile

logger = logging.getLogger(__name__)

_ICON_EXTENSIONS = (".png", ".svg", ".webp", ".jpg", ".jpeg")


class HapParseError(Exception):
    """Raised when parsing a Harmony Ability Package (HAP) fails."""

    pass


def parse_hap(hap_path: Union[str, Path, BinaryIO]) -> dict:
    """
    Parse a Harmony Ability Package (HAP) file.

    Supports:
    - Stage model: `module.json` (OpenHarmony 3.1+ / HarmonyOS NEXT)
    - FA model: `config.json` (legacy OpenHarmony)
    - Additional package info: `pack.info`

    Args:
        hap_path: Path to .hap file or a readable binary file-like object.

    Returns:
        Structured dictionary containing:
        {
            "bundle_name": str,
            "app_name": str,
            "version_name": str,
            "version_code": int,
            "main_ability": Optional[str],
            "abilities": list[dict],
            "icon_path_in_hap": Optional[str],
            "is_stage_model": bool,
        }

    Raises:
        FileNotFoundError: If hap_path file does not exist.
        HapParseError: If HAP is malformed or missing manifest.
    """
    if isinstance(hap_path, (str, Path)):
        p = Path(hap_path)
        if not p.exists():
            raise FileNotFoundError(f"HAP file not found: {hap_path}")
        try:
            zf = zipfile.ZipFile(p, "r")
        except zipfile.BadZipFile as exc:
            raise HapParseError(f"Invalid HAP zip file '{hap_path}': {exc}") from exc
    else:
        if hasattr(hap_path, "seek"):
            hap_path.seek(0)
        try:
            zf = zipfile.ZipFile(hap_path, "r")
        except zipfile.BadZipFile as exc:
            raise HapParseError(f"Invalid HAP binary stream: {exc}") from exc

    try:
        namelist = zf.namelist()
        lower_to_name = {n.lower(): n for n in namelist}

        module_json_name = _find_zip_entry(
            namelist, lower_to_name, ("module.json", "*/module.json")
        )
        config_json_name = _find_zip_entry(
            namelist, lower_to_name, ("config.json", "*/config.json")
        )
        pack_info_name = _find_zip_entry(
            namelist, lower_to_name, ("pack.info", "*/pack.info")
        )

        bundle_name = ""
        app_name = ""
        version_name = ""
        version_code = 0
        main_ability: Optional[str] = None
        abilities: list[dict] = []
        icon_ref: Optional[str] = None
        label_ref: Optional[str] = None
        is_stage_model = False

        if module_json_name:
            # Stage model
            is_stage_model = True
            try:
                manifest_data = json.loads(
                    zf.read(module_json_name).decode("utf-8", errors="replace")
                )
            except Exception as exc:
                raise HapParseError(
                    f"Malformed module.json in HAP: {exc}"
                ) from exc

            app_sec = manifest_data.get("app", {})
            module_sec = manifest_data.get("module", {})

            bundle_name = app_sec.get("bundleName") or ""
            version_code = int(app_sec.get("versionCode") or 0)
            version_name = str(app_sec.get("versionName") or "")
            icon_ref = app_sec.get("icon")
            label_ref = app_sec.get("label")

            main_ability = module_sec.get("mainElement")
            raw_abilities = module_sec.get("abilities")
            if isinstance(raw_abilities, list):
                abilities = [ab for ab in raw_abilities if isinstance(ab, dict)]

            if not main_ability:
                main_ability = _resolve_main_ability_from_abilities(abilities)

        elif config_json_name:
            # FA model
            is_stage_model = False
            try:
                manifest_data = json.loads(
                    zf.read(config_json_name).decode("utf-8", errors="replace")
                )
            except Exception as exc:
                raise HapParseError(
                    f"Malformed config.json in HAP: {exc}"
                ) from exc

            app_sec = manifest_data.get("app", {})
            module_sec = manifest_data.get("module", {})

            bundle_name = app_sec.get("bundleName") or ""
            version_sec = app_sec.get("version", {})
            if isinstance(version_sec, dict):
                version_code = int(version_sec.get("code") or 0)
                version_name = str(version_sec.get("name") or "")
            else:
                version_code = int(app_sec.get("versionCode") or 0)
                version_name = str(app_sec.get("versionName") or "")

            icon_ref = app_sec.get("icon")
            label_ref = app_sec.get("label")

            main_ability = module_sec.get("mainAbility")
            raw_abilities = module_sec.get("abilities")
            if isinstance(raw_abilities, list):
                abilities = [ab for ab in raw_abilities if isinstance(ab, dict)]

            if not main_ability:
                main_ability = _resolve_main_ability_from_abilities(abilities)

        elif pack_info_name:
            # Fallback to pack.info
            is_stage_model = True
            try:
                pack_data = json.loads(
                    zf.read(pack_info_name).decode("utf-8", errors="replace")
                )
            except Exception as exc:
                raise HapParseError(f"Malformed pack.info in HAP: {exc}") from exc

            summary = pack_data.get("summary", {})
            app_sec = summary.get("app", {})
            bundle_name = app_sec.get("bundleName") or ""
            ver_sec = app_sec.get("version", {})
            if isinstance(ver_sec, dict):
                version_code = int(ver_sec.get("code") or 0)
                version_name = str(ver_sec.get("name") or "")
            modules = summary.get("modules", [])
            if modules and isinstance(modules, list) and isinstance(modules[0], dict):
                mod0 = modules[0]
                main_ability = mod0.get("mainAbility")
                raw_abilities = mod0.get("abilities")
                if isinstance(raw_abilities, list):
                    abilities = [ab for ab in raw_abilities if isinstance(ab, dict)]
        else:
            raise HapParseError("Missing module.json and config.json manifest in HAP")

        # Resolve app label / display name
        app_name = _resolve_app_label(zf, namelist, label_ref, bundle_name, main_ability)

        # Resolve icon asset in zip
        icon_path_in_hap = _resolve_icon_in_zip(zf, namelist, icon_ref, abilities)

        return {
            "bundle_name": str(bundle_name),
            "app_name": str(app_name),
            "version_name": str(version_name),
            "version_code": int(version_code),
            "main_ability": main_ability,
            "abilities": abilities,
            "icon_path_in_hap": icon_path_in_hap,
            "is_stage_model": is_stage_model,
        }
    finally:
        zf.close()


def extract_hap_icon(
    hap_path: Union[str, Path, BinaryIO],
    out_icon_path: Union[str, Path],
) -> Optional[str]:
    """
    Extract application icon from HAP archive and save to specified path.

    Args:
        hap_path: Path to HAP file or binary file-like object.
        out_icon_path: Destination file path for the extracted icon.

    Returns:
        Absolute destination file path if successful, otherwise None.
    """
    try:
        hap_info = parse_hap(hap_path)
        icon_in_hap = hap_info.get("icon_path_in_hap")
        if not icon_in_hap:
            return None

        icon_bytes: bytes
        if isinstance(hap_path, (str, Path)):
            with zipfile.ZipFile(hap_path, "r") as zf:
                icon_bytes = zf.read(icon_in_hap)
        else:
            if hasattr(hap_path, "seek"):
                hap_path.seek(0)
            with zipfile.ZipFile(hap_path, "r") as zf:
                icon_bytes = zf.read(icon_in_hap)

        out_dest = Path(out_icon_path)
        out_dest.parent.mkdir(parents=True, exist_ok=True)
        out_dest.write_bytes(icon_bytes)
        return str(out_dest.resolve())
    except Exception as exc:
        logger.warning("Failed to extract HAP icon: %s", exc)
        return None


def save_hap_to_db(
    device_id: str,
    hap_info: dict,
    out_dir: Optional[str] = None,
    icon_path: Optional[str] = None,
) -> bool:
    """
    Persist HAP metadata and launch profiles into SQLite database.

    Saves to:
    - `apps` table:
        (device_id, package_name, app_name, version_code, version_name,
         icon_path, is_system=0, captured_at)
    - `launch_profiles` table (if main_ability is available):
        (device_id, package_name, activity, component, adb_cmd,
         source="hap_parser", ts, updated_at)

    Args:
        device_id: Device identifier or serial.
        hap_info: Dict returned by `parse_hap`.
        out_dir: Optional custom recordings/data directory.
        icon_path: Optional icon path to associate.

    Returns:
        True if successfully saved, False on error.
    """
    if not device_id or not hap_info:
        return False
    bundle_name = hap_info.get("bundle_name")
    if not bundle_name:
        return False

    app_name = hap_info.get("app_name") or bundle_name
    version_code = int(hap_info.get("version_code") or 0)
    version_name = str(hap_info.get("version_name") or "")
    final_icon = icon_path or hap_info.get("icon_path")

    from phone_pilot.core.storage import iso_now, now_ts
    from phone_pilot.extensions.device_db import ensure_db

    try:
        conn = ensure_db(out_dir or "")
        captured_at = iso_now()
        ts = now_ts()

        with conn:
            conn.execute(
                """
                INSERT INTO apps (
                    device_id, package_name, app_name, version_code, version_name,
                    icon_path, icon_dpi, icon_source, is_system, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, package_name) DO UPDATE SET
                    app_name=excluded.app_name,
                    version_code=excluded.version_code,
                    version_name=excluded.version_name,
                    icon_path=coalesce(excluded.icon_path, apps.icon_path),
                    captured_at=excluded.captured_at
                """,
                (
                    device_id,
                    bundle_name,
                    app_name,
                    version_code,
                    version_name,
                    final_icon,
                    None,
                    "hap_parser",
                    0,
                    captured_at,
                ),
            )

            main_ability = hap_info.get("main_ability")
            if main_ability:
                component = f"{bundle_name}/{main_ability}"
                target_arg = f"-t {device_id} " if device_id else ""
                adb_cmd = (
                    f"hdc {target_arg}shell aa start -b {bundle_name} -a {main_ability}"
                )
                conn.execute(
                    """
                    INSERT INTO launch_profiles (
                        device_id, package_name, activity, component, adb_cmd, source,
                        updated_at, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(device_id, package_name) DO UPDATE SET
                        activity=excluded.activity,
                        component=excluded.component,
                        adb_cmd=excluded.adb_cmd,
                        source=excluded.source,
                        updated_at=excluded.updated_at,
                        ts=excluded.ts
                    """,
                    (
                        device_id,
                        bundle_name,
                        main_ability,
                        component,
                        adb_cmd,
                        "hap_parser",
                        captured_at,
                        ts,
                    ),
                )
        conn.close()
        return True
    except Exception as exc:
        logger.error("Failed to save HAP metadata to DB: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------


def _find_zip_entry(
    namelist: list[str],
    lower_to_name: dict[str, str],
    patterns: tuple[str, ...],
) -> Optional[str]:
    """Find an entry in zip namelist matching exact or glob patterns."""
    for pat in patterns:
        if pat.startswith("*/"):
            suffix = "/" + pat[2:].lower()
            for n_low, orig in lower_to_name.items():
                if n_low.endswith(suffix):
                    return orig
        else:
            cand = lower_to_name.get(pat.lower())
            if cand:
                return cand
    return None


def _resolve_main_ability_from_abilities(abilities: list[dict]) -> Optional[str]:
    """Inspect abilities to identify main/home launcher ability."""
    if not abilities:
        return None

    # 1. Look for ability with action.system.home or entity.system.home
    for ab in abilities:
        name = ab.get("name")
        skills = ab.get("skills", [])
        if isinstance(skills, list):
            for sk in skills:
                if not isinstance(sk, dict):
                    continue
                actions = sk.get("actions", [])
                entities = sk.get("entities", [])
                act_list = actions if isinstance(actions, list) else [actions]
                ent_list = entities if isinstance(entities, list) else [entities]
                if any(
                    a in ("action.system.home", "action.ohos.home") for a in act_list
                ) or any("entity.system.home" in str(e) for e in ent_list):
                    return name

    # 2. Default to first ability
    first_name = abilities[0].get("name")
    return first_name if first_name else None


def _resolve_app_label(
    zf: zipfile.ZipFile,
    namelist: list[str],
    label_ref: Optional[str],
    bundle_name: str,
    main_ability: Optional[str],
) -> str:
    """Resolve app display label from resources or fallback."""
    if not label_ref:
        return main_ability or bundle_name

    label_s = str(label_ref).strip()
    if not label_s.startswith("$string:"):
        return label_s

    str_name = label_s[8:]
    # Search string resource files
    candidate_files = [
        "resources/base/element/string.json",
        "resources/zh_CN/element/string.json",
        "resources/en_US/element/string.json",
    ]
    # Add any extra element/string.json entries
    for n in namelist:
        if n.lower().endswith("element/string.json") and n not in candidate_files:
            candidate_files.append(n)

    lower_to_name = {n.lower(): n for n in namelist}
    for cand in candidate_files:
        real_name = lower_to_name.get(cand.lower())
        if not real_name:
            continue
        try:
            content = json.loads(
                zf.read(real_name).decode("utf-8", errors="replace")
            )
            val = _extract_string_value(content, str_name)
            if val is not None:
                return str(val)
        except Exception:
            pass

    return label_s


def _extract_string_value(content: dict, str_name: str) -> Optional[str]:
    """Extract string value from parsed OpenHarmony element/string.json."""
    if not isinstance(content, dict):
        return None
    strings = content.get("string")
    if isinstance(strings, list):
        for item in strings:
            if isinstance(item, dict) and item.get("name") == str_name:
                return item.get("value")
    elif isinstance(strings, dict):
        if str_name in strings:
            return strings[str_name]
    return None


def _resolve_icon_in_zip(
    zf: zipfile.ZipFile,
    namelist: list[str],
    icon_ref: Optional[str],
    abilities: list[dict],
) -> Optional[str]:
    """Find the path of the app icon within the HAP zip archive."""
    lower_to_name = {n.lower(): n for n in namelist}

    # 1. Search based on icon_ref
    if icon_ref:
        ref = str(icon_ref).strip()
        if ref.startswith("$media:"):
            media_stem = ref[7:]
        elif ref.startswith("$"):
            media_stem = ref.split(":")[-1]
        else:
            media_stem = ref

        # Remove extension if already present
        stem_no_ext = Path(media_stem).stem.lower()

        # Try common paths
        direct_candidates = [
            f"resources/base/media/{media_stem}",
            media_stem,
        ]
        for ext in _ICON_EXTENSIONS:
            direct_candidates.append(f"resources/base/media/{stem_no_ext}{ext}")
            direct_candidates.append(f"resources/rawfile/{stem_no_ext}{ext}")

        for cand in direct_candidates:
            matched = lower_to_name.get(cand.lower())
            if matched:
                return matched

        # Suffix match on filename stem
        for n_low, orig in lower_to_name.items():
            if any(n_low.endswith(ext) for ext in _ICON_EXTENSIONS):
                p = Path(n_low)
                if p.stem.lower() == stem_no_ext:
                    return orig

    # 2. Check ability icons
    for ab in abilities:
        ab_icon = ab.get("icon")
        if ab_icon and ab_icon != icon_ref:
            sub = _resolve_icon_in_zip(zf, namelist, ab_icon, [])
            if sub:
                return sub

    # 3. Fallback: match any member with 'icon' in name under media or base
    for n_low, orig in lower_to_name.items():
        if any(n_low.endswith(ext) for ext in _ICON_EXTENSIONS):
            if "icon" in Path(n_low).name.lower():
                return orig

    # 4. Fallback: first image in resources/base/media/
    for n_low, orig in lower_to_name.items():
        if "resources/base/media/" in n_low and any(
            n_low.endswith(ext) for ext in _ICON_EXTENSIONS
        ):
            return orig

    return None


__all__ = [
    "HapParseError",
    "parse_hap",
    "extract_hap_icon",
    "save_hap_to_db",
]
