"""Match user-supplied card names to in-deck Card objects (MDFC, Arena aliases)."""

from __future__ import annotations

from src.lib.cardDB import CardDB
from src.obj.card import Card


def deck_card_identity_key(card: Card) -> str:
    """Stable lowercased key for comparing the same printing across name spellings."""
    raw: str = (card.canonical_name or card.name).strip()
    return raw.lower()


def requested_name_matches_deck_card(deck_card: Card, requested: str) -> bool:
    """True if *requested* refers to the same card as *deck_card* (DB resolve + face fallback)."""
    req: str = requested.strip()
    if not req:
        return False
    primary: Card | None = CardDB.inst().try_resolve_primary_card(req)
    if primary is not None:
        if deck_card_identity_key(primary) == deck_card_identity_key(deck_card):
            return True
    return deck_card.name.strip().lower() == req.lower()


def commander_string_matches_request(commander_str: str, requested: str) -> bool:
    """True if *requested* names the same card as *commander_str* (commander field text)."""
    cs: str = commander_str.strip()
    rq: str = requested.strip()
    if not cs or not rq:
        return False
    cmd_primary: Card | None = CardDB.inst().try_resolve_primary_card(cs)
    req_primary: Card | None = CardDB.inst().try_resolve_primary_card(rq)
    if cmd_primary is not None and req_primary is not None:
        if deck_card_identity_key(cmd_primary) == deck_card_identity_key(req_primary):
            return True
    return cs.lower() == rq.lower()
