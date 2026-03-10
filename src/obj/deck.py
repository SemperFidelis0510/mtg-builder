"""Deck class for managing MTG deck lists and type-based groupings."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.obj.card import Card


def _normalize_cards_arg(
    cards: list["Card"] | list[dict] | None,
    card_cls: type,
) -> list["Card"]:
    """Convert cards argument to list of Card. Accepts list of Card or list of dicts."""
    if cards is None:
        return []
    out: list["Card"] = []
    for c in cards:
        if isinstance(c, card_cls):
            out.append(c)
        elif isinstance(c, dict):
            out.append(card_cls.from_dict(c))
        else:
            raise TypeError(f"deck cards item must be Card or dict, got {type(c).__name__}")
    return out


class Deck:
    """Manages an MTG deck: metadata and card lists by type.

    Attributes:
        name: Deck name.
        colors: Deck colors (e.g. list of "W", "U", "B", "R", "G").
        description: Optional deck description.
        cards: Full list of Card objects in the deck (duplicates for multiple copies).
        creatures: Card names that are creatures.
        artifacts: Card names that are artifacts.
        enchantments: Card names that are enchantments.
        planeswalkers: Card names that are planeswalkers.
        lands: Card names that are lands.
        instants: Card names that are instants.
        sorceries: Card names that are sorceries.
    """

    def __init__(
        self,
        name: str = "",
        colors: list[str] | None = None,
        description: str = "",
        cards: list["Card"] | list[dict] | None = None,
        creatures: list[str] | None = None,
        artifacts: list[str] | None = None,
        enchantments: list[str] | None = None,
        planeswalkers: list[str] | None = None,
        lands: list[str] | None = None,
        instants: list[str] | None = None,
        sorceries: list[str] | None = None,
    ) -> None:
        from src.obj.card import Card as CardCls

        self.name: str = name
        self.colors: list[str] = list(colors) if colors is not None else []
        self.description: str = description
        self.cards: list["Card"] = _normalize_cards_arg(cards, CardCls)
        self.creatures: list[str] = list(creatures) if creatures is not None else []
        self.artifacts: list[str] = list(artifacts) if artifacts is not None else []
        self.enchantments: list[str] = list(enchantments) if enchantments is not None else []
        self.planeswalkers: list[str] = list(planeswalkers) if planeswalkers is not None else []
        self.lands: list[str] = list(lands) if lands is not None else []
        self.instants: list[str] = list(instants) if instants is not None else []
        self.sorceries: list[str] = list(sorceries) if sorceries is not None else []

    def add_cards(self, names: list[str]) -> None:
        """Resolve card names to Card objects from the card database and append them to this deck.

        For each name in the list, looks up a matching Card (by exact name, case-insensitive)
        from the loaded AtomicCards data and appends it to ``self.cards``. Duplicate names
        result in multiple copies of the same card.

        Args:
            names: List of card names to add (e.g. ["Lightning Bolt", "Lightning Bolt"] for two copies).

        Raises:
            FileNotFoundError: If AtomicCards.json is not available.
            ValueError: If any name does not match a card in the database.
        """
        from src.lib.card_data import get_card_data

        data: list["Card"] = get_card_data()
        name_lower_to_card: dict[str, "Card"] = {}
        for c in data:
            key: str = c.name.lower()
            if key not in name_lower_to_card:
                name_lower_to_card[key] = c
        for raw_name in names:
            name_clean: str = raw_name.strip()
            if not name_clean:
                continue
            key = name_clean.lower()
            if key not in name_lower_to_card:
                from src.utils.logger import LOGGER

                LOGGER.error(0, "add_cards: card not found: %s", name_clean)
                raise ValueError(f"add_cards: card not found: {name_clean!r}")
            self.cards.append(name_lower_to_card[key])

    def export(self, format: str) -> str:
        """Export the deck as a string in the given format.

        Args:
            format: One of "json" or "arena" (MTG Arena deck list format).

        Returns:
            The deck as a string (JSON text or Arena-style lines).

        Raises:
            ValueError: If format is not "json" or "arena".
        """
        fmt: str = format.strip().lower()
        if fmt == "json":
            return json.dumps(self.to_dict(), indent=2)
        if fmt == "arena":
            counts: Counter[str] = Counter(c.name for c in self.cards)
            lines: list[str] = [f"{n} {name}" for name, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
            return "\n".join(lines) if lines else ""
        raise ValueError(f"deck export: unsupported format {format!r}; use 'json' or 'arena'")

    def save(self, format: str, path: Path | str) -> None:
        """Export the deck in the given format and write it to a file.

        Args:
            format: One of "json" or "arena".
            path: File path to write to (str or Path).

        Raises:
            ValueError: If format is not "json" or "arena".
        """
        text: str = self.export(format)
        out_path: Path = Path(path) if isinstance(path, str) else path
        out_path.write_text(text, encoding="utf-8")

    def to_dict(self) -> dict:
        """Serialize this Deck to a plain dict (cards as list of card dicts)."""
        return {
            "name": self.name,
            "colors": list(self.colors),
            "description": self.description,
            "cards": [c.to_dict() for c in self.cards],
            "creatures": list(self.creatures),
            "artifacts": list(self.artifacts),
            "enchantments": list(self.enchantments),
            "planeswalkers": list(self.planeswalkers),
            "lands": list(self.lands),
            "instants": list(self.instants),
            "sorceries": list(self.sorceries),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Deck":
        """Construct a Deck from a dict (inverse of to_dict)."""
        from src.obj.card import Card

        cards_arg: list["Card"] | None = None
        if "cards" in data:
            raw_cards: list = data["cards"]
            cards_arg = [Card.from_dict(c) for c in raw_cards] if isinstance(raw_cards, list) else None
        return cls(
            name=data["name"] if "name" in data else "",
            colors=data["colors"] if "colors" in data else None,
            description=data["description"] if "description" in data else "",
            cards=cards_arg,
            creatures=data["creatures"] if "creatures" in data else None,
            artifacts=data["artifacts"] if "artifacts" in data else None,
            enchantments=data["enchantments"] if "enchantments" in data else None,
            planeswalkers=data["planeswalkers"] if "planeswalkers" in data else None,
            lands=data["lands"] if "lands" in data else None,
            instants=data["instants"] if "instants" in data else None,
            sorceries=(
                (data["sorceries"] if "sorceries" in data else [])
                + (data["spells"] if "spells" in data else [])
            )
            or None,
        )
