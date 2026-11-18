"""Negative boundaries execute the real descriptor IO implementation."""

import os
from pathlib import Path

import pytest

from phone_pilot.core.safe_io import UnsafePath, safe_directory


def test_create_replace_and_read_only(tmp_path):
    root = tmp_path / "archive"
    with safe_directory(root, create=True, writable=True) as directory:
        directory.write_bytes("record.json", b"first")
        directory.write_bytes("record.json", b"second", replace=True)
        assert directory.read_bytes("record.json") == b"second"
    root.chmod(0o500)
    try:
        with safe_directory(root) as directory:
            assert directory.read_bytes("record.json") == b"second"
            with pytest.raises(UnsafePath):
                directory.write_bytes("new", b"no")
    finally:
        root.chmod(0o700)


def test_symlink_ancestor_and_leaf_never_write_through(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePath):
        with safe_directory(tmp_path / "link" / "child", create=True, writable=True):
            pass
    assert not (outside / "child").exists()
    target = outside / "target"
    target.write_bytes(b"original")
    (tmp_path / "record").symlink_to(target)
    with safe_directory(tmp_path, writable=True) as directory:
        with pytest.raises(UnsafePath):
            directory.write_bytes("record", b"bad", replace=True)
        with pytest.raises(OSError):
            directory.write_bytes("record", b"bad")
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("mode", [0o770, 0o777, 0o1777])
def test_untrusted_writable_ancestor_rejected(tmp_path, mode):
    ancestor = tmp_path / "shared"
    ancestor.mkdir()
    ancestor.chmod(mode)
    try:
        with pytest.raises(UnsafePath, match="ancestor"):
            with safe_directory(ancestor / "new", create=True, writable=True):
                pass
        assert not (ancestor / "new").exists()
    finally:
        ancestor.chmod(0o700)


def test_platform_missing_primitives_fails_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set())
    with pytest.raises(UnsafePath, match="unavailable"):
        with safe_directory(tmp_path / "new", create=True, writable=True):
            pass
    assert not (tmp_path / "new").exists()


def test_pinned_parent_race_does_not_write_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Verify the exact rename target before moving this fixture-owned directory.
    assert root.absolute() == tmp_path.absolute() / "root"
    with safe_directory(root, writable=True) as directory:
        root.rename(tmp_path / "pinned")
        root.symlink_to(outside, target_is_directory=True)
        directory.write_bytes("evidence", b"safe")
    assert not (outside / "evidence").exists()
    assert (tmp_path / "pinned" / "evidence").read_bytes() == b"safe"


def test_fifo_and_hardlink_replacement_rejected(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    os.link(source, tmp_path / "hardlink")
    os.mkfifo(tmp_path / "fifo")
    with safe_directory(tmp_path, writable=True) as directory:
        with pytest.raises(UnsafePath):
            directory.read_bytes("fifo")
        with pytest.raises(UnsafePath):
            directory.write_bytes("hardlink", b"bad", replace=True)
    assert source.read_bytes() == b"original"


def test_copy_stream_and_path_never_follows_source_link(tmp_path):
    import io

    source = tmp_path / "source"
    source.write_bytes(b"input")
    (tmp_path / "linked").symlink_to(source)
    with safe_directory(tmp_path, writable=True) as directory:
        directory.copy_file("copied", source)
        assert directory.read_bytes("copied") == b"input"
        directory.copy_file("copied", io.BytesIO(b"new"), replace=True)
        assert directory.read_bytes("copied") == b"new"
        with pytest.raises(UnsafePath):
            directory.copy_file("rejected", tmp_path / "linked")
    assert not (tmp_path / "rejected").exists()


def test_failed_copy_never_publishes_partial_file(tmp_path):
    class FailingStream:
        count = 0

        def read(self, size):
            self.count += 1
            if self.count == 1:
                return b"partial"
            raise RuntimeError("injected failure")

    with safe_directory(tmp_path, writable=True) as directory:
        with pytest.raises(RuntimeError):
            directory.copy_file("partial", FailingStream())
    assert not (tmp_path / "partial").exists()
    assert not list(tmp_path.glob(".archive-*"))


def test_missing_stat_support_and_windows_block_before_creation(tmp_path, monkeypatch):
    from phone_pilot.core.safe_io import require_safe_io

    monkeypatch.setattr(os, "supports_follow_symlinks", set())
    with pytest.raises(UnsafePath):
        with safe_directory(tmp_path / "new", create=True, writable=True):
            pass
    assert not (tmp_path / "new").exists()
    monkeypatch.setattr(os, "name", "nt")
    with pytest.raises(UnsafePath):
        require_safe_io()


def test_owner_and_invalid_chunk_boundaries(tmp_path, monkeypatch):
    import types
    from phone_pilot.core.safe_io import _directory

    real_fstat = os.fstat
    monkeypatch.setattr(
        os,
        "fstat",
        lambda fd: types.SimpleNamespace(st_uid=os.geteuid() + 1, st_mode=0o40755),
    )
    with pytest.raises(UnsafePath, match="owner"):
        _directory(1, True)
    monkeypatch.setattr(os, "fstat", real_fstat)
    with safe_directory(tmp_path) as directory:
        for size in (0, -1):
            with pytest.raises(UnsafePath):
                list(directory.iter_chunks("unused", size))


def test_trusted_macos_alias_and_user_symlink_boundary(tmp_path):
    import sys
    from phone_pilot.core.safe_io import normalize_trusted_aliases

    if sys.platform != "darwin":
        pytest.skip("macOS system alias contract")
    assert normalize_trusted_aliases("/var/folders") == Path("/private/var/folders")
    alias = tmp_path / "user-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert normalize_trusted_aliases(alias) == alias
    with pytest.raises(UnsafePath):
        with safe_directory(alias):
            pass
    with safe_directory("/tmp"):
        pass


def test_stream_hash_large_material_without_read_bytes(tmp_path, monkeypatch):
    material = tmp_path / "video.mp4"
    material.write_bytes(b"v" * (33 * 1024 * 1024))
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("whole-file read"))
    with safe_directory(tmp_path) as directory:
        assert len(directory.hash_file("video.mp4")) == 64
        with pytest.raises(UnsafePath, match="limit"):
            directory.read_bytes("video.mp4")
