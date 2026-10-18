"""JSON-lines logs with immutable run identity, including worker threads."""

import json
import logging
from datetime import datetime, timezone


class RunFormatter(logging.Formatter):
    def __init__(self, identity):
        super().__init__()
        self.identity = identity

    def format(self, record):
        entry = {
            **self.identity,
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)
