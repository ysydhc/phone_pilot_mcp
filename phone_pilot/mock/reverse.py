"""
Reverse tunnel lifecycle manager (port forwarding device → host mock server).

封装端口转发，将设备访问的 tcp 端口导回主机 mock server。
- Android: `adb reverse`。
- iOS 模拟器: 与主机共享 localhost，App 直连 127.0.0.1 即命中主机，无需转发（no-op）。
- HarmonyOS: `hdc rport`。
Wraps port forwarding so the device reaches the host mock server.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Callable, Optional

from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.harmony.hdc.runner import HdcCommandRunner
from phone_pilot.harmony.hdc.utils import hdc_prefix

logger = logging.getLogger(__name__)


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
        plat = (platform or "android").strip().lower()
        if plat in ("harmony", "harmonyos", "openharmony"):
            self._platform = "harmony"
        else:
            self._platform = plat

        if runner is not None:
            self._runner = runner
        elif self._platform == "harmony":
            self._runner = HdcCommandRunner.run
        else:
            self._runner = CommandRunner.run

    @property
    def _noop(self) -> bool:
        """iOS simulator shares host loopback → no port forwarding required."""
        if self._platform == "ios":
            try:
                from phone_pilot.ios.pmd.utils import detect_form_factor

                return detect_form_factor(self._serial) != "real"
            except Exception:
                return True
        return False

    def get_target_host(self) -> str:
        """Get the host IP accessible from the target device (127.0.0.1 or LAN IP)."""
        if self._platform == "ios":
            try:
                from phone_pilot.ios.pmd.utils import detect_form_factor

                if detect_form_factor(self._serial) == "real":
                    from phone_pilot.ios.proxy import get_host_lan_ip

                    return get_host_lan_ip()
            except Exception:
                pass
        return "127.0.0.1"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(self, port: int) -> dict:
        """Establish a reverse tunnel for *port*.

        Idempotency guard: removes any stale mapping first (ignoring failure),
        then creates the new mapping.
        Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``.
        """
        if self._platform == "ios":
            try:
                from phone_pilot.ios.pmd.utils import detect_form_factor

                form = detect_form_factor(self._serial)
                if form == "real":
                    from phone_pilot.ios.proxy import get_host_lan_ip

                    lan_ip = get_host_lan_ip()
                    return {
                        "ok": True,
                        "mode": "ios_real_device_proxy",
                        "host": lan_ip,
                        "port": port,
                        "guide": f"Configure iOS device HTTP proxy to {lan_ip}:{port} or point App baseURL to http://{lan_ip}:{port}",
                    }
            except Exception as e:
                logger.debug("detect_form_factor error: %s", e)
            return {"ok": True, "noop": True, "platform": self._platform}
        try:
            tcp_spec = f"tcp:{port}"
            if self._platform == "harmony":
                # Corner-case guard: silently remove any pre-existing mapping.
                self._runner(
                    hdc_prefix(self._serial) + ["fport", "rm", tcp_spec, tcp_spec],
                    check=False,
                )
                # Establish the tunnel.
                self._runner(
                    hdc_prefix(self._serial) + ["rport", tcp_spec, tcp_spec],
                )
            else:
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
            if self._platform == "harmony":
                self._runner(
                    hdc_prefix(self._serial) + ["fport", "rm", tcp_spec, tcp_spec],
                )
            else:
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
            if self._platform == "harmony":
                ports: list[int] = []
                for line in self.list():
                    match = re.search(r"tcp:(\d+)", line)
                    if match:
                        p = int(match.group(1))
                        if p not in ports:
                            ports.append(p)
                    else:
                        token = line.strip().split()[0] if line.strip() else ""
                        if token:
                            self._runner(
                                hdc_prefix(self._serial) + ["fport", "rm", token, token],
                                check=False,
                            )
                for port in ports:
                    self.teardown(port)
                return {"ok": True}

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
            if self._platform == "harmony":
                cmd = hdc_prefix(self._serial) + ["fport", "ls"]
            else:
                cmd = adb_prefix(self._serial) + ["reverse", "--list"]
            result = self._runner(
                cmd,
                check=False,
            )
            lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
            if self._platform == "harmony":
                # Filter for reverse rules if tagged, otherwise retain
                reverse_lines = [item for item in lines if "[reverse]" in item.lower()]
                return reverse_lines if reverse_lines else lines
            return lines
        except Exception:
            return []
