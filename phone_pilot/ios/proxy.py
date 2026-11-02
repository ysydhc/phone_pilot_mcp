"""
iOS proxy & tunnel helpers for real device mock testing.

Because iOS real devices do not support reverse port forwarding (like adb reverse
or hdc rport), this module provides LAN IP discovery, PAC configuration,
and proxy connectivity verification.
"""

from __future__ import annotations

import logging
import socket
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
