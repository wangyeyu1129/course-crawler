"""Explicit, verified compression of old content-addressed JSON snapshots.

Run: python -m crawler.maintenance OUTPUT --keep 20 [--apply]
Default is a read-only plan. Metadata, HTML and databases are never deleted.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile

from crawler.runner import crawl_lock
from crawler.snapshots import CHUNK_SIZE, file_digest


def gzip_digest(path):
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def maintain_history(output: Path, *, keep: int = 20, apply: bool = False) -> list[str]:
    if type(keep) is not int or keep < 1:
        raise ValueError("keep must be a positive integer.")
    output = Path(output).resolve()
    with crawl_lock(output, "output"):
        directory = output.parent / "history" / output.stem
        candidates = sorted(
            (
                p
                for p in directory.glob("*.json")
                if re.fullmatch(r"[0-9a-f]{64}\.json", p.name) and not p.is_symlink()
            ),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
            reverse=True,
        )
        protected = file_digest(output) if output.is_file() else None
        selected = [p for p in candidates[keep:] if p.stem != protected]
        if not apply:
            return [str(p) for p in selected]
        for path in selected:
            if file_digest(path) != path.stem:
                raise OSError(f"Corrupt snapshot: {path}")
            backup = path.with_suffix(".json.gz")
            if not backup.exists():
                temporary = None
                try:
                    with NamedTemporaryFile(
                        dir=directory, prefix=".backup-", delete=False
                    ) as handle:
                        temporary = Path(handle.name)
                        with (
                            path.open("rb") as source,
                            gzip.GzipFile(
                                fileobj=handle, mode="wb", mtime=0
                            ) as compressed,
                        ):
                            shutil.copyfileobj(source, compressed, CHUNK_SIZE)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if gzip_digest(temporary) != path.stem:
                        raise OSError("Compressed backup verification failed.")
                    os.replace(temporary, backup)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            if backup.is_symlink() or gzip_digest(backup) != path.stem:
                raise OSError(f"Invalid backup: {backup}")
            path.unlink()
        return [str(p) for p in selected]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--keep", type=int, default=20)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            maintain_history(args.output, keep=args.keep, apply=args.apply), indent=2
        )
    )


if __name__ == "__main__":
    main()
