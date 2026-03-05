#!/usr/bin/env python3
"""
Logger module for the MTG MCP server.
Writes to a log file in repo logs/ with timestamp and PID in the filename.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and log file name
# ---------------------------------------------------------------------------
_MODULE_DIR: Path = Path(__file__).resolve().parent
_REPO_ROOT: Path = _MODULE_DIR.parent.parent
LOGS_DIR: Path = _REPO_ROOT / "logs"

_LOG_FMT: str = "%(asctime)s %(levelname)s %(name)s %(module)s.%(funcName)s %(message)s"
_DATE_FMT: str = "%Y-%m-%d %H:%M:%S"
_ENV_LOG_LEVEL: str = "MTG_LOG_LEVEL"  # DEBUG, INFO, WARNING, ERROR; default INFO


def _ensure_logs_dir() -> Path:
    """Create logs directory if missing. Raise on failure."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(f"Failed to create logs directory {LOGS_DIR}: {e}") from e
    return LOGS_DIR


def _log_file_path() -> Path:
    """Return log file path: logs/mtg_YYYY-MM-DD_HH-MM-SS_PID.log"""
    _ensure_logs_dir()
    ts: str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pid: int = os.getpid()
    return LOGS_DIR / f"mtg_{ts}_{pid}.log"


def _log_level_from_env() -> int:
    """Read MTG_LOG_LEVEL from environment; default INFO."""
    raw = os.environ.get(_ENV_LOG_LEVEL, "INFO").upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    return level_map.get(raw, logging.INFO)


def _configure_logger() -> logging.Logger:
    """Create and configure the mtg_mcp logger with file and stderr handlers."""
    logger = logging.getLogger("mtg_mcp")
    if logger.handlers:
        return logger
    level: int = _log_level_from_env()
    logger.setLevel(logging.DEBUG)  # capture all in file
    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    file_path: Path = _log_file_path()
    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(level)  # stderr verbosity from env
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


LOGGER: logging.Logger = _configure_logger()
