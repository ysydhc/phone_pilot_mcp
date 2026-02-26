"""
Core module - Platform-agnostic coordination layer.

Contains:
- Protocol definitions (Driver interfaces)
- Driver factory (auto-detect platform and create drivers)
- Skills API (combines Driver + Extension)
- Workflow engine
- Storage utilities
"""

from phone_pilot.core.protocols import (
    InputDriver,
    ScreenDriver,
    UIDriver,
    AppDriver,
    DeviceDriver,
)
from phone_pilot.core.driver_factory import create_driver, detect_device, create_driver_auto
from phone_pilot.core.ui_node import UINode, collect_ui_texts
from phone_pilot.core.skills import DeviceSkills
from phone_pilot.core.storage import (
    now_dirname,
    ensure_abs,
    iso_now,
    safe_name,
    index_path,
    write_json,
    read_json,
    append_jsonl,
    fold_index_records,
    load_recording_by_key,
    infer_start_context,
    now_ts,
)
from phone_pilot.core.healing_experience import (
    HealingExperienceStore,
    HealingRecord,
)
from phone_pilot.core.resource import (
    RES_PREFIX,
    is_res_ref,
    resolve_res_ref,
    resolve_or_cache_path,
    resource_cache_dir,
    resource_index_path,
    res_add,
    res_update,
    res_delete,
    res_get,
    res_list,
    res_resolve,
)

__all__ = [
    # Protocols
    "InputDriver",
    "ScreenDriver",
    "UIDriver",
    "AppDriver",
    "DeviceDriver",
    # Factory
    "create_driver",
    "detect_device",
    "create_driver_auto",
    # UI Node
    "UINode",
    "collect_ui_texts",
    # Skills
    "DeviceSkills",
    # Storage
    "now_dirname",
    "ensure_abs",
    "iso_now",
    "safe_name",
    "index_path",
    "write_json",
    "read_json",
    "append_jsonl",
    "fold_index_records",
    "load_recording_by_key",
    "infer_start_context",
    "now_ts",
    # Healing experience
    "HealingExperienceStore",
    "HealingRecord",
    # Resource cache
    "RES_PREFIX",
    "is_res_ref",
    "resolve_res_ref",
    "resolve_or_cache_path",
    "resource_cache_dir",
    "resource_index_path",
    "res_add",
    "res_update",
    "res_delete",
    "res_get",
    "res_list",
    "res_resolve",
]
