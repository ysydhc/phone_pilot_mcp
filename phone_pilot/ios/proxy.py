"""
iOS proxy & tunnel helpers for real device mock testing.

Because iOS real devices do not support reverse port forwarding (like adb reverse
or hdc rport), this module provides LAN IP discovery, PAC configuration,
and proxy connectivity verification.
"""

from __future__ import annotations

import atexit
import logging
import socket
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)


def get_host_lan_ip() -> str:
    """
    Get the primary LAN IP address of the host machine.
    Falls back to '127.0.0.1' if disconnected.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually send data over the wire
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def generate_pac_script(host_ip: str, port: int, match_domain: Optional[str] = None) -> str:
    """
    Generate a PAC (Proxy Auto-Configuration) script.

    If match_domain is specified, only traffic to that domain will route through the proxy.
    Otherwise, all HTTP/HTTPS traffic is proxied.
    """
    proxy_str = f"PROXY {host_ip}:{port}; DIRECT"
    if match_domain:
        return f"""function FindProxyForURL(url, host) {{
    if (shExpMatch(host, "{match_domain}")) {{
        return "{proxy_str}";
    }}
    return "DIRECT";
}}
"""
    return f"""function FindProxyForURL(url, host) {{
    return "{proxy_str}";
}}
"""


def check_proxy_reachable(host_ip: str, port: int, timeout: float = 1.0) -> bool:
    """Check if the mock proxy server port is actively listening and reachable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host_ip, port))
        sock.close()
        return True
    except Exception:
        return False


class IProxyManager:
    """
    Manager for iOS iproxy tunnel processes with automatic GC / cleanup.
    Tracks active port forwards to avoid orphan process leaks and port collisions.
    """

    _tunnels: dict[tuple[str, int], subprocess.Popen] = {}
    _lock = threading.Lock()

    @classmethod
    def start_tunnel(
        cls,
        udid: str,
        local_port: int,
        remote_port: int,
    ) -> dict:
        """Start an iproxy tunnel forwarding local_port to remote_port on udid."""
        import shutil

        key = (udid, local_port)
        with cls._lock:
            # Clean up any existing tunnel on the same port
            cls.stop_tunnel(udid, local_port)

            exe = shutil.which("iproxy")
            if not exe:
                return {
                    "ok": False,
                    "error": "iproxy_executable_not_found",
                    "hint": "Install libimobiledevice via 'brew install libimobiledevice'",
                }

            try:
                proc = subprocess.Popen(
                    [exe, str(local_port), str(remote_port), "-u", udid],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                cls._tunnels[key] = proc
                return {
                    "ok": True,
                    "udid": udid,
                    "local_port": local_port,
                    "remote_port": remote_port,
                    "pid": proc.pid,
                }
            except Exception as exc:
                logger.error("Failed to spawn iproxy tunnel: %s", exc)
                return {"ok": False, "error": str(exc)}

    @classmethod
    def stop_tunnel(cls, udid: str, local_port: int) -> dict:
        """Stop and garbage-collect a specific iproxy tunnel."""
        key = (udid, local_port)
        with cls._lock:
            proc = cls._tunnels.pop(key, None)
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return {"ok": True, "stopped": True, "local_port": local_port}
        return {"ok": True, "stopped": False, "local_port": local_port}

    @classmethod
    def cleanup_all(cls) -> int:
        """Terminate all active iproxy tunnels."""
        stopped = 0
        with cls._lock:
            for (udid, lport), proc in list(cls._tunnels.items()):
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                stopped += 1
            cls._tunnels.clear()
        return stopped


atexit.register(IProxyManager.cleanup_all)
