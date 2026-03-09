"""Deck class for managing MTG deck lists and type-based groupings."""


class Deck:
    """Manages an MTG deck: metadata and card lists by type.

    Attributes:
        name: Deck name.
        colors: Deck colors (e.g. list of "W", "U", "B", "R", "G").
        description: Optional deck description.
        cards: Full list of card names in the deck (may include duplicates for multiples).
        creatures: Card names that are creatures.
        artifacts: Card names that are artifacts.
        enchantments: Card names that are enchantments.
        planeswalkers: Card names that are planeswalkers.
        lands: Card names that are lands.
        instants: Card names that are instants.
        sorceries: Card names that are sorceries.
        spells: Card names classified as spells (e.g. instant + sorcery or other non-permanents).
    """

    def __init__(
        self,
        name: str = "",
        colors: list[str] | None = None,
        description: str = "",
        cards: list[str] | None = None,
        creatures: list[str] | None = None,
        artifacts: list[str] | None = None,
        enchantments: list[str] | None = None,
        planeswalkers: list[str] | None = None,
        lands: list[str] | None = None,
        instants: list[str] | None = None,
        sorceries: list[str] | None = None,
        spells: list[str] | None = None,
    ) -> None:
        self.name: str = name
        self.colors: list[str] = list(colors) if colors is not None else []
        self.description: str = description
        self.cards: list[str] = list(cards) if cards is not None else []
        self.creatures: list[str] = list(creatures) if creatures is not None else []
        self.artifacts: list[str] = list(artifacts) if artifacts is not None else []
        self.enchantments: list[str] = list(enchantments) if enchantments is not None else []
        self.planeswalkers: list[str] = list(planeswalkers) if planeswalkers is not None else []
        self.lands: list[str] = list(lands) if lands is not None else []
        self.instants: list[str] = list(instants) if instants is not None else []
        self.sorceries: list[str] = list(sorceries) if sorceries is not None else []
        self.spells: list[str] = list(spells) if spells is not None else []
