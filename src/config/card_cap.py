"""Per-card copy limits for deck construction and the bypass list for uncapped cards."""

DEFAULT_CARD_CAP: int = 4
"""Standard maximum copies of a single card across main deck + sideboard."""

COMMANDER_CARD_CAP: int = 1
"""Singleton limit for commander-family formats (commander, brawl, duel, etc.)."""

UNCAPPED_CARD_NAMES: frozenset[str] = frozenset({
    # Basic lands
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    # Snow-Covered basics
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
    # Colorless basic
    "Wastes",
    # Cards with "a deck can have any number of cards named ..."
    "Relentless Rats",
    "Rat Colony",
    "Shadowborn Apostle",
    "Dragon's Approach",
    "Persistent Petitioners",
    "Seven Dwarves",
    "Slime Against Humanity",
    "Hare Apparent",
})
"""Card names exempt from the copy cap (case-sensitive canonical names)."""

_UNCAPPED_LOWER: frozenset[str] = frozenset(n.lower() for n in UNCAPPED_CARD_NAMES)


def is_uncapped(card_name: str) -> bool:
    """Return True if *card_name* is exempt from the per-card copy limit (case-insensitive)."""
    return card_name.lower() in _UNCAPPED_LOWER
