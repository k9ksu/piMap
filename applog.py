"""Shared rotating-file logging and a thread-safe component-status registry.

- get_logger() returns a logger that writes to logs/piMap.log (rotated at
  midnight, a few days kept) and echoes to the console.
- set_status()/get_status() let background workers report load state
  ("loading"/"ok"/"stale"/"failed") for display on screen.
"""

import logging
import os
import threading
from logging.handlers import TimedRotatingFileHandler

_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
_LOG_PATH = os.path.join(_LOG_DIR, "piMap.log")
_BACKUP_DAYS = 3

_configured = False
_cfg_lock = threading.Lock()

_status_lock = threading.Lock()
_status = {}  # component -> (state, detail)


def get_logger(name="piMap"):
    global _configured
    with _cfg_lock:
        if not _configured:
            os.makedirs(_LOG_DIR, exist_ok=True)
            root = logging.getLogger()
            root.setLevel(logging.INFO)

            fileh = TimedRotatingFileHandler(
                _LOG_PATH, when="midnight", backupCount=_BACKUP_DAYS, encoding="utf-8"
            )
            fileh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            ))
            root.addHandler(fileh)

            consoleh = logging.StreamHandler()
            consoleh.setFormatter(logging.Formatter("%(name)s: %(message)s"))
            root.addHandler(consoleh)

            _configured = True
    return logging.getLogger(name)


def set_status(component, state, detail=""):
    with _status_lock:
        _status[component] = (state, detail)


def get_status():
    with _status_lock:
        return dict(_status)
