"""Load MTG keyword explanations from JSON and expand keywords in oracle text for RAG."""

import json
import re
from pathlib import Path

from src.lib.config import KEYWORD_EXPLANATIONS_PATH

_cached_dict: dict[str, str] | None = None
_cached_regex: re.Pattern[str] | None = None
_cached_lower_to_explanation: dict[str, str] | None = None


def _load_keyword_explanations() -> dict[str, str]:
    """Read keyword_explanations.json and return {keyword: explanation}. Raises on missing/invalid file."""
    if not KEYWORD_EXPLANATIONS_PATH.is_file():
        raise FileNotFoundError(
            f"Keyword explanations config not found: {KEYWORD_EXPLANATIONS_PATH}"
        )
    with open(KEYWORD_EXPLANATIONS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"keyword_explanations.json must be a JSON object, got {type(data).__name__}"
        )
    for k, v in data.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError(
                "keyword_explanations.json: every key must be a non-empty string"
            )
        if not isinstance(v, str) or not v.strip():
            raise ValueError(
                f"keyword_explanations.json: value for {k!r} must be a non-empty string"
            )
    return dict(data)


def _get_keyword_data() -> tuple[re.Pattern[str], dict[str, str]]:
    """Return (compiled regex, lowercased keyword -> explanation). Cached."""
    global _cached_dict, _cached_regex, _cached_lower_to_explanation
    if _cached_dict is None:
        _cached_dict = _load_keyword_explanations()
        # Longest-first so "Double Strike" matches before "Strike"
        sorted_keywords = sorted(
            _cached_dict.keys(), key=lambda s: len(s), reverse=True
        )
        pattern = "|".join(re.escape(k) for k in sorted_keywords)
        _cached_regex = re.compile(r"\b(" + pattern + r")\b", re.IGNORECASE)
        _cached_lower_to_explanation = {
            k.lower(): v for k, v in _cached_dict.items()
        }
    assert _cached_regex is not None
    assert _cached_lower_to_explanation is not None
    return _cached_regex, _cached_lower_to_explanation


def expand_keywords(text: str) -> str:
    """Insert keyword explanations in parentheses after each keyword in text.

    Uses keyword_explanations.json. Matching is case-insensitive and word-boundary
    aware. Keywords are tried longest-first so e.g. 'Double Strike' matches before
    'Strike'. Single-pass so inserted text is not re-scanned.
    """
    if not text or not text.strip():
        return text
    regex, lower_to_explanation = _get_keyword_data()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        explanation = lower_to_explanation[key.lower()]
        return f"{key} ({explanation})"

    return regex.sub(repl, text)
