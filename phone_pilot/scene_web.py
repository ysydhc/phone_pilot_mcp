"""Opt-in localhost restoration transport. GET inspects; only approved POST mutates."""

from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from urllib.parse import urlsplit


class SceneWebController:
    def __init__(self, approved_hashes=()):
        self.approved_hashes = frozenset(approved_hashes)
        self.token = secrets.token_urlsafe(32)
        self._jobs = {}
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="scene-restore"
        )

    def inspect(self, run_dir):
        from phone_pilot.core.scene_restore import inspect_scene

        result = inspect_scene(run_dir)
        result["enabled"] = result.get("manifest_sha256") in self.approved_hashes
        result["token"] = self.token if result["enabled"] else None
        return result

    def submit(self, run_dir, *, serial, manifest_hash, token, origin, host):
        expected = "http://" + host
        parsed = urlsplit(origin or "")
        if (
            not origin
            or origin != expected
            or parsed.hostname != "127.0.0.1"
            or not secrets.compare_digest(token or "", self.token)
        ):
            return {
                "ok": False,
                "status": "BLOCKED",
                "error": "origin_or_token_rejected",
            }
        if manifest_hash not in self.approved_hashes:
            return {"ok": False, "status": "BLOCKED", "error": "manifest_not_approved"}
        from phone_pilot.core.scene_restore import restore_scene

        with self._lock:
            if any(not f.done() for f in self._jobs.values()):
                return {
                    "ok": False,
                    "status": "BLOCKED",
                    "error": "restore_already_running",
                }
            # Retain only a bounded recent receipt history.
            while len(self._jobs) >= 100:
                self._jobs.pop(next(iter(self._jobs)))
            job_id = secrets.token_hex(16)
            self._jobs[job_id] = self._pool.submit(
                restore_scene,
                run_dir,
                device_serial=serial,
                approved_manifest_sha256=manifest_hash,
            )
        return {"ok": True, "status": "QUEUED", "job_id": job_id}

    def result(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "job_not_found"}
        if not job.done():
            return {"ok": True, "status": "RUNNING", "job_id": job_id}
        try:
            return job.result()
        except Exception as exc:
            return {"ok": False, "status": "FAILED", "error": str(exc)}

    def busy(self):
        with self._lock:
            return any(not future.done() for future in self._jobs.values())

    def close(self):
        self._pool.shutdown(wait=True)
