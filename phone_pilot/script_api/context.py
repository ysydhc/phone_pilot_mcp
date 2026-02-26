"""脚本执行上下文与异常类 / Script execution context and exception classes.

包含 ScriptContext（脚本运行环境）和 RetryExhausted（重试耗尽异常）。
Contains ScriptContext (script runtime environment) and RetryExhausted (retry exhaustion exception).
"""
from __future__ import annotations

import os
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from phone_pilot.core.protocols import DeviceDriver
from phone_pilot.core.driver_factory import create_driver, detect_device
from phone_pilot.core.run_store import RunSession
from phone_pilot.core.log import _log as _runner_log


# ---------------------------------------------------------------------------
# 异常类
# ---------------------------------------------------------------------------

def _annotate_screenshot(png_bytes: bytes, element: Any) -> bytes:
    """在截图上标注找到的元素位置（绿色矩形 + 标签）/ Annotate found element on screenshot."""
    try:
        bounds = element.bounds() if callable(getattr(element, "bounds", None)) else None
        if not bounds:
            return png_bytes
        x1, y1, x2, y2 = bounds
        label = getattr(element, "display_text", "") or ""

        import io
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(png_bytes))
        draw = ImageDraw.Draw(img)

        # Green rectangle with 3px border
        color = (34, 197, 94)  # #22c55e green
        for offset in range(3):
            draw.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=color,
            )

        # Label background + text
        if label:
            label_text = label[:30]
            font_size = max(18, min(28, img.width // 40))
            try:
                font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", font_size)
            except Exception:
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
                except Exception:
                    font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), label_text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            lx = x1
            ly = max(0, y1 - th - 8)
            draw.rectangle([lx, ly, lx + tw + 10, ly + th + 6], fill=(34, 197, 94, 200))
            draw.text((lx + 5, ly + 2), label_text, fill=(255, 255, 255), font=font)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return png_bytes  # Return original on any error


class RetryExhausted(RuntimeError):
    """重试耗尽异常 / Retry exhaustion exception.

    当 retry() 或 scroll_to_find() 等函数的多次重试均失败后抛出。
    用于中断脚本流程并返回非零退出码。
    Raised when all retry attempts of retry() or scroll_to_find() have failed.
    Used to abort script flow with non-zero exit code.
    """
    pass


@dataclass
class ScriptContext:
    """脚本执行上下文 / Script execution context.

    懒加载创建 DeviceDriver，封装平台差异。脚本应通过 ctx.driver 调用
    input/screen/ui 等能力，而非直接依赖平台包。
    Lazily creates a DeviceDriver, encapsulating platform differences.
    Scripts should use ctx.driver.input, ctx.driver.screen, ctx.driver.ui, etc.

    观测控制 / Observation Control:
    --------------------------------
    - ``auto_log``          — 自动打印每步操作日志 / Auto-print step logs
    - ``auto_screenshot``   — 自动截图到 RunSession 步骤目录 / Auto-screenshot to RunSession
    - ``auto_dump_hprof``   — 脚本成功结束后自动 dump hprof（默认关闭）/ Auto hprof on success (off by default)
    - ``auto_report``       — 脚本结束后自动生成 HTML 可视化报告（默认开启）/ Auto HTML report (on by default)
    - ``start_record()`` / ``stop_record()`` — 录屏控制 / Screen recording control

    错误自愈 / Error Self-Healing:
    ------------------------------
    - ``on_step_fail``      — 步骤失败策略 / Step failure policy: "abort" | "retry" | "screenshot_and_continue"
    - ``popup_guard``       — 启用弹窗自动检测与关闭 / Enable popup auto-detection (default True)
    - ``llm_healing``       — 启用 LLM 全局分析兜底 / Enable LLM analysis fallback (default False)
    - ``max_heal_attempts`` — 单步最大自愈尝试次数 / Max heal attempts per step
    - ``popup_rules``       — 自定义弹窗规则 / Custom popup rules (None for defaults)

    存储 / Storage:
    --------------
    - ``recordings_dir``  — 运行产物根目录（默认 <cwd>/.recordings）/ Run output root
    - ``script_name``     — 脚本名称（用于 RunSession 目录命名）/ Script name for run dir
    - ``script_path``     — 脚本文件路径（会被拷贝到 RunSession）/ Script path to copy into run

    字段说明 / Field descriptions:
    ------------------------------
    - device_serial: 设备序列号，为空时自动检测 / Device serial, auto-detect if None
    - out_dir: 输出根目录 / Output root directory
    - platform: 平台（"android" / "harmony" / "auto"）/ Platform identifier
    - screen_size: 屏幕尺寸缓存（自动获取）/ Screen size cache (auto-fetched)
    """

    device_serial: Optional[str] = None
    out_dir: str = "./.recordings"
    platform: str = "auto"
    screen_size: Optional[tuple[int, int]] = None
    auto_log: bool = False
    auto_screenshot: bool = False
    auto_dump_hprof: bool = False
    auto_meminfo: bool = True             # 自动 before/after meminfo 快照 / Auto before/after meminfo snapshots
    meminfo_interval_s: float = 0         # 后台采样间隔秒数（0=关闭）/ Background sampling interval (0=off)
    perfetto_heap_profile: bool = False   # 启用 Perfetto heapprofd / Enable Perfetto heapprofd
    perfetto_duration_s: float = 0        # Perfetto 采集时长（0=跟随脚本全程）/ Perfetto duration (0=full script)
    auto_report: bool = True              # 脚本结束后自动生成 HTML 报告 / Auto HTML report on finish
    recordings_dir: Optional[str] = None
    script_name: str = ""
    script_path: Optional[str] = None

    # ---- 错误自愈配置 / Error self-healing config ----
    on_step_fail: str = "abort"        # "abort" | "retry" | "screenshot_and_continue"
    popup_guard: bool = True            # 启用弹窗自动检测与关闭 / Enable popup auto-detection
    llm_healing: bool = False           # 启用 LLM 全局分析兜底 / Enable LLM analysis fallback
    max_heal_attempts: int = 3          # 单步最大自愈尝试次数 / Max heal attempts per step
    popup_rules: Optional[list] = None  # 自定义弹窗规则 / Custom popup rules (None for defaults)

    # ---- 内部状态 / Internal state ----
    _driver: Any = field(default=None, repr=False)
    _air: Any = field(default=None, repr=False)
    _poco: Any = field(default=None, repr=False)
    _recording: bool = field(default=False, repr=False)
    _record_name: str = field(default="", repr=False)
    _record_remote: str = field(default="", repr=False)
    _step_counter: int = field(default=0, repr=False)
    _session: Optional[RunSession] = field(default=None, repr=False)
    _started_at: float = field(default_factory=time.time, repr=False)
    _self_healer: Any = field(default=None, repr=False)
    _healing_events: list = field(default_factory=list, repr=False)

    # ---- driver accessor ----

    @property
    def driver(self) -> DeviceDriver:
        """Return (and lazily create) the platform DeviceDriver."""
        if self._driver is not None:
            return self._driver
        self._init_driver()
        return self._driver  # type: ignore[return-value]

    def _init_driver(self) -> None:
        """Detect the device (if needed), instantiate driver, create RunSession."""
        # Suppress verbose DEBUG logging from ALL internal libraries.
        # Must run before any detection/import to avoid noisy output.
        import logging as _logging
        _suppress_names = [
            "phone_pilot.harmony", "hdc",
            "airtest", "airtest.core", "airtest.core.android",
            "airtest.core.android.cap_methods", "airtest.utils",
        ]
        # hmdriver2 configures its own handler at import; trigger import first
        try:
            import hmdriver2 as _hm  # noqa: F401
            _suppress_names.append("hmdriver2")
        except ImportError:
            pass
        for _lname in _suppress_names:
            _lg = _logging.getLogger(_lname)
            _lg.setLevel(_logging.CRITICAL)
            for _h in list(_lg.handlers):
                _h.setLevel(_logging.CRITICAL)

        if not self.device_serial:
            serial, plat = detect_device()
            self.device_serial = serial
            if (self.platform or "auto").lower() == "auto":
                self.platform = plat
        elif (self.platform or "auto").lower() == "auto":
            pass

        self._driver = create_driver(self.device_serial, self.platform)
        self.platform = self._driver.platform

        # Auto-infer script_name from sys.argv if not set
        if not self.script_name:
            try:
                main_file = sys.argv[0] if sys.argv else ""
                if main_file:
                    self.script_name = pathlib.Path(main_file).stem
                    if not self.script_path:
                        self.script_path = main_file
            except Exception:
                self.script_name = "script"

        # Create RunSession for this run
        if self._session is None:
            try:
                self._session = RunSession.create(
                    self.script_name or "script",
                    script_path=self.script_path,
                    recordings_dir=self.recordings_dir,
                    device_serial=self.device_serial,
                    platform=self.platform,
                )
            except Exception:
                pass

        # Print startup banner
        self._print_banner()

    def _print_banner(self) -> None:
        """Print a concise startup banner to stderr."""
        sep = "\u2500" * 52
        _runner_log("")
        _runner_log(sep)
        _runner_log(f"  phone_pilot | {self.device_serial} ({self.platform})")
        if self._session:
            rel = self._session.run_dir
            try:
                rel = self._session.run_dir.relative_to(pathlib.Path.cwd())
            except ValueError:
                rel = self._session.run_dir
            _runner_log(f"  数据目录   | {rel}/")
        _runner_log(sep)
        _runner_log("")

    @property
    def session(self) -> Optional[RunSession]:
        """Return the current RunSession (or None if not created)."""
        return self._session

    # ---- Airtest / Poco (Android-only) ----

    def ensure_air(self):
        if self.driver.platform != "android":
            raise RuntimeError("Airtest/Poco is Android-only.")
        if self._air is not None:
            return self._air
        try:
            import airtest.core.api as air
        except Exception as e:
            air = self._try_install_and_import("airtest.core.api", "airtest", e)
        # Suppress airtest's verbose logging (DEBUG adb paths, ERROR minicap noise, etc.)
        import logging as _logging
        for name in ("airtest", "airtest.core", "airtest.core.android",
                      "airtest.core.android.cap_methods", "airtest.utils"):
            _logging.getLogger(name).setLevel(_logging.CRITICAL)
        serial = self.device_serial or ""
        uri = f"Android:///{serial}" if serial else "Android:///"
        air.connect_device(uri)
        if not self.device_serial:
            try:
                dev = air.device()
                self.device_serial = getattr(dev, "serialno", None) or getattr(dev, "serial", None)
            except Exception:
                pass
        self._air = air
        return air

    def ensure_poco(self):
        if self._poco is not None:
            return self._poco
        try:
            from poco.drivers.android.uiautomation import AndroidUiautomationPoco
        except Exception as e:
            AndroidUiautomationPoco = self._try_install_and_import(
                "poco.drivers.android.uiautomation",
                "pocoui",
                e,
                attr_name="AndroidUiautomationPoco",
            )
        self._poco = AndroidUiautomationPoco()
        return self._poco

    def _try_install_and_import(self, module_name: str, pkg_name: str, err: Exception, *, attr_name: str | None = None):
        import importlib, subprocess, sys  # noqa: E401

        try:
            mod = importlib.import_module(module_name)
            return getattr(mod, attr_name) if attr_name else mod
        except Exception:
            pass
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name])
            mod = importlib.import_module(module_name)
            return getattr(mod, attr_name) if attr_name else mod
        except Exception as e:
            conda_prefix = os.environ.get("CONDA_PREFIX")
            if conda_prefix:
                try:
                    subprocess.check_call(["conda", "run", "-p", conda_prefix, "python", "-m", "pip", "install", pkg_name])
                    mod = importlib.import_module(module_name)
                    return getattr(mod, attr_name) if attr_name else mod
                except Exception:
                    pass
            raise RuntimeError(
                f"{pkg_name} is required but not available in this Python. "
                f"Interpreter: {sys.executable}. Install failed: {e}. "
                f"Original error: {err}"
            ) from e

    # ---- 录屏控制 ----

    def start_record(self, name: str = "") -> dict:
        """开始录屏 / Start screen recording.

        name 为空时自动生成（如 record_HHMMSS）。
        When done, call stop_record() to save to RunSession recordings_dir.
        If name empty, auto-generates (e.g. record_HHMMSS).
        """
        if self._recording:
            return {"ok": False, "error": "already_recording"}
        if not name:
            name = f"record_{time.strftime('%H%M%S')}"
        # 传文件名给 driver，driver 返回 remote_path（平台决定存储位置）
        result = self.driver.screen.start_screenrecord(f"{name}.mp4")
        if result.get("ok"):
            self._recording = True
            self._record_name = name
            # 从 driver 返回值获取实际远程路径，不再硬编码 /sdcard/
            self._record_remote = result.get("remote_path", "")
            _runner_log(f"  [rec] 开始录屏 → {name}")
        return result

    def stop_record(self) -> dict:
        """停止录屏并自动 pull 到 RunSession 目录。
        Stop recording and auto-pull to RunSession recordings dir.

        Returns / 返回值:
            dict: {"ok": bool, "local_path": str, ...}
        """
        if not self._recording:
            return {"ok": False, "error": "not_recording"}
        result = self.driver.screen.stop_screenrecord()
        self._recording = False
        name = self._record_name
        remote = self._record_remote
        self._record_name = ""
        self._record_remote = ""

        # Auto-pull recording to run directory (platform-agnostic via driver.pull_file)
        if self._session and remote:
            try:
                time.sleep(1)  # 等设备文件写入完成
                local_path = self._session.recordings_dir / f"{name}.mp4"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                pull_result = self.driver.pull_file(remote, str(local_path))
                if local_path.exists() and local_path.stat().st_size > 0:
                    _runner_log(f"  [rec] 录屏已保存 → {local_path.name} ({local_path.stat().st_size // 1024}KB)")
                    result["local_path"] = str(local_path)
                else:
                    _runner_log(f"  [rec] 录屏 pull 失败: {pull_result.get('stderr', '')}")
            except Exception as e:
                _runner_log(f"  [rec] 录屏保存异常: {e}")

        # 清理设备上的临时文件 (platform-agnostic)
        if remote:
            try:
                self.driver.remove_remote_file(remote)
            except Exception:
                pass
        return result

    # ---- logcat 捕获控制 ----

    def start_logcat(
        self,
        name: str = "",
        *,
        tag: str = "",
        exclude_tag: str = "",
        level: str = "",
        process: str = "",
        clear_before: bool = True,
    ) -> dict:
        """开始后台捕获 logcat/hilog，支持 Android Studio 风格过滤。
        Start background log capture (logcat/hilog) with tag/level filtering.

        tag / exclude_tag 支持前缀和关键字匹配（非精确匹配），多个用逗号分隔。

        Parameters
        ----------
        name : str
            日志文件名（无需扩展名），为空时自动生成。
        tag : str
            包含的 Tag 或关键字（如 ``"Adm-"``，多个用逗号分隔）。
        exclude_tag : str
            排除的 Tag 或关键字（如 ``"chatty"``，多个用逗号分隔）。
        level : str
            最低日志级别：V/D/I/W/E/F。
        process : str
            进程名或 PID 过滤。

        Returns
        -------
        dict
            ``{"ok": True/False, "name": ..., "pid": ...}``
        """
        if clear_before:
            try:
                self.driver.clear_log()
            except Exception:
                pass
            time.sleep(0.5)

        if not name:
            name = f"logcat_{time.strftime('%H%M%S')}"
        if hasattr(self, "_logcat_processes") and name in self._logcat_processes:
            return {"ok": False, "error": f"logcat '{name}' already running"}

        # Determine output path
        if self._session:
            out_path = self._session.logcat_dir / f"{name}.txt"
        else:
            out_path = pathlib.Path(self.out_dir) / f"{name}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Delegate to driver's platform-specific log capture
        result = self.driver.start_log_capture(
            str(out_path),
            tags=tag,
            exclude_tags=exclude_tag,
            level=level,
            process=process,
        )

        if result.get("ok"):
            if not hasattr(self, "_logcat_processes"):
                self._logcat_processes: dict[str, dict] = {}
            self._logcat_processes[name] = {
                "path": out_path,
                "driver_output_path": str(out_path),
            }
            desc_parts = []
            if tag:
                desc_parts.append(f"tag={tag}")
            if level:
                desc_parts.append(f"level>={level.upper()}")
            if exclude_tag:
                desc_parts.append(f"exclude={exclude_tag}")
            desc = f" ({', '.join(desc_parts)})" if desc_parts else ""
            _runner_log(f"  [log] 开始捕获 logcat → {name}{desc}")
        return {"ok": result.get("ok", False), "name": name, "pid": result.get("pid"), "path": str(out_path)}

    def stop_logcat(self, name: str = "") -> dict:
        """停止 logcat/hilog 捕获并保存文件。
        Stop log capture and save file.

        如果 ``name`` 为空且只有一个在运行，则自动停止它。
        If ``name`` is empty and only one is running, auto-stop it.
        """
        if not hasattr(self, "_logcat_processes"):
            return {"ok": False, "error": "no logcat running"}
        procs = self._logcat_processes

        # Auto-resolve name if only one running
        if not name and len(procs) == 1:
            name = next(iter(procs))
        if name not in procs:
            return {"ok": False, "error": f"logcat '{name}' not found"}

        info = procs.pop(name)
        out_path = info["path"]
        driver_output_path = info.get("driver_output_path", str(out_path))

        # Delegate stop to driver
        result = self.driver.stop_log_capture(driver_output_path)

        lines = result.get("lines", 0)
        _runner_log(f"  [log] logcat 已保存 → {out_path.name} ({lines} 行)")
        return {"ok": result.get("ok", False), "name": name, "path": str(out_path), "lines": lines}

    # ---- 自动观测辅助 ----

    def _observe(
        self,
        action: str,
        detail: str = "",
        *,
        status: str = "ok",
        element: Any = None,
    ) -> None:
        """内部方法：在 API 调用后执行自动日志/截图，并保存到 RunSession。

        Parameters:
            action: 操作名称（find_text / find_image / scroll_to_find 等）
            detail: 操作详情
            status: "ok" | "fail"
            element: 可选 UIElement，成功时传入以在截图上标注元素位置
        """
        self._step_counter += 1
        step = self._step_counter

        # Formatted console output
        if self.auto_log:
            ts = time.strftime("%H:%M:%S")
            mark = "\u2713" if status == "ok" else "\u2717"
            step_str = f"Step {step:>2d}"
            line = f"  [{ts}] {mark} {step_str} | {action}"
            if detail:
                line += f"  {detail}"
            _runner_log(line)

        # Screenshot
        png_bytes: bytes | None = None
        if self.auto_screenshot:
            try:
                png_bytes = self.driver.screen.screenshot()
            except Exception:
                pass

        # Annotate element on screenshot if available
        if png_bytes and element is not None and status == "ok":
            png_bytes = _annotate_screenshot(png_bytes, element)

        # Save to RunSession (consolidated steps.json + screenshot to screenshots/)
        if self._session:
            try:
                self._session.add_step(step, action, status, detail, png_bytes=png_bytes)
            except Exception:
                pass
        elif png_bytes:
            try:
                out_root = pathlib.Path(self.out_dir)
                out_root.mkdir(parents=True, exist_ok=True)
                path = out_root / f"auto_step{step:03d}_{action.replace(' ', '_')[:30]}.png"
                path.write_bytes(png_bytes)
            except Exception:
                pass
