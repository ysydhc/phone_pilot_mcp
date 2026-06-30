"""Resource management MCP tools.

Covers resource cache operations (add/update/delete/get/list/resolve),
recordings listing and retrieval, and replay/monkey script execution.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.core.resource import (
    res_add,
    res_update,
    res_delete,
    res_get,
    res_list,
    res_resolve,
)


# =============================================================================
# Resource Cache Tools
# =============================================================================


@mcp.tool()
async def phone_res_add(
    path: str,
    key: Optional[str] = None,
    out_dir: str = "./.recordings",
    meta: Optional[dict] = None,
) -> dict:
    """添加资源到缓存与索引。
    Add a resource (image) to cache and index.

    Parameters / 参数:
        path: 本地文件路径 / Local file path
        key: 缓存键，未指定则用文件名蛇形命名 / Cache key, auto from filename if omitted
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        meta: 可选元数据 / Optional metadata dict

    Returns / 返回值:
        dict: {"ok": True, "record": {key, origin_path, cached_path, ...}}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 资源复制到 out_dir/cache/pic/ 目录
        - 可通过 @res:key 在其他工具中引用
    """
    try:
        return res_add(path, key=key, out_dir=out_dir, meta=meta)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_update(
    key: str,
    path: Optional[str] = None,
    new_key: Optional[str] = None,
    out_dir: str = "./.recordings",
    meta: Optional[dict] = None,
) -> dict:
    """更新缓存与索引中的资源。
    Update a cached resource.

    Parameters / 参数:
        key: 已有资源的缓存键 / Existing resource key
        path: 可选，新源文件路径以替换 / Optional new source path to replace
        new_key: 可选，重命名键 / Optional new key name
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        meta: 可选，更新元数据 / Optional metadata

    Returns / 返回值:
        dict: {"ok": True, "record": {...}} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 资源仍位于 out_dir/cache/pic/ 目录
    """
    try:
        return res_update(key, path=path, new_key=new_key, out_dir=out_dir, meta=meta)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_delete(
    key: str,
    out_dir: str = "./.recordings",
    delete_file: bool = True,
) -> dict:
    """从缓存与索引删除资源。
    Delete a resource from cache and index.

    Parameters / 参数:
        key: 缓存键 / Cache key
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        delete_file: 是否删除缓存文件，默认 True / Delete cached file, default True

    Returns / 返回值:
        dict: {"ok": True, "deleted": {...}} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 索引位于 out_dir/cache/pic/index.json，文件在 out_dir/cache/pic/
    """
    try:
        return res_delete(key, out_dir=out_dir, delete_file=delete_file)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_get(
    key: str,
    out_dir: str = "./.recordings",
) -> dict:
    """查询缓存资源记录。
    Get a cached resource record.

    Parameters / 参数:
        key: 缓存键 / Cache key
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "record": {key, origin_path, cached_path, ...}}
        或 {"ok": False, "error": "resource_not_found", "key": str}
    """
    try:
        return res_get(key, out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_list(
    out_dir: str = "./.recordings",
) -> dict:
    """列出缓存资源。
    List cached resources.

    Parameters / 参数:
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "records": [...], "count": int}

    Notes / 特殊逻辑:
        - 从 out_dir/cache/pic/index.json 读取索引
    """
    try:
        return res_list(out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_resolve(
    value: str,
    out_dir: str = "./.recordings",
) -> dict:
    """解析 @res:key 到缓存文件路径。
    Resolve @res:key to cached file path.

    Parameters / 参数:
        value: 原始值，可为 @res:key 或普通路径 / Value (e.g. @res:my_image)
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "value", "resolved": str, "cached": bool}
        非 @res: 引用时 resolved=value, cached=False
    """
    try:
        return res_resolve(value, out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_recordings_list(
    out_dir: str = "./.recordings",
    limit: int = 50,
) -> dict:
    """
    List recorded workflows and touch recordings.
    列出录制的工作流和触摸录制。
    """
    from phone_pilot.core.storage import recordings_root

    out_root = recordings_root(out_dir)

    recordings = []

    # List workflow recordings
    workflows_dir = out_root / "workflows"
    if workflows_dir.exists():
        for d in sorted(workflows_dir.iterdir(), reverse=True):
            if d.is_dir():
                meta_path = d / "meta.json"
                recordings.append(
                    {
                        "type": "workflow",
                        "name": d.name,
                        "path": str(d),
                        "has_meta": meta_path.exists(),
                    }
                )
                if len(recordings) >= limit:
                    break

    return {
        "ok": True,
        "recordings": recordings[:limit],
        "count": len(recordings),
        "out_dir": str(out_root),
    }


@mcp.tool()
async def phone_recordings_get(
    recording_key: str,
    out_dir: str = "./.recordings",
) -> dict:
    """
    Get recording details by key.
    根据键获取录制详情。
    """
    from pathlib import Path
    from phone_pilot.core.storage import load_recording_by_key

    return await asyncio.to_thread(
        load_recording_by_key,
        Path(out_dir),
        recording_key,
    )


@mcp.tool()
async def phone_replay_recording(
    recording_key: str,
    device_serial: Optional[str] = None,
    out_dir: str = "./.recordings",
    speed: float = 1.0,
) -> dict:
    """
    Replay a recorded touch sequence.
    重放录制的触摸序列。

    Args:
        recording_key: Recording key/name / 录制键/名称
        device_serial: Device serial / 设备序列号
        out_dir: Recordings directory / 录制目录
        speed: Playback speed multiplier / 播放速度倍数
    """
    from pathlib import Path
    from phone_pilot.core.storage import load_recording_by_key
    from phone_pilot.android.recording.touch import push_script
    from phone_pilot.android.touch.monkey import MonkeyRunner

    out_root = Path(out_dir)
    rec = load_recording_by_key(out_root, recording_key)
    if not rec.get("ok"):
        return rec

    mks_data = rec.get("mks_data")
    if not mks_data:
        return {"ok": False, "error": "no_mks_data", "recording_key": recording_key}

    if not device_serial:
        return {"ok": False, "error": "device_serial_required"}

    # Scale timing by speed
    if speed != 1.0:
        for event in mks_data:
            if "t" in event:
                event["t"] = int(event["t"] / speed)

    # Push and run
    result = push_script(device_serial, mks_data)
    if not result.get("ok"):
        return result

    remote_path = result.get("remote_path")
    runner = MonkeyRunner(device_serial)
    run_result = runner.run_script(remote_path)

    return {
        "ok": run_result.get("ok", False),
        "device_serial": device_serial,
        "recording_key": recording_key,
        "speed": speed,
        "run_result": run_result,
    }


@mcp.tool()
async def phone_push_and_run_monkey(
    device_serial: str,
    script_path: str,
) -> dict:
    """
    Push and run a Monkey script on device.
    推送并运行 Monkey 脚本。
    """
    from phone_pilot.android.touch.monkey import MonkeyRunner

    p = pathlib.Path(script_path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": "script_not_found", "script_path": str(p)}

    runner = MonkeyRunner(device_serial)
    result = runner.push_and_run(str(p))

    return {
        "ok": result.get("ok", False),
        "device_serial": device_serial,
        "script_path": str(p),
        **result,
    }
