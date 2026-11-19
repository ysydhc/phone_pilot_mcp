"""Opt-in localhost restoration transport with multi-device adaptation & real-time progress."""

from __future__ import annotations

import os
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit


class SceneWebController:
    def __init__(self, approved_hashes=(), auto_approve: bool = False):
        self.approved_hashes = set(approved_hashes)
        self.auto_approve = auto_approve
        self.token = secrets.token_urlsafe(32)
        self._jobs = {}
        self._job_states = {}
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="scene-restore"
        )

    def list_devices(self, target_package: str = "cc.admaster.android.demo") -> dict:
        from phone_pilot.core.manifest_generator import get_online_devices_detailed

        devices = get_online_devices_detailed(target_package=target_package)
        return {
            "ok": True,
            "count": len(devices),
            "devices": devices,
        }

    def inspect(self, run_dir):
        from phone_pilot.core.scene_restore import inspect_scene, _action_label
        from phone_pilot.core.manifest_generator import get_online_devices_detailed

        result = inspect_scene(run_dir)
        digest = result.get("manifest_sha256")
        if self.auto_approve and digest and result.get("status") in ("READY", "SUCCESS"):
            self.approved_hashes.add(digest)
        result["enabled"] = digest in self.approved_hashes
        result["token"] = self.token if result["enabled"] else None

        pkg_name = (result.get("package") or {}).get("name", "cc.admaster.android.demo")
        online_devices = get_online_devices_detailed(target_package=pkg_name)
        result["online_devices"] = online_devices

        # Build human-friendly planned steps for immediate UI stepper preview
        raw_actions = result.get("actions", [])
        planned_steps = [
            {
                "index": 1,
                "title": "启动 Mock 服务与设备预检",
                "detail": "锁定目标真机并加载本地 Mock 规则 (:8090)",
                "status": "PENDING",
            }
        ]
        for idx, act in enumerate(raw_actions, start=2):
            t, d = _action_label(act)
            planned_steps.append(
                {"index": idx, "title": t, "detail": d, "status": "PENDING"}
            )
        planned_steps.append(
            {
                "index": len(planned_steps) + 1,
                "title": "真机终态核验与截屏留证",
                "detail": "采集真机最终画面、UI 树与日志证据",
                "status": "PENDING",
            }
        )
        result["planned_steps"] = planned_steps
        return result

    def submit(
        self,
        run_dir,
        *,
        serial,
        manifest_hash,
        token,
        origin,
        host,
        mode: str = "fast",
        scene_id: str | None = None,
    ):
        expected = "http://" + host
        parsed = urlsplit(origin or "")
        if (
            not origin
            or origin != expected
            or parsed.hostname not in ("127.0.0.1", "localhost")
            or not secrets.compare_digest(token or "", self.token)
        ):
            return {
                "ok": False,
                "status": "BLOCKED",
                "error": "origin_or_token_rejected",
            }
        if self.auto_approve:
            if manifest_hash:
                self.approved_hashes.add(manifest_hash)
            os.environ["PHONE_PILOT_SCENE_ALLOW_ONLINE"] = "1"
            os.environ["PHONE_PILOT_SCENE_ALLOW_PERMISSIONS"] = "1"
        elif manifest_hash not in self.approved_hashes:
            return {"ok": False, "status": "BLOCKED", "error": "manifest_not_approved"}

        from phone_pilot.core.scene_restore import restore_scene

        run_name = Path(run_dir).name
        with self._lock:
            if any(not f.done() for f in self._jobs.values()):
                return {
                    "ok": False,
                    "status": "BLOCKED",
                    "error": "restore_already_running",
                }
            while len(self._jobs) >= 100:
                oldest = next(iter(self._jobs))
                self._jobs.pop(oldest, None)
                self._job_states.pop(oldest, None)

            job_id = secrets.token_hex(16)
            state = {
                "job_id": job_id,
                "run_name": run_name,
                "device_serial": serial,
                "mode": mode,
                "started_at": time.time(),
                "current_step": 0,
                "steps": [],
                "logs": [],
            }
            self._job_states[job_id] = state

            def on_progress(evt: dict):
                etype = evt.get("event")
                if etype == "plan_initialized":
                    state["device_serial"] = evt.get("device_serial", serial)
                    state["steps"] = evt.get("steps", [])
                elif etype == "step_start":
                    idx = evt.get("step_index", 0)
                    state["current_step"] = idx
                    for s in state["steps"]:
                        if s.get("index") == idx:
                            s["status"] = "RUNNING"
                            if evt.get("detail"):
                                s["detail"] = evt["detail"]
                    state["logs"].append(
                        f"▶ [步骤 {idx}] 正在执行：{evt.get('title', '')} — {evt.get('detail', '')}"
                    )
                elif etype == "step_done":
                    idx = evt.get("step_index", 0)
                    for s in state["steps"]:
                        if s.get("index") == idx:
                            s["status"] = evt.get("status", "SUCCESS")
                            s["elapsed_ms"] = evt.get("elapsed_ms", 0)
                            if evt.get("detail"):
                                s["detail"] = evt["detail"]
                    state["logs"].append(
                        f"✔ [步骤 {idx}] 完成 ({evt.get('elapsed_ms', 0)}ms)：{evt.get('detail', '')}"
                    )
                elif etype == "log":
                    state["logs"].append(f"  {evt.get('message', '')}")
                elif etype == "scene_failed":
                    for s in state["steps"]:
                        if s.get("status") == "RUNNING":
                            s["status"] = "FAILED"
                    state["logs"].append(f"✖ 异常中断：{evt.get('error', '')}")

            self._jobs[job_id] = self._pool.submit(
                restore_scene,
                run_dir,
                device_serial=serial,
                approved_manifest_sha256=manifest_hash,
                scene_id=scene_id,
                mode=mode,
                progress_callback=on_progress,
            )
        return {"ok": True, "status": "QUEUED", "job_id": job_id, "progress": state}

    def result(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            state = self._job_states.get(job_id, {})
        if job is None:
            return {"ok": False, "error": "job_not_found"}
        if not job.done():
            return {
                "ok": True,
                "status": "RUNNING",
                "job_id": job_id,
                "progress": state,
            }
        try:
            res = dict(job.result())
            res["progress"] = state
            return res
        except Exception as exc:
            return {
                "ok": False,
                "status": "FAILED",
                "error": str(exc),
                "progress": state,
            }

    def busy(self):
        with self._lock:
            return any(not future.done() for future in self._jobs.values())

    def close(self):
        self._pool.shutdown(wait=True)
