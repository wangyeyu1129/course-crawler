"""Advisory exclusive locks shared by command entry points (POSIX)."""

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def crawl_lock(database: Path, resource: str = "database"):
    """Prevent two local workers from consuming the same checkpoint (POSIX)."""
    path = Path(str(database) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Another crawler is using this {resource}.") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
