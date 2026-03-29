"""Tests for deck name identity and matching (MDFC / aliases when DB is available)."""

from __future__ import annotations

import pytest

from src.lib.config import ATOMIC_CARDS_PATH
from src.lib.deck_name_match import deck_card_identity_key, requested_name_matches_deck_card
from src.obj.card import Card


def test_deck_card_identity_key_uses_canonical_when_set() -> None:
    c = Card(name="Front", canonical_name="Front // Back")
    assert deck_card_identity_key(c) == "front // back"


def test_deck_card_identity_key_falls_back_to_name() -> None:
    c = Card(name="Sol Ring")
    assert deck_card_identity_key(c) == "sol ring"


@pytest.mark.skipif(not ATOMIC_CARDS_PATH.is_file(), reason="AtomicCards.json not present")
def test_requested_name_matches_mdfc_full_string_to_face_card() -> None:
    from src.lib.cardDB import CardDB

    db = CardDB.inst()
    db.get_card_data()
    front = db.resolve_primary_card("Fell the Profane")
    assert requested_name_matches_deck_card(front, "Fell the Profane // Fell Mire")
