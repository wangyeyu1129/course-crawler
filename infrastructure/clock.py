"""UTC timestamps shared by persistence adapters and run records."""

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
