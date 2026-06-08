import logging
import os
import sys


def configure_logging():
    # Respect LOG_LEVEL env var, default to INFO.
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    # Force basicConfig to override other loggers (uvicorn may preconfigure logging).
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str):
    configure_logging()
    return logging.getLogger(name)
