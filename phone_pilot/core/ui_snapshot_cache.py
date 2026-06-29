"""Short-TTL shared UI snapshot cache.

进程内短 TTL 缓存，用于减少同一逻辑步骤内重复 dump UI hierarchy 的开销。

设计要点 / Design:
- 缓存原始 UI 层级（XML 字符串或解析后的 UINode 列表），不缓存过滤结果。
- 默认 TTL 500ms，可通过参数覆盖。
- 动作类操作（tap/swipe/input/launch 等）执行成功后应显式失效。
- 按 (platform, device_serial, kind, *extra) 做 key，天然隔离设备与平台。
- 线程安全：内部加锁；compute 回调在锁外执行以避免长 dump 阻塞其他读取。
- 失败兜底：compute 抛异常时不写入缓存，让下次调用重试。

Public API:
- get_or_compute(key, compute, *, ttl_s=DEFAULT_UI_CACHE_TTL_S) -> T
- invalidate(*, platform=None, device_serial=None) -> None
- invalidate_device_ui(device_serial, platform=None) -> None
- DEFAULT_UI_CACHE_TTL_S
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Hashable, TypeVar

T = TypeVar("T")

DEFAULT_UI_CACHE_TTL_S: float = 0.5
_MAX_CACHE_ENTRIES: int = 128

_lock = threading.Lock()
_cache: dict[tuple[Hashable, ...], "CacheEntry"] = {}


@dataclass
class CacheEntry:
    value: Any
    created_at: float


def get_or_compute(
    key: tuple[Hashable, ...],
    compute: Callable[[], T],
    *,
    ttl_s: float = DEFAULT_UI_CACHE_TTL_S,
) -> T:
    """返回 key 对应的缓存值；不存在或已过期时调用 compute 重新计算并写入。

    Args / Parameters:
        key: 缓存键，建议形如 (platform, serial, kind, *extra)。
        compute: 缓存未命中时的计算函数（在锁外执行）。
        ttl_s: 存活秒数，默认 0.5s。

    Returns / 返回:
        缓存或新计算的值。

    Notes / 说明:
        - compute 抛异常时不写入缓存，异常直接向上抛。
        - 命中时返回的是同一个对象引用（不拷贝），调用方不应原地修改。
    """
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry.created_at) <= ttl_s:
            return entry.value  # type: ignore[return-value]

    value = compute()

    with _lock:
        # Evict expired entries and enforce max size to prevent unbounded growth.
        if len(_cache) >= _MAX_CACHE_ENTRIES:
            _evict_expired_or_oldest()
        _cache[key] = CacheEntry(value=value, created_at=time.monotonic())
    return value  # type: ignore[return-value]


def _evict_expired_or_oldest() -> None:
    """清理过期条目；如仍超限，淘汰最旧的。调用方需持锁。"""
    now = time.monotonic()
    # First pass: drop expired entries.
    expired = [
        k for k, v in _cache.items() if (now - v.created_at) > DEFAULT_UI_CACHE_TTL_S
    ]
    for k in expired:
        _cache.pop(k, None)
    # If still at capacity, drop the oldest entry.
    while len(_cache) >= _MAX_CACHE_ENTRIES and _cache:
        oldest_key = min(_cache, key=lambda k: _cache[k].created_at)
        _cache.pop(oldest_key, None)


def invalidate(
    *,
    platform: str | None = None,
    device_serial: str | None = None,
) -> None:
    """失效缓存。

    - 不传参数：清空全部缓存（主要用于测试）。
    - 仅传 platform：清除该平台所有设备的缓存。
    - 仅传 device_serial：清除所有平台下该 serial 的缓存。
    - 同时传：精确清除 (platform, serial) 组合。
    """
    with _lock:
        if platform is None and device_serial is None:
            _cache.clear()
            return

        for key in list(_cache):
            key_platform = str(key[0]) if len(key) > 0 else ""
            key_serial = str(key[1]) if len(key) > 1 else ""
            if platform is not None and key_platform != platform:
                continue
            if device_serial is not None and key_serial != device_serial:
                continue
            _cache.pop(key, None)


def invalidate_device_ui(
    device_serial: str | None,
    platform: str | None = None,
) -> None:
    """便捷封装：按设备失效 UI 缓存。

    device_serial 为空时直接返回（不做全局清空，避免误伤）。
    """
    if not device_serial:
        return
    invalidate(platform=platform, device_serial=device_serial)
