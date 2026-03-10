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


_TYPE_KEYS: list[str] = ["creatures", "non_creatures", "spells", "lands"]
_TYPE_LABELS: dict[str, str] = {
    "creatures": "Creatures",
    "non_creatures": "Non-creatures",
    "spells": "Spells",
    "lands": "Lands",
}
# Reverse map for goldfish import: section label -> type key
_TYPE_LABEL_TO_KEY: dict[str, str] = {v: k for k, v in _TYPE_LABELS.items()}


def _type_line_to_key(type_line: str) -> str:
    """Map MTG type_line to type key: lands, creatures, non_creatures, or spells."""
    if not type_line or not isinstance(type_line, str):
        return "spells"
    t: str = type_line.lower()
    if "land" in t:
        return "lands"
    if "creature" in t:
        return "creatures"
    if "planeswalker" in t or "artifact" in t or "enchantment" in t:
        return "non_creatures"
    if "instant" in t or "sorcery" in t:
        return "spells"
    return "spells"


def _resolve_name_to_type_key(card_name: str) -> tuple[str, str]:
    """Look up card_name in card data; return (canonical_name, type_key). Raises ValueError if not found."""
    from src.lib.card_data import get_card_data
    from src.utils.logger import LOGGER

    name_clean: str = (card_name or "").strip()
    if not name_clean:
        raise ValueError("card name is empty")
    data: list = get_card_data()
    name_lower: str = name_clean.lower()
    for c in data:
        if c.name.lower() == name_lower:
            key: str = _type_line_to_key(getattr(c, "type_line", "") or "")
            return (c.name, key)
    if " // " in name_clean:
        first_part: str = name_clean.split(" // ", 1)[0].strip().lower()
        for c in data:
            if c.name.lower() == first_part:
                key = _type_line_to_key(getattr(c, "type_line", "") or "")
                return (c.name, key)
    LOGGER.error(0, "from_export_text: card not found: %s", name_clean)
    raise ValueError(f"card not found: {name_clean!r}")


class Deck:
    """Manages an MTG deck: metadata and card lists by type.

    Attributes:
        name: Deck name.
        colors: Deck colors (e.g. list of "W", "U", "B", "R", "G").
        description: Optional deck description.
        cards: Full list of Card objects in the deck (duplicates for multiple copies).
        creatures: Card names that are creatures.
        non_creatures: Card names that are artifacts, enchantments, or planeswalkers.
        spells: Card names that are instants or sorceries.
        lands: Card names that are lands.
    """

    # Import and export support the same formats (json, arena, goldfish).
    EXPORT_FORMATS: dict[str, str] = {
        "json": "JSON",
        "arena": "MTG Arena",
        "goldfish": "MTGGoldfish",
    }

    def __init__(
        self,
        name: str = "",
        colors: list[str] | None = None,
        description: str = "",
        cards: list["Card"] | list[dict] | None = None,
        creatures: list[str] | None = None,
        non_creatures: list[str] | None = None,
        spells: list[str] | None = None,
        lands: list[str] | None = None,
    ) -> None:
        from src.obj.card import Card as CardCls

        self.name: str = name
        self.colors: list[str] = list(colors) if colors is not None else []
        self.description: str = description
        self.cards: list["Card"] = _normalize_cards_arg(cards, CardCls)
        self.creatures: list[str] = list(creatures) if creatures is not None else []
        self.non_creatures: list[str] = list(non_creatures) if non_creatures is not None else []
        self.spells: list[str] = list(spells) if spells is not None else []
        self.lands: list[str] = list(lands) if lands is not None else []

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

    def _all_card_names(self) -> list[str]:
        """Return flat list of card names from type-based lists (used when self.cards is empty)."""
        out: list[str] = []
        for key in _TYPE_KEYS:
            lst: list[str] = getattr(self, key, None) or []
            if isinstance(lst, list):
                out.extend(lst)
        return out

    def export(self, format: str) -> str:
        """Export the deck as a string in the given format.

        Args:
            format: One of "json", "arena" (MTG Arena deck list), or "goldfish" (MTGGoldfish with section headers).

        Returns:
            The deck as a string (JSON text or decklist lines).

        Raises:
            ValueError: If format is not "json", "arena", or "goldfish".
        """
        fmt: str = format.strip().lower()
        if fmt == "json":
            return json.dumps(self.to_dict(), indent=2)
        if fmt == "arena":
            if self.cards:
                names: list[str] = [c.name for c in self.cards]
            else:
                names = self._all_card_names()
            counts: Counter[str] = Counter(names)
            lines: list[str] = [
                f"{n} {name}" for name, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
            ]
            return "\n".join(lines) if lines else ""
        if fmt == "goldfish":
            goldfish_lines: list[str] = []
            for key in _TYPE_KEYS:
                arr: list[str] = getattr(self, key, None) or []
                if not isinstance(arr, list) or not arr:
                    continue
                counts_g: Counter[str] = Counter(arr)
                goldfish_lines.append("// " + _TYPE_LABELS.get(key, key))
                for name in sorted(counts_g.keys()):
                    goldfish_lines.append(f"{counts_g[name]} {name}")
                goldfish_lines.append("")
            return "\n".join(goldfish_lines).rstrip("\n")
        raise ValueError(
            f"deck export: unsupported format {format!r}; use 'json', 'arena', or 'goldfish'"
        )

    def save(self, format: str, path: Path | str) -> None:
        """Export the deck in the given format and write it to a file.

        Args:
            format: One of "json", "arena", or "goldfish".
            path: File path to write to (str or Path).

        Raises:
            ValueError: If format is not "json", "arena", or "goldfish".
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
            "non_creatures": list(self.non_creatures),
            "spells": list(self.spells),
            "lands": list(self.lands),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Deck":
        """Construct a Deck from a dict (inverse of to_dict). Accepts merged keys (non_creatures, spells) or legacy (artifacts, enchantments, planeswalkers, instants, sorceries)."""
        from src.obj.card import Card

        cards_arg: list["Card"] | None = None
        if "cards" in data:
            raw_cards: list = data["cards"]
            cards_arg = [Card.from_dict(c) for c in raw_cards] if isinstance(raw_cards, list) else None

        non_creatures: list[str] | None = None
        if "non_creatures" in data and isinstance(data["non_creatures"], list):
            non_creatures = data["non_creatures"]
        else:
            legacy: list[str] = []
            for key in ("artifacts", "enchantments", "planeswalkers"):
                if key in data and isinstance(data[key], list):
                    legacy.extend(data[key])
            if legacy:
                non_creatures = legacy

        spells: list[str] | None = None
        if "spells" in data and isinstance(data["spells"], list):
            spells = data["spells"]
        else:
            legacy_spells: list[str] = []
            for key in ("instants", "sorceries"):
                if key in data and isinstance(data[key], list):
                    legacy_spells.extend(data[key])
            if legacy_spells:
                spells = legacy_spells

        return cls(
            name=data["name"] if "name" in data else "",
            colors=data["colors"] if "colors" in data else None,
            description=data["description"] if "description" in data else "",
            cards=cards_arg,
            creatures=data["creatures"] if "creatures" in data else None,
            non_creatures=non_creatures,
            spells=spells,
            lands=data["lands"] if "lands" in data else None,
        )

    @classmethod
    def from_export_text(cls, text: str, format: str) -> "Deck":
        """Parse decklist text and return a new Deck. Supports same formats as export(): json, arena, goldfish.

        Args:
            text: Pasted decklist string.
            format: One of "json", "arena", or "goldfish".

        Returns:
            A new Deck instance with type lists (and optionally cards for json) populated.

        Raises:
            ValueError: If format is unsupported, text is invalid, or a card name is not found (arena/goldfish).
        """
        fmt: str = (format or "").strip().lower()
        if fmt not in cls.EXPORT_FORMATS:
            raise ValueError(
                f"unsupported import format {format!r}; use 'json', 'arena', or 'goldfish'"
            )
        raw: str = (text or "").strip()
        if not raw and fmt != "json":
            return cls()

        if fmt == "json":
            try:
                data: dict = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"invalid JSON: {e}") from e
            if not isinstance(data, dict):
                raise ValueError("JSON root must be an object")
            return cls.from_dict(data)

        if fmt == "arena":
            creatures: list[str] = []
            non_creatures: list[str] = []
            spells: list[str] = []
            lands: list[str] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    count = int(parts[0])
                except ValueError:
                    continue
                name_part: str = parts[1].strip()
                if count <= 0:
                    continue
                canonical_name, type_key = _resolve_name_to_type_key(name_part)
                target: list[str] = (
                    creatures
                    if type_key == "creatures"
                    else non_creatures
                    if type_key == "non_creatures"
                    else spells
                    if type_key == "spells"
                    else lands
                )
                for _ in range(count):
                    target.append(canonical_name)
            return cls(
                creatures=creatures,
                non_creatures=non_creatures,
                spells=spells,
                lands=lands,
            )

        if fmt == "goldfish":
            creatures_g: list[str] = []
            non_creatures_g: list[str] = []
            spells_g: list[str] = []
            lands_g: list[str] = []
            current_key: str | None = None
            for line in raw.splitlines():
                s: str = line.strip()
                if not s:
                    continue
                if s.startswith("//"):
                    label: str = s[2:].strip()
                    current_key = _TYPE_LABEL_TO_KEY.get(label)
                    continue
                if current_key is None:
                    continue
                parts = s.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    count_g = int(parts[0])
                except ValueError:
                    continue
                name_g: str = parts[1].strip()
                if count_g <= 0:
                    continue
                canonical_g, _ = _resolve_name_to_type_key(name_g)
                target_g: list[str] = (
                    creatures_g
                    if current_key == "creatures"
                    else non_creatures_g
                    if current_key == "non_creatures"
                    else spells_g
                    if current_key == "spells"
                    else lands_g
                )
                for _ in range(count_g):
                    target_g.append(canonical_g)
            return cls(
                creatures=creatures_g,
                non_creatures=non_creatures_g,
                spells=spells_g,
                lands=lands_g,
            )

        raise ValueError(
            f"unsupported import format {format!r}; use 'json', 'arena', or 'goldfish'"
        )
