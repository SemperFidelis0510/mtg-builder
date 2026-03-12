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


# Display order: creature, instant, sorcery, artifact, enchantment, planeswalker, land (land last)
_TYPE_KEYS: list[str] = [
    "creature",
    "instant",
    "sorcery",
    "artifact",
    "enchantment",
    "planeswalker",
    "land",
]
_TYPE_LABELS: dict[str, str] = {
    "creature": "Creature",
    "instant": "Instant",
    "sorcery": "Sorcery",
    "artifact": "Artifact",
    "enchantment": "Enchantment",
    "planeswalker": "Planeswalker",
    "land": "Land",
}
# Reverse map for goldfish import: section label -> type key
_TYPE_LABEL_TO_KEY: dict[str, str] = {v: k for k, v in _TYPE_LABELS.items()}
# Legacy keys (old 4-type) -> map to new keys for from_dict
_LEGACY_TYPE_KEYS: list[str] = ["creatures", "non_creatures", "spells", "lands"]
# Goldfish sideboard section header (label only, no type key)
_GOLDFISH_SIDEBOARD_LABEL: str = "Sideboard"
# Standalone sideboard line (no "//") for Moxfield-style pastes when Goldfish is selected
_SIDEBOARD_LINE_LOWER: frozenset[str] = frozenset(
    {"sideboard", "sideboard:", "side board", "side board:", "sb", "sb:"}
)


def _type_line_to_key(type_line: str) -> str:
    """Map MTG type_line to one of: creature, instant, sorcery, artifact, enchantment, planeswalker, land.
    For multi-type cards we use priority: land > creature > instant > sorcery > artifact > enchantment > planeswalker.
    """
    if not type_line or not isinstance(type_line, str):
        return "sorcery"
    t: str = type_line.lower()
    if "land" in t:
        return "land"
    if "creature" in t:
        return "creature"
    if "instant" in t:
        return "instant"
    if "sorcery" in t:
        return "sorcery"
    if "artifact" in t:
        return "artifact"
    if "enchantment" in t:
        return "enchantment"
    if "planeswalker" in t:
        return "planeswalker"
    return "sorcery"


def _resolve_name_to_type_key(card_name: str) -> tuple[str, str]:
    """Look up card_name in card data; return (canonical_name, type_key). Raises ValueError if not found."""
    from src.lib.cardDB import CardDB
    from src.utils.logger import LOGGER

    name_clean: str = (card_name or "").strip()
    if not name_clean:
        raise ValueError("card name is empty")
    data: list = CardDB.inst().get_card_data()
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
    """Build list of Card from list of card names. Uses CardDB.get_card_data(); raises ValueError if any name not found."""
    from src.lib.cardDB import CardDB
    from src.obj.card import Card
    from src.utils.logger import LOGGER

    if not names:
        return []
    data: list["Card"] = CardDB.inst().get_card_data()
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
        format: Deck format (e.g. "commander", "standard", "modern").
        colorless_only: If True, deck is colorless-only (search filters for colorless cards).
        cards: Full list of Card objects in the main deck (duplicates for multiple copies).
        maybe: Maybe board: list of Card (same form as cards); not counted in type buckets.
        sideboard: Sideboard: list of Card (same form as cards); not counted in type buckets.
        creature: (Read-only.) Card names that are creatures.
        instant: (Read-only.) Card names that are instants.
        sorcery: (Read-only.) Card names that are sorceries.
        artifact: (Read-only.) Card names that are artifacts.
        enchantment: (Read-only.) Card names that are enchantments.
        planeswalker: (Read-only.) Card names that are planeswalkers.
        land: (Read-only.) Card names that are lands.
    """

    # Import and export support the same formats (arena first, json last).
    EXPORT_FORMATS: dict[str, str] = {
        "arena": "MTG Arena",
        "goldfish": "MTGGoldfish",
        "moxfield": "Moxfield",
        "json": "JSON",
    }

    def __init__(
        self,
        name: str = "",
        colors: list[str] | None = None,
        description: str = "",
        format: str = "",
        colorless_only: bool = False,
        cards: list["Card"] | list[dict] | None = None,
        maybe: list["Card"] | list[dict] | None = None,
        sideboard: list["Card"] | list[dict] | None = None,
    ) -> None:
        from src.obj.card import Card as CardCls

        self.name: str = name
        self.colors: list[str] = list(colors) if colors is not None else []
        self.description: str = description
        self.format: str = format
        self.colorless_only: bool = colorless_only
        self.cards: list["Card"] = _normalize_cards_arg(cards, CardCls)
        self.maybe: list["Card"] = _normalize_cards_arg(maybe, CardCls)
        self.sideboard: list["Card"] = _normalize_cards_arg(sideboard, CardCls)

    def _names_by_type_key(self, key: str) -> list[str]:
        """Card names in self.cards whose type_line maps to the given type key."""
        return [c.name for c in self.cards if _type_line_to_key(c.type_line) == key]

    @property
    def creature(self) -> list[str]:
        return self._names_by_type_key("creature")

    @property
    def instant(self) -> list[str]:
        return self._names_by_type_key("instant")

    @property
    def sorcery(self) -> list[str]:
        return self._names_by_type_key("sorcery")

    @property
    def artifact(self) -> list[str]:
        return self._names_by_type_key("artifact")

    @property
    def enchantment(self) -> list[str]:
        return self._names_by_type_key("enchantment")

    @property
    def planeswalker(self) -> list[str]:
        return self._names_by_type_key("planeswalker")

    @property
    def land(self) -> list[str]:
        return self._names_by_type_key("land")

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
        from src.lib.cardDB import CardDB

        data: list["Card"] = CardDB.inst().get_card_data()
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
            format: One of "arena", "goldfish", "moxfield", or "json".

        Returns:
            The deck as a string (JSON text or decklist lines).

        Raises:
            ValueError: If format is not "arena", "goldfish", "moxfield", or "json".
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
            if self.sideboard:
                sb_names: list[str] = [c.name for c in self.sideboard]
                sb_counts: Counter[str] = Counter(sb_names)
                if lines:
                    lines.append("")
                lines.append("Sideboard")
                lines.extend(
                    f"{n} {name}" for name, n in sorted(sb_counts.items(), key=lambda x: (-x[1], x[0]))
                )
            return "\n".join(lines) if lines else ""
        if fmt == "goldfish":
            goldfish_lines: list[str] = []
            for key in _TYPE_KEYS:
                arr = getattr(self, key, None)
                if not isinstance(arr, list) or not arr:
                    continue
                counts_g: Counter[str] = Counter(arr)
                goldfish_lines.append("// " + _TYPE_LABELS.get(key, key))
                for name in sorted(counts_g.keys()):
                    goldfish_lines.append(f"{counts_g[name]} {name}")
                goldfish_lines.append("")
            if self.sideboard:
                sb_counts_g: Counter[str] = Counter(c.name for c in self.sideboard)
                goldfish_lines.append("// Sideboard")
                for name in sorted(sb_counts_g.keys()):
                    goldfish_lines.append(f"{sb_counts_g[name]} {name}")
                goldfish_lines.append("")
            return "\n".join(goldfish_lines).rstrip("\n")
        if fmt == "moxfield":
            mox_lines: list[str] = ["Deck"]
            main_counts: Counter[str] = Counter(self._all_card_names())
            for name in sorted(main_counts.keys()):
                mox_lines.append(f"{main_counts[name]} {name}")
            if self.sideboard:
                mox_lines.append("")
                mox_lines.append("Sideboard")
                sb_counts_m: Counter[str] = Counter(c.name for c in self.sideboard)
                for name in sorted(sb_counts_m.keys()):
                    mox_lines.append(f"{sb_counts_m[name]} {name}")
            return "\n".join(mox_lines)
        raise ValueError(
            f"deck export: unsupported format {format!r}; use 'arena', 'goldfish', 'moxfield', or 'json'"
        )

    def save(self, format: str, path: Path | str) -> None:
        """Export the deck in the given format and write it to a file.

        Args:
            format: One of "arena", "goldfish", "moxfield", or "json".
            path: File path to write to (str or Path).

        Raises:
            ValueError: If format is not "arena", "goldfish", "moxfield", or "json".
        """
        text: str = self.export(format)
        out_path: Path = Path(path) if isinstance(path, str) else path
        out_path.write_text(text, encoding="utf-8")

    def to_dict(self) -> dict:
        """Serialize this Deck to a plain dict. cards/maybe/sideboard are stored as flat name lists."""
        return {
            "name": self.name,
            "colors": list(self.colors),
            "description": self.description,
            "format": self.format,
            "colorless_only": self.colorless_only,
            "cards": [c.name for c in self.cards],
            "maybe": [c.name for c in self.maybe],
            "sideboard": [c.name for c in self.sideboard],
        }

    @classmethod
    def _cards_list_from_data(cls, raw: list) -> list["Card"]:
        """Resolve a JSON list to Card objects. Accepts name strings, card dicts, or Card instances."""
        from src.obj.card import Card

        if not raw:
            return []
        if isinstance(raw[0], str):
            return _cards_from_names(raw)
        return _normalize_cards_arg(raw, Card)

    @classmethod
    def from_dict(cls, data: dict) -> "Deck":
        """Construct a Deck from a dict (inverse of to_dict). Accepts name lists, full card dicts, or legacy type-list keys."""
        cards_arg: list["Card"] = []
        if "cards" in data and isinstance(data["cards"], list):
            cards_arg = cls._cards_list_from_data(data["cards"])
        else:
            all_names: list[str] = []
            # New 7-type keys
            for key in _TYPE_KEYS:
                if key in data and isinstance(data[key], list):
                    all_names.extend(data[key])
            # Legacy 4-type keys (creatures, non_creatures, spells, lands)
            if not all_names:
                if "creatures" in data and isinstance(data["creatures"], list):
                    all_names.extend(data["creatures"])
                if "non_creatures" in data and isinstance(data["non_creatures"], list):
                    all_names.extend(data["non_creatures"])
                if "spells" in data and isinstance(data["spells"], list):
                    all_names.extend(data["spells"])
                if "lands" in data and isinstance(data["lands"], list):
                    all_names.extend(data["lands"])
            if all_names:
                cards_arg = _cards_from_names(all_names)

        maybe_arg: list["Card"] = cls._cards_list_from_data(
            data["maybe"] if "maybe" in data and isinstance(data["maybe"], list) else []
        )
        sideboard_arg: list["Card"] = cls._cards_list_from_data(
            data["sideboard"] if "sideboard" in data and isinstance(data["sideboard"], list) else []
        )

        return cls(
            name=data["name"] if "name" in data else "",
            colors=data["colors"] if "colors" in data else None,
            description=data["description"] if "description" in data else "",
            format=data["format"] if "format" in data else "",
            colorless_only=data["colorless_only"] if "colorless_only" in data else False,
            cards=cards_arg,
            maybe=maybe_arg,
            sideboard=sideboard_arg,
        )

    @classmethod
    def from_export_text(cls, text: str, format: str) -> "Deck":
        """Parse decklist text and return a new Deck. Supports same formats as export(): arena, goldfish, moxfield, json.

        Args:
            text: Pasted decklist string.
            format: One of "arena", "goldfish", "moxfield", or "json".

        Returns:
            A new Deck instance with type lists (and optionally cards for json) populated.

        Raises:
            ValueError: If format is unsupported, text is invalid, or a card name is not found.
        """
        fmt: str = (format or "").strip().lower()
        raw: str = (text or "").strip()
        print("[Deck.from_export_text] fmt=%r raw_len=%d" % (fmt, len(raw)))
        if fmt not in cls.EXPORT_FORMATS:
            raise ValueError(
                f"unsupported import format {format!r}; use 'arena', 'goldfish', 'moxfield', or 'json'"
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
            by_type_arena: dict[str, list[str]] = {k: [] for k in _TYPE_KEYS}
            sideboard_names_arena: list[str] = []
            parsing_sideboard: bool = False
            for line in raw.splitlines():
                s_line: str = line.strip()
                if not s_line:
                    continue
                # Optional "Deck" header: skip. "Sideboard" or "Sideboard:" starts sideboard.
                head_lower: str = s_line.split(None, 1)[0].lower() if s_line else ""
                if head_lower in ("sideboard", "sideboard:"):
                    parsing_sideboard = True
                    continue
                if head_lower == "deck":
                    continue
                parts = s_line.split(None, 1)
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
                if parsing_sideboard:
                    for _ in range(count):
                        sideboard_names_arena.append(canonical_name)
                else:
                    if type_key in by_type_arena:
                        for _ in range(count):
                            by_type_arena[type_key].append(canonical_name)
                    else:
                        for _ in range(count):
                            by_type_arena["sorcery"].append(canonical_name)
            all_names_arena: list[str] = []
            for key in _TYPE_KEYS:
                all_names_arena.extend(by_type_arena[key])
            print("[Deck.from_export_text] arena names count=%d sideboard=%d" % (len(all_names_arena), len(sideboard_names_arena)))
            cards_arena: list["Card"] = _cards_from_names(all_names_arena) if all_names_arena else []
            sb_cards_arena: list["Card"] = _cards_from_names(sideboard_names_arena) if sideboard_names_arena else []
            print("[Deck.from_export_text] arena ok; cards=%d" % len(cards_arena))
            return cls(cards=cards_arena, sideboard=sb_cards_arena)

        if fmt == "goldfish":
            print("[Deck.from_export_text] parsing goldfish; lines=%d" % len(raw.splitlines()))
            by_type_g: dict[str, list[str]] = {k: [] for k in _TYPE_KEYS}
            sideboard_names_g: list[str] = []
            current_key: str | None = None
            for line in raw.splitlines():
                s: str = line.strip()
                if not s:
                    continue
                if s.startswith("//"):
                    label: str = s[2:].strip()
                    if label == _GOLDFISH_SIDEBOARD_LABEL:
                        current_key = "sideboard"
                    else:
                        current_key = _TYPE_LABEL_TO_KEY.get(label)
                    continue
                # Standalone "Sideboard" line (no "//") e.g. from Moxfield-style paste
                if s.lower() in _SIDEBOARD_LINE_LOWER:
                    current_key = "sideboard"
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
                canonical_g, type_key_g = _resolve_name_to_type_key(name_g)
                if current_key == "sideboard":
                    for _ in range(count_g):
                        sideboard_names_g.append(canonical_g)
                elif current_key is None:
                    target_key = type_key_g if type_key_g in by_type_g else "sorcery"
                    for _ in range(count_g):
                        by_type_g[target_key].append(canonical_g)
                else:
                    target_key = current_key if current_key in by_type_g else "sorcery"
                    for _ in range(count_g):
                        by_type_g[target_key].append(canonical_g)
            all_names_g: list[str] = []
            for key in _TYPE_KEYS:
                all_names_g.extend(by_type_g[key])
            print("[Deck.from_export_text] goldfish names count=%d sideboard=%d" % (len(all_names_g), len(sideboard_names_g)))
            cards_g: list["Card"] = _cards_from_names(all_names_g) if all_names_g else []
            sb_cards_g: list["Card"] = _cards_from_names(sideboard_names_g) if sideboard_names_g else []
            print("[Deck.from_export_text] goldfish ok; cards=%d" % len(cards_g))
            return cls(cards=cards_g, sideboard=sb_cards_g)

        if fmt == "moxfield":
            print("[Deck.from_export_text] parsing moxfield; lines=%d" % len(raw.splitlines()))
            main_names_m: list[str] = []
            sideboard_names_m: list[str] = []
            in_sideboard: bool = False
            for line in raw.splitlines():
                s_m: str = line.strip()
                if not s_m:
                    continue
                # Section headers (case-insensitive)
                first_word: str = s_m.split(None, 1)[0].lower() if s_m else ""
                if first_word == "sideboard" or first_word == "sideboard:":
                    in_sideboard = True
                    continue
                if s_m.lower() == "deck":
                    continue
                # Card line: "N Name" or "Nx Name" with optional " (SET) 123" and " *F*" or " F"
                parts_m: list[str] = s_m.split(None, 1)
                if len(parts_m) < 2:
                    continue
                count_str: str = parts_m[0].rstrip("xX")
                try:
                    count_m: int = int(count_str)
                except ValueError:
                    continue
                if count_m <= 0:
                    continue
                name_rest: str = parts_m[1].strip()
                # Strip optional " (SET) number" and foil markers
                if " (" in name_rest:
                    name_rest = name_rest.rsplit(" (", 1)[0].strip()
                if name_rest.endswith(" *F*"):
                    name_rest = name_rest[:-4].strip()
                if name_rest.endswith(" F"):
                    name_rest = name_rest[:-2].strip()
                if not name_rest:
                    continue
                canonical_m, _ = _resolve_name_to_type_key(name_rest)
                if in_sideboard:
                    for _ in range(count_m):
                        sideboard_names_m.append(canonical_m)
                else:
                    for _ in range(count_m):
                        main_names_m.append(canonical_m)
            cards_m: list["Card"] = _cards_from_names(main_names_m) if main_names_m else []
            sb_cards_m: list["Card"] = _cards_from_names(sideboard_names_m) if sideboard_names_m else []
            print("[Deck.from_export_text] moxfield ok; cards=%d sideboard=%d" % (len(cards_m), len(sb_cards_m)))
            return cls(cards=cards_m, sideboard=sb_cards_m)

        raise ValueError(
            f"unsupported import format {format!r}; use 'arena', 'goldfish', 'moxfield', or 'json'"
        )
