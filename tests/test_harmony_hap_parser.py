#!/usr/bin/env python3
"""
Unit tests for Harmony Ability Package (HAP) metadata and icon parser.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from phone_pilot.harmony.device.hap_parser import (
    HapParseError,
    extract_hap_icon,
    parse_hap,
    save_hap_to_db,
)
from phone_pilot.harmony.device.store import save_hap_profile
from phone_pilot.extensions.device_db import db_path, ensure_db, load_device_snapshot


def _create_synthetic_hap(
    *,
    is_stage: bool = True,
    bundle_name: str = "com.example.testapp",
    version_code: int = 100,
    version_name: str = "1.0.0",
    label: str = "Test App",
    main_ability: str = "EntryAbility",
    icon_ref: str = "$media:app_icon",
    icon_filename: str = "resources/base/media/app_icon.png",
    icon_bytes: bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest_icon_content",
    string_resources: dict[str, str] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> io.BytesIO:
    """Create an in-memory zip archive mimicking a HAP package."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        if is_stage:
            module_json = {
                "app": {
                    "bundleName": bundle_name,
                    "versionCode": version_code,
                    "versionName": version_name,
                    "icon": icon_ref,
                    "label": label,
                },
                "module": {
                    "name": "entry",
                    "type": "entry",
                    "mainElement": main_ability,
                    "abilities": [
                        {
                            "name": main_ability,
                            "icon": icon_ref,
                            "label": label,
                            "skills": [
                                {
                                    "actions": ["action.system.home"],
                                    "entities": ["entity.system.home"],
                                }
                            ],
                        }
                    ],
                },
            }
            zf.writestr("module.json", json.dumps(module_json, indent=2))
        else:
            config_json = {
                "app": {
                    "bundleName": bundle_name,
                    "version": {
                        "code": version_code,
                        "name": version_name,
                    },
                    "icon": icon_ref,
                    "label": label,
                },
                "module": {
                    "package": bundle_name,
                    "mainAbility": main_ability,
                    "abilities": [
                        {
                            "name": main_ability,
                            "icon": icon_ref,
                            "label": label,
                            "skills": [
                                {
                                    "actions": ["action.system.home"],
                                    "entities": ["entity.system.home"],
                                }
                            ],
                        }
                    ],
                },
            }
            zf.writestr("config.json", json.dumps(config_json, indent=2))

        if icon_filename and icon_bytes:
            zf.writestr(icon_filename, icon_bytes)

        if string_resources:
            str_data = {
                "string": [
                    {"name": k, "value": v} for k, v in string_resources.items()
                ]
            }
            zf.writestr(
                "resources/base/element/string.json", json.dumps(str_data, indent=2)
            )

        if extra_files:
            for fname, content in extra_files.items():
                zf.writestr(fname, content)

    bio.seek(0)
    return bio


class TestHarmonyHapParser(unittest.TestCase):
    """Test suite for HAP metadata parsing, icon extraction, and persistence."""

    def test_parse_stage_model_success(self) -> None:
        """Parse Stage model module.json with resolved string resource and icon."""
        hap_stream = _create_synthetic_hap(
            is_stage=True,
            bundle_name="com.example.stageapp",
            version_code=102,
            version_name="1.0.2",
            label="$string:app_name",
            main_ability="EntryAbility",
            icon_ref="$media:app_icon",
            icon_filename="resources/base/media/app_icon.png",
            string_resources={"app_name": "Stage Test App"},
        )

        res = parse_hap(hap_stream)

        self.assertEqual(res["bundle_name"], "com.example.stageapp")
        self.assertEqual(res["app_name"], "Stage Test App")
        self.assertEqual(res["version_code"], 102)
        self.assertEqual(res["version_name"], "1.0.2")
        self.assertEqual(res["main_ability"], "EntryAbility")
        self.assertTrue(res["is_stage_model"])
        self.assertEqual(
            res["icon_path_in_hap"], "resources/base/media/app_icon.png"
        )
        self.assertEqual(len(res["abilities"]), 1)
        self.assertEqual(res["abilities"][0]["name"], "EntryAbility")

    def test_parse_fa_model_success(self) -> None:
        """Parse FA model config.json with literal label and icon."""
        hap_stream = _create_synthetic_hap(
            is_stage=False,
            bundle_name="com.example.faapp",
            version_code=201,
            version_name="2.0.1",
            label="FA Test Application",
            main_ability="MainAbility",
            icon_ref="$media:icon",
            icon_filename="resources/base/media/icon.png",
        )

        res = parse_hap(hap_stream)

        self.assertEqual(res["bundle_name"], "com.example.faapp")
        self.assertEqual(res["app_name"], "FA Test Application")
        self.assertEqual(res["version_code"], 201)
        self.assertEqual(res["version_name"], "2.0.1")
        self.assertEqual(res["main_ability"], "MainAbility")
        self.assertFalse(res["is_stage_model"])
        self.assertEqual(res["icon_path_in_hap"], "resources/base/media/icon.png")
        self.assertEqual(len(res["abilities"]), 1)
        self.assertEqual(res["abilities"][0]["name"], "MainAbility")

    def test_parse_pack_info_fallback(self) -> None:
        """Parse HAP containing only pack.info."""
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
            pack_info = {
                "summary": {
                    "app": {
                        "bundleName": "com.example.packinfoapp",
                        "version": {"code": 301, "name": "3.0.1"},
                    },
                    "modules": [
                        {
                            "name": "entry",
                            "mainAbility": "PackEntryAbility",
                            "abilities": [{"name": "PackEntryAbility"}],
                        }
                    ],
                }
            }
            zf.writestr("pack.info", json.dumps(pack_info))
        bio.seek(0)

        res = parse_hap(bio)
        self.assertEqual(res["bundle_name"], "com.example.packinfoapp")
        self.assertEqual(res["version_code"], 301)
        self.assertEqual(res["version_name"], "3.0.1")
        self.assertEqual(res["main_ability"], "PackEntryAbility")

    def test_parse_file_path(self) -> None:
        """parse_hap accepts filesystem Path/str."""
        hap_stream = _create_synthetic_hap(
            bundle_name="com.example.filepath",
            label="File Path App",
        )
        with tempfile.NamedTemporaryFile(suffix=".hap", delete=False) as tf:
            tf.write(hap_stream.getvalue())
            tf_path = Path(tf.name)

        try:
            res = parse_hap(tf_path)
            self.assertEqual(res["bundle_name"], "com.example.filepath")
            self.assertEqual(res["app_name"], "File Path App")
        finally:
            if tf_path.exists():
                tf_path.unlink()

    def test_extract_hap_icon(self) -> None:
        """extract_hap_icon writes icon bytes to output file."""
        expected_bytes = b"\x89PNG\r\n\x1a\ncustom_icon_payload_12345"
        hap_stream = _create_synthetic_hap(
            icon_filename="resources/base/media/app_icon.png",
            icon_bytes=expected_bytes,
        )

        with tempfile.TemporaryDirectory() as td:
            out_icon = Path(td) / "extracted_icon.png"
            extracted_path = extract_hap_icon(hap_stream, out_icon)

            self.assertIsNotNone(extracted_path)
            self.assertTrue(out_icon.exists())
            self.assertEqual(out_icon.read_bytes(), expected_bytes)
            self.assertEqual(extracted_path, str(out_icon.resolve()))

    def test_extract_hap_icon_missing(self) -> None:
        """extract_hap_icon returns None when no icon is present."""
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "module.json",
                json.dumps(
                    {
                        "app": {"bundleName": "com.example.noicon"},
                        "module": {"abilities": []},
                    }
                ),
            )
        bio.seek(0)

        with tempfile.TemporaryDirectory() as td:
            out_icon = Path(td) / "icon.png"
            res = extract_hap_icon(bio, out_icon)
            self.assertIsNone(res)
            self.assertFalse(out_icon.exists())

    def test_save_hap_to_db(self) -> None:
        """save_hap_to_db writes app and launch profile records into sqlite db."""
        hap_info = {
            "bundle_name": "com.example.dbapp",
            "app_name": "Database App",
            "version_code": 105,
            "version_name": "1.0.5",
            "main_ability": "EntryAbility",
            "abilities": [{"name": "EntryAbility"}],
            "icon_path_in_hap": "resources/base/media/app_icon.png",
            "is_stage_model": True,
        }

        with tempfile.TemporaryDirectory() as td:
            # Pre-register device so snapshot is queryable
            conn_init = ensure_db(td)
            conn_init.execute(
                "INSERT OR REPLACE INTO devices (device_id, device_serial) VALUES (?, ?)",
                ("device_harmony_01", "device_harmony_01"),
            )
            conn_init.commit()
            conn_init.close()

            saved = save_hap_to_db(
                device_id="device_harmony_01",
                hap_info=hap_info,
                out_dir=td,
                icon_path="/icons/com.example.dbapp.png",
            )
            self.assertTrue(saved)

            # Query database directly
            p = db_path(td)
            self.assertTrue(p.exists())
            conn = sqlite3.connect(str(p))
            conn.row_factory = sqlite3.Row

            app_row = conn.execute(
                "SELECT * FROM apps WHERE device_id = ? AND package_name = ?",
                ("device_harmony_01", "com.example.dbapp"),
            ).fetchone()
            self.assertIsNotNone(app_row)
            self.assertEqual(app_row["app_name"], "Database App")
            self.assertEqual(app_row["version_code"], 105)
            self.assertEqual(app_row["version_name"], "1.0.5")
            self.assertEqual(app_row["icon_path"], "/icons/com.example.dbapp.png")
            self.assertEqual(app_row["is_system"], 0)

            lp_row = conn.execute(
                "SELECT * FROM launch_profiles WHERE device_id = ? AND package_name = ?",
                ("device_harmony_01", "com.example.dbapp"),
            ).fetchone()
            self.assertIsNotNone(lp_row)
            self.assertEqual(lp_row["activity"], "EntryAbility")
            self.assertEqual(lp_row["component"], "com.example.dbapp/EntryAbility")
            self.assertIn("aa start -b com.example.dbapp -a EntryAbility", lp_row["adb_cmd"])
            self.assertEqual(lp_row["source"], "hap_parser")

            conn.close()

            # Also check load_device_snapshot
            snapshot = load_device_snapshot(td, device_id="device_harmony_01")
            self.assertTrue(snapshot["ok"])
            self.assertEqual(len(snapshot["apps"]), 1)
            self.assertEqual(len(snapshot["launch_profiles"]), 1)

    def test_save_hap_profile_integration(self) -> None:
        """Test save_hap_profile helper from store.py."""
        hap_stream = _create_synthetic_hap(
            bundle_name="com.example.storedapp",
            label="Stored App",
            main_ability="MainAbility",
        )
        with tempfile.TemporaryDirectory() as td:
            res = save_hap_profile(
                device_id="device_002",
                hap_path=hap_stream,
                out_dir=td,
                extract_icon=True,
            )
            self.assertTrue(res["ok"])
            self.assertIsNotNone(res["icon_path"])
            self.assertTrue(Path(res["icon_path"]).exists())
            self.assertEqual(res["hap_info"]["bundle_name"], "com.example.storedapp")

    def test_invalid_hap_error_handling(self) -> None:
        """Handle non-existent files, corrupt zip, missing manifests gracefully."""
        # 1. Non-existent file
        with self.assertRaises(FileNotFoundError):
            parse_hap("/path/does/not/exist/app.hap")

        # 2. Corrupt zip
        with self.assertRaises(HapParseError):
            parse_hap(io.BytesIO(b"not a valid zip file"))

        # 3. Missing manifest
        empty_zip = io.BytesIO()
        with zipfile.ZipFile(empty_zip, "w") as zf:
            zf.writestr("readme.txt", "hello")
        empty_zip.seek(0)
        with self.assertRaises(HapParseError) as ctx:
            parse_hap(empty_zip)
        self.assertIn("Missing module.json", str(ctx.exception))

        # 4. Malformed JSON manifest
        broken_json_zip = io.BytesIO()
        with zipfile.ZipFile(broken_json_zip, "w") as zf:
            zf.writestr("module.json", "{broken json")
        broken_json_zip.seek(0)
        with self.assertRaises(HapParseError) as ctx2:
            parse_hap(broken_json_zip)
        self.assertIn("Malformed module.json", str(ctx2.exception))


if __name__ == "__main__":
    unittest.main()
