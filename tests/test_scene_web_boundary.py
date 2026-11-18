"""Exercise actual localhost transport isolation; no device execution."""

import threading
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from phone_pilot import web
from phone_pilot.scene_web import SceneWebController


def test_untrusted_html_and_symlink_are_not_control_origin(monkeypatch, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "evil.html").write_text('<script>fetch("/api/scene-plan")</script>')
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    (run / "escape.txt").symlink_to(outside)
    (run / "report.html").write_text("<script>window.pwn=1</script>")
    monkeypatch.setattr(web, "_runs_dir", lambda: tmp_path)
    server = HTTPServer(("127.0.0.1", 0), web._Handler)
    server.scene_controller = SceneWebController()
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/run/run/evil.html") as response:
            assert response.headers["Content-Disposition"] == "attachment"
            assert "sandbox" in response.headers["Content-Security-Policy"]
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        try:
            urlopen(base + "/run/run/escape.txt")
            assert False, "symlink must be forbidden"
        except HTTPError as error:
            assert error.code == 403
        with urlopen(base + "/run/run/report.html") as response:
            assert "window.pwn=1" not in response.read().decode()
        try:
            urlopen(Request(base + "/api/delete-run", data=b"name=run", method="POST"))
            assert False, "cross-origin/no-origin POST must be rejected"
        except HTTPError as error:
            assert error.code == 403
        assert run.is_dir()
    finally:
        server.shutdown()
        thread.join()
        server.scene_controller.close()
        server.server_close()


def test_web_uses_same_recordings_root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_PILOT_RECORDINGS_DIR", str(tmp_path))
    from phone_pilot.core.run_store import run_recordings_root

    assert web._recordings_root() == run_recordings_root()
    monkeypatch.setenv("PHONE_PILOT_CACHE_DIR", str(tmp_path / "resources"))
    from phone_pilot.core.resource import resource_cache_dir

    assert web._res_cache_dir() == resource_cache_dir()
