"""Tests for AndroidDriver UI dump caching.

验证 AndroidUIDriver.dump_ui_hierarchy 接入了 ui_snapshot_cache：
- TTL 内重复调用只触发一次底层 dump。
- 不同 serial 隔离。
- 显式失效后重新 dump。
"""

from __future__ import annotations

from unittest.mock import patch

from phone_pilot.android.driver import AndroidDriver
from phone_pilot.core import ui_snapshot_cache


def _fake_dump_result(xml: str = "<hierarchy/>") -> dict:
    return {"ok": True, "xml": xml}


def test_dump_ui_hierarchy_cached_within_ttl():
    ui_snapshot_cache.invalidate()

    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value=_fake_dump_result("<hierarchy><node/></hierarchy>"),
    ) as mock_dump:
        driver = AndroidDriver("test_serial_cache_1")

        xml1 = driver.ui.dump_ui_hierarchy()
        xml2 = driver.ui.dump_ui_hierarchy()

        assert xml1 == xml2
        assert mock_dump.call_count == 1


def test_dump_ui_hierarchy_recomputes_after_invalidate():
    ui_snapshot_cache.invalidate()

    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value=_fake_dump_result("<hierarchy/>"),
    ) as mock_dump:
        driver = AndroidDriver("test_serial_cache_2")

        driver.ui.dump_ui_hierarchy()
        ui_snapshot_cache.invalidate_device_ui(
            "test_serial_cache_2", platform="android"
        )
        driver.ui.dump_ui_hierarchy()

        assert mock_dump.call_count == 2


def test_dump_ui_hierarchy_different_serial_isolated():
    ui_snapshot_cache.invalidate()

    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value=_fake_dump_result("<hierarchy/>"),
    ) as mock_dump:
        d1 = AndroidDriver("serial_iso_A")
        d2 = AndroidDriver("serial_iso_B")

        d1.ui.dump_ui_hierarchy()
        d2.ui.dump_ui_hierarchy()

        assert mock_dump.call_count == 2


def test_dump_ui_nodes_uses_cached_hierarchy():
    ui_snapshot_cache.invalidate()

    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value=_fake_dump_result("<hierarchy/>"),
    ) as mock_dump:
        driver = AndroidDriver("test_serial_cache_3")

        driver.ui.dump_ui_nodes()
        driver.ui.dump_ui_nodes()

        # dump_ui_nodes calls dump_ui_hierarchy internally; with cache,
        # the underlying dump_ui_xml should only be called once.
        assert mock_dump.call_count == 1


def test_dump_ui_hierarchy_compressed_dimension_isolated():
    """compressed=True 和 compressed=False 的缓存不应互相污染。"""
    ui_snapshot_cache.invalidate()

    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value=_fake_dump_result("<hierarchy/>"),
    ) as mock_dump:
        driver = AndroidDriver("test_serial_cache_4")

        driver.ui.dump_ui_hierarchy(compressed=True)
        driver.ui.dump_ui_hierarchy(compressed=False)

        assert mock_dump.call_count == 2
