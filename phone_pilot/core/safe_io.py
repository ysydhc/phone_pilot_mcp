"""Descriptor-relative archive IO; never fall back to check-then-open writes.

The write trust boundary is the OS user. Other users must not be able to rename
any ancestor, except root-owned sticky directories (e.g. /private/tmp). Read-only
archives do not need owner write permission and never create or replace files.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid


class UnsafePath(ValueError):
    pass


def require_safe_io():
    if (
        os.name != "posix"
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or any(
            fn not in os.supports_dir_fd
            for fn in (os.open, os.mkdir, os.rename, os.unlink, os.stat, os.link)
        )
        or os.stat not in os.supports_follow_symlinks
    ):
        raise UnsafePath("safe descriptor-relative archive IO unavailable")


def _name(name):
    if (
        not isinstance(name, str)
        or name in ("", ".", "..")
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise UnsafePath("expected a single safe path component")
    return name


def _directory(fd, writable):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafePath("not a directory")
    if writable:
        if info.st_uid not in (0, os.geteuid()):
            raise UnsafePath("untrusted ancestor directory owner")
        shared = info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        root_sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if shared and not root_sticky:
            raise UnsafePath("untrusted group/other-writable ancestor directory")
    return info


class SafeDirectory:
    def __init__(self, fd, path, writable=False):
        self.fd = fd
        self.path = Path(path)
        self.writable = writable

    def _file(self, name):
        fd = os.open(
            _name(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd
        )
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise UnsafePath("archive evidence must be a regular file")
        return fd

    @contextmanager
    def open(self, name):
        """Yield a read-only binary stream pinned to a regular no-follow file."""
        fd = self._file(name)
        with os.fdopen(fd, "rb") as stream:
            yield stream

    def iter_chunks(self, name, chunk_size=1024 * 1024):
        if not isinstance(chunk_size, int) or not 0 < chunk_size <= 8 * 1024 * 1024:
            raise UnsafePath("invalid bounded chunk size")
        with self.open(name) as stream:
            for chunk in iter(lambda: stream.read(chunk_size), b""):
                yield chunk

    def copy_file(self, name, source, replace=False):
        """Stream a regular file into an exclusive/atomic descriptor destination.

        Source is a binary stream (already pinned by the caller) or a Path. Paths
        are opened with safe_directory so intermediate symlinks cannot be followed.
        """
        if not hasattr(source, "read"):
            source = Path(source).expanduser().absolute()
            with safe_directory(source.parent) as parent:
                with parent.open(source.name) as stream:
                    return self.copy_file(name, stream, replace=replace)
        name = _name(name)
        if not self.writable:
            raise UnsafePath("read-only directory cannot write")
        _directory(self.fd, True)
        if replace:
            try:
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            ):
                raise UnsafePath(
                    "refusing to replace linked or nonregular archive file"
                )
        temporary = ".archive-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            try:
                source_before = os.fstat(source.fileno())
            except (AttributeError, OSError):
                source_before = None
            total = 0
            with os.fdopen(fd, "wb") as stream:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    stream.write(block)
                    total += len(block)
                if source_before is not None:
                    source_after = os.fstat(source.fileno())
                    if total != source_before.st_size or (
                        source_before.st_size,
                        source_before.st_mtime_ns,
                        source_before.st_ctime_ns,
                    ) != (
                        source_after.st_size,
                        source_after.st_mtime_ns,
                        source_after.st_ctime_ns,
                    ):
                        raise UnsafePath("source changed while copying")
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                # rename replaces the directory entry, never follows a late symlink.
                os.rename(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=self.fd,
                    dst_dir_fd=self.fd,
                    follow_symlinks=False,
                )
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def stat(self, name):
        fd = self._file(name)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)

    def read_bytes(self, name, max_bytes=32 * 1024 * 1024):
        fd = self._file(name)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > max_bytes:
                raise UnsafePath("archive file exceeds read limit")
            raw = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > max_bytes:
                raise UnsafePath("archive file exceeds read limit")
            if len(raw) != before.st_size or (
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_mtime_ns, after.st_ctime_ns):
                raise UnsafePath("archive file changed while reading")
            return raw

    def hash_file(self, name):
        fd = self._file(name)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise UnsafePath("archive file changed while hashing")
            return digest.hexdigest()

    @contextmanager
    def subdir(self, name, create=False):
        name = _name(name)
        if create and not self.writable:
            raise UnsafePath("read-only directory cannot create children")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=self.fd)
            except FileExistsError:
                pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            _directory(fd, self.writable)
            yield SafeDirectory(fd, self.path / name, self.writable)
        finally:
            os.close(fd)

    def write_bytes(self, name, data, replace=False):
        name = _name(name)
        if not self.writable:
            raise UnsafePath("read-only directory cannot write")
        _directory(self.fd, True)
        if replace:
            try:
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None and (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            ):
                raise UnsafePath(
                    "refusing to replace linked or nonregular archive file"
                )
        temporary = ".archive-" + uuid.uuid4().hex
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.fd,
        )
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if replace:
                # rename replaces the directory entry, never follows a late symlink.
                os.rename(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            else:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=self.fd,
                    dst_dir_fd=self.fd,
                    follow_symlinks=False,
                )
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


def normalize_trusted_aliases(path):
    """Normalize only verified macOS root-owned system aliases, never user links."""
    path = Path(path).expanduser().absolute()
    if sys.platform == "darwin" and len(path.parts) > 1:
        expected = {"var": "/private/var", "tmp": "/private/tmp", "etc": "/private/etc"}
        target = expected.get(path.parts[1])
        if target is not None:
            alias = Path("/") / path.parts[1]
            info = alias.lstat()
            if stat.S_ISLNK(info.st_mode) and info.st_uid == 0:
                link = os.readlink(alias)
                destination = os.path.normpath(os.path.join("/", link))
                if destination == target:
                    path = Path(target).joinpath(*path.parts[2:])
    return path


@contextmanager
def safe_directory(path, create=False, writable=False):
    require_safe_io()
    path = normalize_trusted_aliases(path)
    if ".." in path.parts:
        raise UnsafePath("parent traversal not supported")
    if create and not writable:
        raise UnsafePath("read-only directory cannot create")
    descriptors = []
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        _directory(fd, writable)
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            descriptors.append(fd)
            _directory(fd, writable)
        yield SafeDirectory(fd, path, writable)
    except OSError as exc:
        raise UnsafePath("unsafe archive path or failed descriptor IO") from exc
    finally:
        for fd in reversed(descriptors):
            os.close(fd)
