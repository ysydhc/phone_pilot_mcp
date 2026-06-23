"""
ADB reverse tunnel lifecycle manager.

封装 `adb reverse` 命令，将设备上的 tcp 端口转发回主机 mock server。
Wraps `adb reverse` commands to forward device tcp ports back to the host mock server.
"""

from __future__ import annotations

import subprocess
from typing import Callable

from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.adb.utils import adb_prefix


class ReverseManager:
    """Manage adb reverse tunnel lifecycle for a single device serial."""

    def __init__(
        self,
        serial: str,
        *,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ) -> None:
        self._serial = serial
        self._runner: Callable[..., subprocess.CompletedProcess] = (
            runner if runner is not None else CommandRunner.run
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, port: int) -> dict:
        """Establish a reverse tunnel for *port*.

        Idempotency guard: removes any stale mapping first (ignoring failure),
        then creates the new mapping.
        Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
        """
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
        try:
            self._runner(
                adb_prefix(self._serial) + ["reverse", "--remove-all"],
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def list(self) -> list[str]:
        """Return currently active reverse tunnels as a list of non-empty lines."""
        try:
            result = self._runner(
                adb_prefix(self._serial) + ["reverse", "--list"],
                check=False,
            )
            return [line for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            return []
