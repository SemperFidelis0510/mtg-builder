"""Deck class for managing MTG deck lists and type-based groupings."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
# from typing import TYPE_CHECKING
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
    print("[_resolve_name_to_type_key] card not found: %r" % name_clean)
    LOGGER.error(0, "from_export_text: card not found: %s", name_clean)
    raise ValueError(f"card not found: {name_clean!r}")


def _cards_from_names(names: list[str]) -> list["Card"]:
    """Build list of Card from list of card names. Uses get_card_data(); raises ValueError if any name not found."""
    from src.lib.card_data import get_card_data
    from src.obj.card import Card
    from src.utils.logger import LOGGER

    if not names:
        return []
    data: list["Card"] = get_card_data()
    name_lower_to_card: dict[str, "Card"] = {}
    for c in data:
        key: str = c.name.lower()
        if key not in name_lower_to_card:
            name_lower_to_card[key] = c
    out: list["Card"] = []
    for raw_name in names:
        name_clean: str = (raw_name or "").strip()
        if not name_clean:
            continue
        key = name_clean.lower()
        if key not in name_lower_to_card and " // " in name_clean:
            key = name_clean.split(" // ", 1)[0].strip().lower()
        if key not in name_lower_to_card:
            LOGGER.error(0, "_cards_from_names: card not found: %s", name_clean)
            raise ValueError(f"_cards_from_names: card not found: {name_clean!r}")
        out.append(name_lower_to_card[key])
    return out


class Deck:
    """Manages an MTG deck: metadata and card lists by type.

    Attributes:
        name: Deck name.
        colors: Deck colors (e.g. list of "W", "U", "B", "R", "G").
        description: Optional deck description.
        cards: Full list of Card objects in the main deck (duplicates for multiple copies).
        maybe: Maybe board: list of Card (same form as cards); not counted in type buckets.
        sideboard: Sideboard: list of Card (same form as cards); not counted in type buckets.
        creatures: (Read-only.) Card names that are creatures, computed from cards.
        non_creatures: (Read-only.) Card names that are artifacts, enchantments, or planeswalkers.
        spells: (Read-only.) Card names that are instants or sorceries.
        lands: (Read-only.) Card names that are lands.
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
        maybe: list["Card"] | list[dict] | None = None,
        sideboard: list["Card"] | list[dict] | None = None,
    ) -> None:
        from src.obj.card import Card as CardCls

        self.name: str = name
        self.colors: list[str] = list(colors) if colors is not None else []
        self.description: str = description
        self.cards: list["Card"] = _normalize_cards_arg(cards, CardCls)
        self.maybe: list["Card"] = _normalize_cards_arg(maybe, CardCls)
        self.sideboard: list["Card"] = _normalize_cards_arg(sideboard, CardCls)

    @property
    def creatures(self) -> list[str]:
        """Card names that are creatures (computed from self.cards)."""
        return [c.name for c in self.cards if _type_line_to_key(c.type_line) == "creatures"]

    @property
    def non_creatures(self) -> list[str]:
        """Card names that are artifacts, enchantments, or planeswalkers (computed from self.cards)."""
        return [c.name for c in self.cards if _type_line_to_key(c.type_line) == "non_creatures"]

    @property
    def spells(self) -> list[str]:
        """Card names that are instants or sorceries (computed from self.cards)."""
        return [c.name for c in self.cards if _type_line_to_key(c.type_line) == "spells"]

    @property
    def lands(self) -> list[str]:
        """Card names that are lands (computed from self.cards)."""
        return [c.name for c in self.cards if _type_line_to_key(c.type_line) == "lands"]

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
        """Return flat list of card names from self.cards."""
        return [c.name for c in self.cards]

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
            names: list[str] = self._all_card_names()
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
        """Serialize this Deck to a plain dict (only __init__ fields: name, colors, description, cards, maybe, sideboard)."""
        return {
            "name": self.name,
            "colors": list(self.colors),
            "description": self.description,
            "cards": [c.to_dict() for c in self.cards],
            "maybe": [c.to_dict() for c in self.maybe],
            "sideboard": [c.to_dict() for c in self.sideboard],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Deck":
        """Construct a Deck from a dict (inverse of to_dict). Accepts merged keys (non_creatures, spells) or legacy (artifacts, enchantments, planeswalkers, instants, sorceries)."""
        from src.obj.card import Card

        cards_arg: list["Card"] = []
        if "cards" in data and isinstance(data["cards"], list):
            cards_arg = [Card.from_dict(c) for c in data["cards"]]
        else:
            all_names: list[str] = []
            if "creatures" in data and isinstance(data["creatures"], list):
                all_names.extend(data["creatures"])
            non_creatures: list[str] = []
            if "non_creatures" in data and isinstance(data["non_creatures"], list):
                non_creatures = data["non_creatures"]
            else:
                for key in ("artifacts", "enchantments", "planeswalkers"):
                    if key in data and isinstance(data[key], list):
                        non_creatures.extend(data[key])
            all_names.extend(non_creatures)
            spells: list[str] = []
            if "spells" in data and isinstance(data["spells"], list):
                spells = data["spells"]
            else:
                for key in ("instants", "sorceries"):
                    if key in data and isinstance(data[key], list):
                        spells.extend(data[key])
            all_names.extend(spells)
            if "lands" in data and isinstance(data["lands"], list):
                all_names.extend(data["lands"])
            if all_names:
                cards_arg = _cards_from_names(all_names)

        maybe_arg: list["Card"] = _normalize_cards_arg(
            data["maybe"] if "maybe" in data and isinstance(data["maybe"], list) else None,
            Card,
        )
        sideboard_arg: list["Card"] = _normalize_cards_arg(
            data["sideboard"] if "sideboard" in data and isinstance(data["sideboard"], list) else None,
            Card,
        )

        return cls(
            name=data["name"] if "name" in data else "",
            colors=data["colors"] if "colors" in data else None,
            description=data["description"] if "description" in data else "",
            cards=cards_arg,
            maybe=maybe_arg,
            sideboard=sideboard_arg,
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
        raw: str = (text or "").strip()
        print("[Deck.from_export_text] fmt=%r raw_len=%d" % (fmt, len(raw)))
        if fmt not in cls.EXPORT_FORMATS:
            raise ValueError(
                f"unsupported import format {format!r}; use 'json', 'arena', or 'goldfish'"
            )
        if not raw and fmt != "json":
            print("[Deck.from_export_text] empty raw, returning empty Deck")
            return cls()

        if fmt == "json":
            print("[Deck.from_export_text] parsing json")
            try:
                data: dict = json.loads(raw)
            except json.JSONDecodeError as e:
                print("[Deck.from_export_text] JSONDecodeError:", e)
                raise ValueError(f"invalid JSON: {e}") from e
            if not isinstance(data, dict):
                raise ValueError("JSON root must be an object")
            d = cls.from_dict(data)
            print("[Deck.from_export_text] json ok; cards=%d" % len(d.cards))
            return d

        if fmt == "arena":
            print("[Deck.from_export_text] parsing arena; lines=%d" % len(raw.splitlines()))
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
            all_names_arena: list[str] = creatures + non_creatures + spells + lands
            print("[Deck.from_export_text] arena names count=%d" % len(all_names_arena))
            cards_arena: list["Card"] = _cards_from_names(all_names_arena) if all_names_arena else []
            print("[Deck.from_export_text] arena ok; cards=%d" % len(cards_arena))
            return cls(cards=cards_arena)

        if fmt == "goldfish":
            print("[Deck.from_export_text] parsing goldfish; lines=%d" % len(raw.splitlines()))
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
                # If no section header yet, resolve card to get type (arena-style); else use current section
                if current_key is None:
                    canonical_g, type_key_g = _resolve_name_to_type_key(name_g)
                    target_g = (
                        creatures_g
                        if type_key_g == "creatures"
                        else non_creatures_g
                        if type_key_g == "non_creatures"
                        else spells_g
                        if type_key_g == "spells"
                        else lands_g
                    )
                else:
                    canonical_g, _ = _resolve_name_to_type_key(name_g)
                    target_g = (
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
            all_names_g: list[str] = creatures_g + non_creatures_g + spells_g + lands_g
            print("[Deck.from_export_text] goldfish names count=%d" % len(all_names_g))
            cards_g: list["Card"] = _cards_from_names(all_names_g) if all_names_g else []
            print("[Deck.from_export_text] goldfish ok; cards=%d" % len(cards_g))
            return cls(cards=cards_g)

        raise ValueError(
            f"unsupported import format {format!r}; use 'json', 'arena', or 'goldfish'"
        )
