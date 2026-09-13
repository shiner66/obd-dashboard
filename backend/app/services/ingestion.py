"""Serialization and immutable, verified source snapshots for ingestion."""
from __future__ import annotations

import gzip
import hashlib
import os
import shutil
import tempfile
import threading
from pathlib import Path

LOCK = threading.RLock()


class SourceChangedError(RuntimeError):
    """A source changed while its snapshot was being prepared; retry later."""


def signature(path: Path) -> tuple[int, int, int, int]:
    """Identify the current file version without opening or modifying it."""
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def content_sha256(path: Path) -> str:
    """Hash the uncompressed bytes, identically for raw and gzip sources."""
    digest = hashlib.sha256()
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_snapshot(path: Path, base_dir: Path) -> Path:
    """Create a verified gzip snapshot keyed by hash, retaining the original.

    A producer may still hold its file open, even after a quiet interval. Keeping
    the original lets later modifications trigger another import without losing
    the writer's tail. Publication is atomic and happens only after verification.
    """
    before = signature(path)
    digest = content_sha256(path)
    if signature(path) != before:
        raise SourceChangedError("File ancora in scrittura")
    name = path.name[:-3] if path.name.lower().endswith(".gz") else path.name
    directory = base_dir / "archive" / digest
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (name + ".gz")
    if target.exists():
        try:
            if content_sha256(target) == digest and signature(path) == before:
                return target
        except (OSError, EOFError):
            pass
    descriptor, temp_name = tempfile.mkstemp(prefix=".snapshot-", dir=directory)
    temporary = Path(temp_name)
    try:
        opener = gzip.open if path.name.lower().endswith(".gz") else open
        with os.fdopen(descriptor, "wb") as raw:
            with opener(path, "rb") as source, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as compressed:
                shutil.copyfileobj(source, compressed)
            raw.flush()
            os.fsync(raw.fileno())
        verified = hashlib.sha256()
        with gzip.open(temporary, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                verified.update(chunk)
        if verified.hexdigest() != digest or signature(path) != before:
            raise SourceChangedError("File modificato durante l'archiviazione")
        os.utime(temporary, ns=(before[3], before[3]))
        os.replace(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return target
    finally:
        temporary.unlink(missing_ok=True)
