import struct
import zipfile

from phone_pilot.android.device import utils


def _encode_length8(value: int) -> bytes:
    if value < 0x80:
        return bytes([value])
    return bytes([(value >> 7) | 0x80, value & 0x7F])


def _build_string_pool(strings: list[str]) -> bytes:
    string_count = len(strings)
    flags = 0x00000100  # UTF-8
    string_offsets = []
    string_data = b""
    for s in strings:
        string_offsets.append(len(string_data))
        encoded = s.encode("utf-8")
        string_data += _encode_length8(len(s))
        string_data += _encode_length8(len(encoded))
        string_data += encoded + b"\x00"
    header_size = 28
    strings_start = header_size + (string_count * 4)
    total_size = strings_start + len(string_data)
    header = struct.pack(
        "<HHI", 0x0001, header_size, total_size
    ) + struct.pack(
        "<IIII", string_count, 0, flags, strings_start
    ) + struct.pack("<I", 0)
    offsets = b"".join(struct.pack("<I", off) for off in string_offsets)
    return header + offsets + string_data


def _build_start_element(name_idx: int, attributes: list[tuple[int, int, int, int, int]]) -> bytes:
    # attributes: (ns_idx, name_idx, raw_value_idx, value_type, value_data)
    header_size = 16
    attr_ext_size = 20
    attribute_start = attr_ext_size
    attribute_size = 20
    attribute_count = len(attributes)
    node_header = struct.pack("<HHI", 0x0102, header_size, 0) + struct.pack("<II", 1, 0xFFFFFFFF)
    attr_ext = struct.pack(
        "<IIHHHHHH",
        0xFFFFFFFF,
        name_idx,
        attribute_start,
        attribute_size,
        attribute_count,
        0,
        0,
        0,
    )
    attr_bytes = b""
    for ns_idx, name_idx, raw_idx, value_type, value_data in attributes:
        attr_bytes += struct.pack(
            "<IIIHBBI",
            ns_idx,
            name_idx,
            raw_idx,
            8,
            0,
            value_type,
            value_data,
        )
    size = header_size + len(attr_ext) + len(attr_bytes)
    node_header = struct.pack("<HHI", 0x0102, header_size, size) + struct.pack("<II", 1, 0xFFFFFFFF)
    return node_header + attr_ext + attr_bytes


def _build_manifest_axml(icon_value: str, attr_name: str = "icon") -> bytes:
    strings = [
        "application",
        attr_name,
        "http://schemas.android.com/apk/res/android",
        icon_value,
    ]
    string_pool = _build_string_pool(strings)
    icon_value_idx = strings.index(icon_value)
    start_application = _build_start_element(
        name_idx=strings.index("application"),
        attributes=[
            (strings.index("http://schemas.android.com/apk/res/android"), strings.index(attr_name), icon_value_idx, 0x03, icon_value_idx),
        ],
    )
    total_size = 8 + len(string_pool) + len(start_application)
    xml_header = struct.pack("<HHI", 0x0003, 8, total_size)
    return xml_header + string_pool + start_application


def test_axml_manifest_icon_ref_parses_binary_manifest(tmp_path):
    apk_path = tmp_path / "app.apk"
    manifest_bytes = _build_manifest_axml("@mipmap/ic_launcher")
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("AndroidManifest.xml", manifest_bytes)
    icon_ref, err = utils._axml_manifest_icon_ref(apk_path)
    assert err is None
    assert icon_ref == "@mipmap/ic_launcher"


def test_axml_manifest_round_icon_ref_fallback(tmp_path):
    apk_path = tmp_path / "app_round.apk"
    manifest_bytes = _build_manifest_axml("@mipmap/ic_launcher_round", attr_name="roundIcon")
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("AndroidManifest.xml", manifest_bytes)
    icon_ref, err = utils._axml_manifest_icon_ref(apk_path, prefer_round_icon=True)
    assert err is None
    assert icon_ref == "@mipmap/ic_launcher_round"


def test_axml_manifest_activity_icon_fallback(tmp_path):
    apk_path = tmp_path / "app_activity.apk"
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app">
    <application>
        <activity android:name=".MainActivity" android:icon="@mipmap/ic_launcher_alt">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("AndroidManifest.xml", manifest)
    icon_ref, err = utils._axml_manifest_icon_ref(apk_path)
    assert err is None
    assert icon_ref == "@mipmap/ic_launcher_alt"


def test_axml_manifest_prefers_logo_over_icon(tmp_path):
    apk_path = tmp_path / "app_logo.apk"
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app">
    <application
        android:logo="@mipmap/ic_logo"
        android:icon="@mipmap/ic_launcher">
    </application>
</manifest>
"""
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("AndroidManifest.xml", manifest)
    icon_ref, err = utils._axml_manifest_icon_ref(apk_path)
    assert err is None
    assert icon_ref == "@mipmap/ic_logo"


def test_axml_manifest_activity_alias_before_launcher(tmp_path):
    apk_path = tmp_path / "app_alias.apk"
    manifest = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app">
    <application>
        <activity android:name=".MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
        <activity-alias
            android:name=".LauncherAlias"
            android:targetActivity=".MainActivity"
            android:icon="@mipmap/ic_alias">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity-alias>
    </application>
</manifest>
"""
    with zipfile.ZipFile(apk_path, "w") as zf:
        zf.writestr("AndroidManifest.xml", manifest)
    icon_ref, err = utils._axml_manifest_icon_ref(apk_path)
    assert err is None
    assert icon_ref == "@mipmap/ic_alias"
