"""Tests for HTML visual report generation.

覆盖范围：
- _load_run_data: 各种 JSON 缺失 / 存在的场景
- _render_html: Jinja2 渲染是否产出合法 HTML
- generate_html_report: 端到端，验证 report.html 生成
- 边界场景：空目录、缺失文件、损坏 JSON
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phone_pilot.core.html_report import (
    generate_html_report,
    _load_run_data,
    _render_html,
    _read_json,
)


def _make_run_dir(
    *,
    with_meta: bool = True,
    with_steps: bool = True,
    with_screenshots: bool = True,
    with_logcat: bool = False,
    with_meminfo: bool = False,
    with_healing: bool = False,
    with_console: bool = False,
    exit_code: int = 0,
    num_steps: int = 3,
) -> Path:
    """Create a mock RunSession directory for testing."""
    tmpdir = Path(tempfile.mkdtemp())

    if with_meta:
        meta = {
            "script_name": "test_script",
            "script_path": "test_script.py",
            "started_at": "2026-02-06T10:00:00",
            "finished_at": "2026-02-06T10:01:30",
            "duration_s": 90.0,
            "exit_code": exit_code,
            "status": "passed" if exit_code == 0 else "failed",
            "error_message": "" if exit_code == 0 else "something went wrong",
            "device": {"serial": "ABC123", "platform": "android"},
            "git": {"commit": "abc1234", "branch": "main", "dirty": False, "dirty_files_count": 0},
            "steps": {"total": num_steps, "passed": num_steps if exit_code == 0 else num_steps - 1, "failed": 0 if exit_code == 0 else 1},
            "artifacts": {"screenshots": num_steps, "screen_recordings": 0, "logcat_dumps": 0, "meminfo_snapshots": 0, "console_log_lines": 50},
            "run_dir": str(tmpdir),
        }
        (tmpdir / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    if with_steps:
        steps = []
        for i in range(1, num_steps + 1):
            status = "ok"
            if exit_code != 0 and i == num_steps:
                status = "fail"
            step = {
                "step": i,
                "action": f"find_text_{i}",
                "timestamp": f"2026-02-06T10:{i:02d}:00",
                "status": status,
                "detail": f"Found element {i}",
                "screenshot": f"{i:03d}_find_text_{i}.png" if with_screenshots else None,
            }
            steps.append(step)
        (tmpdir / "steps.json").write_text(json.dumps({"steps": steps}), encoding="utf-8")

    if with_screenshots:
        ss_dir = tmpdir / "screenshots"
        ss_dir.mkdir()
        for i in range(1, num_steps + 1):
            # Create a minimal valid PNG (1x1 pixel)
            (ss_dir / f"{i:03d}_find_text_{i}.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
            )

    if with_logcat:
        lc_dir = tmpdir / "logcat"
        lc_dir.mkdir()
        (lc_dir / "verify.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

    if with_meminfo:
        mi_dir = tmpdir / "meminfo"
        mi_dir.mkdir()
        (mi_dir / "before.json").write_text(json.dumps({"totalPss": 128000, "nativeHeap": 45000}), encoding="utf-8")
        (mi_dir / "after.json").write_text(json.dumps({"totalPss": 135000, "nativeHeap": 48000}), encoding="utf-8")

    if with_healing:
        events = [
            {"timestamp": "2026-02-06T10:00:30", "action": "find_text", "query": "OK", "error_type": "popup_blocked", "fix_type": "popup_dismiss", "fix_source": "popup_guard", "fix_detail": {"dismiss_text": "OK"}, "success": True, "duration_ms": 120},
            {"timestamp": "2026-02-06T10:01:00", "action": "find_image", "query": "btn.png", "error_type": "element_not_found", "fix_type": "llm_fix", "fix_source": "llm_analysis", "fix_detail": {}, "success": False, "duration_ms": 3500},
        ]
        (tmpdir / "healing_events.json").write_text(json.dumps({"healing_events": events, "count": 2}), encoding="utf-8")

    if with_console:
        lines = [f"[10:{i:02d}:00] Step {i} completed" for i in range(60)]
        (tmpdir / "console.log").write_text("\n".join(lines), encoding="utf-8")

    return tmpdir


class TestReadJson(unittest.TestCase):
    def test_valid_json(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        tmp.write_text('{"a": 1}')
        self.assertEqual(_read_json(tmp), {"a": 1})
        tmp.unlink()

    def test_missing_file(self):
        self.assertIsNone(_read_json(Path("/nonexistent/file.json")))

    def test_invalid_json(self):
        tmp = Path(tempfile.mktemp(suffix=".json"))
        tmp.write_text("not json")
        self.assertIsNone(_read_json(tmp))
        tmp.unlink()


class TestLoadRunData(unittest.TestCase):
    def test_full_data(self):
        run_dir = _make_run_dir(
            with_logcat=True, with_meminfo=True,
            with_healing=True, with_console=True,
        )
        data = _load_run_data(run_dir)

        self.assertEqual(data["meta"]["script_name"], "test_script")
        self.assertEqual(data["steps_pass_rate"], 100)
        self.assertEqual(data["duration_display"], "1m30s")
        self.assertEqual(len(data["steps"]), 3)
        self.assertEqual(len(data["screenshots"]), 3)
        self.assertEqual(len(data["logcat_files"]), 1)
        self.assertTrue(data["meminfo_files"])
        self.assertEqual(len(data["healing_events"]), 2)
        self.assertEqual(data["healing_success_rate"], 50)
        self.assertTrue(data["console_tail"])

    def test_empty_dir(self):
        run_dir = _make_run_dir(
            with_meta=False, with_steps=False, with_screenshots=False,
        )
        data = _load_run_data(run_dir)
        self.assertEqual(data["meta"], {
            "device": {}, "git": {},
            "steps": {"total": 0, "passed": 0, "failed": 0},
            "artifacts": {"screenshots": 0, "screen_recordings": 0, "logcat_dumps": 0, "meminfo_snapshots": 0, "console_log_lines": 0},
        })
        self.assertEqual(data["steps"], [])
        self.assertEqual(data["screenshots"], [])

    def test_failed_run(self):
        run_dir = _make_run_dir(exit_code=1, num_steps=5)
        data = _load_run_data(run_dir)
        self.assertEqual(data["meta"]["exit_code"], 1)
        self.assertEqual(data["steps_pass_rate"], 80)

    def test_no_steps(self):
        run_dir = _make_run_dir(num_steps=0)
        data = _load_run_data(run_dir)
        self.assertEqual(data["steps_pass_rate"], 100)  # 0/0 = 100%

    def test_console_tail_truncation(self):
        run_dir = _make_run_dir(with_console=True)
        data = _load_run_data(run_dir)
        # console has 60 lines, tail should be 50
        self.assertEqual(data["console_tail"].count("\n"), 49)  # 50 lines = 49 newlines


class TestRenderHtml(unittest.TestCase):
    def test_renders_valid_html(self):
        run_dir = _make_run_dir()
        data = _load_run_data(run_dir)
        html = _render_html(data)

        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("test_script", html)
        self.assertIn("PASSED", html)
        self.assertIn("</html>", html)

    def test_renders_failed_run(self):
        run_dir = _make_run_dir(exit_code=1)
        data = _load_run_data(run_dir)
        html = _render_html(data)

        self.assertIn("FAILED", html)
        self.assertIn("something went wrong", html)

    def test_renders_healing_events(self):
        run_dir = _make_run_dir(with_healing=True)
        data = _load_run_data(run_dir)
        html = _render_html(data)

        self.assertIn("popup_dismiss", html)
        self.assertIn("50%", html)  # healing success rate

    def test_renders_logcat(self):
        run_dir = _make_run_dir(with_logcat=True)
        data = _load_run_data(run_dir)
        html = _render_html(data)

        self.assertIn("verify.txt", html)

    def test_renders_meminfo(self):
        run_dir = _make_run_dir(with_meminfo=True)
        data = _load_run_data(run_dir)
        html = _render_html(data)

        self.assertIn("内存分析", html)


class TestGenerateHtmlReport(unittest.TestCase):
    def test_generates_report_file(self):
        run_dir = _make_run_dir(
            with_logcat=True, with_meminfo=True,
            with_healing=True, with_console=True,
        )
        report_path = generate_html_report(run_dir)

        self.assertTrue(report_path.exists())
        self.assertEqual(report_path.name, "report.html")
        self.assertGreater(report_path.stat().st_size, 1000)

        html = report_path.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("test_script", html)

    def test_minimal_run(self):
        run_dir = _make_run_dir(with_screenshots=False, num_steps=0)
        report_path = generate_html_report(run_dir)
        self.assertTrue(report_path.exists())

    def test_nonexistent_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            generate_html_report("/nonexistent/dir")

    def test_screenshot_relative_paths(self):
        run_dir = _make_run_dir()
        report_path = generate_html_report(run_dir)
        html = report_path.read_text(encoding="utf-8")

        # Verify screenshots are referenced with relative paths
        self.assertIn('src="screenshots/', html)
        # No base64 data URIs
        self.assertNotIn("data:image/png;base64", html)


if __name__ == "__main__":
    unittest.main()
