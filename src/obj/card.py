"""Card dataclass -- single source of truth for MTG card data representation."""

import dataclasses
import json
import re
from dataclasses import dataclass, field, fields
from typing import Any

from src.config.keyword_explanations import expand_keywords as _expand_keywords
from src.lib.config import KEYWORD_EXPLANATIONS_PATH
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Compiled regexes for ability parsing (module-level for reuse)
# ---------------------------------------------------------------------------
_REX_TRIGGERED = re.compile(
    r"^(When|Whenever|At)\s+(.+?),\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_REX_ACTIVATED = re.compile(r"^(.+?):\s*(.+)$", re.DOTALL)
_REX_LOYALTY_COST = re.compile(r"^[+\-\u2212]\d+$|^0$")
_REX_MANA_SYMBOL = re.compile(r"\{[^}]+\}")

_KNOWN_KEYWORDS: set[str] | None = None


@dataclass(frozen=True)
class MechanicSignature:
    """Normalized graph-facing mechanics for one canonical card face.

    ``provides`` are outcomes this card creates, ``needs`` are events/resources
    its abilities consume or reward, and ``traits`` are durable attributes that
    make it part of a deck package.  The compact controlled vocabulary makes
    relationship generation deterministic and inspectable.
    """

    provides: frozenset[str]
    needs: frozenset[str]
    traits: frozenset[str]


def _get_known_keywords() -> set[str]:
    """Return set of keyword names from keyword_explanations.json. Cached."""
    global _KNOWN_KEYWORDS
    if _KNOWN_KEYWORDS is None:
        if not KEYWORD_EXPLANATIONS_PATH.is_file():
            LOGGER.error("_get_known_keywords: required file not found: %s", KEYWORD_EXPLANATIONS_PATH)
            raise FileNotFoundError(f"Required keyword vocabulary not found: {KEYWORD_EXPLANATIONS_PATH}")
        else:
            with open(KEYWORD_EXPLANATIONS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            _KNOWN_KEYWORDS = {k.strip() for k in data if isinstance(k, str) and k.strip()}
            LOGGER.debug("_get_known_keywords: loaded %d keywords from %s", len(_KNOWN_KEYWORDS), KEYWORD_EXPLANATIONS_PATH)
    return _KNOWN_KEYWORDS


def _match_keyword_line(
    line: str, known_kw: set[str], card_keywords: list[str],
) -> tuple[str, str] | None:
    """If *line* is a known keyword optionally followed by a parameter, return ``(keyword, param)``.

    Matches longest keyword first so e.g. "Flashback" beats "Flash".
    Returns ``None`` when *line* does not start with any known keyword.
    """
    all_kw = known_kw | set(card_keywords or [])
    for kw in sorted(all_kw, key=len, reverse=True):
        kw_lower = kw.lower()
        line_lower = line.lower()
        if line_lower == kw_lower:
            return (kw, "")
        if line_lower.startswith(kw_lower) and len(line) > len(kw):
            sep = line[len(kw)]
            if sep in (" ", "\u2014", "\u2013", "-"):
                param = line[len(kw):].strip().lstrip("\u2014\u2013- ").strip()
                return (kw, param)
    return None


def _parse_activated_cost(cost_str: str) -> list[str]:
    """Parse the cost side of an activated ability into trigger phrases."""
    cost = (cost_str or "").strip()
    if not cost:
        return []
    triggers: list[str] = []

    if _REX_LOYALTY_COST.match(cost):
        triggers.append(f"Loyalty {cost.replace(chr(0x2212), '-')}")
        return triggers

    if re.search(r"\{T\}", cost, re.IGNORECASE):
        triggers.append("Tap this card")
    if re.search(r"\{Q\}", cost, re.IGNORECASE):
        triggers.append("Untap this card")

    all_braces = _REX_MANA_SYMBOL.findall(cost)
    mana_parts = [m for m in all_braces if m.upper() not in ("{T}", "{Q}")]
    if mana_parts:
        triggers.append("Pay " + "".join(mana_parts))

    rest = re.sub(r"\{[^}]+\}", "", cost)
    rest = re.sub(r"[,;]\s*", " ", rest).strip()
    if rest:
        triggers.append(rest)
    return triggers


def _field_default(f: dataclasses.Field) -> Any:
    """Return the effective default value for a dataclass field."""
    if f.default is not dataclasses.MISSING:
        return f.default
    assert f.default_factory is not dataclasses.MISSING, f"Field {f.name} has no default"
    return f.default_factory()


def _normalize_face_names(raw_face_names: list[str], fallback_name: str) -> list[str]:
    cleaned: list[str] = []
    for raw in raw_face_names:
        face = (raw or "").strip()
        if face:
            cleaned.append(face)
    if not cleaned:
        cleaned = [fallback_name]
    unique: list[str] = []
    seen_lower: set[str] = set()
    for face in cleaned:
        lowered = face.lower()
        if lowered not in seen_lower:
            seen_lower.add(lowered)
            unique.append(face)
    if len(unique) == 1 and " // " in unique[0]:
        split_faces: list[str] = [p.strip() for p in unique[0].split(" // ") if p.strip()]
        if split_faces:
            return split_faces
    return unique


@dataclass
class Card:
    """Represents a Magic: The Gathering card (one face).

    Field metadata drives implicit JSON parsing and display formatting via
    ``fields()`` iteration -- adding a new field
    automatically propagates through the whole pipeline.

    Metadata keys per field:
        json_key      -- key in AtomicCards.json (required for MTGJSON-sourced fields)
        fallback_key  -- secondary JSON key tried when the primary is None
        source        -- "derived" for fields not in MTGJSON (e.g. triggers, effects)
    """

    name: str = field(default="", metadata={"json_key": "name"})
    type_line: str = field(default="", metadata={"json_key": "type"})
    types: list[str] = field(default_factory=list, metadata={"json_key": "types"})
    subtypes: list[str] = field(default_factory=list, metadata={"json_key": "subtypes"})
    supertypes: list[str] = field(default_factory=list, metadata={"json_key": "supertypes"})
    text: str = field(default="", metadata={"json_key": "text"})
    mana_cost: str = field(default="", metadata={"json_key": "manaCost"})
    mana_value: float = field(
        default=0.0,
        metadata={"json_key": "manaValue", "fallback_key": "convertedManaCost"},
    )
    colors: list[str] = field(default_factory=list, metadata={"json_key": "colors"})
    color_identity: list[str] = field(default_factory=list, metadata={"json_key": "colorIdentity"})
    power: str = field(default="", metadata={"json_key": "power"})
    toughness: str = field(default="", metadata={"json_key": "toughness"})
    keywords: list[str] = field(default_factory=list, metadata={"json_key": "keywords"})
    loyalty: str = field(default="", metadata={"json_key": "loyalty"})
    defense: str = field(default="", metadata={"json_key": "defense"})
    edhrec_rank: int = field(default=0, metadata={"json_key": "edhrecRank"})
    legalities: dict[str, str] = field(
        default_factory=dict,
        metadata={"json_key": "legalities"},
    )
    triggers: list[str] = field(
        default_factory=list,
        metadata={"source": "derived"},
    )
    effects: list[str] = field(
        default_factory=list,
        metadata={"source": "derived"},
    )
    price_usd: float = field(
        default=-1.0,
        metadata={"source": "price"},
    )
    # Two-sided identity fields (derived from AtomicCards row context).
    card_name: str = field(default="", metadata={"json_key": "cardName"})
    face_name: str = field(default="", metadata={"json_key": "faceName"})
    canonical_name: str = field(default="", metadata={"json_key": "canonicalName"})
    face_index: int = field(default=0, metadata={"json_key": "faceIndex"})
    face_count: int = field(default=1, metadata={"json_key": "faceCount"})
    face_names: list[str] = field(default_factory=list, metadata={"json_key": "faceNames"})

    def __iter__(self):
        for f in fields(self):
            yield f.name, getattr(self, f.name)

    def __post_init__(self) -> None:
        if not self.face_name:
            self.face_name = self.name
        if not self.face_names:
            if self.canonical_name and " // " in self.canonical_name:
                self.face_names = [p.strip() for p in self.canonical_name.split(" // ") if p.strip()]
            elif self.face_name:
                self.face_names = [self.face_name]
        if self.face_count < 1:
            if self.face_names:
                self.face_count = len(self.face_names)
            else:
                self.face_count = 1
        if not self.canonical_name:
            if self.face_count > 1 and self.face_names:
                self.canonical_name = " // ".join(self.face_names)
            elif self.face_name:
                self.canonical_name = self.face_name
            else:
                self.canonical_name = self.name
        if not self.card_name:
            self.card_name = self.canonical_name
        if not self.name:
            self.name = self.face_name or self.canonical_name

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_json_face(
        cls,
        face: dict,
        card_name: str,
        face_index: int = 0,
        face_count: int = 1,
        face_names: list[str] | None = None,
    ) -> "Card":
        """Parse a raw JSON face dict from AtomicCards.json into a Card."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.metadata.get("source") in ("derived", "price"):
                continue
            json_key: str = f.metadata["json_key"]
            raw = face.get(json_key)
            if raw is None and "fallback_key" in f.metadata:
                raw = face.get(f.metadata["fallback_key"])

            default = _field_default(f)

            if isinstance(default, float):
                kwargs[f.name] = float(raw) if raw is not None else default
            elif isinstance(default, int):
                kwargs[f.name] = int(raw) if raw is not None else default
            elif isinstance(default, list):
                kwargs[f.name] = raw if isinstance(raw, list) else []
            elif isinstance(default, dict):
                kwargs[f.name] = raw if isinstance(raw, dict) else {}
            else:
                kwargs[f.name] = raw or default

        if not kwargs["name"]:
            kwargs["name"] = card_name
        resolved_face_names: list[str] = _normalize_face_names(
            list(face_names) if face_names else [kwargs["name"]],
            kwargs["name"],
        )
        resolved_face_count: int = face_count if face_count > 0 else len(resolved_face_names)
        if resolved_face_count < len(resolved_face_names):
            resolved_face_count = len(resolved_face_names)
        face_name: str = kwargs["name"]
        if resolved_face_names and resolved_face_count > 1:
            idx = face_index
            if idx < 0:
                idx = 0
            if idx >= len(resolved_face_names):
                idx = len(resolved_face_names) - 1
            face_name = resolved_face_names[idx]
            kwargs["name"] = face_name
        kwargs["card_name"] = card_name
        kwargs["face_name"] = face_name
        kwargs["face_index"] = face_index
        kwargs["face_count"] = resolved_face_count
        kwargs["face_names"] = resolved_face_names
        if kwargs["face_count"] > 1:
            kwargs["canonical_name"] = " // ".join(resolved_face_names)
        else:
            kwargs["canonical_name"] = resolved_face_names[0]
        return cls(**kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Card":
        """Create a Card from a dict with field-name keys."""
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name in data:
                kwargs[f.name] = data[f.name]
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Serialization / display
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize this Card to a plain dict keyed by field name."""
        result: dict[str, Any] = {}
        for name, value in self:
            if isinstance(value, list):
                result[name] = list(value)
            elif isinstance(value, dict):
                result[name] = dict(value)
            else:
                result[name] = value
        return result

    def to_document(self, template: str) -> str:
        """Build a document string by filling *template* with this card's fields."""
        values = self.to_dict()
        if not values["text"].strip():
            values["text"] = "(No rules text)"
        return template.format_map(values)

    def normalize_oracle_text(self) -> str:
        """Return oracle text normalized for RAG: card name replaced with 'this card', keywords expanded with explanations."""
        raw = self.text.strip()
        if not raw:
            return "(Vanilla)"
        if not self.name:
            text = raw
        else:
            text = re.sub(re.escape(self.name), "this card", raw, flags=re.IGNORECASE)
        return _expand_keywords(text)

    def _derive_mechanics(self) -> None:
        """Populate self.triggers and self.effects from oracle text, type line, and intrinsic properties."""
        raw = (self.text or "").strip()
        if self.name:
            text = re.sub(re.escape(self.name), "this card", raw, flags=re.IGNORECASE)
        else:
            text = raw
        statements: list[str] = [s.strip() for s in re.split(r"[\n\u2022]", text) if s.strip()]

        triggers_out: list[str] = []
        effects_out: list[str] = []
        known_kw = _get_known_keywords()

        for line in statements:
            # --- 1. Triggered ability: "When/Whenever/At <condition>, <effect>" ---
            triggered_match = _REX_TRIGGERED.match(line)
            if triggered_match:
                when_word = triggered_match.group(1).strip()
                condition = triggered_match.group(2).strip()
                effect_part = triggered_match.group(3).strip()
                triggers_out.append(f"{when_word} {condition}")
                if effect_part:
                    effects_out.append(effect_part)
                continue

            # --- 2. Keyword ability (single or comma-separated, with optional parameter) ---
            comma_parts = [p.strip() for p in line.split(",") if p.strip()]
            if comma_parts and all(
                _match_keyword_line(p, known_kw, self.keywords) is not None
                for p in comma_parts
            ):
                for p in comma_parts:
                    result = _match_keyword_line(p, known_kw, self.keywords)
                    assert result is not None
                    kw_name, kw_param = result
                    mana_syms = _REX_MANA_SYMBOL.findall(kw_param) if kw_param else []
                    if mana_syms:
                        effects_out.append(kw_name)
                        triggers_out.append(f"Pay {''.join(mana_syms)}")
                    elif kw_param:
                        effects_out.append(f"{kw_name} {kw_param}")
                    else:
                        effects_out.append(kw_name)
                continue

            # --- 3. Activated ability: "cost: effect" ---
            activated_match = _REX_ACTIVATED.match(line)
            if activated_match:
                cost_part = activated_match.group(1).strip()
                effect_part = activated_match.group(2).strip()
                triggers_out.extend(_parse_activated_cost(cost_part))
                if effect_part:
                    effects_out.append(effect_part)
                continue

            # --- 4. Static ability (fallback): effect only, no trigger ---
            effects_out.append(line)

        # --- 5. Intrinsic card properties ---
        if self.mana_cost:
            triggers_out.append(f"Cast for {self.mana_cost}")
        for t in self.types:
            effects_out.append(t)
        for st in self.subtypes:
            effects_out.append(st)
        for spt in self.supertypes:
            effects_out.append(spt)
        if "Creature" in self.types:
            if self.power:
                effects_out.append(f"Has Power {self.power}")
            if self.toughness:
                effects_out.append(f"Has Toughness {self.toughness}")

        self.triggers = triggers_out
        self.effects = effects_out

    def get_triggers(self) -> list[str]:
        """Return this card's triggers (causes). Derives mechanics from oracle text if not yet computed."""
        if (self.text or self.type_line) and not self.triggers and not self.effects:
            self._derive_mechanics()
        return list(self.triggers)

    def get_effects(self) -> list[str]:
        """Return this card's effects (results). Derives mechanics from oracle text if not yet computed."""
        if (self.text or self.type_line) and not self.triggers and not self.effects:
            self._derive_mechanics()
        return list(self.effects)

    def mechanic_signature(self) -> MechanicSignature:
        """Return deterministic, normalized mechanics used by graph retrieval.

        This deliberately describes broad deck-building relationships rather
        than attempting to model every Comprehensive Rules interaction.  Each
        token is sourced from this card's own oracle text, extracted mechanics,
        keywords, or type data so a recommendation can cite its evidence.
        """
        trigger_text: str = " ".join(self.get_triggers()).lower()
        effect_text: str = " ".join(self.get_effects()).lower()
        rules_text: str = f"{trigger_text} {effect_text} {self.normalize_oracle_text().lower()}"

        provides: set[str] = set()
        needs: set[str] = set()
        traits: set[str] = set()

        for card_type in self.types:
            if card_type.strip():
                traits.add(f"type:{card_type.strip().lower()}")
        for subtype in self.subtypes:
            if subtype.strip():
                traits.add(f"subtype:{subtype.strip().lower()}")
        controlled_keywords = {keyword.casefold() for keyword in _get_known_keywords()}
        for keyword in self.keywords:
            if keyword.strip() and keyword.casefold() in controlled_keywords:
                traits.add(f"keyword:{keyword.strip().lower()}")
        for color in self.color_identity:
            if color.strip():
                traits.add(f"color:{color.strip().upper()}")

        outcome_patterns: tuple[tuple[str, str], ...] = (
            ("draw", "draw_cards"),
            ("create", "create_tokens"),
            ("add {", "produce_mana"),
            ("add one mana", "produce_mana"),
            ("search your library", "tutor"),
            ("return target", "recursion"),
            ("return ", "recursion"),
            ("put a +1/+1 counter", "plus_one_counters"),
            ("proliferate", "proliferate"),
            ("destroy target", "remove_permanent"),
            ("exile target", "remove_permanent"),
            ("counter target spell", "counter_spell"),
            ("hexproof", "protect_permanents"),
            ("indestructible", "protect_permanents"),
            ("phase out", "protect_permanents"),
            ("protection from", "protect_permanents"),
            ("gain ", "gain_life"),
            ("deals ", "deal_damage"),
            ("mill", "mill_cards"),
        )
        for needle, token in outcome_patterns:
            if needle in effect_text:
                provides.add(token)
        if "graveyard" in rules_text and (
            "return" in rules_text or "onto the battlefield" in rules_text
        ):
            provides.add("recursion")

        trigger_patterns: tuple[tuple[str, str], ...] = (
            ("a creature dies", "creature_dies"),
            ("another creature dies", "creature_dies"),
            ("sacrifice a creature", "creature_sacrifice"),
            ("creature enters the battlefield", "creature_etb"),
            ("another creature enters the battlefield", "creature_etb"),
            ("a land enters the battlefield", "land_etb"),
            ("cast an instant or sorcery", "instant_sorcery_cast"),
            ("cast a spell", "spell_cast"),
            ("draw a card", "draw_cards"),
            ("create", "create_tokens"),
            ("put a +1/+1 counter", "plus_one_counters"),
            ("gain life", "gain_life"),
            ("discard", "discard_cards"),
            ("cards leave your graveyard", "graveyard_exit"),
        )
        for needle, token in trigger_patterns:
            if needle in trigger_text:
                needs.add(token)
        if "creature enters" in trigger_text:
            needs.add("creature_etb")
        if re.search(r"\bcreatures?\b.*\b(?:die|dies)\b", trigger_text):
            needs.add("creature_dies")
        if "instant or sorcery" in trigger_text and (
            "cast" in trigger_text or "copy" in trigger_text
        ):
            needs.add("instant_sorcery_cast")

        # Activated costs create an explicit resource need even when the
        # effect parser has already categorized the resulting ability.
        if "pay {" in trigger_text:
            needs.add("mana")
        if "tap this card" in trigger_text:
            needs.add("untapped_permanent")
        if "sacrifice" in trigger_text:
            needs.add("creature_sacrifice")

        if "landfall" in rules_text:
            needs.add("land_etb")
        if "magecraft" in rules_text or "prowess" in rules_text:
            needs.add("instant_sorcery_cast")
        if "morbid" in rules_text:
            needs.add("creature_dies")

        return MechanicSignature(
            provides=frozenset(provides),
            needs=frozenset(needs),
            traits=frozenset(traits),
        )

    def to_graph_description(self) -> str:
        """Create a provenance-rich text unit for deterministic graph indexing."""
        signature: MechanicSignature = self.mechanic_signature()
        display_name: str = self.canonical_name or self.name
        return "\n".join(
            [
                f"Card: {display_name}",
                f"Type: {self.type_line}",
                f"Oracle Text: {self.normalize_oracle_text()}",
                f"Provides: {', '.join(sorted(signature.provides)) or '(none)'}",
                f"Needs: {', '.join(sorted(signature.needs)) or '(none)'}",
                f"Traits: {', '.join(sorted(signature.traits)) or '(none)'}",
            ]
        )

    def format_display(self, index: int, total: int) -> str:
        """Format this card for display in search / filter results."""
        text: str = self.text.strip() or "(No rules text)"
        price_str: str = f"${self.price_usd:.2f}" if self.price_usd >= 0 else "N/A"
        display_name: str = self.canonical_name if self.canonical_name else self.name
        return (
            f"--- Card {index} of {total} ---\n"
            f"Name: {display_name}\nMana Cost: {self.mana_cost}\nType: {self.type_line}\n"
            f"Price (USD): {price_str}\nOracle Text: {text}"
        )
