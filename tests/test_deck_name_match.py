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


@pytest.mark.skipif(not ATOMIC_CARDS_PATH.is_file(), reason="AtomicCards.json not present")
def test_card_arena_export_name_mdfc_uses_front_face() -> None:
    from src.lib.cardDB import CardDB

    db = CardDB.inst()
    card = db.resolve_primary_card("Bala Ged Recovery // Bala Ged Sanctuary")
    assert db.card_arena_export_name(card) == "Bala Ged Recovery"


@pytest.mark.skipif(not ATOMIC_CARDS_PATH.is_file(), reason="AtomicCards.json not present")
def test_card_arena_export_name_split_uses_full_name() -> None:
    from src.lib.cardDB import CardDB

    db = CardDB.inst()
    card = db.resolve_primary_card("Fire // Ice")
    assert db.card_arena_export_name(card) == "Fire // Ice"


@pytest.mark.skipif(not ATOMIC_CARDS_PATH.is_file(), reason="AtomicCards.json not present")
def test_deck_export_arena_mdfc_lines() -> None:
    from src.obj.deck import Deck

    deck = Deck()
    deck.add_cards(
        [
            "Bala Ged Recovery // Bala Ged Sanctuary",
            "Turntimber Symbiosis // Turntimber, Serpentine Wood",
            "Fire // Ice",
        ]
    )
    text = deck.export("arena")
    assert "1 Bala Ged Recovery" in text
    assert "Bala Ged Sanctuary" not in text
    assert "Turntimber, Serpentine Wood" not in text
    assert "1 Fire // Ice" in text
    assert "Bala Ged Recovery //" not in text
