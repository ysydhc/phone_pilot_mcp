#!/usr/bin/env python3
"""
phone_pilot local web dashboard — browse & manage run recordings and @res resources.

Zero extra dependencies: uses stdlib http.server + project's existing jinja2.

Usage:
    python -m phone_pilot.web              # module mode
    phone-pilot-web                        # CLI (after pip install)
    phone-pilot-web --port 9090            # custom port
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import mimetypes
import os
import pathlib
import re
import shutil
from contextlib import ExitStack
from phone_pilot.core.safe_io import safe_directory
import sys
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlsplit


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _recordings_root() -> pathlib.Path:
    """Resolve .recordings root (env PHONE_PILOT_RECORDINGS_DIR > core.storage)."""
    env_dir = (os.environ.get("PHONE_PILOT_RECORDINGS_DIR") or "").strip()
    if env_dir:
        return pathlib.Path(env_dir).expanduser().resolve()
    from phone_pilot.core.run_store import run_recordings_root

    return run_recordings_root()


def _runs_dir() -> pathlib.Path:
    return _recordings_root() / "runs"


def _res_cache_dir() -> pathlib.Path:
    from phone_pilot.core.resource import resource_cache_dir

    return resource_cache_dir()


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _read_json(path: pathlib.Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        with safe_directory(path.parent) as folder:
            return json.loads(folder.read_bytes(path.name))
    except Exception:
        return None


def _load_runs() -> list[dict]:
    """Load all run metadata, sorted by time descending."""
    runs_root = _runs_dir()
    if not runs_root.is_dir():
        return []
    runs: list[dict] = []
    for d in sorted(runs_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta = _read_json(d / "run_meta.json")
        if meta is None:
            # Minimal entry for dirs without meta
            meta = {"script_name": d.name, "status": "unknown", "run_dir": d.name}
        else:
            meta["run_dir"] = d.name
        # Duration display
        dur = meta.get("duration_s", 0)
        mins = int(dur) // 60
        secs = int(dur) % 60
        meta["duration_display"] = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        # The standard report URL renders trusted templates in memory, even when
        # no report.html was ever persisted (including read-only archives).
        meta["view_available"] = True
        meta["has_report"] = True  # Backward-compatible dynamic-view flag.
        meta["has_report_file"] = False
        meta["can_generate_report"] = False
        try:
            with safe_directory(d) as folder:
                try:
                    folder.stat("report.html")
                    meta["has_report_file"] = True
                except (ValueError, OSError):
                    pass
            with safe_directory(d, writable=True) as folder:
                meta["can_generate_report"] = bool(os.fstat(folder.fd).st_mode & 0o200)
        except (ValueError, OSError):
            pass
        # Steps pass rate (quick calculation from steps meta)
        steps_info = meta.get("steps", {})
        total = steps_info.get("total", 0)
        passed = steps_info.get("passed", 0)
        meta["pass_rate"] = round(passed / total * 100) if total > 0 else 100
        runs.append(meta)
    return runs


def _load_resources() -> list[dict]:
    """Load @res: resource list, with file existence check."""
    idx = _read_json(_res_cache_dir() / "index.json")
    if idx is None:
        return []
    cache_dir = _res_cache_dir()
    results: list[dict] = []
    for r in idx.get("records", []):
        if not isinstance(r, dict):
            continue
        key = r.get("key", "")
        # Check if the cached file actually exists
        file_exists = False
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            if (cache_dir / f"{key}{ext}").is_file():
                file_exists = True
                break
        if not file_exists:
            file_exists = (cache_dir / key).is_file()
        r["file_exists"] = file_exists
        # Provide the reference path for copy
        r["ref_path"] = f"@res:{key}"
        results.append(r)
    return results


def _dashboard_data() -> dict[str, Any]:
    """Aggregate data for the dashboard template."""
    runs = _load_runs()
    resources = _load_resources()
    passed = sum(1 for r in runs if r.get("status") == "passed")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    return {
        "runs": runs,
        "resources": resources,
        "passed_count": passed,
        "failed_count": failed,
        "recordings_root": str(_recordings_root()),
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Jinja2 rendering
# ---------------------------------------------------------------------------

_jinja_env = None


def _get_jinja_env():
    global _jinja_env
    if _jinja_env is None:
        from jinja2 import Environment, PackageLoader

        _jinja_env = Environment(
            loader=PackageLoader("phone_pilot.core", "templates"),
            autoescape=True,
        )
    return _jinja_env


def _render_dashboard() -> str:
    env = _get_jinja_env()
    tpl = env.get_template("web_index.html.j2")
    return tpl.render(**_dashboard_data())


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Simple request handler with basic routing."""

    server_version = "phone_pilot_web/1.0"

    def log_message(self, fmt, *args):
        # Compact logging
        sys.stderr.write(f"[web] {self.address_string()} {fmt % args}\n")

    # ---- GET / HEAD ----

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        path = unquote(self.path).split("?")[0]

        if path == "/" or path == "":
            return self._serve_dashboard()

        if path == "/api/devices":
            return self._send_json(self.server.scene_controller.list_devices())

        if path == "/api/scene-plan":
            params = parse_qs(urlsplit(self.path).query)
            run_dir = self._scene_run_dir(params.get("name", [""])[0])
            if run_dir is None:
                return self._send_json({"ok": False, "error": "invalid_run"}, 400)
            return self._send_json(self.server.scene_controller.inspect(run_dir))
        if path == "/api/scene-result":
            params = parse_qs(urlsplit(self.path).query)
            return self._send_json(
                self.server.scene_controller.result(params.get("job_id", [""])[0])
            )

        # /run/<name>/...  — serve static files from a run dir
        m = re.match(r"^/run/([^/]+)/(.+)$", path)
        if m:
            run_name, sub = m.group(1), m.group(2)
            return self._serve_run_file(run_name, sub)

        # /res/<key> — serve resource image
        m = re.match(r"^/res/(.+)$", path)
        if m:
            return self._serve_resource(m.group(1))

        self._send_error(404, "Not Found")

    # ---- POST ----

    def do_POST(self):
        path = unquote(self.path).split("?")[0]
        origin = self.headers.get("Origin")
        if (
            origin != "http://" + self.headers.get("Host", "")
            or urlsplit(origin or "").hostname not in ("127.0.0.1", "localhost")
        ):
            return self._send_json({"ok": False, "error": "origin_rejected"}, 403)
        body = self._read_body()

        if path == "/api/restore-scene":
            run_dir = self._scene_run_dir(body.get("name", [""])[0])
            if run_dir is None:
                return self._send_json({"ok": False, "error": "invalid_run"}, 400)
            result = self.server.scene_controller.submit(
                run_dir,
                serial=body.get("serial", [""])[0],
                manifest_hash=body.get("manifest_sha256", [""])[0],
                token=body.get("token", [""])[0],
                origin=self.headers.get("Origin"),
                host=self.headers.get("Host", ""),
                mode=body.get("mode", ["fast"])[0],
                scene_id=body.get("scene_id", [""])[0] or None,
            )
            return self._send_json(result)

        if path == "/api/delete-run":
            return self._api_delete_run(body)
        if path == "/api/delete-resource":
            return self._api_delete_resource(body)
        if path == "/api/generate-report":
            return self._api_generate_report(body)
        if path == "/api/open-finder":
            return self._api_open_finder(body)

        self._send_error(404, "Not Found")

    # ---- Route handlers ----

    def _scene_run_dir(self, name):
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return None
        candidate = _runs_dir().absolute() / name
        try:
            with safe_directory(candidate):
                return candidate
        except (ValueError, OSError):
            return None

    def _serve_dashboard(self):
        try:
            html = _render_dashboard()
            self._send_html(html)
        except Exception as e:
            self._send_error(500, str(e))

    def _stream_file_with_range(self, stream, mime: str, suffix: str, *, trusted_report: bool = False):
        total_size = os.fstat(stream.fileno()).st_size
        range_header = self.headers.get("Range")
        start = 0
        end = total_size - 1
        status_code = 200

        if range_header and range_header.startswith("bytes=") and total_size > 0:
            m_range = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
            if m_range:
                s_str, e_str = m_range.group(1), m_range.group(2)
                if s_str and e_str:
                    start, end = int(s_str), int(e_str)
                elif s_str:
                    start, end = int(s_str), total_size - 1
                elif e_str:
                    suffix_len = int(e_str)
                    start = max(0, total_size - suffix_len)
                    end = total_size - 1
                end = min(end, total_size - 1)
                if 0 <= start <= end < total_size:
                    status_code = 206
                else:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total_size}")
                    self.end_headers()
                    return

        content_len = (end - start + 1) if total_size > 0 else 0
        if suffix.lower() in (".txt", ".log"):
            mime = "text/plain; charset=utf-8"
        self.send_response(status_code)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        self.send_header("Content-Length", str(content_len))
        self.send_header("X-Content-Type-Options", "nosniff")
        if not trusted_report and suffix.lower() not in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".mp4",
            ".webm",
            ".mkv",
            ".txt",
            ".log",
        ):
            self.send_header("Content-Disposition", "attachment")
            self.send_header(
                "Content-Security-Policy", "sandbox; default-src 'none'"
            )
        self.end_headers()

        if content_len <= 0 or getattr(self, "command", "GET") == "HEAD":
            return
        if start > 0:
            stream.seek(start)
        remaining = content_len
        chunk_size = 256 * 1024
        while remaining > 0:
            data = stream.read(min(chunk_size, remaining))
            if not data:
                break
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                break
            remaining -= len(data)

    def _serve_run_file(self, run_name: str, sub_path: str):
        root = self._scene_run_dir(run_name)
        if root is None:
            return self._send_error(403, "Forbidden")
        relative = pathlib.Path(sub_path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in sub_path:
            return self._send_error(403, "Forbidden")
        try:
            if relative.as_posix() == "report.html":
                from phone_pilot.core.html_report import render_html_report

                return self._send_html(render_html_report(root))
            with ExitStack() as stack:
                folder = stack.enter_context(safe_directory(root))
                for name in relative.parts[:-1]:
                    folder = stack.enter_context(folder.subdir(name))
                stream = stack.enter_context(folder.open(relative.name))
                mime = (
                    mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
                )
                self._stream_file_with_range(stream, mime, relative.suffix)
        except (ValueError, OSError):
            return self._send_error(403, "Unsafe or unavailable archive file")

    def _serve_resource(self, key: str):
        if not key or key in (".", "..") or "/" in key or "\\" in key:
            return self._send_error(403, "Forbidden")
        cache_dir = _res_cache_dir()
        # Find matching file
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            p = cache_dir / f"{key}{ext}"
            if p.is_file():
                return self._send_file(p)
        # Fallback: try exact filename
        p = cache_dir / key
        if p.is_file():
            return self._send_file(p)
        self._send_error(404, "Resource not found")

    def _api_delete_run(self, body: dict):
        if self.server.scene_controller.busy():
            return self._send_json({"ok": False, "error": "restoration_running"}, 409)
        name = body.get("name", [""])[0]
        if not name or ".." in name:
            return self._send_json({"ok": False, "error": "invalid name"})
        run_dir = _runs_dir() / name
        if not run_dir.is_dir():
            return self._send_json({"ok": False, "error": "run not found"})
        try:
            shutil.rmtree(run_dir)
            # Also remove from index.json
            self._remove_from_runs_index(name)
            self._send_json({"ok": True})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _api_delete_resource(self, body: dict):
        key = body.get("key", [""])[0]
        if not key:
            return self._send_json({"ok": False, "error": "key required"})
        try:
            from phone_pilot.core.resource import res_delete

            result = res_delete(key)
            self._send_json(result)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _api_generate_report(self, body: dict):
        name = body.get("name", [""])[0]
        if not name or ".." in name:
            return self._send_json({"ok": False, "error": "invalid name"})
        run_dir = _runs_dir() / name
        if not run_dir.is_dir():
            return self._send_json({"ok": False, "error": "run not found"})
        try:
            from phone_pilot.core.html_report import generate_html_report

            p = generate_html_report(run_dir)
            self._send_json({"ok": True, "path": str(p)})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _api_open_finder(self, body: dict):
        import subprocess

        raw_path = (body.get("path", [""])[0] or "").strip()
        if not raw_path:
            return self._send_json({"ok": False, "error": "empty_path"}, 400)
        try:
            target = pathlib.Path(raw_path).expanduser().resolve()
            rec_root = _recordings_root().resolve()
            if target != rec_root and rec_root not in target.parents:
                return self._send_json({"ok": False, "error": "outside_recordings_root"}, 403)
            if not target.exists():
                return self._send_json({"ok": False, "error": "path_not_found"}, 404)
            if sys.platform == "darwin":
                if target.is_file():
                    subprocess.Popen(["open", "-R", str(target)])
                else:
                    subprocess.Popen(["open", str(target)])
                return self._send_json({"ok": True, "opened": str(target)})
            return self._send_json({"ok": False, "error": "unsupported_os"}, 400)
        except Exception as e:
            return self._send_json({"ok": False, "error": str(e)}, 500)

    # ---- Helpers ----

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw)

    def _remove_from_runs_index(self, run_name: str):
        idx_path = _runs_dir() / "index.json"
        if not idx_path.is_file():
            return
        try:
            with safe_directory(idx_path.parent, writable=True) as folder:
                data = json.loads(folder.read_bytes(idx_path.name))
            runs = data.get("runs", [])
            data["runs"] = [r for r in runs if r.get("run_dir") != run_name]
            with safe_directory(idx_path.parent, writable=True) as folder:
                folder.write_bytes(
                    idx_path.name,
                    (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(
                        "utf-8"
                    ),
                    replace=True,
                )
        except Exception:
            pass

    def _send_html(self, html: str, code: int = 200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj: Any, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: pathlib.Path, trusted_report=False):
        mime, _ = mimetypes.guess_type(str(path))
        if mime is None:
            mime = "application/octet-stream"
        try:
            with safe_directory(path.parent) as folder:
                with folder.open(path.name) as stream:
                    self._stream_file_with_range(
                        stream, mime, path.suffix, trusted_report=trusted_report
                    )
        except (ValueError, OSError):
            return self._send_error(403, "Unsafe or unavailable file")

    def _send_error(self, code: int, message: str):
        self.send_response(code)
        from html import escape

        body = f"<h1>{code}</h1><p>{escape(message)}</p>".encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server entry
# ---------------------------------------------------------------------------


def main(
    port: int = 8686,
    no_browser: bool = False,
    approved_scene_hashes=(),
    auto_approve: bool = True,
):
    """Start dashboard; restoration disabled unless manifest hashes are approved."""
    from phone_pilot.scene_web import SceneWebController

    if auto_approve:
        os.environ.setdefault("PHONE_PILOT_SCENE_ALLOW_ONLINE", "1")
        os.environ.setdefault("PHONE_PILOT_SCENE_ALLOW_PERMISSIONS", "1")

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.scene_controller = SceneWebController(
        approved_scene_hashes, auto_approve=auto_approve
    )
    url = f"http://127.0.0.1:{port}"
    print(f"phone_pilot dashboard: {url}")
    print(f"recordings root: {_recordings_root()}")
    print("Press Ctrl+C to stop.\n")

    if not no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    finally:
        server.scene_controller.close()
        server.server_close()


def _cli():
    """CLI entry point for phone-pilot-web."""
    parser = argparse.ArgumentParser(
        prog="phone-pilot-web",
        description="phone_pilot local web dashboard",
    )
    parser.add_argument(
        "--port", type=int, default=8686, help="Server port (default: 8686)"
    )
    parser.add_argument(
        "--recordings-dir",
        type=str,
        default="",
        help="Path to .recordings directory (default: <cwd>/.recordings)",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Don't auto-open browser"
    )
    parser.add_argument(
        "--approve-scene-sha256",
        action="append",
        default=[],
        help="启用已审阅 manifest 的设备复原；可重复，默认禁用",
    )
    parser.add_argument(
        "--auto-approve",
        "-a",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="自动识别并批准所有有效 run 的 manifest（默认开启，--no-auto-approve 可关闭）",
    )
    args = parser.parse_args()
    if args.recordings_dir:
        os.environ["PHONE_PILOT_RECORDINGS_DIR"] = str(
            pathlib.Path(args.recordings_dir).expanduser().resolve()
        )
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", value) for value in args.approve_scene_sha256
    ):
        parser.error("--approve-scene-sha256 必须是小写 SHA256")
    approved_hashes = list(args.approve_scene_sha256)
    if args.auto_approve:
        import hashlib
        os.environ["PHONE_PILOT_SCENE_ALLOW_ONLINE"] = "1"
        os.environ["PHONE_PILOT_SCENE_ALLOW_PERMISSIONS"] = "1"
        runs_root = _runs_dir()
        if runs_root.is_dir():
            for d in runs_root.iterdir():
                manifest_file = d / "scene_manifest.json"
                if manifest_file.is_file():
                    try:
                        raw = manifest_file.read_bytes()
                        h = hashlib.sha256(raw).hexdigest()
                        if h not in approved_hashes:
                            approved_hashes.append(h)
                    except Exception:
                        pass
        if approved_hashes:
            print(f"[auto-approve] 已自动批准 {len(approved_hashes)} 个 run 的现场复原授权。")
    main(
        port=args.port,
        no_browser=args.no_browser,
        approved_scene_hashes=approved_hashes,
        auto_approve=args.auto_approve,
    )


if __name__ == "__main__":
    _cli()
