"""Preserve exact export bytes in content-addressed, write-once snapshots."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

CHUNK_SIZE = 64 * 1024


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_equal(left: Path, right: Path) -> bool:
    with Path(left).open("rb") as first, Path(right).open("rb") as second:
        while True:
            chunk = first.read(CHUNK_SIZE)
            if chunk != second.read(CHUNK_SIZE):
                return False
            if not chunk:
                return True


def archive_file(source: Path, directory: Path, *, suffix: str = ".json") -> Path:
    """Archive a file by SHA-256 without overwriting an existing snapshot.

    The archive owns a separate inode, so editing the latest export cannot
    mutate history. Publish a fully flushed temporary file with an exclusive
    hard link; a concurrent identical archive is harmless.
    """
    if suffix not in {".json", ".html"}:
        raise ValueError("Unsupported snapshot suffix.")
    source = Path(source)
    digest = file_digest(source)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix}"

    def verify_existing() -> Path:
        if not files_equal(source, target):
            raise OSError(f"Snapshot integrity check failed: {target}")
        return target

    if target.exists():
        return verify_existing()
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=directory, prefix=".snapshot-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            copied = hashlib.sha256()
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    handle.write(chunk)
                    copied.update(chunk)
            if copied.hexdigest() != digest:
                raise OSError("Source changed during snapshot creation.")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            return verify_existing()
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
