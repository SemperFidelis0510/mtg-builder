"""Utilities for parsing card-name input strings safely."""

from __future__ import annotations

from src.lib.cardDB import CardDB
from src.obj.card import Card

_CARD_NAME_INDEX: dict[str, str] | None = None


def _get_card_name_index() -> dict[str, str]:
    """Return lower-case card name -> canonical card name map."""
    global _CARD_NAME_INDEX
    if _CARD_NAME_INDEX is None:
        data: list[Card] = CardDB.inst().get_card_data()
        index: dict[str, str] = {}
        for card in data:
            key: str = card.name.lower()
            if key not in index:
                index[key] = card.name
        _CARD_NAME_INDEX = index
    return _CARD_NAME_INDEX


def parse_card_names_arg(card_names: str) -> list[str]:
    """Parse a comma-delimited card_names arg while preserving names that contain commas."""
    if not isinstance(card_names, str):
        raise TypeError(f"card_names must be str, got {type(card_names).__name__}")
    tokens: list[str] = [token.strip() for token in card_names.split(",") if token.strip()]
    if not tokens:
        return []
    name_index: dict[str, str] = _get_card_name_index()
    parsed_names: list[str] = []
    i: int = 0
    while i < len(tokens):
        matched_end: int | None = None
        matched_name: str | None = None
        for end in range(len(tokens), i, -1):
            candidate: str = ", ".join(tokens[i:end]).strip()
            key: str = candidate.lower()
            if key in name_index:
                matched_end = end
                matched_name = name_index[key]
                break
        if matched_end is None:
            parsed_names.append(tokens[i])
            i += 1
            continue
        assert matched_name is not None, "matched_name must be set when matched_end is set"
        parsed_names.append(matched_name)
        i = matched_end
    return parsed_names
