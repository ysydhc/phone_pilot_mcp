#!/usr/bin/env python3
"""
Convert `adb shell getevent -lt` touch logs into a Monkey `--scriptfile`.

The script focuses on single-slot touch streams (default slot 0). It parses
SYN_REPORT boundaries to reconstruct DOWN/MOVE/UP actions and emits a
`DispatchPointer` sequence with `UserWait` delays to preserve timing.

Usage:
    python convert.py --input getevent.log --output touch.monkey
    adb shell getevent -lt | python convert.py --output touch.monkey
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from typing import Dict, Iterable, List, Optional, TextIO, Tuple, Union


GETEVENT_RE = re.compile(
    r"""
    ^\[\s*(?P<ts>\d+\.\d+)\]\s+
    (?:(?P<device>\S+):\s+)?   # device may be omitted in some logs
    (?P<etype>\S+)\s+
    (?P<code>\S+)\s+
    (?P<value>\S+)
    """,
    re.VERBOSE,
)


@dataclasses.dataclass
class SlotState:
    """State of a single touch slot."""
    tracking_id: Optional[int] = None
    x: Optional[int] = None
    y: Optional[int] = None


@dataclasses.dataclass
class PointerAction:
    """A pointer (touch) action event."""
    timestamp: float
    slot: int
    action: str  # DOWN, MOVE, UP
    x: int
    y: int


@dataclasses.dataclass
class KeyAction:
    """A key press/release action event."""
    timestamp: float
    action: str  # DOWN, UP
    keycode: int  # Android KEYCODE_*


Action = Union[PointerAction, KeyAction]


def _linux_key_to_android_keycode(code: str) -> Optional[int]:
    """
    Convert Linux input key name (e.g. KEY_H) to Android KeyEvent keyCode int.

    Only maps the common keys we need for typical text entry and navigation.
    """
    c = code.strip().upper()
    if not c.startswith("KEY_"):
        return None
    name = c[4:]

    # Letters: KEY_A..KEY_Z -> KEYCODE_A(29)..KEYCODE_Z(54)
    if len(name) == 1 and "A" <= name <= "Z":
        return 29 + (ord(name) - ord("A"))

    # Digits: KEY_0..KEY_9 -> KEYCODE_0(7)..KEYCODE_9(16)
    if len(name) == 1 and "0" <= name <= "9":
        return 7 + int(name)

    # Common control/navigation keys.
    common = {
        "SPACE": 62,  # KEYCODE_SPACE
        "ENTER": 66,  # KEYCODE_ENTER
        "TAB": 61,  # KEYCODE_TAB
        "ESC": 111,  # KEYCODE_ESCAPE
        "ESCAPE": 111,  # KEYCODE_ESCAPE
        "BACKSPACE": 67,  # KEYCODE_DEL
        "DEL": 67,  # KEYCODE_DEL
        "FORWARDDEL": 112,  # KEYCODE_FORWARD_DEL
        "BACK": 4,  # KEYCODE_BACK
        "HOME": 3,  # KEYCODE_HOME
        "MENU": 82,  # KEYCODE_MENU
        "SEARCH": 84,  # KEYCODE_SEARCH
        "DPAD_UP": 19,
        "DPAD_DOWN": 20,
        "DPAD_LEFT": 21,
        "DPAD_RIGHT": 22,
        "DPAD_CENTER": 23,
    }
    return common.get(name)


def parse_getevent(
    stream: TextIO, keep_device: Optional[str], keep_slot: int
) -> Iterable[Action]:
    """
    Yield actions from getevent logs:
    - Pointer actions reconstructed from ABS_MT_* + SYN_REPORT boundaries
    - Key actions from EV_KEY lines (as DispatchKey-compatible events)

    Notes:
    - Touch parsing is scoped to a single "chosen" touch device inferred from the
      first observed ABS_MT_* stream (or filtered by keep_device).
    - We only finalize touch frames on SYN_REPORT from that same touch device,
      preventing keyboard/other devices' SYN_REPORT from corrupting touch actions.
    """
    # Touch state (single device stream).
    slots: Dict[int, SlotState] = {}
    prev_slots: Dict[int, SlotState] = {}
    active_slot = 0
    chosen_touch_device: Optional[str] = None

    for raw_line in stream:
        line = raw_line.strip()
        if not line:
            continue
        m = GETEVENT_RE.match(line)
        if not m:
            continue
        ts = float(m.group("ts"))
        device = m.group("device")
        if keep_device and (not device or keep_device not in device):
            continue
        etype, code, value = m.group("etype"), m.group("code"), m.group("value")
        try:
            value_int = int(value, 16)
        except ValueError:
            value_int = None

        if etype == "EV_KEY":
            # getevent -lt usually prints DOWN/UP; sometimes numeric.
            v = value.strip().upper()
            if v in ("DOWN", "UP"):
                key_action = v
            elif value_int is not None:
                key_action = "DOWN" if value_int != 0 else "UP"
            else:
                continue

            keycode = _linux_key_to_android_keycode(code)
            if keycode is None:
                continue
            yield KeyAction(ts, key_action, keycode)

        elif etype == "EV_ABS" and value_int is not None:
            # Infer which device is the touch device if not pinned.
            if chosen_touch_device is None and device:
                if code in ("ABS_MT_TRACKING_ID", "ABS_MT_POSITION_X", "ABS_MT_POSITION_Y", "ABS_MT_SLOT"):
                    chosen_touch_device = device
            # Ignore ABS events from other devices (e.g. sensors) once chosen.
            if chosen_touch_device is not None and device and device != chosen_touch_device:
                continue

            if code == "ABS_MT_SLOT":
                active_slot = value_int
            slot = slots.setdefault(active_slot, SlotState())
            if code == "ABS_MT_TRACKING_ID":
                slot.tracking_id = None if value_int == 0xFFFFFFFF else value_int
            elif code == "ABS_MT_POSITION_X":
                slot.x = value_int
            elif code == "ABS_MT_POSITION_Y":
                slot.y = value_int
        elif etype == "EV_SYN" and code == "SYN_REPORT":
            # Only flush touch frame on the chosen touch device's SYN_REPORT.
            if chosen_touch_device is not None and device and device != chosen_touch_device:
                continue
            curr = slots.get(keep_slot)
            prev = prev_slots.get(keep_slot)
            action = detect_action(prev, curr)
            if action and (curr or prev):
                ref = curr if curr and curr.tracking_id is not None else prev
                if ref and ref.x is not None and ref.y is not None:
                    yield PointerAction(ts, keep_slot, action, ref.x, ref.y)
            prev_slots = {k: dataclasses.replace(v) for k, v in slots.items()}


def detect_action(prev: Optional[SlotState], curr: Optional[SlotState]) -> Optional[str]:
    """Determine DOWN/MOVE/UP transitions for a slot."""
    if curr and curr.tracking_id is not None:
        if prev is None or prev.tracking_id is None:
            return "DOWN"
        if curr.tracking_id != prev.tracking_id:
            return "DOWN"
        if curr.x != prev.x or curr.y != prev.y:
            return "MOVE"
    if prev and prev.tracking_id is not None and (curr is None or curr.tracking_id is None):
        return "UP"
    return None


def to_monkey_script(actions: List[Action]) -> List[str]:
    """Render actions into Monkey raw event script lines."""
    if not actions:
        return [
            "type= raw events",
            "count= 0",
            "speed= 1.0",
            "start data >>",
        ]
    start_ts = actions[0].timestamp
    lines: List[str] = ["type= raw events"]
    body: List[str] = []
    prev_ts = start_ts
    pointer_down_time_ms = 0
    key_down_time_ms = 0

    for act in actions:
        wait_ms = int(round((act.timestamp - prev_ts) * 1000))
        if wait_ms > 0:
            body.append(f"UserWait({wait_ms})")
        event_time_ms = int(round((act.timestamp - start_ts) * 1000))

        if isinstance(act, PointerAction):
            if act.action == "DOWN":
                pointer_down_time_ms = event_time_ms
            action_code = {"DOWN": 0, "UP": 1, "MOVE": 2}[act.action]
            body.append(
                "DispatchPointer("
                f"{pointer_down_time_ms},{event_time_ms},{action_code},"
                f"{float(act.x):.1f},{float(act.y):.1f},"
                "1.0,1.0,0,0,0,0,0)"
            )
        else:
            # DispatchKey(downTime,eventTime,action,keyCode,repeat,metaState,device,scancode)
            if act.action == "DOWN":
                key_down_time_ms = event_time_ms
            key_action_code = {"DOWN": 0, "UP": 1}[act.action]
            body.append(
                "DispatchKey("
                f"{key_down_time_ms},{event_time_ms},{key_action_code},"
                f"{act.keycode},0,0,0,0)"
            )
        prev_ts = act.timestamp

    lines.append(f"count= {len(body)}")
    lines.append("speed= 1.0")
    lines.append("start data >>")
    lines.extend(body)
    return lines


def scale_actions(
    actions: List[PointerAction],
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    clamp: Optional[Tuple[int, int]] = None,
) -> List[PointerAction]:
    """
    Scale raw touch coordinates to screen pixel coordinates.

    Many devices report ABS_MT_POSITION_X/Y in a higher-resolution range than
    screen pixels (e.g. max X=10800 for a 1080px wide display). Monkey expects
    pixel coordinates in DispatchPointer().
    """
    if scale_x == 1.0 and scale_y == 1.0 and clamp is None:
        return actions

    out: List[PointerAction] = []
    max_x: Optional[int] = None
    max_y: Optional[int] = None
    if clamp is not None:
        max_x, max_y = clamp

    for a in actions:
        x = a.x * scale_x
        y = a.y * scale_y
        # keep integers for stable output (DispatchPointer renders floats anyway)
        xi = int(round(x))
        yi = int(round(y))
        if max_x is not None:
            xi = max(0, min(max_x, xi))
        if max_y is not None:
            yi = max(0, min(max_y, yi))
        out.append(PointerAction(a.timestamp, a.slot, a.action, xi, yi))
    return out


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for getevent-to-monkey conversion."""
    parser = argparse.ArgumentParser(
        description="Convert `adb shell getevent -lt` output to Monkey script."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default="-",
        help="Path to getevent log (default: stdin).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="-",
        help="Path for generated monkey script (default: stdout).",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        default=None,
        help="Substring of device path to keep, e.g. '/dev/input/event2'.",
    )
    parser.add_argument(
        "--slot",
        type=int,
        default=0,
        help="ABS_MT slot to convert (default: 0).",
    )
    parser.add_argument(
        "--scale-x",
        type=float,
        default=1.0,
        help="Multiply X by this factor before emitting DispatchPointer (default: 1.0).",
    )
    parser.add_argument(
        "--scale-y",
        type=float,
        default=1.0,
        help="Multiply Y by this factor before emitting DispatchPointer (default: 1.0).",
    )
    parser.add_argument(
        "--clamp-w",
        type=int,
        default=None,
        help="Optional clamp width (max X). When set, X will be clamped to [0..w-1].",
    )
    parser.add_argument(
        "--clamp-h",
        type=int,
        default=None,
        help="Optional clamp height (max Y). When set, Y will be clamped to [0..h-1].",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for converter."""
    parser = build_parser()
    args = parser.parse_args(argv)

    input_stream: TextIO
    if args.input == "-":
        input_stream = sys.stdin
    else:
        input_stream = open(args.input, "r", encoding="utf-8")

    actions = list(parse_getevent(input_stream, args.device, args.slot))
    if input_stream is not sys.stdin:
        input_stream.close()

    clamp: Optional[Tuple[int, int]] = None
    if args.clamp_w is not None and args.clamp_h is not None:
        clamp = (int(args.clamp_w) - 1, int(args.clamp_h) - 1)
    # Filter to only PointerActions for scaling
    pointer_actions = [a for a in actions if isinstance(a, PointerAction)]
    scaled_actions = scale_actions(
        pointer_actions, scale_x=float(args.scale_x), scale_y=float(args.scale_y), clamp=clamp
    )

    script_lines = to_monkey_script(scaled_actions)

    if args.output == "-":
        for line in script_lines:
            print(line)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(script_lines))
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
