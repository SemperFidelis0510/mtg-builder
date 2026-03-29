"""Atomic remove/move planning on in-memory board lists (deck editor)."""

from __future__ import annotations

from src.lib.deck_name_match import requested_name_matches_deck_card
from src.obj.card import Card


def collect_matching_indices_asc(
    board: list[Card],
    names: list[str],
    count_per_name: int,
) -> tuple[list[int] | None, list[str]]:
    """Pick board indices for each name (count_per_name copies per name), first occurrence order.

    Returns (indices ascending, []) on success, or (None, not_found_names) if any name lacks enough copies.
    """
    assigned: set[int] = set()
    indices: list[int] = []
    not_found: list[str] = []
    for name in names:
        got: int = 0
        for i, card in enumerate(board):
            if i in assigned:
                continue
            if requested_name_matches_deck_card(card, name):
                indices.append(i)
                assigned.add(i)
                got += 1
                if got >= count_per_name:
                    break
        if got < count_per_name:
            not_found.append(name)
    if not_found:
        return None, not_found
    return sorted(indices), []


def remove_cards_at_indices(board: list[Card], indices_asc: list[int]) -> None:
    """Remove slots at given indices; *indices_asc* sorted ascending."""
    for idx in sorted(indices_asc, reverse=True):
        board.pop(idx)


def move_cards_at_indices(
    source: list[Card],
    dest: list[Card],
    indices_asc: list[int],
) -> None:
    """Move cards at indices from source to dest, preserving ascending board order in dest tail."""
    ordered: list[Card] = [source[i] for i in sorted(indices_asc)]
    for idx in sorted(indices_asc, reverse=True):
        source.pop(idx)
    dest.extend(ordered)
