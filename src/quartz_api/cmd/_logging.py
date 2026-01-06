import json
import logging
import sys
import time
from logging import LogRecord


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for log records.

    Enables usage of 'traceid' attribute in log record 'extra' dictionary, e.g.:

    >>> logger.info("This is a log message", extra={"traceid": "12345"})
    """

    def format(self, record: LogRecord) -> str:
        base = {
            "ts": int(time.time()),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        if getattr(record, "trace_id", None) is not None:
            base["trace_id"] = record.trace_id
        if getattr(record, "process_time", None) is not None:
            base["process_time"] = record.process_time

        return json.dumps(base, ensure_ascii=False)

def setup_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]
