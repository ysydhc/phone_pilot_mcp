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
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict, List, Optional, Tuple

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
class StackTrace:
    """Stack trace information from hprof."""
    serial: int
    thread_serial: int = 0
    frames: List[StackFrame] = field(default_factory=list)


@dataclass
class BitmapInfo:
    """Information about a Bitmap object."""
    object_id: int
    width: int = 0
    height: int = 0
    density: int = 0
    config_ordinal: int = -1
    config_name: str = "ARGB_8888"
    bytes_per_pixel: int = 4
    estimated_pixel_bytes: int = 0
    is_recycled: bool = False
    is_mutable: bool = False
    stack_serial: int = 0  # Reference to allocation stack trace
    allocation_stack: List[str] = field(default_factory=list)  # Resolved stack frames


@dataclass
class ClassFieldInfo:
    """Field information from class dump."""
    name: str
    type_id: int
    offset: int = 0  # Offset within instance data


class BitmapHprofParser:
    """
    Specialized parser for extracting Bitmap info from hprof.
    专门用于从 hprof 提取 Bitmap 信息的解析器。
    """

    def __init__(self, hprof_path: str):
        self.file_path = pathlib.Path(hprof_path)
        self.id_size = 4
        self.strings: Dict[int, str] = {}
        self.classes: Dict[int, Dict[str, Any]] = {}  # class_id -> {name, fields, ...}
        self.class_name_to_id: Dict[str, int] = {}  # class_name -> class_id
        self.bitmaps: List[BitmapInfo] = []
        self.bitmap_class_id: Optional[int] = None
        # Stack trace support / 堆栈跟踪支持
        self.stack_frames: Dict[int, StackFrame] = {}  # frame_id -> StackFrame
        self.stack_traces: Dict[int, StackTrace] = {}  # serial -> StackTrace

    def _read_u1(self, f: BinaryIO) -> int:
        return struct.unpack(">B", f.read(1))[0]

    def _read_u2(self, f: BinaryIO) -> int:
        return struct.unpack(">H", f.read(2))[0]

    def _read_u4(self, f: BinaryIO) -> int:
        return struct.unpack(">I", f.read(4))[0]

    def _read_u8(self, f: BinaryIO) -> int:
        return struct.unpack(">Q", f.read(8))[0]

    def _read_id(self, f: BinaryIO) -> int:
        if self.id_size == 8:
            return self._read_u8(f)
        return self._read_u4(f)

    def _get_type_size(self, type_id: int) -> int:
        if type_id == TYPE_OBJECT:
            return self.id_size
        return TYPE_SIZES.get(type_id, 0)

    def parse(self) -> bool:
        """
        Parse hprof file and extract Bitmap information.
        解析 hprof 文件并提取 Bitmap 信息。
        """
        try:
            with open(self.file_path, "rb") as f:
                # Read header
                header = b""
                while True:
                    b = f.read(1)
                    if b == b"\x00":
                        break
                    header += b

                self.id_size = self._read_u4(f)
                _timestamp_high = self._read_u4(f)
                _timestamp_low = self._read_u4(f)

                # First pass: collect strings, classes, and find Bitmap class
                self._first_pass(f)

                # Find Bitmap class ID
                for class_id, cls_info in self.classes.items():
                    if cls_info.get("name") == "android.graphics.Bitmap":
                        self.bitmap_class_id = class_id
                        break

                if self.bitmap_class_id is None:
                    return True  # No Bitmap class found, but not an error

                # Second pass: extract Bitmap instances
                f.seek(0)
                # Skip header again
                while f.read(1) != b"\x00":
                    pass
                f.read(12)  # id_size + timestamp

                self._second_pass(f)

            # Post-process: resolve stack traces for each bitmap
            self._resolve_allocation_stacks()

            return True

        except Exception as e:
            import sys
            print(f"[bitmap_analyzer] parse error: {e}", file=sys.stderr)
            return False

    def _first_pass(self, f: BinaryIO) -> None:
        """First pass: collect strings, class info, and stack traces."""
        while True:
            try:
                tag = self._read_u1(f)
            except struct.error:
                break

            _timestamp = self._read_u4(f)
            length = self._read_u4(f)

            if length == 0:
                continue

            start_pos = f.tell()

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
                class_name = self.strings.get(name_id, "")
                self.classes[class_id] = {"name": class_name, "name_id": name_id, "fields": []}
                if class_name:
                    self.class_name_to_id[class_name] = class_id

            elif tag == HPROF_STACK_FRAME:
                # Parse stack frame: frame_id, method_name_id, method_sig_id, source_file_id, class_serial, line_number
                frame_id = self._read_id(f)
                method_name_id = self._read_id(f)
                method_sig_id = self._read_id(f)
                source_file_id = self._read_id(f)
                class_serial = self._read_u4(f)
                line_number = struct.unpack(">i", f.read(4))[0]  # Signed int

                # Resolve class name from class_serial (need to find class by serial)
                class_name = ""
                for cid, cls in self.classes.items():
                    if cls.get("serial") == class_serial:
                        class_name = cls.get("name", "")
                        break

                self.stack_frames[frame_id] = StackFrame(
                    frame_id=frame_id,
                    method_name=self.strings.get(method_name_id, ""),
                    method_signature=self.strings.get(method_sig_id, ""),
                    source_file=self.strings.get(source_file_id, ""),
                    class_name=class_name,
                    line_number=line_number,
                )

            elif tag == HPROF_STACK_TRACE:
                # Parse stack trace: serial, thread_serial, num_frames, frame_ids[]
                serial = self._read_u4(f)
                thread_serial = self._read_u4(f)
                num_frames = self._read_u4(f)

                frame_ids = []
                for _ in range(num_frames):
                    frame_ids.append(self._read_id(f))

                frames = [self.stack_frames.get(fid) for fid in frame_ids if fid in self.stack_frames]
                self.stack_traces[serial] = StackTrace(
                    serial=serial,
                    thread_serial=thread_serial,
                    frames=[f for f in frames if f is not None],
                )

            elif tag in (HPROF_HEAP_DUMP, HPROF_HEAP_DUMP_SEGMENT):
                self._parse_heap_dump_first_pass(f, length)

            # Ensure we've consumed exactly 'length' bytes
            end_pos = f.tell()
            consumed = end_pos - start_pos
            if consumed < length:
                f.seek(length - consumed, 1)

    def _parse_heap_dump_first_pass(self, f: BinaryIO, length: int) -> None:
        """Parse heap dump to collect class field info."""
        end_pos = f.tell() + length

        while f.tell() < end_pos:
            sub_tag = self._read_u1(f)

            if sub_tag == HPROF_GC_CLASS_DUMP:
                self._parse_class_dump(f)
            elif sub_tag == HPROF_GC_INSTANCE_DUMP:
                # Skip instance during first pass
                _object_id = self._read_id(f)
                _stack_serial = self._read_u4(f)
                _class_id = self._read_id(f)
                data_size = self._read_u4(f)
                f.seek(data_size, 1)
            elif sub_tag == 0x22:  # Object array
                _object_id = self._read_id(f)
                _stack_serial = self._read_u4(f)
                arr_len = self._read_u4(f)
                _class_id = self._read_id(f)
                f.seek(arr_len * self.id_size, 1)
            elif sub_tag == 0x23:  # Primitive array
                _object_id = self._read_id(f)
                _stack_serial = self._read_u4(f)
                arr_len = self._read_u4(f)
                elem_type = self._read_u1(f)
                elem_size = self._get_type_size(elem_type)
                f.seek(arr_len * elem_size, 1)
            elif sub_tag in (0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08):
                # GC roots - skip
                self._skip_gc_root(f, sub_tag)
            elif sub_tag == 0xFE:
                # HPROF_HEAP_DUMP_INFO (Android)
                self._read_u4(f)
                self._read_id(f)
            elif sub_tag in (0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x90):
                # Android-specific roots
                self._read_id(f)
            elif sub_tag == 0x8E:
                # HPROF_ROOT_JNI_MONITOR
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            else:
                # Unknown tag, try to continue
                continue

    def _skip_gc_root(self, f: BinaryIO, sub_tag: int) -> None:
        """Skip GC root records."""
        if sub_tag == 0xFF:  # UNKNOWN
            self._read_id(f)
        elif sub_tag == 0x01:  # JNI_GLOBAL
            self._read_id(f)
            self._read_id(f)
        elif sub_tag == 0x02:  # JNI_LOCAL
            self._read_id(f)
            self._read_u4(f)
            self._read_u4(f)
        elif sub_tag == 0x03:  # JAVA_FRAME
            self._read_id(f)
            self._read_u4(f)
            self._read_u4(f)
        elif sub_tag == 0x04:  # NATIVE_STACK
            self._read_id(f)
            self._read_u4(f)
        elif sub_tag == 0x05:  # STICKY_CLASS
            self._read_id(f)
        elif sub_tag == 0x06:  # THREAD_BLOCK
            self._read_id(f)
            self._read_u4(f)
        elif sub_tag == 0x07:  # MONITOR_USED
            self._read_id(f)
        elif sub_tag == 0x08:  # THREAD_OBJ
            self._read_id(f)
            self._read_u4(f)
            self._read_u4(f)

    def _parse_class_dump(self, f: BinaryIO) -> None:
        """Parse class dump to get field info."""
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
            size = self._get_type_size(type_id)
            f.read(size)

        # Static fields
        static_count = self._read_u2(f)
        for _ in range(static_count):
            _name_id = self._read_id(f)
            type_id = self._read_u1(f)
            size = self._get_type_size(type_id)
            f.read(size)

        # Instance fields
        inst_field_count = self._read_u2(f)
        fields: List[ClassFieldInfo] = []
        offset = 0
        for _ in range(inst_field_count):
            name_id = self._read_id(f)
            type_id = self._read_u1(f)
            name = self.strings.get(name_id, f"field_{name_id}")
            field_info = ClassFieldInfo(name=name, type_id=type_id, offset=offset)
            fields.append(field_info)
            offset += self._get_type_size(type_id)

        if class_id in self.classes:
            self.classes[class_id]["super_class_id"] = super_class_id
            self.classes[class_id]["instance_size"] = instance_size
            self.classes[class_id]["fields"] = fields

    def _second_pass(self, f: BinaryIO) -> None:
        """Second pass: extract Bitmap instances."""
        while True:
            try:
                tag = self._read_u1(f)
            except struct.error:
                break

            _timestamp = self._read_u4(f)
            length = self._read_u4(f)

            if length == 0:
                continue

            start_pos = f.tell()

            if tag in (HPROF_HEAP_DUMP, HPROF_HEAP_DUMP_SEGMENT):
                self._parse_heap_dump_second_pass(f, length)

            # Ensure we've consumed exactly 'length' bytes
            end_pos = f.tell()
            consumed = end_pos - start_pos
            if consumed < length:
                f.seek(length - consumed, 1)

    def _parse_heap_dump_second_pass(self, f: BinaryIO, length: int) -> None:
        """Parse heap dump to extract Bitmap instances."""
        end_pos = f.tell() + length

        while f.tell() < end_pos:
            sub_tag = self._read_u1(f)

            if sub_tag == HPROF_GC_INSTANCE_DUMP:
                object_id = self._read_id(f)
                stack_serial = self._read_u4(f)
                class_id = self._read_id(f)
                data_size = self._read_u4(f)

                # Check if this is a Bitmap instance
                if class_id == self.bitmap_class_id:
                    instance_data = f.read(data_size)
                    bitmap_info = self._parse_bitmap_instance(object_id, class_id, instance_data, stack_serial)
                    if bitmap_info:
                        self.bitmaps.append(bitmap_info)
                else:
                    f.seek(data_size, 1)

            elif sub_tag == HPROF_GC_CLASS_DUMP:
                # Skip class dump
                self._skip_class_dump(f)
            elif sub_tag == 0x22:  # Object array
                _object_id = self._read_id(f)
                _stack_serial = self._read_u4(f)
                arr_len = self._read_u4(f)
                _class_id = self._read_id(f)
                f.seek(arr_len * self.id_size, 1)
            elif sub_tag == 0x23:  # Primitive array
                _object_id = self._read_id(f)
                _stack_serial = self._read_u4(f)
                arr_len = self._read_u4(f)
                elem_type = self._read_u1(f)
                elem_size = self._get_type_size(elem_type)
                f.seek(arr_len * elem_size, 1)
            elif sub_tag in (0xFF, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08):
                self._skip_gc_root(f, sub_tag)
            elif sub_tag == 0xFE:
                self._read_u4(f)
                self._read_id(f)
            elif sub_tag in (0x89, 0x8A, 0x8B, 0x8C, 0x8D, 0x90):
                self._read_id(f)
            elif sub_tag == 0x8E:
                self._read_id(f)
                self._read_u4(f)
                self._read_u4(f)
            else:
                continue

    def _skip_class_dump(self, f: BinaryIO) -> None:
        """Skip class dump record."""
        _class_id = self._read_id(f)
        _stack_serial = self._read_u4(f)
        _super_class_id = self._read_id(f)
        _class_loader_id = self._read_id(f)
        _signers_id = self._read_id(f)
        _prot_domain_id = self._read_id(f)
        _reserved1 = self._read_id(f)
        _reserved2 = self._read_id(f)
        _instance_size = self._read_u4(f)

        const_count = self._read_u2(f)
        for _ in range(const_count):
            _idx = self._read_u2(f)
            type_id = self._read_u1(f)
            size = self._get_type_size(type_id)
            f.read(size)

        static_count = self._read_u2(f)
        for _ in range(static_count):
            _name_id = self._read_id(f)
            type_id = self._read_u1(f)
            size = self._get_type_size(type_id)
            f.read(size)

        inst_field_count = self._read_u2(f)
        for _ in range(inst_field_count):
            _name_id = self._read_id(f)
            _type_id = self._read_u1(f)

    def _parse_bitmap_instance(self, object_id: int, class_id: int, data: bytes, stack_serial: int = 0) -> Optional[BitmapInfo]:
        """
        Parse Bitmap instance data to extract width, height, etc.
        解析 Bitmap 实例数据以提取宽度、高度等。

        Note: ART VM may reorder fields for memory optimization.
        We use heuristics to find width/height values:
        1. First try the field offsets from class definition
        2. If values are unreasonable, scan for likely dimension values

        注意：ART VM 可能会重排字段以优化内存。
        我们使用启发式方法来找到宽度/高度值：
        1. 首先尝试类定义中的字段偏移
        2. 如果值不合理，扫描可能的尺寸值
        """
        bitmap_info = BitmapInfo(object_id=object_id)
        bitmap_info.stack_serial = stack_serial

        # Method 1: Try to parse using field definitions
        all_fields = self._get_all_fields(class_id)
        for field in all_fields:
            offset = field.offset
            field_size = self._get_type_size(field.type_id)

            if offset + field_size > len(data):
                continue

            if field.name == "mWidth" and field.type_id == TYPE_INT:
                val = struct.unpack(">i", data[offset:offset+4])[0]
                if 0 < val < 65536:  # Reasonable dimension
                    bitmap_info.width = val
            elif field.name == "mHeight" and field.type_id == TYPE_INT:
                val = struct.unpack(">i", data[offset:offset+4])[0]
                if 0 < val < 65536:
                    bitmap_info.height = val
            elif field.name == "mDensity" and field.type_id == TYPE_INT:
                val = struct.unpack(">i", data[offset:offset+4])[0]
                if 0 < val < 1000:
                    bitmap_info.density = val
            elif field.name == "mRecycled" and field.type_id == TYPE_BOOLEAN:
                bitmap_info.is_recycled = data[offset] != 0
            elif field.name == "mIsMutable" and field.type_id == TYPE_BOOLEAN:
                bitmap_info.is_mutable = data[offset] != 0

        # Method 2: If width/height not found, use heuristic scan
        # ART often places mHeight around offset 16 and mWidth around offset 38
        # (after field reordering)
        if bitmap_info.width <= 0 or bitmap_info.height <= 0:
            # Try common ART layouts
            heuristic_offsets = [
                # (height_offset, width_offset) - observed patterns
                (16, 38),  # Common Android 10+ pattern
                (12, 36),  # Alternate pattern
                (20, 40),  # Another variant
            ]
            for h_off, w_off in heuristic_offsets:
                if h_off + 4 <= len(data) and w_off + 4 <= len(data):
                    h_val = struct.unpack(">I", data[h_off:h_off+4])[0]
                    w_val = struct.unpack(">I", data[w_off:w_off+4])[0]
                    # Check if these look like valid dimensions
                    if 0 < h_val < 65536 and 0 < w_val < 65536:
                        bitmap_info.height = h_val
                        bitmap_info.width = w_val
                        break

        # Method 3: If still not found, scan for candidate pairs
        if bitmap_info.width <= 0 or bitmap_info.height <= 0:
            candidates = []
            for i in range(0, len(data) - 3, 4):
                val = struct.unpack(">I", data[i:i+4])[0]
                if 0 < val < 65536:
                    candidates.append((i, val))
            # Look for two values that could be dimensions (usually adjacent or nearby)
            if len(candidates) >= 2:
                # Take first two reasonable values as height and width
                bitmap_info.height = candidates[0][1]
                bitmap_info.width = candidates[-1][1] if len(candidates) > 1 else candidates[0][1]

        # Check mRecycled more carefully - scan for it if needed
        # mRecycled is typically a single byte that's 0 (false) or non-zero (true)
        # After field reordering, it's often near the end of the object
        if bitmap_info.width > 0 and bitmap_info.height > 0:
            # If we found valid dimensions, the bitmap is probably not recycled
            # (recycled bitmaps typically have their dimensions zeroed or invalid)
            bitmap_info.is_recycled = False
        else:
            bitmap_info.is_recycled = True

        # Estimate pixel memory (assuming ARGB_8888 = 4 bytes per pixel)
        if bitmap_info.width > 0 and bitmap_info.height > 0:
            bitmap_info.estimated_pixel_bytes = (
                bitmap_info.width * bitmap_info.height * bitmap_info.bytes_per_pixel
            )

        return bitmap_info

    def _get_all_fields(self, class_id: int) -> List[ClassFieldInfo]:
        """Get all fields for a class including inherited fields."""
        all_fields: List[ClassFieldInfo] = []
        current_class_id = class_id

        # Collect fields from class hierarchy (child to parent)
        class_chain: List[int] = []
        while current_class_id and current_class_id in self.classes:
            class_chain.append(current_class_id)
            current_class_id = self.classes[current_class_id].get("super_class_id", 0)

        # Reverse to process parent first (fields are ordered parent->child in instance data)
        # Also recalculate offsets as they're relative to each class, not the whole instance
        current_offset = 0
        for cid in reversed(class_chain):
            cls_info = self.classes.get(cid, {})
            fields = cls_info.get("fields", [])
            for field in fields:
                # Create new field with correct absolute offset
                new_field = ClassFieldInfo(
                    name=field.name,
                    type_id=field.type_id,
                    offset=current_offset
                )
                all_fields.append(new_field)
                current_offset += self._get_type_size(field.type_id)

        return all_fields

    def _resolve_allocation_stacks(self) -> None:
        """
        Resolve stack traces for each bitmap.
        解析每个 Bitmap 的分配堆栈。
        """
        for bitmap in self.bitmaps:
            if bitmap.stack_serial and bitmap.stack_serial in self.stack_traces:
                trace = self.stack_traces[bitmap.stack_serial]
                stack_lines = []
                for frame in trace.frames:
                    # Format: class.method(file:line)
                    class_name = frame.class_name or self._resolve_class_name_for_frame(frame)
                    method = frame.method_name or "<unknown>"
                    source = frame.source_file or "<unknown>"
                    line = frame.line_number

                    if line > 0:
                        stack_lines.append(f"{class_name}.{method}({source}:{line})")
                    elif line == -2:
                        stack_lines.append(f"{class_name}.{method}({source}:native)")
                    else:
                        stack_lines.append(f"{class_name}.{method}({source})")

                bitmap.allocation_stack = stack_lines

    def _resolve_class_name_for_frame(self, frame: StackFrame) -> str:
        """Try to resolve class name from method signature or other hints."""
        # This is a fallback - ideally class name is already set
        if frame.method_signature:
            # Try to extract class from signature
            sig = frame.method_signature
            if sig.startswith("L") and ";" in sig:
                # JNI format: Lcom/example/Class;
                return sig[1:sig.index(";")].replace("/", ".")
        return "<unknown>"

    def get_bitmap_summary(self) -> dict:
        """
        Get summary of Bitmap analysis.
        获取 Bitmap 分析摘要。
        """
        total_count = len(self.bitmaps)
        recycled_count = sum(1 for b in self.bitmaps if b.is_recycled)
        active_count = total_count - recycled_count

        active_bitmaps = [b for b in self.bitmaps if not b.is_recycled]
        total_estimated_bytes = sum(b.estimated_pixel_bytes for b in active_bitmaps)

        # Sort by size
        sorted_bitmaps = sorted(active_bitmaps, key=lambda b: b.estimated_pixel_bytes, reverse=True)

        # Top bitmaps
        top_bitmaps = []
        for b in sorted_bitmaps[:20]:
            bitmap_info = {
                "object_id": b.object_id,
                "width": b.width,
                "height": b.height,
                "density": b.density,
                "config": b.config_name,
                "bytes_per_pixel": b.bytes_per_pixel,
                "estimated_bytes": b.estimated_pixel_bytes,
                "estimated_mb": round(b.estimated_pixel_bytes / (1024 * 1024), 2),
                "is_mutable": b.is_mutable,
            }
            # Add allocation stack if available
            if b.allocation_stack:
                bitmap_info["allocation_stack"] = b.allocation_stack
            top_bitmaps.append(bitmap_info)

        # Group by size range
        size_distribution = {
            "tiny (<10KB)": 0,
            "small (10KB-100KB)": 0,
            "medium (100KB-1MB)": 0,
            "large (1MB-10MB)": 0,
            "huge (>10MB)": 0,
        }
        for b in active_bitmaps:
            size = b.estimated_pixel_bytes
            if size < 10 * 1024:
                size_distribution["tiny (<10KB)"] += 1
            elif size < 100 * 1024:
                size_distribution["small (10KB-100KB)"] += 1
            elif size < 1024 * 1024:
                size_distribution["medium (100KB-1MB)"] += 1
            elif size < 10 * 1024 * 1024:
                size_distribution["large (1MB-10MB)"] += 1
            else:
                size_distribution["huge (>10MB)"] += 1

        return {
            "total_count": total_count,
            "recycled_count": recycled_count,
            "active_count": active_count,
            "total_estimated_bytes": total_estimated_bytes,
            "total_estimated_mb": round(total_estimated_bytes / (1024 * 1024), 2),
            "average_size_bytes": round(total_estimated_bytes / active_count, 2) if active_count > 0 else 0,
            "average_size_kb": round(total_estimated_bytes / active_count / 1024, 2) if active_count > 0 else 0,
            "top_bitmaps": top_bitmaps,
            "size_distribution": size_distribution,
        }


def analyze_bitmaps(hprof_path: str) -> dict:
    """
    Analyze Bitmap objects in an hprof file.
    分析 hprof 文件中的 Bitmap 对象。

    This extracts all android.graphics.Bitmap instances and estimates
    their native pixel memory based on width * height * bytes_per_pixel.

    Args:
        hprof_path: Path to hprof file / hprof 文件路径

    Returns:
        dict with bitmap analysis results / 包含 Bitmap 分析结果的字典
    """
    path = pathlib.Path(hprof_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {hprof_path}"}

    parser = BitmapHprofParser(str(path))
    if not parser.parse():
        return {"ok": False, "error": "Failed to parse hprof file"}

    summary = parser.get_bitmap_summary()
    summary["ok"] = True
    summary["file_path"] = str(path)
    summary["file_size"] = path.stat().st_size

    return summary


def diff_bitmaps(before_path: str, after_path: str) -> dict:
    """
    Compare Bitmap analysis between two hprof files.
    比较两个 hprof 文件之间的 Bitmap 分析。

    Args:
        before_path: Path to "before" hprof file / "之前"的 hprof 文件路径
        after_path: Path to "after" hprof file / "之后"的 hprof 文件路径

    Returns:
        dict with diff results / 包含差异结果的字典
    """
    before = analyze_bitmaps(before_path)
    after = analyze_bitmaps(after_path)

    if not before.get("ok"):
        return {"ok": False, "error": f"Failed to analyze before file: {before.get('error')}"}
    if not after.get("ok"):
        return {"ok": False, "error": f"Failed to analyze after file: {after.get('error')}"}

    return {
        "ok": True,
        "before": {
            "file_path": before["file_path"],
            "active_count": before["active_count"],
            "total_estimated_mb": before["total_estimated_mb"],
        },
        "after": {
            "file_path": after["file_path"],
            "active_count": after["active_count"],
            "total_estimated_mb": after["total_estimated_mb"],
        },
        "diff": {
            "count_diff": after["active_count"] - before["active_count"],
            "estimated_mb_diff": round(after["total_estimated_mb"] - before["total_estimated_mb"], 2),
        },
        "before_detail": before,
        "after_detail": after,
    }
