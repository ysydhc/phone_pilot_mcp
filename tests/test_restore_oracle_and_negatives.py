"""Round 3 Deterministic Unit & Fault-Injection Negative Gates (`R3-TC-01` .. `R3-TC-04`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from phone_pilot.core.scene_restore import (
    SceneError,
    _validate_png,
    evaluate_restore_verification_oracle,
    inspect_png_non_blank,
)
from phone_pilot.scene_web import SceneWebController


def _encode_png_bgr(img_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img_bgr)
    assert ok
    return bytes(buf)


def test_r3_tc01_solid_black_and_solid_white_dead_screen_blocked():
    """R3-TC-01: Solid black or solid white 1080x2400 dead screen MUST fail `visual_non_blank`."""
    black_img = np.zeros((2400, 1080, 3), dtype=np.uint8)
    # Even if system status bar at top 5% has white clock pixels, center viewport is dead black!
    black_img[20:60, 60:220] = (255, 255, 255)
    black_png = _encode_png_bgr(black_img)

    vis = inspect_png_non_blank(black_png)
    assert vis["ok"] is False
    assert vis["dark_ratio"] >= 0.96

    oracle = evaluate_restore_verification_oracle(
        png_bytes=black_png,
        log_text="11-25 03:40:01.100  1234  1234 I Demo: Loaded",
        ui_xml="<hierarchy><node text='Loaded'/></hierarchy>",
        process_alive=True,
        has_mock_rules=True,
        mock_requests_count=2,
    )
    assert oracle["ok"] is False
    by_id = {c["id"]: c for c in oracle["checks"]}
    assert by_id["visual_non_blank"]["status"] == "FAIL"

    white_img = np.full((2400, 1080, 3), 255, dtype=np.uint8)
    white_png = _encode_png_bgr(white_img)
    assert inspect_png_non_blank(white_png)["ok"] is False


def test_r3_tc02_crash_anr_and_dead_process_blocked():
    """R3-TC-02: `FATAL EXCEPTION` / `ANR in` or dead process MUST fail verification oracle."""
    normal_img = np.full((1280, 720, 3), 245, dtype=np.uint8)
    cv2.rectangle(normal_img, (80, 220), (640, 960), (37, 99, 235), -1)
    cv2.rectangle(normal_img, (120, 780), (600, 880), (34, 197, 94), -1)
    png_bytes = _encode_png_bgr(normal_img)

    crash_oracle = evaluate_restore_verification_oracle(
        png_bytes=png_bytes,
        log_text="11-25 03:40:02.500  4321  4321 E AndroidRuntime: FATAL EXCEPTION: main\njava.lang.NullPointerException",
        process_alive=True,
        mock_requests_count=1,
    )
    assert crash_oracle["ok"] is False
    by_id = {c["id"]: c for c in crash_oracle["checks"]}
    assert by_id["zero_crash"]["status"] == "FAIL"
    assert by_id["visual_non_blank"]["status"] == "PASS"

    dead_proc_oracle = evaluate_restore_verification_oracle(
        png_bytes=png_bytes,
        log_text="11-25 03:40:02.500  4321  4321 I Demo: Ready",
        process_alive=False,
        mock_requests_count=1,
    )
    assert dead_proc_oracle["ok"] is False
    assert {c["id"]: c for c in dead_proc_oracle["checks"]}["process_alive"]["status"] == "FAIL"

    # Real system ANR (`ActivityManager: ANR in ...`) MUST fail, while benign `REASON_ANR in recent` MUST pass
    real_anr = evaluate_restore_verification_oracle(
        png_bytes=png_bytes,
        log_text="11-25 04:03:47.355  1000  1020 E ActivityManager: ANR in com.vendor.demo",
        process_alive=True,
        mock_requests_count=1,
    )
    assert real_anr["ok"] is False
    assert {c["id"]: c for c in real_anr["checks"]}["zero_crash"]["status"] == "FAIL"

    benign_exit_info = evaluate_restore_verification_oracle(
        png_bytes=png_bytes,
        log_text="11-25 04:03:47.355 26126 26171 I CrashCollector: ANR exit_info: no REASON_ANR in recent 16 exit records",
        process_alive=True,
        mock_requests_count=1,
    )
    assert benign_exit_info["ok"] is True
    assert {c["id"]: c for c in benign_exit_info["checks"]}["zero_crash"]["status"] == "PASS"


def test_r3_tc03_dark_mode_ui_passes_and_corrupted_crc_png_rejected():
    """R3-TC-03: Genuine Dark Mode UI (#121212 + white card/CTA) passes; corrupted IDAT CRC fails `_validate_png`."""
    dark_ui = np.full((2400, 1080, 3), 18, dtype=np.uint8)  # #121212 dark background
    # White split-card & blue CTA button in Dark Mode
    cv2.rectangle(dark_ui, (70, 1350), (1010, 2050), (248, 250, 252), -1)
    cv2.rectangle(dark_ui, (120, 1850), (960, 1980), (37, 99, 235), -1)
    dark_png = _encode_png_bgr(dark_ui)

    custom_prof = {
        "verify_contract": {
            "load_tokens": ["customLoadOk"],
            "show_tokens": ["customRenderShown"],
            "expected_error_tokens": ["ERR_NO_INVENTORY_204"],
        }
    }
    ok_oracle = evaluate_restore_verification_oracle(
        png_bytes=dark_png,
        log_text="I/SDK: customLoadOk\nI/SDK: customRenderShown",
        ui_xml="<hierarchy><node text='Install'/></hierarchy>",
        process_alive=True,
        has_mock_rules=True,
        mock_requests_count=3,
        pp_profile=custom_prof,
    )
    assert ok_oracle["ok"] is True
    assert ok_oracle["target_loaded"] is True
    assert ok_oracle["target_shown"] is True
    assert all(c["status"] == "PASS" for c in ok_oracle["checks"])

    # Expected error fallback scene (e.g. No-Fill) passes cleanly via verify_contract
    err_oracle = evaluate_restore_verification_oracle(
        png_bytes=dark_png,
        log_text="W/SDK: ERR_NO_INVENTORY_204",
        process_alive=True,
        has_mock_rules=True,
        mock_requests_count=1,
        pp_profile=custom_prof,
    )
    assert err_oracle["ok"] is True
    assert err_oracle["expected_error_hit"] is True
    assert "预期异常兜底" in err_oracle["summary"]

    # Corrupt a byte inside IDAT payload so CRC32 mismatches
    corrupted = bytearray(dark_png)
    idat_pos = bytes(corrupted).find(b"IDAT")
    assert idat_pos > 0
    corrupted[idat_pos + 6] ^= 0xFF
    with pytest.raises(SceneError) as exc_info:
        _validate_png(bytes(corrupted))
    assert exc_info.value.code == "evidence_failed"
    assert "CRC" in str(exc_info.value)


def test_r3_tc04_scene_restore_free_of_ad_callback_literals_and_csrf_blocked(tmp_path: Path):
    """R3-TC-04: `scene_restore.py` has zero hardcoded `onAdLoaded`/`SHOW_CALLED`, and CSRF is blocked."""
    import re

    sr_path = Path(__file__).resolve().parents[1] / "phone_pilot" / "core" / "scene_restore.py"
    sr_text = sr_path.read_text(encoding="utf-8")
    for banned in ("onAdLoaded", "onAdFailed", "SHOW_CALLED", "200000"):
        assert re.search(rf"\b{re.escape(banned)}\b", sr_text) is None, (
            f"Residual domain literal '{banned}' still in scene_restore.py"
        )

    ctrl = SceneWebController(["f" * 64], auto_approve=False)
    try:
        blocked = ctrl.submit(
            tmp_path,
            serial="98e516bf0922",
            manifest_hash="f" * 64,
            token=ctrl.token,
            origin="http://attacker.example.com:8686",
            host="127.0.0.1:8686",
        )
        assert blocked["ok"] is False
        assert blocked["status"] == "BLOCKED"
    finally:
        ctrl.close()


def test_r4_tc01_package_injection_and_cross_site_get_blocked(monkeypatch):
    """R4-TC-01: Malicious `target_package` NEVER interpolates into `adb shell`, and cross-site GET is 403."""
    import io
    import subprocess
    import phone_pilot.web as web
    from phone_pilot.core import manifest_generator as mg

    captured_cmds: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        captured_cmds.append(list(cmd))

        class _Out:
            returncode = 0
            stdout = "Redmi\nNote12\n14\nPhysical size: 1080x2400\n"

        return _Out()

    monkeypatch.setattr(mg, "get_online_devices", lambda: ["98e516bf0922"])
    monkeypatch.setattr(subprocess, "run", fake_run)
    devs = mg.get_online_devices_detailed(
        target_package="cc.demo; touch /sdcard/pwned", platform_filter="android"
    )
    assert len(devs) == 1
    assert captured_cmds, "Expected adb shell call"
    shell_str = " ".join(captured_cmds[0])
    assert "pwned" not in shell_str and "pm path" not in shell_str

    # Verify web.py blocks DNS rebinding Host and Sec-Fetch-Site: cross-site on /api/devices
    for bad_headers in (
        {"Host": "evil.attacker.com:8686"},
        {"Host": "127.0.0.1:8686", "Sec-Fetch-Site": "cross-site"},
        {"Host": "127.0.0.1:8686", "Origin": "http://evil.example.com"},
    ):
        handler = object.__new__(web._Handler)
        handler.path = "/api/devices?package=com.example.app"
        handler.headers = bad_headers
        handler.wfile = io.BytesIO()
        responses: list[tuple[Any, int]] = []
        handler._send_json = lambda obj, code=200, **kw: responses.append((obj, code))
        handler.do_GET()
        assert responses and responses[0][1] == 403


def test_r4_tc02_readonly_zero_writeback_and_scene_id_traversal_blocked(tmp_path: Path, monkeypatch):
    """R4-TC-02: `render_html_report` does ZERO disk writes even with `ALLOW_ONLINE=1`, `duration_s: null`, and `../` case_id."""
    import json
    from phone_pilot.core.html_report import render_html_report
    from phone_pilot.core.safe_io import SafeDirectory

    monkeypatch.setenv("PHONE_PILOT_SCENE_ALLOW_ONLINE", "1")
    run_dir = tmp_path / "runs" / "readonly_traversal_run"
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps({"script_name": "readonly_check", "duration_s": None, "status": "passed"}),
        encoding="utf-8",
    )
    (run_dir / "business_report.json").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "cases": [{"case_id": "../../escape_attempt", "title": "Traversal Case"}],
                "business_steps": [{"case_id": "../../escape_attempt", "status": "FAIL", "actual": "failed"}],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        SafeDirectory,
        "write_bytes",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected write_bytes during GET")),
    )
    run_dir.chmod(0o555)
    try:
        html = render_html_report(run_dir)
        assert "Traversal Case" in html
        assert not (run_dir / "scene_manifest.json").exists()
        assert not (run_dir / "scene_catalog.json").exists()
        assert not (tmp_path / "escape_attempt").exists()
    finally:
        run_dir.chmod(0o700)


def test_r4_tc03_html_attribute_and_xss_escaping(tmp_path: Path):
    """R4-TC-03: Quotes in `meta.run_dir` or `<img onerror>` in step details never break HTML attributes."""
    import json
    from phone_pilot.core.html_report import render_html_report

    run_dir = tmp_path / "runs" / "xss_check_run"
    run_dir.mkdir(parents=True)
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "script_name": "xss_check",
                "run_dir": '/tmp/weird"quote/run',
                "duration_s": 5.0,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "steps.json").write_text(
        json.dumps({"steps": [{"action": "tap_text", "detail": '<img src=x onerror="alert(1)">'}]}),
        encoding="utf-8",
    )
    html = render_html_report(run_dir)
    assert 'onclick="if(window.copyToClipboard)window.copyToClipboard(\'bash \' + "' not in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_r4_tc04_rulestore_thread_safety_and_head_empty_body():
    """R4-TC-04: `RuleStore` survives concurrent clear/set/match without RuntimeError, and `HEAD` returns empty body."""
    import threading
    from phone_pilot.mock.rules import RequestLog, RuleStore

    store = RuleStore()
    req_log = RequestLog()
    errors: list[Exception] = []

    def writer():
        try:
            for i in range(120):
                store.set_rule("GET", f"/item/{i % 8}", status=200, body={"idx": i})
                if i % 15 == 0:
                    store.clear()
        except Exception as exc:
            errors.append(exc)

    def reader():
        try:
            for i in range(120):
                _ = store.list_rules()
                _ = store.match("GET", f"/item/{i % 8}?q=1")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert errors == []

    store.set_rule("GET", "/probe", status=200, body={"hello": "world"})
    handler_cls = __import__(
        "phone_pilot.mock.server", fromlist=["_make_data_handler"]
    )._make_data_handler(store, req_log)
    import io

    h = object.__new__(handler_cls)
    h.command = "HEAD"
    h.path = "/probe"
    h.request_version = "HTTP/1.1"
    h.headers = {}
    h.wfile = io.BytesIO()
    sent_status: list[int] = []
    sent_headers: dict[str, str] = {}
    h.send_response = lambda code, msg=None: sent_status.append(code)
    h.send_header = lambda k, v: sent_headers.__setitem__(k, v)
    h.end_headers = lambda: None
    h.do_HEAD()
    assert sent_status == [200]
    assert int(sent_headers.get("Content-Length") or "0") > 0
    assert h.wfile.getvalue() == b""


def test_r5_tc01_launcher_bash32_empty_array_safe_and_resolve_run_cors(tmp_path: Path):
    """R5-TC-01: `open_pp_server.command` passes `/bin/bash -n` and `set -u` empty-array expansion without unbound variable."""
    import io
    import subprocess
    import phone_pilot.web as web
    from phone_pilot.core.html_report import _write_launcher_script

    isolated_run = tmp_path / "isolated_workspace" / "proj" / ".recordings" / "runs" / "sample_run"
    isolated_run.mkdir(parents=True)
    launcher = _write_launcher_script(isolated_run)
    script_txt = launcher.read_text(encoding="utf-8")
    assert '${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}' in script_txt
    chk = subprocess.run(["/bin/bash", "-n", str(launcher)], capture_output=True, text=True)
    assert chk.returncode == 0, chk.stderr
    # Verify `/bin/bash -u` actually evaluates empty EXTRA_ARGS=() cleanly
    u_chk = subprocess.run(
        ["/bin/bash", "-c", 'set -euo pipefail; EXTRA_ARGS=(); printf "%s" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}'],
        capture_output=True,
        text=True,
    )
    assert u_chk.returncode == 0, u_chk.stderr

    # Verify `/api/resolve-run` works from `file://` (`Origin: null`) with `allow_cors=True`, while `/api/scene-plan` stays 403
    h_resolve = object.__new__(web._Handler)
    h_resolve.path = "/api/resolve-run?name=sample_run"
    h_resolve.headers = {"Host": "127.0.0.1:8686", "Origin": "null", "Sec-Fetch-Site": "cross-site"}
    h_resolve._scene_run_dir = lambda name: isolated_run if name == "sample_run" else None
    out_res: list[tuple[Any, int, bool]] = []
    h_resolve._send_json = lambda obj, code=200, allow_cors=False: out_res.append((obj, code, allow_cors))
    h_resolve.do_GET()
    assert out_res == [({"ok": True, "found": True}, 200, True)]

    h_plan = object.__new__(web._Handler)
    h_plan.path = "/api/scene-plan?name=sample_run"
    h_plan.headers = {"Host": "127.0.0.1:8686", "Origin": "null", "Sec-Fetch-Site": "cross-site"}
    h_plan.wfile = io.BytesIO()
    out_plan: list[tuple[Any, int]] = []
    h_plan._send_json = lambda obj, code=200, **kw: out_plan.append((obj, code))
    h_plan.do_GET()
    assert out_plan and out_plan[0][1] == 403


def test_r5_tc02_stale_cross_platform_actions_healed_and_top_level_subcases_extracted(tmp_path: Path):
    """R5-TC-02: Stale foreign-scheme `actions.json` is discarded, summary deduplicates whitespace keys, and top-level subcases extract."""
    import json
    from phone_pilot.core.manifest_generator import ensure_scene_catalog

    run_dir = tmp_path / "runs" / "historic_ios_run"
    (run_dir / "scenes" / "suite_1_edge").mkdir(parents=True)
    # Poison cached actions.json with a foreign platform scheme (`app_android://`)
    (run_dir / "scenes" / "suite_1_edge" / "actions.json").write_text(
        json.dumps([{"type": "open_url", "url": "app_android://page/wrong"}]),
        encoding="utf-8",
    )
    (run_dir / "pp_profile.json").write_text(
        json.dumps(
            {
                "schema": "phone_pilot.pp_profile.v2",
                "platforms": {
                    "android": {"package": "com.demo.android", "scheme_prefix": "app_android://"},
                    "ios": {"package": "com.demo.ios", "scheme_prefix": "app_ios://"},
                },
                "scene_recipes": [
                    {
                        "id": "edge",
                        "match_any": ["edge"],
                        "actions": [{"type": "open_url", "url": "app_ios://page/correct"}],
                        "target": {"activity": ".Main", "selectors": [{"text": "Ready"}]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_meta.json").write_text(
        json.dumps({"script_name": "test_release_gate", "device": {"platform": "ios", "serial": "SIM-1"}}),
        encoding="utf-8",
    )
    (run_dir / "business_report.json").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "cases": [{"case_id": "suite_1_edge", "title": "边角与变体套件", "group": "edge"}],
                "business_steps": [{"case_id": "suite_1_edge", "status": "FAIL", "actual": "1/2"}],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "release_gate_summary.json").write_text(
        json.dumps(
            {
                "ok": False,
                "summary": [
                    {
                        "file": "test_edge.py",
                        "suite": "边角与变体套件",
                        "group": "edge",
                        "ok": False,
                        "failure_reason": "show_fault_guards assertion mismatch",
                        "raw": {
                            "ok": False,
                            "debug_env": {"ok": True, "detail": "PASS"},
                            "show_fault_guards": {"ok": False, "faults": {"null_listener": {"ok": False}}},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cat = ensure_scene_catalog(run_dir, persist=False)
    assert len(cat["scenes"]) == 1
    sc = cat["scenes"][0]
    assert sc["actions"] == [{"type": "open_url", "url": "app_ios://page/correct"}]
    sub_names = [c["name"] for c in sc["sub_cases"]]
    assert "show_fault_guards" in sub_names and "debug_env" in sub_names
    assert any("失败归因: show_fault_guards assertion mismatch" in item["line"] for item in sc["timeline_logs"])

