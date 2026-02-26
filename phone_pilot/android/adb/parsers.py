#!/usr/bin/env python3
"""
Parsers for common ADB command outputs.
"""

from __future__ import annotations


def parse_adb_devices(output: str) -> list[dict[str, str]]:
    """
    Parse output from `adb devices -l`.

    Args:
        output: Raw stdout from adb devices command

    Returns:
        List of device dicts with keys: serial, state, description
    """
    devices: list[dict[str, str]] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        # formats:
        # serial<TAB>device
        # serial<TAB>unauthorized
        # serial<TAB>device product:... model:... device:... transport_id:...
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        desc = " ".join(parts[2:]) if len(parts) > 2 else ""
        devices.append({"serial": serial, "state": state, "description": desc})
    return devices
