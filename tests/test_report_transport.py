import hashlib
import json
from phone_pilot.core.html_report import generate_html_report
from phone_pilot.core.report_evidence import evidence_projection
from phone_pilot.core.run_store import RunSession
from phone_pilot.scene_web import SceneWebController


def test_evidence_traversal_checksum_and_log_range(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("first\n<script>bad</script>\nlast")
    entry = {
        "path": "log.txt",
        "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "metadata": {"log_range": {"start": 2, "end": 2}},
    }
    item = evidence_projection(tmp_path, entry)
    assert item["available"] and item["preview"] == "2: <script>bad</script>"
    assert not evidence_projection(tmp_path, dict(entry, path="../private"))[
        "available"
    ]
    assert not evidence_projection(tmp_path, dict(entry, sha256="0" * 64))["available"]
    bad = evidence_projection(
        tmp_path, dict(entry, metadata={"log_range": {"start": None}})
    )
    assert not bad["available"] and bad["url"] is None


def test_render_business_fail_recovery_and_missing_declared(tmp_path):
    session = RunSession.create("test", recordings_dir=str(tmp_path))
    session.declare_cases(
        [
            {
                "case_id": "TC",
                "steps": [
                    {
                        "business_step_id": key,
                        "title": key,
                        "required_dimensions": ["ui"],
                        "not_applicable": {
                            "callback_log": "offline UI only",
                            "network": "offline",
                        },
                    }
                    for key in ("load", "close")
                ],
            }
        ]
    )
    session.begin_business_step("TC", "load")
    session.end_business_step("FAIL", actual="<script>bad()</script>")
    session.begin_business_step("TC", "load")
    e = session.add_evidence("ui", data="<hierarchy/>")
    session.add_assertion(
        "ui", "PASS", "present", "present", evidence_refs=[e["evidence_id"]]
    )
    session.end_business_step(actual="visible")
    session.finalize()
    rendered = generate_html_report(session.run_dir).read_text()
    assert "RECOVERED" in rendered and "NOT_RUN" in rendered
    assert "&lt;script&gt;bad()&lt;/script&gt;" in rendered
    assert "声明 2 步" in rendered
    assert "不是打开历史截图" in rendered
    assert "原始失败与每次尝试" in rendered


def test_storyboard_does_not_relabel_failed_screenshot_as_final_pass(tmp_path):
    from phone_pilot.core.business_report import load_business_report
    from phone_pilot.core.report_evidence import business_projection
    from phone_pilot.core.html_report import render_html_report

    session = RunSession.create("retry_storyboard", recordings_dir=str(tmp_path))
    session.declare_cases(
        [
            {
                "case_id": "TC",
                "steps": [
                    {
                        "business_step_id": "load",
                        "required_dimensions": ["ui"],
                        "not_applicable": {
                            "callback_log": "offline",
                            "network": "offline",
                        },
                    }
                ],
            }
        ]
    )
    session.begin_business_step("TC", "load")
    failed = session.add_evidence("screenshot", data=b"firstfailed.png")
    session.end_business_step("FAIL")
    session.begin_business_step("TC", "load")
    final = session.add_evidence("ui_tree", data="<hierarchy/>")
    session.add_assertion(
        "ui", "PASS", "present", "present", evidence_refs=[final["evidence_id"]]
    )
    assert session.end_business_step()["status"] == "PASS"
    session.finalize()
    report = business_projection(session.run_dir, load_business_report(session.run_dir))
    step = report["steps"][0]
    assert step["final_result"] == "PASS"
    assert step["screens"] == []
    assert (
        step["attempts"][0]["display_evidence"][0]["evidence_id"]
        == failed["evidence_id"]
    )
    assert step["attempts"][0]["display_evidence"][0]["available"]
    rendered = render_html_report(session.run_dir)
    storyboard = rendered.split('<div class="storyboard">', 1)[1].split(
        "</section>", 1
    )[0]
    assert "未采集完整屏幕" in storyboard
    assert failed["path"] not in storyboard


def test_restore_web_rejects_csrf_and_unapproved_without_execution(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        "phone_pilot.core.scene_restore.restore_scene",
        lambda *a, **kw: calls.append(kw),
    )
    controller = SceneWebController(["a" * 64])
    try:
        for args in (
            dict(token=controller.token, origin="http://evil", manifest_hash="a" * 64),
            dict(token="wrong", origin="http://127.0.0.1:8686", manifest_hash="a" * 64),
            dict(
                token=controller.token,
                origin="http://127.0.0.1:8686",
                manifest_hash="b" * 64,
            ),
            dict(token=controller.token, origin=None, manifest_hash="a" * 64),
        ):
            result = controller.submit(
                tmp_path, serial="explicit", host="127.0.0.1:8686", **args
            )
            assert result["status"] == "BLOCKED"
        assert calls == []
    finally:
        controller.close()


def test_readonly_report_get_never_writes_archive(tmp_path, monkeypatch):
    from phone_pilot.web import _Handler
    from phone_pilot.core.safe_io import SafeDirectory

    root = tmp_path / "run"
    root.mkdir()
    (root / "report.html").write_text("<script>stored-untrusted</script>")
    (root / "run_meta.json").write_text('{"script_name":"readonly"}')
    before = (root / "report.html").read_bytes()
    root.chmod(0o555)
    tmp_path.chmod(0o777)  # readable shared ancestor is legal for read-only viewing
    monkeypatch.setattr(
        SafeDirectory,
        "write_bytes",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("write-back")),
    )
    handler = object.__new__(_Handler)
    handler._scene_run_dir = lambda name: root
    output = []
    handler._send_html = output.append
    handler._send_error = lambda *a: (_ for _ in ()).throw(AssertionError(a))
    try:
        handler._serve_run_file("run", "report.html")
        assert "readonly" in output[0]
        assert "stored-untrusted" not in output[0]
        assert (root / "report.html").read_bytes() == before
    finally:
        root.chmod(0o700)
        tmp_path.chmod(0o700)


def test_large_projection_streams_video_and_logs(tmp_path, monkeypatch):
    from phone_pilot.core.safe_io import SafeDirectory

    for name in ("large.mp4", "large.log"):
        path = tmp_path / name
        with path.open("wb") as stream:
            stream.write(b"first\nsecond\n")
            stream.truncate(33 * 1024 * 1024)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        monkeypatch.setattr(
            SafeDirectory,
            "read_bytes",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("whole load")),
        )
        item = evidence_projection(
            tmp_path,
            {
                "path": name,
                "sha256": digest.hexdigest(),
                "metadata": {"log_range": {"start": 1, "end": 2}},
            },
        )
        assert item["available"]
        if name.endswith("log"):
            assert item["preview"] == "1: first\n2: second"


def test_static_resource_rejects_symlink_and_traversal(tmp_path, monkeypatch):
    import io
    import phone_pilot.web as web

    outside = tmp_path / "private.txt"
    outside.write_text("secret")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "image.png").symlink_to(outside)
    handler = object.__new__(web._Handler)
    handler.wfile = io.BytesIO()
    errors = []
    handler._send_error = lambda *args: errors.append(args)
    monkeypatch.setattr(web, "_res_cache_dir", lambda: cache)
    handler._serve_resource("image")
    handler._serve_resource("../private.txt")
    assert [e[0] for e in errors] == [403, 403]
    assert handler.wfile.getvalue() == b""


def test_readonly_run_without_persisted_html_has_standard_view(tmp_path, monkeypatch):
    import phone_pilot.web as web

    runs = tmp_path / "runs"
    root = runs / "readonly"
    root.mkdir(parents=True)
    (root / "run_meta.json").write_text('{"script_name":"readonly"}')
    root.chmod(0o555)
    monkeypatch.setattr(web, "_runs_dir", lambda: runs)
    monkeypatch.setattr(web, "_load_resources", lambda: [])
    try:
        assert web._load_runs()[0]["has_report"]
        assert "/run/readonly/report.html" in web._render_dashboard()
        handler = object.__new__(web._Handler)
        rendered = []
        handler._send_html = rendered.append
        handler._send_error = lambda *a: (_ for _ in ()).throw(AssertionError(a))
        handler._serve_run_file("readonly", "report.html")
        assert "readonly" in rendered[0]
        assert not (root / "report.html").exists()
    finally:
        root.chmod(0o700)


def test_projection_null_metadata_is_unavailable_not_exception(tmp_path):
    item = evidence_projection(tmp_path, {"path": "log.txt", "metadata": None})
    assert not item["available"]
    assert "metadata" in item["error"]


def test_nonfinite_log_range_is_unavailable_not_exception(tmp_path):
    for field in ("start", "end"):
        for value in (float("inf"), float("-inf"), float("nan")):
            item = evidence_projection(
                tmp_path, {"path": "log.txt", "metadata": {"log_range": {field: value}}}
            )
            assert not item["available"] and item["error"]


def test_report_view_and_generation_flags_are_independent(tmp_path, monkeypatch):
    import phone_pilot.web as web

    runs = tmp_path / "runs"
    for name in ("writable", "readonly", "persisted"):
        root = runs / name
        root.mkdir(parents=True)
        (root / "run_meta.json").write_text(json.dumps({"script_name": name}))
    (runs / "persisted" / "report.html").write_text("stored")
    (runs / "readonly").chmod(0o555)
    monkeypatch.setattr(web, "_runs_dir", lambda: runs)
    monkeypatch.setattr(web, "_load_resources", lambda: [])
    try:
        records = {r["run_dir"]: r for r in web._load_runs()}
        assert all(r["view_available"] for r in records.values())
        assert not records["writable"]["has_report_file"]
        assert records["writable"]["can_generate_report"]
        assert not records["readonly"]["can_generate_report"]
        assert records["persisted"]["has_report_file"]
        dashboard = web._render_dashboard()
        assert "/run/writable/report.html" in dashboard
        assert "/run/readonly/report.html" in dashboard
        assert 'class="btn-gen" data-run="writable"' in dashboard
        assert 'class="btn-gen" data-run="readonly"' not in dashboard
        assert 'class="btn-gen" data-run="persisted"' not in dashboard
        assert ".btn-gen[data-run]" in dashboard  # existing batch selector survives
    finally:
        (runs / "readonly").chmod(0o700)
