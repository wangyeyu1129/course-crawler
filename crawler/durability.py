"""POSIX publication durability and conservative orphan temporary cleanup."""

import argparse
import fcntl
import json
import logging
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

TEMP_NAME = re.compile(r"\.crawler-tmp-([1-9][0-9]*)-[a-z0-9_]+$")


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_mkdir(directory: Path) -> None:
    directory = Path(directory)
    if directory.is_dir():
        return
    durable_mkdir(directory.parent)
    directory.mkdir(exist_ok=True)
    sync_directory(directory.parent)


@contextmanager
def temporary_file(directory: Path, *, text: bool = False):
    durable_mkdir(directory)
    with NamedTemporaryFile(
        mode="w" if text else "w+b",
        encoding="utf-8" if text else None,
        dir=directory,
        prefix=f".crawler-tmp-{os.getpid()}-",
        delete=False,
    ) as handle:
        path = Path(handle.name)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield handle, path
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                path.unlink(missing_ok=True)
                sync_directory(directory)
            except OSError:
                if not failed:
                    raise
                logging.getLogger(__name__).exception("temporary_cleanup_failed")


def cleanup_orphans(
    directory: Path, *, min_age: float = 86400, apply: bool = False
) -> list[str]:
    if not isinstance(min_age, (int, float)) or not 0 <= min_age < float("inf"):
        raise ValueError("min_age must be finite and nonnegative.")
    removed = []
    for path in Path(directory).iterdir():
        match = TEMP_NAME.fullmatch(path.name)
        if match is None:
            continue
        try:
            os.kill(int(match.group(1)), 0)
        except ProcessLookupError:
            pass
        except (PermissionError, OverflowError):
            continue
        else:
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            info = os.fstat(fd)
            current = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_ino != current.st_ino
                or info.st_dev != current.st_dev
                or time.time() - info.st_mtime < min_age
            ):
                continue
            removed.append(str(path))
            if apply:
                path.unlink()
                sync_directory(path.parent)
        except FileNotFoundError:
            continue
        finally:
            os.close(fd)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--min-age", type=float, default=86400)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            cleanup_orphans(args.directory, min_age=args.min_age, apply=args.apply),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
