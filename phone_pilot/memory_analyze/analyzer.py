#!/usr/bin/env python3
"""
HPROF File Analyzer.
HPROF 文件分析器。

Provides functionality to analyze Android heap dump files:
提供分析 Android 堆内存 dump 文件的功能：
- Parse hprof binary format / 解析 hprof 二进制格式
- Calculate memory statistics / 计算内存统计信息
- Find large objects / 查找大对象
- Diff two snapshots / 对比两个快照
"""

from __future__ import annotations

import pathlib
import struct
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, List, Tuple

# HPROF record tags
HPROF_UTF8 = 0x01
HPROF_LOAD_CLASS = 0x02
HPROF_UNLOAD_CLASS = 0x03
HPROF_STACK_FRAME = 0x04
HPROF_STACK_TRACE = 0x05
HPROF_ALLOC_SITES = 0x06
HPROF_HEAP_SUMMARY = 0x07
HPROF_START_THREAD = 0x0A
HPROF_END_THREAD = 0x0B
HPROF_HEAP_DUMP = 0x0C
HPROF_HEAP_DUMP_SEGMENT = 0x1C
HPROF_HEAP_DUMP_END = 0x2C
HPROF_CPU_SAMPLES = 0x0D
HPROF_CONTROL_SETTINGS = 0x0E

# Heap dump sub-records
HPROF_GC_ROOT_UNKNOWN = 0xFF
HPROF_GC_ROOT_JNI_GLOBAL = 0x01
HPROF_GC_ROOT_JNI_LOCAL = 0x02
HPROF_GC_ROOT_JAVA_FRAME = 0x03
HPROF_GC_ROOT_NATIVE_STACK = 0x04
HPROF_GC_ROOT_STICKY_CLASS = 0x05
HPROF_GC_ROOT_THREAD_BLOCK = 0x06
HPROF_GC_ROOT_MONITOR_USED = 0x07
HPROF_GC_ROOT_THREAD_OBJ = 0x08
HPROF_GC_CLASS_DUMP = 0x20
HPROF_GC_INSTANCE_DUMP = 0x21
HPROF_GC_OBJ_ARRAY_DUMP = 0x22
HPROF_GC_PRIM_ARRAY_DUMP = 0x23

# Basic type sizes
HPROF_BASIC_TYPES = {
    2: ("object", 4),    # Object reference (will be id_size)
    4: ("boolean", 1),
    5: ("char", 2),
    6: ("float", 4),
    7: ("double", 8),
    8: ("byte", 1),
    9: ("short", 2),
    10: ("int", 4),
    11: ("long", 8),
}


@dataclass
class ClassInfo:
    """Class information from hprof."""
    class_id: int
    name_id: int
    name: str = ""
    super_class_id: int = 0
    instance_size: int = 0
    instance_count: int = 0
    total_size: int = 0
    fields: List[Tuple[str, int]] = field(default_factory=list)  # (name, type)


@dataclass
class InstanceInfo:
    """Instance information."""
    obj_id: int
    class_id: int
    size: int = 0
    fields: Dict[str, Any] = field(default_factory=dict)


class HprofParser:
    """Parser for HPROF files."""

    def __init__(self, file_path: str):
        self.file_path = pathlib.Path(file_path)
        self.id_size = 4
        self.string_table: Dict[int, str] = {}
        self.classes: Dict[int, ClassInfo] = {}
        self.instances: Dict[int, InstanceInfo] = {}
        self.array_instances: Dict[int, InstanceInfo] = {}
        self.class_stats: Dict[str, Dict[str, Any]] = {}

    def parse(self) -> dict:
        """Parse the HPROF file and return summary."""
        if not self.file_path.exists():
            return {"ok": False, "error": "file_not_found"}

        try:
            with open(self.file_path, "rb") as f:
                self._parse_header(f)
                self._parse_records(f)

            return {
                "ok": True,
                "file": str(self.file_path),
                "id_size": self.id_size,
                "class_count": len(self.classes),
                "instance_count": len(self.instances),
                "array_count": len(self.array_instances),
                "stats": self._build_stats(),
            }

        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _parse_header(self, f: BinaryIO) -> None:
        # Read null-terminated string
        header = b""
        while True:
            b = f.read(1)
            if b == b"\x00" or not b:
                break
            header += b

        # Read id size (4 bytes)
        self.id_size = struct.unpack(">I", f.read(4))[0]

        # Read timestamp (8 bytes, ignore)
        f.read(8)

    def _parse_records(self, f: BinaryIO) -> None:
        while True:
            # Read record header
            header = f.read(9)  # tag (1) + time (4) + length (4)
            if len(header) < 9:
                break

            tag = header[0]
            length = struct.unpack(">I", header[5:9])[0]
            data = f.read(length)

            if tag == HPROF_UTF8:
                self._parse_string_record(data)
            elif tag == HPROF_LOAD_CLASS:
                self._parse_load_class_record(data)
            elif tag == HPROF_HEAP_DUMP or tag == HPROF_HEAP_DUMP_SEGMENT:
                self._parse_heap_dump(data)

    def _parse_string_record(self, data: bytes) -> None:
        string_id = self._read_id(data, 0)
        try:
            string = data[self.id_size:].decode("utf-8", errors="replace")
        except Exception:
            string = ""
        self.string_table[string_id] = string

    def _parse_load_class_record(self, data: bytes) -> None:
        # class_serial (4), class_id (id), stack_serial (4), name_id (id)
        class_id = self._read_id(data, 4)
        name_id = self._read_id(data, 4 + self.id_size + 4)
        class_info = ClassInfo(class_id=class_id, name_id=name_id)
        class_info.name = self.string_table.get(name_id, "")
        self.classes[class_id] = class_info

    def _parse_heap_dump(self, data: bytes) -> None:
        offset = 0
        length = len(data)

        while offset < length:
            sub_tag = data[offset]
            offset += 1

            if sub_tag == HPROF_GC_CLASS_DUMP:
                offset = self._parse_class_dump(data, offset)
            elif sub_tag == HPROF_GC_INSTANCE_DUMP:
                offset = self._parse_instance_dump(data, offset)
            elif sub_tag == HPROF_GC_OBJ_ARRAY_DUMP:
                offset = self._parse_array_dump(data, offset, is_primitive=False)
            elif sub_tag == HPROF_GC_PRIM_ARRAY_DUMP:
                offset = self._parse_array_dump(data, offset, is_primitive=True)
            else:
                # Skip other GC root records
                offset = self._skip_gc_root(sub_tag, data, offset)

    def _parse_class_dump(self, data: bytes, offset: int) -> int:
        # class_id (id)
        class_id = self._read_id(data, offset)
        offset += self.id_size

        # stack_trace_serial (4)
        offset += 4

        # super_class_id (id)
        super_class_id = self._read_id(data, offset)
        offset += self.id_size

        # skip class loader, signers, protection domain, reserved, instance size
        offset += self.id_size * 4
        instance_size = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4

        # Skip constant pool
        cp_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        for _ in range(cp_count):
            offset += 2  # constant pool index
            type_id = data[offset]
            offset += 1
            _, size = HPROF_BASIC_TYPES.get(type_id, ("", 0))
            offset += size

        # Skip static fields
        static_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        for _ in range(static_count):
            offset += self.id_size  # name_id
            type_id = data[offset]
            offset += 1
            _, size = HPROF_BASIC_TYPES.get(type_id, ("", 0))
            offset += size

        # Read instance fields
        field_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        fields = []
        for _ in range(field_count):
            name_id = self._read_id(data, offset)
            offset += self.id_size
            type_id = data[offset]
            offset += 1
            fields.append((self.string_table.get(name_id, ""), type_id))

        # Update class info
        cls = self.classes.get(class_id)
        if cls:
            cls.super_class_id = super_class_id
            cls.instance_size = instance_size
            cls.fields = fields

        return offset

    def _parse_instance_dump(self, data: bytes, offset: int) -> int:
        obj_id = self._read_id(data, offset)
        offset += self.id_size

        # stack_trace_serial (4)
        offset += 4

        class_id = self._read_id(data, offset)
        offset += self.id_size

        # instance data length
        data_len = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4

        # Skip instance data for now
        offset += data_len

        # Record instance
        inst = InstanceInfo(obj_id=obj_id, class_id=class_id, size=data_len)
        self.instances[obj_id] = inst

        # Update class stats
        cls = self.classes.get(class_id)
        if cls:
            cls.instance_count += 1
            cls.total_size += data_len

        return offset

    def _parse_array_dump(self, data: bytes, offset: int, *, is_primitive: bool) -> int:
        obj_id = self._read_id(data, offset)
        offset += self.id_size
        offset += 4  # stack trace serial

        num_elements = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4

        if is_primitive:
            elem_type = data[offset]
            offset += 1
            _, elem_size = HPROF_BASIC_TYPES.get(elem_type, ("", 0))
            data_len = num_elements * elem_size
        else:
            self._read_id(data, offset)  # skip class_id
            offset += self.id_size
            data_len = num_elements * self.id_size

        offset += data_len

        inst = InstanceInfo(obj_id=obj_id, class_id=0, size=data_len)
        self.array_instances[obj_id] = inst
        return offset

    def _skip_gc_root(self, sub_tag: int, data: bytes, offset: int) -> int:
        # All GC roots have an object ID followed by type-specific data
        offset += self.id_size
        if sub_tag == HPROF_GC_ROOT_JNI_LOCAL:
            offset += 8  # thread serial + frame depth
        elif sub_tag == HPROF_GC_ROOT_JAVA_FRAME:
            offset += 8
        elif sub_tag == HPROF_GC_ROOT_NATIVE_STACK:
            offset += 4
        elif sub_tag == HPROF_GC_ROOT_THREAD_BLOCK:
            offset += 4
        elif sub_tag == HPROF_GC_ROOT_THREAD_OBJ:
            offset += 8
        return offset

    def _read_id(self, data: bytes, offset: int) -> int:
        if self.id_size == 4:
            return struct.unpack(">I", data[offset:offset + 4])[0]
        if self.id_size == 8:
            return struct.unpack(">Q", data[offset:offset + 8])[0]
        raise ValueError("Unsupported id_size")

    def _build_stats(self) -> dict:
        stats = []
        for cls_id, cls in self.classes.items():
            if cls.instance_count > 0:
                stats.append({
                    "class_name": cls.name,
                    "instance_count": cls.instance_count,
                    "total_size": cls.total_size,
                    "instance_size": cls.instance_size,
                })

        # Sort by total size desc
        stats.sort(key=lambda x: x["total_size"], reverse=True)
        return stats


def analyze_hprof(
    file_path: str,
    *,
    large_object_threshold: int = 1024 * 1024,
) -> dict:
    """Analyze hprof file and return summary."""
    _ = large_object_threshold
    parser = HprofParser(file_path)
    return parser.parse()


def get_large_objects(file_path: str, top_n: int = 20) -> dict:
    """Get largest objects by total size."""
    res = analyze_hprof(file_path)
    if not res.get("ok"):
        return res
    stats = res.get("stats", [])
    return {"ok": True, "top": stats[:max(1, int(top_n))]}


def diff_hprof(
    before_path: str,
    after_path: str,
    *,
    large_object_threshold: int = 1024 * 1024,
) -> dict:
    """Diff two hprof snapshots."""
    _ = large_object_threshold
    before = analyze_hprof(before_path)
    after = analyze_hprof(after_path)
    if not before.get("ok"):
        return before
    if not after.get("ok"):
        return after

    before_stats = {s["class_name"]: s for s in before.get("stats", [])}
    after_stats = {s["class_name"]: s for s in after.get("stats", [])}

    diff = []
    all_classes = set(before_stats.keys()) | set(after_stats.keys())
    for cls in all_classes:
        b = before_stats.get(cls, {"instance_count": 0, "total_size": 0})
        a = after_stats.get(cls, {"instance_count": 0, "total_size": 0})
        diff.append({
            "class_name": cls,
            "instance_count_diff": a["instance_count"] - b["instance_count"],
            "total_size_diff": a["total_size"] - b["total_size"],
        })

    diff.sort(key=lambda x: abs(x["total_size_diff"]), reverse=True)

    return {
        "ok": True,
        "before": before.get("file"),
        "after": after.get("file"),
        "diff": diff,
    }
