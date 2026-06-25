"""
Reverse tunnel lifecycle manager (port forwarding device → host mock server).

封装端口转发，将设备访问的 tcp 端口导回主机 mock server。
- Android: `adb reverse`。
- iOS 模拟器: 与主机共享 localhost，App 直连 127.0.0.1 即命中主机，无需转发（no-op）。
Wraps port forwarding so the device reaches the host mock server.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Optional

from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.adb.utils import adb_prefix


class ReverseManager:
    """Manage reverse-tunnel lifecycle for a single device serial.

    For iOS simulators no tunnel is needed (shared host loopback), so all
    operations are successful no-ops.
    """

    def __init__(
        self,
        serial: str,
        *,
        platform: str = "android",
        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ) -> None:
        self._serial = serial
        self._platform = (platform or "android").strip().lower()
        self._runner: Callable[..., subprocess.CompletedProcess] = (
            runner if runner is not None else CommandRunner.run
        )

    @property
    def _noop(self) -> bool:
        """iOS simulator shares host loopback → no port forwarding required."""
        return self._platform == "ios"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, port: int) -> dict:
        """Establish a reverse tunnel for *port*.

        Idempotency guard: removes any stale mapping first (ignoring failure),
        then creates the new mapping.
        Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
        """
        if self._noop:
            return {"ok": True, "noop": True, "platform": self._platform}
        try:
            tcp_spec = f"tcp:{port}"
            # Corner-case guard: silently remove any pre-existing mapping.
            self._runner(
                adb_prefix(self._serial) + ["reverse", "--remove", tcp_spec],
                check=False,
            )
            # Establish the tunnel.
            self._runner(
                adb_prefix(self._serial) + ["reverse", tcp_spec, tcp_spec],
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def teardown(self, port: int) -> dict:
        """Remove the reverse tunnel for *port*.

        Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
        """
        if self._noop:
            return {"ok": True, "noop": True, "platform": self._platform}
        try:
            tcp_spec = f"tcp:{port}"
            self._runner(
                adb_prefix(self._serial) + ["reverse", "--remove", tcp_spec],
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def remove_all(self) -> dict:
        """Remove all reverse tunnels on the device.

        Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
        """
        if self._noop:
            return {"ok": True, "noop": True, "platform": self._platform}
        try:
            self._runner(
                adb_prefix(self._serial) + ["reverse", "--remove-all"],
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list(self) -> list[str]:
        """Return currently active reverse tunnels as a list of non-empty lines."""
        if self._noop:
            return []
        try:
            result = self._runner(
                adb_prefix(self._serial) + ["reverse", "--list"],
                check=False,
            )
            return [line for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            return []
