#!/usr/bin/env python3
"""
Android Bitmap Analyzer for HPROF files.
Android Bitmap 分析器（用于 HPROF 文件）。

Extracts Bitmap objects from hprof and estimates native pixel memory.
从 hprof 提取 Bitmap 对象并估算 native 像素内存。

Note: On Android 8.0+, Bitmap pixels are stored in native memory,
so they won't appear in the Java heap. This analyzer extracts
Bitmap object metadata (width, height) to estimate the actual memory usage.

注意：在 Android 8.0+ 上，Bitmap 像素存储在 native 内存中，
因此它们不会出现在 Java 堆中。此分析器提取 Bitmap 对象的元数据
（宽度、高度）来估算实际的内存使用。
"""

from __future__ import annotations

import pathlib
import struct
from dataclasses import dataclass
from typing import BinaryIO, Dict, List, Optional

# HPROF record tags (same as analyzer.py)
HPROF_UTF8 = 0x01
HPROF_LOAD_CLASS = 0x02
HPROF_STACK_FRAME = 0x04
HPROF_STACK_TRACE = 0x05
HPROF_HEAP_DUMP = 0x0C
HPROF_HEAP_DUMP_SEGMENT = 0x1C

# Heap dump sub-records
HPROF_GC_CLASS_DUMP = 0x20
HPROF_GC_INSTANCE_DUMP = 0x21

# Basic type IDs
TYPE_OBJECT = 2
TYPE_BOOLEAN = 4
TYPE_CHAR = 5
TYPE_FLOAT = 6
TYPE_DOUBLE = 7
TYPE_BYTE = 8
TYPE_SHORT = 9
TYPE_INT = 10
TYPE_LONG = 11

# Type sizes
TYPE_SIZES = {
    TYPE_OBJECT: 4,  # Will be id_size
    TYPE_BOOLEAN: 1,
    TYPE_CHAR: 2,
    TYPE_FLOAT: 4,
    TYPE_DOUBLE: 8,
    TYPE_BYTE: 1,
    TYPE_SHORT: 2,
    TYPE_INT: 4,
    TYPE_LONG: 8,
}

# Bitmap config bytes per pixel
BITMAP_CONFIGS = {
    "ALPHA_8": 1,
    "RGB_565": 2,
    "ARGB_4444": 2,
    "ARGB_8888": 4,
    "RGBA_F16": 8,
    "HARDWARE": 4,  # Usually ARGB_8888
}


@dataclass
class StackFrame:
    """Stack frame information from hprof."""
    frame_id: int
    method_name: str = ""
    method_signature: str = ""
    source_file: str = ""
    class_name: str = ""
    line_number: int = -1  # -1 means unknown, -2 means compiled method


@dataclass
class BitmapInfo:
    """Bitmap object information."""
    obj_id: int
    width: int = 0
    height: int = 0
    config: str = "ARGB_8888"
    allocation_byte_count: int = 0
    byte_count: int = 0
    native_size: int = 0


class BitmapAnalyzer:
    """Analyze Bitmap objects in HPROF."""

    def __init__(self, file_path: str):
        self.file_path = pathlib.Path(file_path)
        self.id_size = 4
        self.string_table: Dict[int, str] = {}
        self.class_name_by_id: Dict[int, str] = {}
        self.bitmap_class_id: Optional[int] = None
        self.bitmaps: List[BitmapInfo] = []

    def analyze(self) -> dict:
        if not self.file_path.exists():
            return {"ok": False, "error": "file_not_found"}

        try:
            with open(self.file_path, "rb") as f:
                self._parse_header(f)
                self._parse_records(f)

            return {
                "ok": True,
                "file": str(self.file_path),
                "bitmap_count": len(self.bitmaps),
                "bitmaps": [b.__dict__ for b in self.bitmaps],
                "summary": self._build_summary(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _parse_header(self, f: BinaryIO) -> None:
        header = b""
        while True:
            b = f.read(1)
            if b == b"\x00" or not b:
                break
            header += b
        self.id_size = struct.unpack(">I", f.read(4))[0]
        f.read(8)  # timestamp

    def _parse_records(self, f: BinaryIO) -> None:
        while True:
            header = f.read(9)
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
        class_id = self._read_id(data, 4)
        name_id = self._read_id(data, 4 + self.id_size + 4)
        class_name = self.string_table.get(name_id, "")
        self.class_name_by_id[class_id] = class_name
        if class_name == "android.graphics.Bitmap":
            self.bitmap_class_id = class_id

    def _parse_heap_dump(self, data: bytes) -> None:
        offset = 0
        length = len(data)
        while offset < length:
            sub_tag = data[offset]
            offset += 1
            if sub_tag == HPROF_GC_CLASS_DUMP:
                offset = self._skip_class_dump(data, offset)
            elif sub_tag == HPROF_GC_INSTANCE_DUMP:
                offset = self._parse_instance_dump(data, offset)
            else:
                offset = self._skip_gc_root(sub_tag, data, offset)

    def _skip_class_dump(self, data: bytes, offset: int) -> int:
        # class_id (id)
        offset += self.id_size
        offset += 4  # stack_trace_serial
        offset += self.id_size  # super_class_id
        offset += self.id_size * 4  # class_loader, signers, protection, reserved
        offset += 4  # instance_size

        cp_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        for _ in range(cp_count):
            offset += 2
            type_id = data[offset]
            offset += 1
            offset += TYPE_SIZES.get(type_id, 0)

        static_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        for _ in range(static_count):
            offset += self.id_size
            type_id = data[offset]
            offset += 1
            offset += TYPE_SIZES.get(type_id, 0)

        field_count = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        offset += field_count * (self.id_size + 1)
        return offset

    def _parse_instance_dump(self, data: bytes, offset: int) -> int:
        obj_id = self._read_id(data, offset)
        offset += self.id_size
        offset += 4  # stack_trace_serial
        class_id = self._read_id(data, offset)
        offset += self.id_size
        data_len = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        inst_data = data[offset:offset + data_len]
        offset += data_len

        if self.bitmap_class_id and class_id == self.bitmap_class_id:
            bitmap = self._parse_bitmap_instance(obj_id, inst_data)
            if bitmap:
                self.bitmaps.append(bitmap)

        return offset

    def _parse_bitmap_instance(self, obj_id: int, data: bytes) -> Optional[BitmapInfo]:
        # Simplified heuristic: read width/height/config if present
        # This depends on Android version; here we try to interpret as ints.
        if len(data) < 16:
            return None
        try:
            width = struct.unpack(">I", data[0:4])[0]
            height = struct.unpack(">I", data[4:8])[0]
            config_id = struct.unpack(">I", data[8:12])[0]
            byte_count = struct.unpack(">I", data[12:16])[0]
        except Exception:
            return None

        config_name = "ARGB_8888"
        if isinstance(config_id, int):
            config_name = "ARGB_8888"

        native_size = width * height * BITMAP_CONFIGS.get(config_name, 4)

        return BitmapInfo(
            obj_id=obj_id,
            width=width,
            height=height,
            config=config_name,
            allocation_byte_count=byte_count,
            byte_count=byte_count,
            native_size=native_size,
        )

    def _skip_gc_root(self, sub_tag: int, data: bytes, offset: int) -> int:
        offset += self.id_size
        if sub_tag == 0x02:  # JNI_LOCAL
            offset += 8
        elif sub_tag == 0x03:  # JAVA_FRAME
            offset += 8
        elif sub_tag == 0x04:  # NATIVE_STACK
            offset += 4
        elif sub_tag == 0x06:  # THREAD_BLOCK
            offset += 4
        elif sub_tag == 0x08:  # THREAD_OBJ
            offset += 8
        return offset

    def _read_id(self, data: bytes, offset: int) -> int:
        if self.id_size == 4:
            return struct.unpack(">I", data[offset:offset + 4])[0]
        if self.id_size == 8:
            return struct.unpack(">Q", data[offset:offset + 8])[0]
        raise ValueError("Unsupported id_size")

    def _build_summary(self) -> dict:
        total_native = sum(b.native_size for b in self.bitmaps)
        return {
            "total_native_bytes": total_native,
            "total_native_mb": round(total_native / (1024 * 1024), 2),
        }


def analyze_bitmaps(file_path: str) -> dict:
    analyzer = BitmapAnalyzer(file_path)
    return analyzer.analyze()


def diff_bitmaps(before_path: str, after_path: str) -> dict:
    before = analyze_bitmaps(before_path)
    after = analyze_bitmaps(after_path)
    if not before.get("ok"):
        return before
    if not after.get("ok"):
        return after

    return {
        "ok": True,
        "before": before.get("summary"),
        "after": after.get("summary"),
        "diff_native_bytes": (after.get("summary", {}).get("total_native_bytes", 0) -
                              before.get("summary", {}).get("total_native_bytes", 0)),
        "diff_native_mb": (after.get("summary", {}).get("total_native_mb", 0) -
                           before.get("summary", {}).get("total_native_mb", 0)),
    }
