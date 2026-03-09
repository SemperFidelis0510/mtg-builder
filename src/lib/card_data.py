"""Card data loading and filtering for the MTG MCP project."""

import json

from src.lib.config import ATOMIC_CARDS_PATH
from src.obj.card import Card
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Lazy singleton for flattened card data
# ---------------------------------------------------------------------------
_card_data: list[Card] | None = None


def get_card_data() -> list[Card]:
    """Lazy-load AtomicCards.json and return a flattened list of Card objects."""
    global _card_data
    if _card_data is None:
        if not ATOMIC_CARDS_PATH.is_file():
            LOGGER.error(0, "get_card_data: required file not found: %s", ATOMIC_CARDS_PATH)
            raise FileNotFoundError(f"get_card_data: required file not found: {ATOMIC_CARDS_PATH}")
        with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        data = raw.get("data")
        if data is None:
            LOGGER.error(0, "get_card_data: AtomicCards.json missing 'data' key")
            raise ValueError("get_card_data: AtomicCards.json missing 'data' key")
        out: list[Card] = []
        for card_name, faces in data.items():
            if not isinstance(faces, list):
                continue
            for face in faces:
                if not isinstance(face, dict):
                    continue
                out.append(Card.from_json_face(face, card_name))
        _card_data = out
        LOGGER.info("Card data loaded faces=%s path=%s", len(_card_data), ATOMIC_CARDS_PATH)
    return _card_data


def make_id(card_name: str, face_index: int) -> str:
    """Build a unique ID from the dict key (guaranteed unique) and face index."""
    return f"{card_name}::{face_index}"


def _parse_colors(colors_str: str) -> set[str]:
    """Parse a comma-separated color string (e.g. 'W,U') into a set of single letters."""
    if not colors_str or not colors_str.strip():
        return set()
    return {c.strip().upper() for c in colors_str.split(",") if c.strip()}


def filter_cards_list(
    name: str = "",
    oracle_text: str | list[str] = "",
    type_line: str = "",
    colors: str = "",
    color_identity: str = "",
    color_identity_colorless: bool = False,
    colorless_only: bool = False,
    mana_value: float = -1.0,
    mana_value_min: float = -1.0,
    mana_value_max: float = -1.0,
    power: str = "",
    toughness: str = "",
    keywords: str = "",
    subtype: str = "",
    supertype: str = "",
    format_legal: str = "",
    n_results: int = 20,
    offset: int = 0,
) -> list[Card]:
    """Filter MTG cards by exact/filter properties. All filters are AND-combined. Returns list of Card. At least one filter must be set. offset/n_results support pagination."""
    _oracle_list: list[str] = (
        [s.strip() for s in oracle_text] if isinstance(oracle_text, list) else [oracle_text.strip()] if oracle_text else []
    )
    _oracle_list = [s for s in _oracle_list if s]
    has_filter: bool = (
        bool(name.strip())
        or bool(_oracle_list)
        or bool(type_line.strip())
        or bool(colors.strip())
        or bool(color_identity.strip())
        or color_identity_colorless
        or colorless_only
        or mana_value >= 0
        or mana_value_min >= 0
        or mana_value_max >= 0
        or bool(power.strip())
        or bool(toughness.strip())
        or bool(keywords.strip())
        or bool(subtype.strip())
        or bool(supertype.strip())
        or bool(format_legal.strip())
    )
    if not has_filter:
        LOGGER.error(0, "filter_cards_list: at least one filter parameter must be set")
        raise ValueError("filter_cards_list: at least one filter parameter must be set")

    cards: list[Card] = get_card_data()
    name_lower: str = name.strip().lower() if name else ""
    oracle_lower_list: list[str] = [s.lower() for s in _oracle_list]
    type_lower: str = type_line.strip().lower() if type_line else ""
    colors_filter: set[str] = _parse_colors(colors)
    color_identity_filter: set[str] = _parse_colors(color_identity)
    power_val: str = power.strip() if power else ""
    toughness_val: str = toughness.strip() if toughness else ""
    keywords_lower: str = keywords.strip().lower() if keywords else ""
    subtype_lower: str = subtype.strip().lower() if subtype else ""
    supertype_lower: str = supertype.strip().lower() if supertype else ""
    format_lower: str = format_legal.strip().lower() if format_legal else ""

    results: list[Card] = []
    skipped: int = 0
    for card in cards:
        if name_lower and name_lower not in card.name.lower():
            continue
        if oracle_lower_list:
            card_text_lower: str = card.text.lower()
            if not all(phrase in card_text_lower for phrase in oracle_lower_list):
                continue
        if type_lower and type_lower not in card.type_line.lower():
            continue
        if colors_filter:
            card_colors: set[str] = {c.upper() for c in card.colors}
            if card_colors != colors_filter:
                continue
        if color_identity_filter or color_identity_colorless:
            card_identity: set[str] = {c.upper() for c in card.color_identity}
            if color_identity_filter and color_identity_colorless:
                if not card_identity.issubset(color_identity_filter) and len(card_identity) > 0:
                    continue
            elif color_identity_filter:
                if not card_identity.issubset(color_identity_filter):
                    continue
            elif color_identity_colorless:
                if len(card_identity) > 0:
                    continue
        if colorless_only and len(card.colors) > 0:
            continue
        if mana_value >= 0 and card.mana_value != mana_value:
            continue
        if mana_value_min >= 0 and card.mana_value < mana_value_min:
            continue
        if mana_value_max >= 0 and card.mana_value > mana_value_max:
            continue
        if power_val and card.power.strip() != power_val:
            continue
        if toughness_val and card.toughness.strip() != toughness_val:
            continue
        if keywords_lower:
            card_kw: list[str] = [k.lower() for k in card.keywords]
            if keywords_lower not in card_kw and not any(keywords_lower in k for k in card_kw):
                continue
        if subtype_lower:
            card_sub: list[str] = [s.lower() for s in card.subtypes]
            if subtype_lower not in card_sub and not any(subtype_lower in s for s in card_sub):
                continue
        if supertype_lower:
            card_super: list[str] = [s.lower() for s in card.supertypes]
            if supertype_lower not in card_super and not any(supertype_lower in s for s in card_super):
                continue
        if format_lower:
            legal_val: str = ""
            for k, v in card.legalities.items():
                if k.lower() == format_lower and v:
                    legal_val = (v if isinstance(v, str) else str(v)).lower()
                    break
            if legal_val != "legal":
                continue

        if skipped < offset:
            skipped += 1
            continue
        results.append(card)
        if len(results) >= n_results:
            break

    return results


def filter_cards(
    name: str = "",
    oracle_text: str = "",
    type_line: str = "",
    colors: str = "",
    color_identity: str = "",
    color_identity_colorless: bool = False,
    colorless_only: bool = False,
    mana_value: float = -1.0,
    mana_value_min: float = -1.0,
    mana_value_max: float = -1.0,
    power: str = "",
    toughness: str = "",
    keywords: str = "",
    subtype: str = "",
    supertype: str = "",
    format_legal: str = "",
    n_results: int = 20,
) -> str:
    """Filter MTG cards by exact/filter properties. All filters are AND-combined. At least one filter must be set."""
    results: list[Card] = filter_cards_list(
        name=name,
        oracle_text=oracle_text,
        type_line=type_line,
        colors=colors,
        color_identity=color_identity,
        color_identity_colorless=color_identity_colorless,
        colorless_only=colorless_only,
        mana_value=mana_value,
        mana_value_min=mana_value_min,
        mana_value_max=mana_value_max,
        power=power,
        toughness=toughness,
        keywords=keywords,
        subtype=subtype,
        supertype=supertype,
        format_legal=format_legal,
        n_results=n_results,
    )
    parts: list[str] = [card.format_display(i, len(results)) for i, card in enumerate(results, 1)]
    return "\n\n".join(parts) if parts else "No cards found."
