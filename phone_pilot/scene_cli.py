"""Explicit local entry point for reviewed, declarative Android scene restoration."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser(description="重建设备语义现场；不是截图或录像回放")
    parser.add_argument("run_dir")
    parser.add_argument("--device-serial", help="必须显式选择 manifest 中的设备")
    parser.add_argument(
        "--approve-manifest-sha256", help="明确批准已经审阅的 manifest 原始文件 SHA256"
    )
    args = parser.parse_args()
    from phone_pilot.core.scene_restore import inspect_scene, restore_scene

    if args.approve_manifest_sha256:
        if not args.device_serial:
            parser.error("执行复原必须指定 --device-serial")
        result = restore_scene(
            args.run_dir,
            device_serial=args.device_serial,
            approved_manifest_sha256=args.approve_manifest_sha256,
        )
    else:
        result = inspect_scene(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
