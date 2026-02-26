"""Tests for RunSession lifecycle: create, add_step, finalize."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from phone_pilot.core.run_store import RunSession, run_recordings_root


class TestRunRecordingsRoot(unittest.TestCase):
    """Test the recordings root resolution logic."""

    def test_default_is_cwd_recordings(self):
        root = run_recordings_root(None)
        self.assertTrue(str(root).endswith(".recordings"))

    def test_explicit_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = run_recordings_root(tmp)
            self.assertEqual(root, Path(tmp).resolve())

    def test_env_var_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PHONE_PILOT_RECORDINGS_DIR"] = tmp
            try:
                root = run_recordings_root(None)
                self.assertEqual(root, Path(tmp).resolve())
            finally:
                del os.environ["PHONE_PILOT_RECORDINGS_DIR"]


class TestRunSessionLifecycle(unittest.TestCase):
    """Test RunSession create, add_step, save_screenshot, finalize."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_session(self):
        return RunSession.create(
            "test_script",
            recordings_dir=self.tmpdir,
            device_serial="fake_serial",
            platform="android",
        )

    def test_create_directory_structure(self):
        session = self._make_session()
        self.assertTrue(session.run_dir.exists())
        self.assertTrue(session.screenshots_dir.exists())
        self.assertTrue(session.recordings_dir.exists())
        self.assertTrue(session.logcat_dir.exists())
        self.assertTrue((session.run_dir / "git_info.json").exists())
        session.close()

    def test_add_step_records(self):
        session = self._make_session()
        session.add_step(1, "find_text", "ok", "Found 'Settings'")
        session.add_step(2, "tap", "ok", "Tapped (100, 200)")
        self.assertEqual(len(session._steps), 2)
        self.assertEqual(session._steps[0]["action"], "find_text")
        self.assertEqual(session._steps[1]["step"], 2)
        session.close()

    def test_add_step_with_screenshot(self):
        session = self._make_session()
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        session.add_step(1, "screenshot", "ok", "", png_bytes=fake_png)
        # Screenshot should be saved
        pngs = list(session.screenshots_dir.glob("*.png"))
        self.assertEqual(len(pngs), 1)
        self.assertTrue(pngs[0].name.startswith("001_"))
        session.close()

    def test_add_failed_step(self):
        session = self._make_session()
        session.add_step(1, "find_text", "fail", "Not found after 3 retries")
        self.assertEqual(session._steps[0]["status"], "fail")
        session.close()

    def test_finalize_writes_steps_json_and_meta(self):
        session = self._make_session()
        session.add_step(1, "find_text", "ok", "Found")
        session.add_step(2, "tap", "fail", "Element gone")

        meta_path = session.finalize(
            exit_code=1,
            steps_total=2,
            steps_passed=1,
            steps_failed=1,
            error_message="Step 2 failed",
        )

        # steps.json exists with correct content
        steps_path = session.run_dir / "steps.json"
        self.assertTrue(steps_path.exists())
        steps_data = json.loads(steps_path.read_text("utf-8"))
        self.assertEqual(len(steps_data["steps"]), 2)
        self.assertEqual(steps_data["steps"][0]["status"], "ok")
        self.assertEqual(steps_data["steps"][1]["status"], "fail")

        # run_meta.json exists
        self.assertTrue(meta_path.exists())
        meta = json.loads(meta_path.read_text("utf-8"))
        self.assertEqual(meta["status"], "failed")
        self.assertEqual(meta["exit_code"], 1)
        self.assertEqual(meta["steps"]["total"], 2)
        self.assertEqual(meta["steps"]["failed"], 1)

    def test_save_logcat(self):
        session = self._make_session()
        path = session.save_logcat("test_log", "D/MyTag: hello world\n")
        self.assertTrue(path.exists())
        self.assertIn("hello world", path.read_text("utf-8"))
        session.close()

    def test_save_recording(self):
        session = self._make_session()
        path = session.save_recording("demo", b"\x00\x00\x00\x18ftypmp42")
        self.assertTrue(path.exists())
        self.assertTrue(path.name.endswith(".mp4"))
        session.close()


if __name__ == "__main__":
    unittest.main()
