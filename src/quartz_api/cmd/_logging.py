import logging
import json
import sys
import time

class JsonFormatter(logging.Formatter):
    """Custom JSON formatter for log records.

    Enables usage of 'traceid' attribute in log record 'extra' dictionary, e.g.:
    
    >>> logger.info("This is a log message", extra={"traceid": "12345"})
    """

    def format(self, record):
        base = {
            "ts": int(time.time()),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        if record.trace_id:
            base["trace_id"] = record.trace_id
        if record.process_time:
            base["process_time"] = record.process_time

        return json.dumps(base, ensure_ascii=False)

def setup_json_logging(level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]
