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
import sys
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional
from urllib.parse import parse_qs, unquote


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _recordings_root() -> pathlib.Path:
    """Resolve .recordings root (same logic as core.storage)."""
    env = os.getenv("PHONE_PILOT_CACHE_DIR")
    if env:
        return pathlib.Path(env).expanduser().resolve() / ".recordings"
    repo = pathlib.Path(__file__).resolve().parents[1]
    return (repo / ".recordings").resolve()


def _runs_dir() -> pathlib.Path:
    return _recordings_root() / "runs"


def _res_cache_dir() -> pathlib.Path:
    return _recordings_root() / "cache" / "pic"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _read_json(path: pathlib.Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
        # Has report?
        meta["has_report"] = (d / "report.html").is_file()
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

    # ---- GET ----

    def do_GET(self):
        path = unquote(self.path).split("?")[0]

        if path == "/" or path == "":
            return self._serve_dashboard()

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
        body = self._read_body()

        if path == "/api/delete-run":
            return self._api_delete_run(body)
        if path == "/api/delete-resource":
            return self._api_delete_resource(body)
        if path == "/api/generate-report":
            return self._api_generate_report(body)

        self._send_error(404, "Not Found")

    # ---- Route handlers ----

    def _serve_dashboard(self):
        try:
            html = _render_dashboard()
            self._send_html(html)
        except Exception as e:
            self._send_error(500, str(e))

    def _serve_run_file(self, run_name: str, sub_path: str):
        # Validate: no path traversal
        if ".." in run_name or ".." in sub_path:
            return self._send_error(403, "Forbidden")
        fpath = _runs_dir() / run_name / sub_path
        if not fpath.is_file():
            return self._send_error(404, "File not found")
        self._send_file(fpath)

    def _serve_resource(self, key: str):
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
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            runs = data.get("runs", [])
            data["runs"] = [r for r in runs if r.get("run_dir") != run_name]
            idx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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

    def _send_file(self, path: pathlib.Path):
        mime, _ = mimetypes.guess_type(str(path))
        if mime is None:
            mime = "application/octet-stream"
        try:
            data = path.read_bytes()
        except Exception:
            return self._send_error(500, "Cannot read file")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, code: int, message: str):
        self.send_response(code)
        body = f"<h1>{code}</h1><p>{message}</p>".encode("utf-8")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Server entry
# ---------------------------------------------------------------------------

def main(port: int = 8686, no_browser: bool = False):
    """Start the local web dashboard server."""
    server = HTTPServer(("127.0.0.1", port), _Handler)
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


def _cli():
    """CLI entry point for phone-pilot-web."""
    parser = argparse.ArgumentParser(
        prog="phone-pilot-web",
        description="phone_pilot local web dashboard",
    )
    parser.add_argument("--port", type=int, default=8686, help="Server port (default: 8686)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()
    main(port=args.port, no_browser=args.no_browser)


if __name__ == "__main__":
    _cli()
