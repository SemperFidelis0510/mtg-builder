#!/usr/bin/env python3
"""
Logger module for the MTG MCP server and related scripts.
Writes to logs/<script_name>/ with timestamp and PID in the filename.
Call init_logger(script_name) from each main entry point before any work.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

# ---------------------------------------------------------------------------
# Paths and log file name
# ---------------------------------------------------------------------------
_MODULE_DIR: Path = Path(__file__).resolve().parent
_REPO_ROOT: Path = _MODULE_DIR.parent.parent
LOGS_DIR: Path = _REPO_ROOT / "logs"

_LOG_FMT: str = "%(asctime)s %(levelname)s %(name)s %(module)s.%(funcName)s %(message)s"
_DATE_FMT: str = "%Y-%m-%d %H:%M:%S"
_ENV_LOG_LEVEL: str = "MTG_LOG_LEVEL"  # DEBUG, INFO, WARNING, ERROR; default INFO

# Global singleton; handlers added by init_logger()
LOGGER: logging.Logger = logging.getLogger("mtg_mcp")

# Set by init_logger() so deck_editor can tee stdout/stderr to the log file
_log_file_stream: TextIO | None = None


def _ensure_logs_subdir(script_name: str) -> Path:
    """Create logs/<script_name> directory if missing. Raise on failure."""
    subdir: Path = LOGS_DIR / script_name
    try:
        subdir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OSError(f"Failed to create logs directory {subdir}: {e}") from e
    return subdir


def _log_file_path(script_name: str) -> Path:
    """Return log file path: logs/<script_name>/mtg_YYYY-MM-DD_HH-MM-SS_PID.log"""
    subdir: Path = _ensure_logs_subdir(script_name)
    ts: str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    pid: int = os.getpid()
    return subdir / f"mtg_{ts}_{pid}.log"


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


def init_logger(script_name: str) -> logging.Logger:
    """Initialize the global LOGGER with file + stderr handlers.
    Log file: logs/<script_name>/mtg_YYYY-MM-DD_HH-MM-SS_PID.log
    Idempotent: no-op if handlers are already attached."""
    global _log_file_stream
    if LOGGER.handlers:
        return LOGGER
    level: int = _log_level_from_env()
    LOGGER.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_LOG_FMT, datefmt=_DATE_FMT)

    file_path: Path = _log_file_path(script_name)
    fh = logging.FileHandler(file_path, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(formatter)
    LOGGER.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(formatter)
    LOGGER.addHandler(sh)

    if script_name == "deck_editor":
        _log_file_stream = fh.stream
        uv_logger = logging.getLogger("uvicorn")
        uv_logger.addHandler(fh)
        if uv_logger.level == logging.NOTSET:
            uv_logger.setLevel(level)

    return LOGGER


def get_log_file_stream() -> TextIO | None:
    """Return the current log file stream, if any (e.g. for teeing stdout/stderr)."""
    return _log_file_stream


class _Tee:
    """Writes to both the original stream and the log file so console and file see the same output.
    If the log stream is closed (e.g. during shutdown), writes only to the original stream to avoid
    'I/O operation on closed file' and broken stderr."""

    def __init__(self, original: TextIO, log_stream: TextIO) -> None:
        self._original = original
        self._log: TextIO | None = log_stream

    def _write_to_log(self, s: str) -> None:
        if self._log is None:
            return
        try:
            self._log.write(s)
            self._log.flush()
        except (OSError, ValueError):
            self._log = None

    def write(self, s: str) -> int:
        self._original.write(s)
        self._write_to_log(s)
        return len(s)

    def flush(self) -> None:
        self._original.flush()
        if self._log is not None:
            try:
                self._log.flush()
            except (OSError, ValueError):
                self._log = None

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return getattr(self._original, "isatty", lambda: False)()


def tee_stdout_stderr_to_log() -> None:
    """Replace sys.stdout and sys.stderr with Tee to the log file so all prints and warnings appear there too.
    No-op if the log file stream is not set (e.g. init_logger was not called or script is not deck_editor)."""
    stream = get_log_file_stream()
    if stream is None:
        return
    sys.stdout = _Tee(sys.stdout, stream)
    sys.stderr = _Tee(sys.stderr, stream)
