from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phone_pilot.core.resource import resolve_or_cache_path, resource_cache_dir  # noqa: E402


def test_resolve_or_cache_path_handles_cached_file(tmp_path):
    cache_dir = resource_cache_dir(str(tmp_path))
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / "sample.png"
    cached.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal header

    resolved = resolve_or_cache_path(str(cached), out_dir=str(tmp_path))
    assert resolved == str(cached)
