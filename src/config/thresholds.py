"""Load RAG category thresholds from INI and classify numeric values."""

import configparser
from pathlib import Path

from src.lib.config import THRESHOLDS_INI_PATH

_REQUIRED_KEYS: tuple[str, ...] = ("medium", "high", "very_high")
_VALID_SECTIONS: frozenset[str] = frozenset(("power_toughness", "price"))


def _load_thresholds() -> dict[str, dict[str, float]]:
    """Read thresholds.ini and return {section: {key: float}}. Raises on missing file/section/keys."""
    if not THRESHOLDS_INI_PATH.is_file():
        raise FileNotFoundError(
            f"Thresholds config not found: {THRESHOLDS_INI_PATH}"
        )
    parser = configparser.ConfigParser()
    parser.read(THRESHOLDS_INI_PATH, encoding="utf-8")
    out: dict[str, dict[str, float]] = {}
    for section in _VALID_SECTIONS:
        if not parser.has_section(section):
            raise ValueError(
                f"thresholds.ini missing section [{section}]"
            )
        sec: dict[str, float] = {}
        for key in _REQUIRED_KEYS:
            if not parser.has_option(section, key):
                raise ValueError(
                    f"thresholds.ini section [{section}] missing key: {key}"
                )
            raw = parser.get(section, key)
            try:
                sec[key] = float(raw)
            except ValueError as e:
                raise ValueError(
                    f"thresholds.ini [{section}] {key}={raw!r}: must be a number"
                ) from e
        if sec["medium"] >= sec["high"] or sec["high"] >= sec["very_high"]:
            raise ValueError(
                f"thresholds.ini [{section}]: must have medium < high < very_high"
            )
        out[section] = sec
    return out


_cached: dict[str, dict[str, float]] | None = None


def _get_thresholds() -> dict[str, dict[str, float]]:
    """Return loaded thresholds (cached)."""
    global _cached
    if _cached is None:
        _cached = _load_thresholds()
    return _cached


def classify(value: float, section: str) -> str:
    """Classify a numeric value into low, medium, high, or very high.

    section must be 'power_toughness' or 'price'. Uses thresholds from thresholds.ini.
    Raises ValueError if section is invalid or config is missing/invalid.
    """
    if section not in _VALID_SECTIONS:
        raise ValueError(
            f"classify: section must be one of {sorted(_VALID_SECTIONS)}, got {section!r}"
        )
    th = _get_thresholds()[section]
    if value < th["medium"]:
        return "low"
    if value < th["high"]:
        return "medium"
    if value < th["very_high"]:
        return "high"
    return "very high"
