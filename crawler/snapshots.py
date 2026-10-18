"""Preserve exact export bytes in content-addressed, write-once snapshots."""

import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def archive_file(source: Path, directory: Path) -> Path:
    """Archive a file by SHA-256 without overwriting an existing snapshot.

    The archive owns a separate inode, so editing the latest export cannot
    mutate history. Publish a fully flushed temporary file with an exclusive
    hard link; a concurrent identical archive is harmless.
    """
    payload = Path(source).read_bytes()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{hashlib.sha256(payload).hexdigest()}.json"

    def verify_existing() -> Path:
        if target.read_bytes() != payload:
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
            handle.write(payload)
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
