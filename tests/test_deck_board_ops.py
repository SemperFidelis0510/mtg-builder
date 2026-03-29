"""Tests for atomic board remove/move index planning."""

from __future__ import annotations

from src.lib.deck_board_ops import collect_matching_indices_asc, remove_cards_at_indices
from src.obj.card import Card


def test_collect_matching_fails_without_mutation() -> None:
    board: list[Card] = [Card(name="OnlyOne")]
    idx_asc, not_found = collect_matching_indices_asc(board, ["OnlyOne", "Missing"], 1)
    assert idx_asc is None
    assert not_found == ["Missing"]
    assert len(board) == 1
    assert board[0].name == "OnlyOne"


def test_remove_cards_at_indices_after_successful_plan() -> None:
    board: list[Card] = [Card(name="X"), Card(name="Y")]
    idx_asc, not_found = collect_matching_indices_asc(board, ["X"], 1)
    assert not_found == []
    assert idx_asc == [0]
    remove_cards_at_indices(board, idx_asc)
    assert len(board) == 1
    assert board[0].name == "Y"


def test_count_per_name_two_copies_same_label() -> None:
    board: list[Card] = [Card(name="A"), Card(name="A"), Card(name="B")]
    idx_asc, not_found = collect_matching_indices_asc(board, ["A"], 2)
    assert not_found == []
    assert idx_asc == [0, 1]
    remove_cards_at_indices(board, idx_asc)
    assert len(board) == 1
    assert board[0].name == "B"
