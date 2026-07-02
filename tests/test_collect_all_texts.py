"""Tests for collect_all_texts returning List[str] consistently."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.android.driver import AndroidUIDriver, _ensure_str


def test_ensure_str_decodes_bytes():
    """_ensure_str decodes bytes to str."""
    assert _ensure_str(b"hello") == "hello"


def test_ensure_str_passes_str():
    """_ensure_str passes through str unchanged."""
    assert _ensure_str("hello") == "hello"


def test_ensure_str_converts_int():
    """_ensure_str converts non-str non-bytes to str."""
    assert _ensure_str(42) == "42"


def test_collect_all_texts_filters_out_bytes():
    """collect_all_texts returns List[str] even when raw has bytes."""
    driver = AndroidUIDriver("fake_serial")
    with patch.object(driver, "dump_ui_hierarchy", return_value="<hierarchy></hierarchy>"):
        with patch(
            "phone_pilot.android.driver.parse_uiautomator_nodes",
            return_value=[],
        ):
            with patch(
                "phone_pilot.android.ui.query.collect_node_texts",
                return_value=["text1", b"text2", "text3"],
            ):
                result = driver.collect_all_texts()

    assert result == ["text1", "text2", "text3"]
    assert all(isinstance(t, str) for t in result)


def test_collect_all_texts_empty_xml():
    """collect_all_texts returns empty list when no xml."""
    driver = AndroidUIDriver("fake_serial")
    with patch.object(driver, "dump_ui_hierarchy", return_value=""):
        result = driver.collect_all_texts()

    assert result == []
