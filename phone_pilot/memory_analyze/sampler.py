#!/usr/bin/env python3
"""MemInfoSampler — 后台定时采集 meminfo 的线程。
MemInfoSampler — Background thread for periodic meminfo sampling.

不阻塞主脚本执行，每次仅提取 summary 核心字段。
Does not block main script execution; captures only summary core fields per sample.

用法 / Usage:
    sampler = MemInfoSampler("device_serial", "com.example.app", interval_s=30)
    sampler.start()
    # ... script runs ...
    samples = sampler.stop()
    # samples: [{"elapsed_s": float, "total_pss_mb": float, ...}, ...]

性能影响 / Performance Impact:
    - dumpsys meminfo 单次 ~0.5-1.5s
    - 默认关闭 (interval_s=0)
    - 30s 间隔: 3 分钟脚本约 6 次采样, 额外 ~6s (+3%)
    - 采集失败（设备断连等）静默跳过
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional



class MemInfoSampler:
    """后台线程定时采集 meminfo / Background thread for periodic meminfo sampling."""

    def __init__(
        self,
        device_serial: Optional[str],
        package_name: str,
        interval_s: float = 30.0,
    ):
        """初始化采样器 / Initialize sampler.

        Parameters:
            device_serial: 设备序列号 / Device serial
            package_name: 应用包名 / App package name
            interval_s: 采样间隔秒数 / Sampling interval in seconds
        """
        self._device_serial = device_serial
        self._package_name = package_name
        self._interval_s = max(1.0, float(interval_s))
        self._samples: list[dict[str, Any]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_time: float = 0.0

    def start(self) -> None:
        """启动后台采样线程 / Start background sampling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._samples = []
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="MemInfoSampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        """停止采样并返回所有样本 / Stop sampling and return all samples.

        Returns:
            list of sample dicts with keys:
                elapsed_s, total_pss_mb, native_heap_mb, dalvik_heap_mb,
                graphics_mb, gl_mtrack_mb, egl_mtrack_mb, timestamp
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        return list(self._samples)

    @property
    def samples(self) -> list[dict[str, Any]]:
        """当前已采集的样本 / Current collected samples."""
        return list(self._samples)

    @property
    def count(self) -> int:
        """已采集样本数 / Number of collected samples."""
        return len(self._samples)

    def _run_loop(self) -> None:
        """采样循环（在后台线程中运行）/ Sampling loop (runs in background thread)."""
        while not self._stop_event.is_set():
            try:
                sample = self._take_sample()
                if sample:
                    self._samples.append(sample)
            except Exception:
                pass  # 静默跳过采集失败
            # Wait for interval or until stopped
            self._stop_event.wait(timeout=self._interval_s)

    def _take_sample(self) -> Optional[dict[str, Any]]:
        """采集一次 meminfo 快照（仅 summary 字段）/ Take a single meminfo snapshot."""
        try:
            from phone_pilot.memory_analyze.meminfo import capture_meminfo
            result = capture_meminfo(self._device_serial, self._package_name, timeout_s=10.0)
            if not result.get("ok"):
                return None

            summary = result.get("summary", {})
            elapsed = time.time() - self._start_time

            return {
                "elapsed_s": round(elapsed, 1),
                "total_pss_mb": summary.get("total_pss_mb", 0),
                "native_heap_mb": summary.get("native_heap_mb", 0),
                "dalvik_heap_mb": summary.get("dalvik_heap_mb", 0),
                "java_heap_mb": summary.get("java_heap_mb", 0),
                "graphics_mb": summary.get("graphics_mb", 0),
                "gl_mtrack_mb": round(summary.get("gl_mtrack_kb", 0) / 1024, 2),
                "egl_mtrack_mb": round(summary.get("egl_mtrack_kb", 0) / 1024, 2),
                "timestamp": time.strftime("%H:%M:%S"),
            }
        except Exception:
            return None

    def to_timeline(self) -> dict[str, Any]:
        """将全部样本转为时间线数据 / Convert all samples to timeline data.

        Returns:
            {"samples": [...], "count": int, "interval_s": float}
        """
        return {
            "samples": list(self._samples),
            "count": len(self._samples),
            "interval_s": self._interval_s,
        }
