"""Tests for the short-TTL UI snapshot cache.

phone_pilot/core/ui_snapshot_cache.py 共享 UI 快照缓存的单元测试。

覆盖：命中、TTL 过期、按 serial/platform 隔离、显式失效。
"""

from __future__ import annotations

import time

from phone_pilot.core import ui_snapshot_cache as cache


def test_get_or_compute_hits_within_ttl():
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return {"value": calls}

    cache.invalidate()
    first = cache.get_or_compute(("android", "serial1", "ui_nodes"), compute, ttl_s=0.5)
    second = cache.get_or_compute(("android", "serial1", "ui_nodes"), compute, ttl_s=0.5)

    assert first is second
    assert calls == 1


def test_get_or_compute_recomputes_after_ttl_expire():
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return calls

    cache.invalidate()
    cache.get_or_compute(("android", "serial1", "ui_xml"), compute, ttl_s=0.05)
    time.sleep(0.06)
    second = cache.get_or_compute(("android", "serial1", "ui_xml"), compute, ttl_s=0.05)

    assert calls == 2
    assert second == 2


def test_different_serial_isolated():
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return calls

    cache.invalidate()
    cache.get_or_compute(("android", "serialA", "ui_nodes"), compute, ttl_s=0.5)
    cache.get_or_compute(("android", "serialB", "ui_nodes"), compute, ttl_s=0.5)

    assert calls == 2


def test_different_platform_isolated():
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return calls

    cache.invalidate()
    cache.get_or_compute(("android", "serial1", "ui_nodes"), compute, ttl_s=0.5)
    cache.get_or_compute(("ios", "serial1", "ui_nodes"), compute, ttl_s=0.5)

    assert calls == 2


def test_invalidate_by_platform_and_serial():
    cache.invalidate()
    cache.get_or_compute(
        ("android", "serial1", "ui_nodes"), lambda: "v1", ttl_s=0.5
    )
    cache.get_or_compute(
        ("android", "serial2", "ui_nodes"), lambda: "v2", ttl_s=0.5
    )

    cache.invalidate(platform="android", device_serial="serial1")

    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return "recomputed"

    result = cache.get_or_compute(
        ("android", "serial1", "ui_nodes"), compute, ttl_s=0.5
    )
    assert result == "recomputed"
    assert calls == 1

    # serial2 should still be cached.
    cached = cache.get_or_compute(
        ("android", "serial2", "ui_nodes"), lambda: "miss", ttl_s=0.5
    )
    assert cached == "v2"


def test_invalidate_all_clears_everything():
    cache.invalidate()
    cache.get_or_compute(("android", "s1", "ui_nodes"), lambda: 1, ttl_s=0.5)
    cache.get_or_compute(("ios", "s2", "ui_nodes"), lambda: 2, ttl_s=0.5)

    cache.invalidate()

    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return "fresh"

    cache.get_or_compute(("android", "s1", "ui_nodes"), compute, ttl_s=0.5)
    cache.get_or_compute(("ios", "s2", "ui_nodes"), compute, ttl_s=0.5)

    assert calls == 2


def test_invalidate_device_helper():
    cache.invalidate()
    cache.get_or_compute(
        ("android", "serialX", "ui_nodes"), lambda: "v", ttl_s=0.5
    )

    cache.invalidate_device_ui("serialX", platform="android")

    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return "new"

    result = cache.get_or_compute(
        ("android", "serialX", "ui_nodes"), compute, ttl_s=0.5
    )
    assert result == "new"
    assert calls == 1


def test_default_ttl_constant_exists():
    assert cache.DEFAULT_UI_CACHE_TTL_S == 0.5


def test_max_cache_entries_enforced():
    """超过 _MAX_CACHE_ENTRIES 时应淘汰过期/最旧条目。"""
    cache.invalidate()
    # Fill cache to capacity.
    for i in range(cache._MAX_CACHE_ENTRIES):
        cache.get_or_compute(("android", f"serial{i}", "ui_nodes"), lambda i=i: i)

    assert len(cache._cache) <= cache._MAX_CACHE_ENTRIES

    # Add one more — should trigger eviction.
    cache.get_or_compute(("android", "new_serial", "ui_nodes"), lambda: "new")

    # Cache size must not exceed the cap.
    assert len(cache._cache) <= cache._MAX_CACHE_ENTRIES
    # The new entry must be present.
    assert ("android", "new_serial", "ui_nodes") in cache._cache


def test_max_cache_entries_constant_exists():
    assert cache._MAX_CACHE_ENTRIES > 0
    assert isinstance(cache._MAX_CACHE_ENTRIES, int)
