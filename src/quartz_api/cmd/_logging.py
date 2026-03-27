import json
import logging
import sys
import time
from logging import LogRecord

from quartz_api.internal.middleware.trace import get_trace_id


class TraceIdFilter(logging.Filter):
    """Injects trace_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for log records."""

    def format(self, record: LogRecord) -> str:
        base = {
            "ts": int(time.time()),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": record.process,
        }

        # Add custom extra attributes if they exist
        for extra_attr in ["trace_id", "process_time", "client_ip", "user_id", "request_url"]:
            if hasattr(record, extra_attr):
                base[extra_attr] = getattr(record, extra_attr)

        return json.dumps(base, ensure_ascii=False)


def setup_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(TraceIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]

    for logger_name in [
            "gunicorn",
            "gunicorn.error",
            "gunicorn.access",
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access",
        ]:
        logger = logging.getLogger(logger_name)
        logger.handlers = [handler]
        logger.setLevel(level)
        logger.propagate = False

