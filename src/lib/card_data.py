"""Card data loading, formatting, and filtering for the MTG MCP project."""

import json
from pathlib import Path

from src.lib.config import ATOMIC_CARDS_PATH
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Lazy singleton for flattened card data
# ---------------------------------------------------------------------------
_card_data: list[dict] | None = None


def get_card_data() -> list[dict]:
    """Lazy-load AtomicCards.json and return a flattened list of card-face dicts."""
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
        out: list[dict] = []
        for card_name, faces in data.items():
            if not isinstance(faces, list):
                continue
            for face in faces:
                if not isinstance(face, dict):
                    continue
                name = face.get("name") or card_name
                type_line = face.get("type") or ""
                types_list = face.get("types") or []
                subtypes_list = face.get("subtypes") or []
                supertypes_list = face.get("supertypes") or []
                text = face.get("text") or ""
                mana_cost = face.get("manaCost") or ""
                mana_val = face.get("manaValue")
                if mana_val is None:
                    mana_val = face.get("convertedManaCost")
                mana_value: float = float(mana_val) if mana_val is not None else 0.0
                colors_list = face.get("colors") or []
                color_identity_list = face.get("colorIdentity") or []
                power = face.get("power") or ""
                toughness = face.get("toughness") or ""
                keywords_list = face.get("keywords") or []
                loyalty = face.get("loyalty") or ""
                defense = face.get("defense") or ""
                legalities = face.get("legalities")
                if legalities is None or not isinstance(legalities, dict):
                    legalities = {}
                out.append({
                    "name": name,
                    "type": type_line,
                    "types": types_list,
                    "subtypes": subtypes_list,
                    "supertypes": supertypes_list,
                    "text": text,
                    "manaCost": mana_cost,
                    "manaValue": mana_value,
                    "colors": colors_list,
                    "colorIdentity": color_identity_list,
                    "power": power,
                    "toughness": toughness,
                    "keywords": keywords_list,
                    "loyalty": loyalty,
                    "defense": defense,
                    "legalities": legalities,
                })
        _card_data = out
        LOGGER.info("Card data loaded faces=%s path=%s", len(_card_data), ATOMIC_CARDS_PATH)
    return _card_data


def card_to_document(face: dict, card_name: str) -> str:
    """Build a single document string for one card face."""
    name: str = face.get("name") or card_name
    mana: str = face.get("manaCost") or ""
    type_line: str = face.get("type") or ""
    text: str | None = face.get("text")
    if text is None or (isinstance(text, str) and not text.strip()):
        text = "(No rules text)"
    return (
        f"Name: {name}\nMana Cost: {mana}\nType: {type_line}\nOracle Text: {text}"
    )


def make_id(card_name: str, face_index: int) -> str:
    """Build a unique ID from the dict key (guaranteed unique) and face index."""
    return f"{card_name}::{face_index}"


def _parse_colors(colors_str: str) -> set[str]:
    """Parse a comma-separated color string (e.g. 'W,U') into a set of single letters."""
    if not colors_str or not colors_str.strip():
        return set()
    return {c.strip().upper() for c in colors_str.split(",") if c.strip()}


def filter_cards(
    name: str = "",
    oracle_text: str = "",
    type_line: str = "",
    colors: str = "",
    color_identity: str = "",
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
    has_filter: bool = (
        bool(name.strip())
        or bool(oracle_text.strip())
        or bool(type_line.strip())
        or bool(colors.strip())
        or bool(color_identity.strip())
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
        LOGGER.error(0, "filter_cards: at least one filter parameter must be set")
        raise ValueError("filter_cards: at least one filter parameter must be set")

    cards: list[dict] = get_card_data()
    name_lower: str = name.strip().lower() if name else ""
    oracle_lower: str = oracle_text.strip().lower() if oracle_text else ""
    type_lower: str = type_line.strip().lower() if type_line else ""
    colors_filter: set[str] = _parse_colors(colors)
    color_identity_filter: set[str] = _parse_colors(color_identity)
    power_val: str = power.strip() if power else ""
    toughness_val: str = toughness.strip() if toughness else ""
    keywords_lower: str = keywords.strip().lower() if keywords else ""
    subtype_lower: str = subtype.strip().lower() if subtype else ""
    supertype_lower: str = supertype.strip().lower() if supertype else ""
    format_lower: str = format_legal.strip().lower() if format_legal else ""

    results: list[dict] = []
    for card in cards:
        if name_lower and name_lower not in (card.get("name") or "").lower():
            continue
        if oracle_lower and oracle_lower not in (card.get("text") or "").lower():
            continue
        if type_lower and type_lower not in (card.get("type") or "").lower():
            continue
        if colors_filter:
            card_colors: set[str] = set((c or "").upper() for c in (card.get("colors") or []))
            if card_colors != colors_filter:
                continue
        if color_identity_filter:
            card_identity: set[str] = set((c or "").upper() for c in (card.get("colorIdentity") or []))
            if not card_identity.issubset(color_identity_filter):
                continue
        mv = card.get("manaValue")
        if mv is None:
            mv = 0.0
        if mana_value >= 0 and mv != mana_value:
            continue
        if mana_value_min >= 0 and mv < mana_value_min:
            continue
        if mana_value_max >= 0 and mv > mana_value_max:
            continue
        if power_val and (card.get("power") or "").strip() != power_val:
            continue
        if toughness_val and (card.get("toughness") or "").strip() != toughness_val:
            continue
        if keywords_lower:
            card_kw: list[str] = [(k or "").lower() for k in (card.get("keywords") or [])]
            if keywords_lower not in card_kw and not any(keywords_lower in k for k in card_kw):
                continue
        if subtype_lower:
            card_sub: list[str] = [(s or "").lower() for s in (card.get("subtypes") or [])]
            if subtype_lower not in card_sub and not any(subtype_lower in s for s in card_sub):
                continue
        if supertype_lower:
            card_super: list[str] = [(s or "").lower() for s in (card.get("supertypes") or [])]
            if supertype_lower not in card_super and not any(supertype_lower in s for s in card_super):
                continue
        if format_lower:
            leg = card.get("legalities") or {}
            if not isinstance(leg, dict):
                continue
            legal_val: str = ""
            for k, v in leg.items():
                if k.lower() == format_lower and v:
                    legal_val = (v if isinstance(v, str) else str(v)).lower()
                    break
            if legal_val != "legal":
                continue

        results.append(card)
        if len(results) >= n_results:
            break

    parts: list[str] = []
    for i, card in enumerate(results, 1):
        cname: str = card.get("name") or "Unknown"
        mana: str = card.get("manaCost") or ""
        ctype: str = card.get("type") or ""
        text: str = (card.get("text") or "").strip() or "(No rules text)"
        parts.append(
            f"--- Card {i} of {len(results)} ---\n"
            f"Name: {cname}\nMana Cost: {mana}\nType: {ctype}\nOracle Text: {text}"
        )
    return "\n\n".join(parts) if parts else "No cards found."
