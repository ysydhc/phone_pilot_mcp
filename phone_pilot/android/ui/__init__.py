"""
Android UI automation utilities.

Provides UIAutomator integration, element finding, and UI interactions.
"""

from phone_pilot.android.ui.automator import (
    UINode,
    dump_ui_xml,
    parse_uiautomator_nodes,
    pick_scrollable_bounds,
    ui_signature,
    find_nodes,
)

__all__ = [
    # Automator
    "UINode",
    "dump_ui_xml",
    "parse_uiautomator_nodes",
    "pick_scrollable_bounds",
    "ui_signature",
    "find_nodes",
]
