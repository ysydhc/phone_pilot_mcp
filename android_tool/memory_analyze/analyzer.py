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
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

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
    """Instance information from hprof."""
    object_id: int
    class_id: int
    size: int


@dataclass
class ArrayInfo:
    """Array information from hprof."""
    object_id: int
    element_type: int
    length: int
    size: int
    class_id: int = 0  # For object arrays


@dataclass
class HprofSummary:
    """Summary of hprof analysis."""
    file_path: str
    file_size: int
    total_instances: int
    total_arrays: int
    total_classes: int
    total_heap_size: int
    class_stats: Dict[str, Dict[str, Any]]  # class_name -> {count, total_size, avg_size}
    large_objects: List[Dict[str, Any]]
    top_classes_by_count: List[Dict[str, Any]]
    top_classes_by_size: List[Dict[str, Any]]


class HprofParser:
    """
    Parser for HPROF binary format.
    HPROF 二进制格式解析器。
    """

    def __init__(self, file_path: str):
        self.file_path = pathlib.Path(file_path)
        self.id_size = 4  # Default, will be read from header
        self.strings: Dict[int, str] = {}  # string_id -> string
        self.classes: Dict[int, ClassInfo] = {}  # class_id -> ClassInfo
        self.class_name_to_id: Dict[int, int] = {}  # name_id -> class_id
        self.instances: List[InstanceInfo] = []
        self.arrays: List[ArrayInfo] = []
        self.total_heap_size = 0

    def _read_id(self, f: BinaryIO) -> int:
        """Read an ID value based on id_size."""
        data = f.read(self.id_size)
        if len(data) < self.id_size:
            return 0
        if self.id_size == 4:
            return struct.unpack(">I", data)[0]
        elif self.id_size == 8:
            return struct.unpack(">Q", data)[0]
        return int.from_bytes(data, "big")

    def _read_u1(self, f: BinaryIO) -> int:
        data = f.read(1)
        return data[0] if data else 0

    def _read_u2(self, f: BinaryIO) -> int:
        data = f.read(2)
        return struct.unpack(">H", data)[0] if len(data) == 2 else 0

    def _read_u4(self, f: BinaryIO) -> int:
        data = f.read(4)
        return struct.unpack(">I", data)[0] if len(data) == 4 else 0

    def _read_u8(self, f: BinaryIO) -> int:
        data = f.read(8)
        return struct.unpack(">Q", data)[0] if len(data) == 8 else 0

    def _get_basic_type_size(self, type_id: int) -> int:
        """Get size of a basic type."""
        if type_id == 2:  # Object reference
            return self.id_size
        return HPROF_BASIC_TYPES.get(type_id, ("unknown", 1))[1]

    def parse(self) -> bool:
        """
        Parse the hprof file.
        解析 hprof 文件。

        Returns:
            True if parsing succeeded / 解析成功返回 True
        """
        try:
            with open(self.file_path, "rb") as f:
                # Read header
                # Format: null-terminated string, u4 id_size, u8 timestamp
                header = b""
                while True:
                    b = f.read(1)
                    if not b or b == b"\x00":
                        break
                    header += b

                if not header.startswith(b"JAVA PROFILE"):
                    # Try to handle Android format
                    pass

                self.id_size = self._read_u4(f)
                if self.id_size not in (4, 8):
                    self.id_size = 4  # Default fallback

                _timestamp_high = self._read_u4(f)
                _timestamp_low = self._read_u4(f)

                # Read records
                while True:
                    tag_data = f.read(1)
                    if not tag_data:
                        break
                    tag = tag_data[0]

                    _time = self._read_u4(f)
                    length = self._read_u4(f)

                    if length == 0:
                        continue

                    start_pos = f.tell()
                    self._parse_record(f, tag, length)

                    # Ensure we've consumed exactly 'length' bytes
                    end_pos = f.tell()
                    consumed = end_pos - start_pos
                    if consumed < length:
                        f.seek(length - consumed, 1)  # Skip remaining bytes

            # Build class names
            for class_id, cls in self.classes.items():
                if cls.name_id in self.strings:
                    cls.name = self.strings[cls.name_id]

            return True

        except Exception as e:
            import sys
            print(f"[memory_analyze] parse error: {e}", file=sys.stderr)
            return False

    def _parse_record(self, f: BinaryIO, tag: int, length: int) -> None:
        """Parse a single record."""
        if tag == HPROF_UTF8:
            string_id = self._read_id(f)
            str_len = length - self.id_size
            if str_len > 0:
                str_data = f.read(str_len)
                try:
                    self.strings[string_id] = str_data.decode("utf-8", errors="replace")
                except Exception:
                    self.strings[string_id] = str_data.decode("latin-1", errors="replace")

        elif tag == HPROF_LOAD_CLASS:
            _serial = self._read_u4(f)
            class_id = self._read_id(f)
            _stack_serial = self._read_u4(f)
            name_id = self._read_id(f)
            self.classes[class_id] = ClassInfo(class_id=class_id, name_id=name_id)
            self.class_name_to_id[name_id] = class_id

        elif tag in (HPROF_HEAP_DUMP, HPROF_HEAP_DUMP_SEGMENT):
            self._parse_heap_dump(f, length)

    def _parse_heap_dump(self, f: BinaryIO, length: int) -> None:
        """Parse heap dump segment."""
        end_pos = f.tell() + length

        while f.tell() < end_pos:
            sub_tag = self._read_u1(f)

            if sub_tag == HPROF_GC_ROOT_UNKNOWN:
                self._read_id(f)
            elif sub_tag == HPROF_GC_ROOT_JNI_GLOBAL:
                self._read_id(f)
                self._read_id(f)
            elif sub_tag == HPROF_GC_ROOT_JNI_LOCAL:
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            elif sub_tag == HPROF_GC_ROOT_JAVA_FRAME:
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            elif sub_tag == HPROF_GC_ROOT_NATIVE_STACK:
                self._read_id(f)
                self._read_u4(f)
            elif sub_tag == HPROF_GC_ROOT_STICKY_CLASS:
                self._read_id(f)
            elif sub_tag == HPROF_GC_ROOT_THREAD_BLOCK:
                self._read_id(f)
                self._read_u4(f)
            elif sub_tag == HPROF_GC_ROOT_MONITOR_USED:
                self._read_id(f)
            elif sub_tag == HPROF_GC_ROOT_THREAD_OBJ:
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            elif sub_tag == HPROF_GC_CLASS_DUMP:
                self._parse_class_dump(f)
            elif sub_tag == HPROF_GC_INSTANCE_DUMP:
                self._parse_instance_dump(f)
            elif sub_tag == HPROF_GC_OBJ_ARRAY_DUMP:
                self._parse_obj_array_dump(f)
            elif sub_tag == HPROF_GC_PRIM_ARRAY_DUMP:
                self._parse_prim_array_dump(f)
            elif sub_tag == 0xFE:
                # HPROF_HEAP_DUMP_INFO (Android specific)
                # heap_type (u4) + heap_name_id
                self._read_u4(f)
                self._read_id(f)
            elif sub_tag == 0x89:
                # HPROF_ROOT_INTERNED_STRING (Android specific)
                self._read_id(f)
            elif sub_tag == 0x8A:
                # HPROF_ROOT_FINALIZING (Android specific)
                self._read_id(f)
            elif sub_tag == 0x8B:
                # HPROF_ROOT_DEBUGGER (Android specific)
                self._read_id(f)
            elif sub_tag == 0x8C:
                # HPROF_ROOT_REFERENCE_CLEANUP (Android specific)
                self._read_id(f)
            elif sub_tag == 0x8D:
                # HPROF_ROOT_VM_INTERNAL (Android specific)
                self._read_id(f)
            elif sub_tag == 0x8E:
                # HPROF_ROOT_JNI_MONITOR (Android specific)
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            elif sub_tag == 0x90:
                # HPROF_UNREACHABLE (Android specific) - skip
                self._read_id(f)
            else:
                # Unknown tag - log but continue parsing
                # Don't break; try to skip to next valid position
                import sys
                print(f"[memory_analyze] unknown heap dump sub_tag: 0x{sub_tag:02X} at pos {f.tell()}", file=sys.stderr)
                # Try to continue - the file position might be off, but continue anyway
                continue

    def _parse_class_dump(self, f: BinaryIO) -> None:
        """Parse class dump record."""
        class_id = self._read_id(f)
        _stack_serial = self._read_u4(f)
        super_class_id = self._read_id(f)
        _class_loader_id = self._read_id(f)
        _signers_id = self._read_id(f)
        _prot_domain_id = self._read_id(f)
        _reserved1 = self._read_id(f)
        _reserved2 = self._read_id(f)
        instance_size = self._read_u4(f)

        # Constant pool
        const_count = self._read_u2(f)
        for _ in range(const_count):
            _idx = self._read_u2(f)
            type_id = self._read_u1(f)
            size = self._get_basic_type_size(type_id)
            f.read(size)

        # Static fields
        static_count = self._read_u2(f)
        for _ in range(static_count):
            _name_id = self._read_id(f)
            type_id = self._read_u1(f)
            size = self._get_basic_type_size(type_id)
            f.read(size)

        # Instance fields
        inst_field_count = self._read_u2(f)
        fields = []
        for _ in range(inst_field_count):
            name_id = self._read_id(f)
            type_id = self._read_u1(f)
            name = self.strings.get(name_id, f"field_{name_id}")
            fields.append((name, type_id))

        if class_id in self.classes:
            self.classes[class_id].super_class_id = super_class_id
            self.classes[class_id].instance_size = instance_size
            self.classes[class_id].fields = fields

    def _parse_instance_dump(self, f: BinaryIO) -> None:
        """Parse instance dump record."""
        object_id = self._read_id(f)
        _stack_serial = self._read_u4(f)
        class_id = self._read_id(f)
        data_size = self._read_u4(f)
        f.read(data_size)  # Skip instance data

        # Calculate actual size (header + data)
        size = self.id_size + 4 + self.id_size + 4 + data_size

        self.instances.append(InstanceInfo(object_id=object_id, class_id=class_id, size=size))
        self.total_heap_size += size

        if class_id in self.classes:
            self.classes[class_id].instance_count += 1
            self.classes[class_id].total_size += size

    def _parse_obj_array_dump(self, f: BinaryIO) -> None:
        """Parse object array dump record."""
        object_id = self._read_id(f)
        _stack_serial = self._read_u4(f)
        length = self._read_u4(f)
        class_id = self._read_id(f)

        # Read element IDs
        f.read(length * self.id_size)

        # Calculate size
        size = self.id_size + 4 + 4 + self.id_size + (length * self.id_size)

        self.arrays.append(ArrayInfo(
            object_id=object_id,
            element_type=2,  # Object
            length=length,
            size=size,
            class_id=class_id,
        ))
        self.total_heap_size += size

    def _parse_prim_array_dump(self, f: BinaryIO) -> None:
        """Parse primitive array dump record."""
        object_id = self._read_id(f)
        _stack_serial = self._read_u4(f)
        length = self._read_u4(f)
        element_type = self._read_u1(f)

        element_size = self._get_basic_type_size(element_type)
        f.read(length * element_size)

        # Calculate size
        size = self.id_size + 4 + 4 + 1 + (length * element_size)

        self.arrays.append(ArrayInfo(
            object_id=object_id,
            element_type=element_type,
            length=length,
            size=size,
        ))
        self.total_heap_size += size

    def get_summary(self, large_object_threshold: int = 1024 * 1024) -> HprofSummary:
        """
        Get analysis summary.
        获取分析摘要。

        Args:
            large_object_threshold: Size threshold for large objects in bytes / 大对象阈值（字节）

        Returns:
            HprofSummary with statistics / 包含统计信息的摘要
        """
        # Build class statistics
        class_stats: Dict[str, Dict[str, Any]] = {}
        for class_id, cls in self.classes.items():
            if cls.instance_count > 0:
                name = cls.name or f"class_{class_id}"
                class_stats[name] = {
                    "count": cls.instance_count,
                    "total_size": cls.total_size,
                    "avg_size": cls.total_size // cls.instance_count if cls.instance_count > 0 else 0,
                }

        # Find large objects
        large_objects: List[Dict[str, Any]] = []

        for inst in self.instances:
            if inst.size >= large_object_threshold:
                cls = self.classes.get(inst.class_id)
                class_name = cls.name if cls else f"class_{inst.class_id}"
                large_objects.append({
                    "type": "instance",
                    "object_id": inst.object_id,
                    "class_name": class_name,
                    "size": inst.size,
                })

        for arr in self.arrays:
            if arr.size >= large_object_threshold:
                type_name = HPROF_BASIC_TYPES.get(arr.element_type, ("unknown", 1))[0]
                if arr.element_type == 2 and arr.class_id in self.classes:
                    cls = self.classes[arr.class_id]
                    type_name = f"{cls.name}[]" if cls.name else f"Object[]"
                else:
                    type_name = f"{type_name}[]"
                large_objects.append({
                    "type": "array",
                    "object_id": arr.object_id,
                    "class_name": type_name,
                    "length": arr.length,
                    "size": arr.size,
                })

        large_objects.sort(key=lambda x: x["size"], reverse=True)

        # Top classes by count
        top_by_count = sorted(
            [{"name": k, **v} for k, v in class_stats.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:20]

        # Top classes by size
        top_by_size = sorted(
            [{"name": k, **v} for k, v in class_stats.items()],
            key=lambda x: x["total_size"],
            reverse=True,
        )[:20]

        return HprofSummary(
            file_path=str(self.file_path),
            file_size=self.file_path.stat().st_size if self.file_path.exists() else 0,
            total_instances=len(self.instances),
            total_arrays=len(self.arrays),
            total_classes=len([c for c in self.classes.values() if c.instance_count > 0]),
            total_heap_size=self.total_heap_size,
            class_stats=class_stats,
            large_objects=large_objects[:50],  # Top 50 large objects
            top_classes_by_count=top_by_count,
            top_classes_by_size=top_by_size,
        )


def analyze_hprof(
    hprof_path: str,
    *,
    large_object_threshold: int = 1024 * 1024,  # 1MB default
) -> dict:
    """
    Analyze an hprof file.
    分析 hprof 文件。

    Args:
        hprof_path: Path to hprof file / hprof 文件路径
        large_object_threshold: Size threshold for large objects in bytes / 大对象阈值（字节）

    Returns:
        dict with analysis results / 包含分析结果的字典
    """
    path = pathlib.Path(hprof_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {hprof_path}"}

    parser = HprofParser(str(path))
    if not parser.parse():
        return {"ok": False, "error": "Failed to parse hprof file"}

    summary = parser.get_summary(large_object_threshold=large_object_threshold)

    return {
        "ok": True,
        "file_path": summary.file_path,
        "file_size": summary.file_size,
        "total_instances": summary.total_instances,
        "total_arrays": summary.total_arrays,
        "total_classes": summary.total_classes,
        "total_heap_size": summary.total_heap_size,
        "total_heap_size_mb": round(summary.total_heap_size / (1024 * 1024), 2),
        "large_objects": summary.large_objects,
        "large_objects_count": len(summary.large_objects),
        "top_classes_by_count": summary.top_classes_by_count,
        "top_classes_by_size": summary.top_classes_by_size,
    }


def get_large_objects(
    hprof_path: str,
    *,
    threshold_bytes: int = 1024 * 1024,  # 1MB default
    limit: int = 50,
) -> dict:
    """
    Get large objects from hprof file.
    从 hprof 文件获取大对象。

    Args:
        hprof_path: Path to hprof file / hprof 文件路径
        threshold_bytes: Minimum size to be considered large / 大对象最小阈值（字节）
        limit: Maximum number of objects to return / 返回的最大对象数量

    Returns:
        dict with large objects list and statistics / 包含大对象列表和统计的字典
    """
    result = analyze_hprof(hprof_path, large_object_threshold=threshold_bytes)
    if not result.get("ok"):
        return result

    large_objects = result.get("large_objects", [])[:limit]

    # Calculate statistics
    total_large_size = sum(obj.get("size", 0) for obj in large_objects)
    total_heap_size = result.get("total_heap_size", 1)
    large_ratio = round(total_large_size / total_heap_size * 100, 2) if total_heap_size > 0 else 0

    # Group by class
    by_class: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0, "total_size": 0})
    for obj in large_objects:
        class_name = obj.get("class_name", "unknown")
        by_class[class_name]["count"] += 1
        by_class[class_name]["total_size"] += obj.get("size", 0)

    by_class_list = sorted(
        [{"class_name": k, **v} for k, v in by_class.items()],
        key=lambda x: x["total_size"],
        reverse=True,
    )

    return {
        "ok": True,
        "threshold_bytes": threshold_bytes,
        "large_objects": large_objects,
        "large_objects_count": len(large_objects),
        "total_large_size": total_large_size,
        "total_large_size_mb": round(total_large_size / (1024 * 1024), 2),
        "total_heap_size": total_heap_size,
        "total_heap_size_mb": round(total_heap_size / (1024 * 1024), 2),
        "large_ratio_percent": large_ratio,
        "by_class": by_class_list,
    }


def diff_hprof(
    before_path: str,
    after_path: str,
    *,
    large_object_threshold: int = 1024 * 1024,
) -> dict:
    """
    Compare two hprof files and show differences.
    对比两个 hprof 文件并显示差异。

    Args:
        before_path: Path to the "before" hprof file / "之前"的 hprof 文件路径
        after_path: Path to the "after" hprof file / "之后"的 hprof 文件路径
        large_object_threshold: Size threshold for large objects / 大对象阈值

    Returns:
        dict with diff results including:
        - heap_size_diff: Change in total heap size / 堆大小变化
        - class_diffs: Per-class changes / 每个类的变化
        - new_large_objects: Large objects only in "after" / 仅在"之后"中的大对象
    """
    before = analyze_hprof(before_path, large_object_threshold=large_object_threshold)
    after = analyze_hprof(after_path, large_object_threshold=large_object_threshold)

    if not before.get("ok"):
        return {"ok": False, "error": f"Failed to analyze before file: {before.get('error')}"}
    if not after.get("ok"):
        return {"ok": False, "error": f"Failed to analyze after file: {after.get('error')}"}

    # Calculate heap size diff
    before_heap = before.get("total_heap_size", 0)
    after_heap = after.get("total_heap_size", 0)
    heap_diff = after_heap - before_heap
    heap_diff_percent = round(heap_diff / before_heap * 100, 2) if before_heap > 0 else 0

    # Build class lookup for before snapshot
    before_parser = HprofParser(before_path)
    before_parser.parse()
    before_summary = before_parser.get_summary(large_object_threshold)

    after_parser = HprofParser(after_path)
    after_parser.parse()
    after_summary = after_parser.get_summary(large_object_threshold)

    # Compare classes
    all_classes = set(before_summary.class_stats.keys()) | set(after_summary.class_stats.keys())

    class_diffs: List[Dict[str, Any]] = []
    for class_name in all_classes:
        before_stats = before_summary.class_stats.get(class_name, {"count": 0, "total_size": 0})
        after_stats = after_summary.class_stats.get(class_name, {"count": 0, "total_size": 0})

        count_diff = after_stats["count"] - before_stats["count"]
        size_diff = after_stats["total_size"] - before_stats["total_size"]

        if count_diff != 0 or size_diff != 0:
            class_diffs.append({
                "class_name": class_name,
                "before_count": before_stats["count"],
                "after_count": after_stats["count"],
                "count_diff": count_diff,
                "before_size": before_stats["total_size"],
                "after_size": after_stats["total_size"],
                "size_diff": size_diff,
            })

    # Sort by size diff (descending by absolute value)
    class_diffs.sort(key=lambda x: abs(x["size_diff"]), reverse=True)

    # Top increases
    top_increases = [d for d in class_diffs if d["size_diff"] > 0][:20]

    # Top decreases
    top_decreases = [d for d in class_diffs if d["size_diff"] < 0][:20]

    # New classes (only in after)
    new_classes = [d for d in class_diffs if d["before_count"] == 0 and d["after_count"] > 0]

    # Removed classes (only in before)
    removed_classes = [d for d in class_diffs if d["before_count"] > 0 and d["after_count"] == 0]

    return {
        "ok": True,
        "before": {
            "path": before_path,
            "heap_size": before_heap,
            "heap_size_mb": round(before_heap / (1024 * 1024), 2),
            "instances": before.get("total_instances", 0),
            "arrays": before.get("total_arrays", 0),
        },
        "after": {
            "path": after_path,
            "heap_size": after_heap,
            "heap_size_mb": round(after_heap / (1024 * 1024), 2),
            "instances": after.get("total_instances", 0),
            "arrays": after.get("total_arrays", 0),
        },
        "diff": {
            "heap_size_diff": heap_diff,
            "heap_size_diff_mb": round(heap_diff / (1024 * 1024), 2),
            "heap_diff_percent": heap_diff_percent,
            "instances_diff": after.get("total_instances", 0) - before.get("total_instances", 0),
            "arrays_diff": after.get("total_arrays", 0) - before.get("total_arrays", 0),
        },
        "top_increases": top_increases,
        "top_decreases": top_decreases,
        "new_classes": new_classes[:20],
        "removed_classes": removed_classes[:20],
        "all_class_diffs_count": len(class_diffs),
    }
