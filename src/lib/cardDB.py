"""Card database: loading, filtering, and RAG semantic search for the MTG MCP project."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import fields as _dc_fields
from typing import Any

from src.lib.config import (
    ATOMIC_CARDS_PATH,
    CHROMA_PATH,
    COLLECTION_NAME,
    EFFECTS_COLLECTION_NAME,
    MODEL_NAME,
    TRIGGERS_COLLECTION_NAME,
)
from src.lib.prices import load_prices
from src.obj.card import Card
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# CardDB singleton: AtomicCards + ChromaDB + embedding model
# ---------------------------------------------------------------------------

# Max Chroma rows to pull when combining semantic ranking with structural filters.
_CHROMA_SEMANTIC_FILTER_CAP: int = 25000


class CardDB:
    """Unified card database: lazy-loaded AtomicCards list, structured filtering, and RAG semantic search."""

    _instance: CardDB | None = None

    @classmethod
    def inst(cls) -> CardDB:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._card_data: list[Card] | None = None
        self._name_to_card: dict[str, Card] | None = None
        self._name_to_faces: dict[str, list[Card]] | None = None
        self._canonical_to_faces: dict[str, list[Card]] | None = None
        self._embedding_model = None
        self._chroma_client = None
        self._collections: dict[str, object] = {}
        self._rag_lock: threading.Lock = threading.Lock()
        self._rag_ready: bool = False

    # -----------------------------------------------------------------------
    # AtomicCards loading and filtering
    # -----------------------------------------------------------------------

    def get_card_data(self) -> list[Card]:
        """Lazy-load AtomicCards.json and return a flattened list of Card objects."""
        if self._card_data is None:
            if not ATOMIC_CARDS_PATH.is_file():
                LOGGER.error( "get_card_data: required file not found: %s", ATOMIC_CARDS_PATH)
                raise FileNotFoundError(f"get_card_data: required file not found: {ATOMIC_CARDS_PATH}")
            with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            data = raw.get("data")
            if data is None:
                LOGGER.error( "get_card_data: AtomicCards.json missing 'data' key")
                raise ValueError("get_card_data: AtomicCards.json missing 'data' key")
            out: list[Card] = []
            for card_name, faces in data.items():
                if not isinstance(faces, list):
                    continue
                face_names: list[str] = []
                for face in faces:
                    if not isinstance(face, dict):
                        continue
                    if "name" in face and isinstance(face["name"], str) and face["name"].strip():
                        face_names.append(face["name"].strip())
                    else:
                        face_names.append(card_name)
                if not face_names:
                    face_names = [card_name]
                face_count: int = len(face_names)
                face_pos: int = 0
                for face in faces:
                    if not isinstance(face, dict):
                        continue
                    out.append(
                        Card.from_json_face(
                            face=face,
                            card_name=card_name,
                            face_index=face_pos,
                            face_count=face_count,
                            face_names=face_names,
                        )
                    )
                    face_pos += 1
            self._card_data = out
            price_map: dict[str, float] = load_prices()
            for c in self._card_data:
                if c.card_name in price_map:
                    c.price_usd = price_map[c.card_name]
                elif c.canonical_name in price_map:
                    c.price_usd = price_map[c.canonical_name]
                elif c.name in price_map:
                    c.price_usd = price_map[c.name]
                else:
                    c.price_usd = -1.0
            LOGGER.info("Card data loaded faces=%s path=%s", len(self._card_data), ATOMIC_CARDS_PATH)
        return self._card_data

    def reload_prices(self) -> None:
        """Re-read prices.json and update price_usd on all loaded cards. No-op if card data not yet loaded."""
        if self._card_data is None:
            return
        price_map: dict[str, float] = load_prices()
        for c in self._card_data:
            if c.card_name in price_map:
                c.price_usd = price_map[c.card_name]
            elif c.canonical_name in price_map:
                c.price_usd = price_map[c.canonical_name]
            elif c.name in price_map:
                c.price_usd = price_map[c.name]
            else:
                c.price_usd = -1.0
        LOGGER.info("reload_prices: updated %s cards", len(self._card_data))

    def _build_name_indexes(self) -> None:
        if self._name_to_card is not None and self._name_to_faces is not None and self._canonical_to_faces is not None:
            return
        cards: list[Card] = self.get_card_data()
        canonical_to_faces: dict[str, list[Card]] = {}
        for card in cards:
            canonical: str = card.canonical_name if card.canonical_name else card.name
            if canonical not in canonical_to_faces:
                canonical_to_faces[canonical] = []
            canonical_to_faces[canonical].append(card)

        alias_to_primary: dict[str, Card] = {}
        alias_to_faces: dict[str, list[Card]] = {}
        for canonical, faces in canonical_to_faces.items():
            ordered_faces: list[Card] = sorted(faces, key=lambda c: c.face_index)
            primary: Card = ordered_faces[0]
            aliases: set[str] = set()
            aliases.add(canonical.lower())
            for face in ordered_faces:
                aliases.add(face.name.lower())
                aliases.add(face.face_name.lower())
                for fname in face.face_names:
                    aliases.add(fname.lower())
            for alias in aliases:
                if alias not in alias_to_primary:
                    alias_to_primary[alias] = primary
                if alias not in alias_to_faces:
                    alias_to_faces[alias] = ordered_faces

        self._name_to_card = alias_to_primary
        self._name_to_faces = alias_to_faces
        self._canonical_to_faces = canonical_to_faces
        LOGGER.info("Name indexes built aliases=%s canonicals=%s", len(alias_to_primary), len(canonical_to_faces))

    def _get_name_to_card(self) -> dict[str, Card]:
        """Lazy-build case-insensitive alias -> primary face lookup."""
        self._build_name_indexes()
        assert self._name_to_card is not None, "_build_name_indexes must initialize _name_to_card"
        return self._name_to_card

    def resolve_primary_card(self, name: str) -> Card:
        """Resolve any alias (front/back/combined) to the primary face Card."""
        if not name or not name.strip():
            LOGGER.error("resolve_primary_card: name is empty")
            raise ValueError("resolve_primary_card: name is empty")
        key: str = name.strip().lower()
        name_map: dict[str, Card] = self._get_name_to_card()
        if key not in name_map:
            LOGGER.error("resolve_primary_card: card not found: %r", name)
            raise ValueError(f"Card not found: {name!r}")
        return name_map[key]

    def try_resolve_primary_card(self, name: str) -> Card | None:
        """Resolve name to primary face Card, or None if unknown. Does not log."""
        if not name or not name.strip():
            return None
        key: str = name.strip().lower()
        name_map: dict[str, Card] = self._get_name_to_card()
        return name_map[key] if key in name_map else None

    def resolve_faces(self, name: str) -> list[Card]:
        """Resolve any alias (front/back/combined) to all faces of the card."""
        if not name or not name.strip():
            LOGGER.error("resolve_faces: name is empty")
            raise ValueError("resolve_faces: name is empty")
        self._build_name_indexes()
        assert self._name_to_faces is not None, "_build_name_indexes must initialize _name_to_faces"
        key: str = name.strip().lower()
        if key not in self._name_to_faces:
            LOGGER.error("resolve_faces: card not found: %r", name)
            raise ValueError(f"Card not found: {name!r}")
        return list(self._name_to_faces[key])

    @staticmethod
    def card_display_name(card: Card) -> str:
        if card.canonical_name:
            return card.canonical_name
        return card.name

    @staticmethod
    def _name_query_matches_card(card: Card, query_lower: str) -> bool:
        aliases: list[str] = []
        aliases.append(card.name.lower())
        aliases.append(card.face_name.lower())
        if card.canonical_name:
            aliases.append(card.canonical_name.lower())
        for face_name in card.face_names:
            aliases.append(face_name.lower())
        for alias in aliases:
            if query_lower in alias:
                return True
        return False

    def get_cards_info(self, names: list[str], card_fields: list[str]) -> str:
        """Look up one or more cards by exact name and return requested fields as JSON.

        Args:
            names: card names (case-insensitive exact match).
            card_fields: Card field names to include in each result entry.

        Returns:
            JSON string -- array of objects with the requested fields.
            Cards not found get ``{"name": "<input>", "error": "Card not found"}``.

        Raises:
            ValueError: if *names* is empty, *card_fields* is empty, or a field name is invalid.
        """
        if not names:
            LOGGER.error("get_cards_info: names list must not be empty")
            raise ValueError("get_cards_info: names list must not be empty")
        if not card_fields:
            LOGGER.error("get_cards_info: card_fields list must not be empty")
            raise ValueError("get_cards_info: card_fields list must not be empty")

        valid_field_names: set[str] = {f.name for f in _dc_fields(Card)}
        invalid: list[str] = [f for f in card_fields if f not in valid_field_names]
        if invalid:
            LOGGER.error(
                "get_cards_info: invalid field name(s): %s. Valid: %s",
                invalid, sorted(valid_field_names),
            )
            raise ValueError(
                f"get_cards_info: invalid field name(s): {invalid}. "
                f"Valid fields: {sorted(valid_field_names)}"
            )

        results: list[dict[str, Any]] = []
        for name in names:
            try:
                card: Card = self.resolve_primary_card(name)
            except ValueError:
                results.append({"name": name, "error": "Card not found"})
                LOGGER.warning("get_cards_info: card not found: %r", name)
                continue
            full: dict[str, Any] = card.to_dict()
            full["name"] = self.card_display_name(card)
            results.append({f: full[f] for f in card_fields})

        LOGGER.info("get_cards_info: requested=%s found=%s",
                     len(names), sum(1 for r in results if "error" not in r))
        return json.dumps(results, indent=2, ensure_ascii=False)

    def get_card_mechanics(self, name: str, extract_type: str) -> str:
        """Return triggers or effects for a card by exact name as a single string.

        Args:
            name: card name (case-insensitive exact match).
            extract_type: "triggers" or "effects".

        Returns:
            Semicolon-joined list of trigger/effect phrases, or "(none)" if empty.

        Raises:
            ValueError: if extract_type is not "triggers" or "effects", or card not found.
        """
        extract_clean: str = (extract_type or "").strip().lower()
        if extract_clean not in ("triggers", "effects"):
            LOGGER.error("get_card_mechanics: extract_type must be 'triggers' or 'effects', got %r", extract_type)
            raise ValueError("get_card_mechanics: extract_type must be 'triggers' or 'effects'")
        card: Card = self.resolve_primary_card(name)
        items: list[str] = card.get_triggers() if extract_clean == "triggers" else card.get_effects()
        if not items:
            return "(none)"
        return "; ".join(items)

    def get_synergy_score(self, name_a: str, name_b: str) -> float:
        """Return synergy score between two cards by name (higher = better synergy).

        Uses embedding model with cosine similarity: cos_sim(triggers_A, effects_B) + cos_sim(effects_A, triggers_B).
        Raises ValueError if either card is not found. Requires RAG (embedding model) to be loaded.
        """
        card_a: Card = self.resolve_primary_card(name_a)
        card_b: Card = self.resolve_primary_card(name_b)
        model = self.get_embedding_model()
        def encode_fn(texts: list[str]):
            return model.encode(texts).tolist()
        return card_a.synergy_with(card_b, encode_fn)

    @staticmethod
    def make_id(card_name: str, face_index: int) -> str:
        """Build a unique ID from the dict key (guaranteed unique) and face index."""
        return f"{card_name}::{face_index}"

    @staticmethod
    def _parse_colors(colors_str: str) -> set[str]:
        """Parse a comma-separated color string (e.g. 'W,U') into a set of single letters."""
        if not colors_str or not colors_str.strip():
            return set()
        return {c.strip().upper() for c in colors_str.split(",") if c.strip()}

    @staticmethod
    def _collection_name_for_search_type(search_type: str) -> str:
        st: str = (search_type or "").strip().lower()
        if st == "general":
            return COLLECTION_NAME
        if st == "trigger":
            return TRIGGERS_COLLECTION_NAME
        if st == "effect":
            return EFFECTS_COLLECTION_NAME
        LOGGER.error("filter_cards_list: search_type must be general/trigger/effect, got %r", search_type)
        raise ValueError(f"search_type must be general/trigger/effect, got {search_type!r}")

    def _face_matches_filters(
        self,
        card: Card,
        *,
        name_lower: str,
        oracle_lower_list: list[str],
        type_lower: str,
        colors_filter: set[str],
        color_identity_filter: set[str],
        color_identity_colorless: bool,
        colorless_only: bool,
        mana_value: float,
        mana_value_min: float,
        mana_value_max: float,
        price_usd_min: float,
        price_usd_max: float,
        power_val: str,
        toughness_val: str,
        keywords_lower: str,
        subtype_lower: str,
        supertype_lower: str,
        format_lower: str,
    ) -> bool:
        """True if this card face satisfies all active structural filters (AND)."""
        if name_lower and not self._name_query_matches_card(card, name_lower):
            return False
        if oracle_lower_list:
            card_text_lower: str = card.text.lower()
            if not all(phrase in card_text_lower for phrase in oracle_lower_list):
                return False
        if type_lower and type_lower not in card.type_line.lower():
            return False
        if colors_filter:
            card_colors: set[str] = {c.upper() for c in card.colors}
            if card_colors != colors_filter:
                return False
        if color_identity_filter or color_identity_colorless:
            card_identity: set[str] = {c.upper() for c in card.color_identity}
            if color_identity_filter and color_identity_colorless:
                if not card_identity.issubset(color_identity_filter) and len(card_identity) > 0:
                    return False
            elif color_identity_filter:
                if not card_identity.issubset(color_identity_filter):
                    return False
            elif color_identity_colorless:
                if len(card_identity) > 0:
                    return False
        if colorless_only and len(card.colors) > 0:
            return False
        if mana_value >= 0 and card.mana_value != mana_value:
            return False
        if mana_value_min >= 0 and card.mana_value < mana_value_min:
            return False
        if mana_value_max >= 0 and card.mana_value > mana_value_max:
            return False
        if price_usd_min >= 0 and (card.price_usd < 0 or card.price_usd < price_usd_min):
            return False
        if price_usd_max >= 0 and (card.price_usd < 0 or card.price_usd > price_usd_max):
            return False
        if power_val and card.power.strip() != power_val:
            return False
        if toughness_val and card.toughness.strip() != toughness_val:
            return False
        if keywords_lower:
            card_kw: list[str] = [k.lower() for k in card.keywords]
            if keywords_lower not in card_kw and not any(keywords_lower in k for k in card_kw):
                return False
        if subtype_lower:
            card_sub: list[str] = [s.lower() for s in card.subtypes]
            if subtype_lower not in card_sub and not any(subtype_lower in s for s in card_sub):
                return False
        if supertype_lower:
            card_super: list[str] = [s.lower() for s in card.supertypes]
            if supertype_lower not in card_super and not any(supertype_lower in s for s in card_super):
                return False
        if format_lower:
            legal_val: str = ""
            for k, v in card.legalities.items():
                if k.lower() == format_lower and v:
                    legal_val = (v if isinstance(v, str) else str(v)).lower()
                    break
            if legal_val != "legal":
                return False
        return True

    def _faces_for_chroma_card_name(self, chroma_name: str) -> list[Card] | None:
        """Resolve Chroma metadata name to all faces for that card, or None if unknown."""
        cn: str = chroma_name.strip()
        if not cn:
            return None
        self._build_name_indexes()
        assert self._canonical_to_faces is not None
        if cn in self._canonical_to_faces:
            return self._canonical_to_faces[cn]
        al: str = cn.lower()
        assert self._name_to_faces is not None
        if al in self._name_to_faces:
            return self._name_to_faces[al]
        try:
            prim: Card = self.resolve_primary_card(cn)
            ckey: str = prim.canonical_name if prim.canonical_name else prim.name
            return self._canonical_to_faces.get(ckey, [prim])
        except ValueError:
            return None

    def _canonical_matches_structural_filters(
        self,
        chroma_name: str,
        *,
        name_lower: str,
        oracle_lower_list: list[str],
        type_lower: str,
        colors_filter: set[str],
        color_identity_filter: set[str],
        color_identity_colorless: bool,
        colorless_only: bool,
        mana_value: float,
        mana_value_min: float,
        mana_value_max: float,
        price_usd_min: float,
        price_usd_max: float,
        power_val: str,
        toughness_val: str,
        keywords_lower: str,
        subtype_lower: str,
        supertype_lower: str,
        format_lower: str,
    ) -> tuple[bool, Card | None]:
        """True if any face matches filters; returns primary face for display when True."""
        faces: list[Card] | None = self._faces_for_chroma_card_name(chroma_name)
        if not faces:
            return False, None
        ordered: list[Card] = sorted(faces, key=lambda c: c.face_index)
        primary: Card = ordered[0]
        kw = dict(
            name_lower=name_lower,
            oracle_lower_list=oracle_lower_list,
            type_lower=type_lower,
            colors_filter=colors_filter,
            color_identity_filter=color_identity_filter,
            color_identity_colorless=color_identity_colorless,
            colorless_only=colorless_only,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
            power_val=power_val,
            toughness_val=toughness_val,
            keywords_lower=keywords_lower,
            subtype_lower=subtype_lower,
            supertype_lower=supertype_lower,
            format_lower=format_lower,
        )
        for face in ordered:
            if self._face_matches_filters(face, **kw):
                return True, primary
        return False, None

    def _filter_cards_list_semantic_ranked(
        self,
        semantic_query: str,
        collection_name: str,
        *,
        name_lower: str,
        oracle_lower_list: list[str],
        type_lower: str,
        colors_filter: set[str],
        color_identity_filter: set[str],
        color_identity_colorless: bool,
        colorless_only: bool,
        mana_value: float,
        mana_value_min: float,
        mana_value_max: float,
        price_usd_min: float,
        price_usd_max: float,
        power_val: str,
        toughness_val: str,
        keywords_lower: str,
        subtype_lower: str,
        supertype_lower: str,
        format_lower: str,
        n_results: int,
        offset: int,
    ) -> list[Card]:
        """Chroma-ranked hits filtered by structural rules; dedupe by canonical; honor offset/limit."""
        need: int = offset + n_results
        n_chroma: int = min(_CHROMA_SEMANTIC_FILTER_CAP, max(100, need * 4))
        filter_kw = dict(
            name_lower=name_lower,
            oracle_lower_list=oracle_lower_list,
            type_lower=type_lower,
            colors_filter=colors_filter,
            color_identity_filter=color_identity_filter,
            color_identity_colorless=color_identity_colorless,
            colorless_only=colorless_only,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
            power_val=power_val,
            toughness_val=toughness_val,
            keywords_lower=keywords_lower,
            subtype_lower=subtype_lower,
            supertype_lower=supertype_lower,
            format_lower=format_lower,
        )
        while True:
            ranked: list[tuple[str, str]] = self._semantic_query(collection_name, semantic_query, n_chroma)
            seen_canonical: set[str] = set()
            skipped_qualified: int = 0
            out: list[Card] = []
            for _doc, raw_name in ranked:
                name_key: str = (raw_name or "").strip()
                if not name_key:
                    continue
                if name_key in seen_canonical:
                    continue
                seen_canonical.add(name_key)
                ok, primary = self._canonical_matches_structural_filters(name_key, **filter_kw)
                if not ok or primary is None:
                    continue
                if skipped_qualified < offset:
                    skipped_qualified += 1
                    continue
                out.append(primary)
                if len(out) >= n_results:
                    return out
            if n_chroma >= _CHROMA_SEMANTIC_FILTER_CAP:
                if need > len(out) + offset:
                    LOGGER.warning(
                        "filter_cards_list semantic: exhausted Chroma cap=%s (query=%r collection=%s); "
                        "returning %s row(s), offset=%s",
                        _CHROMA_SEMANTIC_FILTER_CAP,
                        semantic_query,
                        collection_name,
                        len(out),
                        offset,
                    )
                return out
            n_chroma = min(_CHROMA_SEMANTIC_FILTER_CAP, n_chroma * 2)

    def _filter_cards_list_structural_scan_deduped(
        self,
        offset: int,
        n_results: int,
        *,
        name_lower: str,
        oracle_lower_list: list[str],
        type_lower: str,
        colors_filter: set[str],
        color_identity_filter: set[str],
        color_identity_colorless: bool,
        colorless_only: bool,
        mana_value: float,
        mana_value_min: float,
        mana_value_max: float,
        price_usd_min: float,
        price_usd_max: float,
        power_val: str,
        toughness_val: str,
        keywords_lower: str,
        subtype_lower: str,
        supertype_lower: str,
        format_lower: str,
    ) -> list[Card]:
        """Linear scan with structural filters, one primary row per canonical (like semantic path)."""
        cards: list[Card] = self.get_card_data()
        seen_canonical: set[str] = set()
        skipped: int = 0
        out: list[Card] = []
        fkw = dict(
            name_lower=name_lower,
            oracle_lower_list=oracle_lower_list,
            type_lower=type_lower,
            colors_filter=colors_filter,
            color_identity_filter=color_identity_filter,
            color_identity_colorless=color_identity_colorless,
            colorless_only=colorless_only,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
            power_val=power_val,
            toughness_val=toughness_val,
            keywords_lower=keywords_lower,
            subtype_lower=subtype_lower,
            supertype_lower=supertype_lower,
            format_lower=format_lower,
        )
        for card in cards:
            if not self._face_matches_filters(card, **fkw):
                continue
            prim: Card | None = self.try_resolve_primary_card(card.name)
            if prim is None and card.canonical_name:
                prim = self.try_resolve_primary_card(card.canonical_name)
            if prim is None:
                prim = card
            ckey: str = (prim.canonical_name or prim.name).strip().lower()
            if ckey in seen_canonical:
                continue
            seen_canonical.add(ckey)
            if skipped < offset:
                skipped += 1
                continue
            out.append(prim)
            if len(out) >= n_results:
                break
        return out

    def filter_cards_list(
        self,
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
        price_usd_min: float = -1.0,
        price_usd_max: float = -1.0,
        power: str = "",
        toughness: str = "",
        keywords: str = "",
        subtype: str = "",
        supertype: str = "",
        format_legal: str = "",
        n_results: int = 20,
        offset: int = 0,
        semantic_query: str = "",
        search_type: str = "general",
    ) -> list[Card]:
        """Filter MTG cards by exact/filter properties. All filters are AND-combined. Returns list of Card. At least one filter must be set. offset/n_results support pagination.

        If semantic_query is non-empty, results are Chroma-ranked by similarity within the given search_type
        collection, intersected with the same structural filters (deduped by canonical card).
        """
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
            or price_usd_min >= 0
            or price_usd_max >= 0
            or bool(power.strip())
            or bool(toughness.strip())
            or bool(keywords.strip())
            or bool(subtype.strip())
            or bool(supertype.strip())
            or bool(format_legal.strip())
        )
        if not has_filter:
            LOGGER.error( "filter_cards_list: at least one filter parameter must be set")
            raise ValueError("filter_cards_list: at least one filter parameter must be set")

        name_lower: str = name.strip().lower() if name else ""
        oracle_lower_list: list[str] = [s.lower() for s in _oracle_list]
        type_lower: str = type_line.strip().lower() if type_line else ""
        colors_filter: set[str] = self._parse_colors(colors)
        color_identity_filter: set[str] = self._parse_colors(color_identity)
        power_val: str = power.strip() if power else ""
        toughness_val: str = toughness.strip() if toughness else ""
        keywords_lower: str = keywords.strip().lower() if keywords else ""
        subtype_lower: str = subtype.strip().lower() if subtype else ""
        supertype_lower: str = supertype.strip().lower() if supertype else ""
        format_lower: str = format_legal.strip().lower() if format_legal else ""

        sem: str = (semantic_query or "").strip()
        if sem:
            if not self.is_rag_ready():
                LOGGER.error("filter_cards_list: semantic_query set but RAG is not ready")
                raise ValueError("Semantic search requires RAG; the embedding index is not ready yet.")
            coll: str = self._collection_name_for_search_type(search_type)
            ranked: list[Card] = self._filter_cards_list_semantic_ranked(
                sem,
                coll,
                name_lower=name_lower,
                oracle_lower_list=oracle_lower_list,
                type_lower=type_lower,
                colors_filter=colors_filter,
                color_identity_filter=color_identity_filter,
                color_identity_colorless=color_identity_colorless,
                colorless_only=colorless_only,
                mana_value=mana_value,
                mana_value_min=mana_value_min,
                mana_value_max=mana_value_max,
                price_usd_min=price_usd_min,
                price_usd_max=price_usd_max,
                power_val=power_val,
                toughness_val=toughness_val,
                keywords_lower=keywords_lower,
                subtype_lower=subtype_lower,
                supertype_lower=supertype_lower,
                format_lower=format_lower,
                n_results=n_results,
                offset=offset,
            )
            if len(ranked) == 0:
                LOGGER.info(
                    "filter_cards_list: semantic returned 0 matches; structural fallback "
                    "semantic_query=%r search_type=%r type_line=%r format_legal=%r color_identity=%r",
                    sem,
                    search_type,
                    type_line,
                    format_legal,
                    color_identity,
                )
                return self._filter_cards_list_structural_scan_deduped(
                    offset=offset,
                    n_results=n_results,
                    name_lower=name_lower,
                    oracle_lower_list=oracle_lower_list,
                    type_lower=type_lower,
                    colors_filter=colors_filter,
                    color_identity_filter=color_identity_filter,
                    color_identity_colorless=color_identity_colorless,
                    colorless_only=colorless_only,
                    mana_value=mana_value,
                    mana_value_min=mana_value_min,
                    mana_value_max=mana_value_max,
                    price_usd_min=price_usd_min,
                    price_usd_max=price_usd_max,
                    power_val=power_val,
                    toughness_val=toughness_val,
                    keywords_lower=keywords_lower,
                    subtype_lower=subtype_lower,
                    supertype_lower=supertype_lower,
                    format_lower=format_lower,
                )
            return ranked

        cards: list[Card] = self.get_card_data()
        results: list[Card] = []
        skipped: int = 0
        fkw = dict(
            name_lower=name_lower,
            oracle_lower_list=oracle_lower_list,
            type_lower=type_lower,
            colors_filter=colors_filter,
            color_identity_filter=color_identity_filter,
            color_identity_colorless=color_identity_colorless,
            colorless_only=colorless_only,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
            power_val=power_val,
            toughness_val=toughness_val,
            keywords_lower=keywords_lower,
            subtype_lower=subtype_lower,
            supertype_lower=supertype_lower,
            format_lower=format_lower,
        )
        for card in cards:
            if not self._face_matches_filters(card, **fkw):
                continue
            if skipped < offset:
                skipped += 1
                continue
            results.append(card)
            if len(results) >= n_results:
                break

        return results

    def filter_cards(
        self,
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
        price_usd_min: float = -1.0,
        price_usd_max: float = -1.0,
        power: str = "",
        toughness: str = "",
        keywords: str = "",
        subtype: str = "",
        supertype: str = "",
        format_legal: str = "",
        n_results: int = 20,
        semantic_query: str = "",
        search_type: str = "general",
    ) -> str:
        """Filter MTG cards by exact/filter properties. All filters are AND-combined. At least one filter must be set."""
        results: list[Card] = self.filter_cards_list(
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
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
            power=power,
            toughness=toughness,
            keywords=keywords,
            subtype=subtype,
            supertype=supertype,
            format_legal=format_legal,
            n_results=n_results,
            semantic_query=semantic_query,
            search_type=search_type,
        )
        parts: list[str] = [card.format_display(i, len(results)) for i, card in enumerate(results, 1)]
        return "\n\n".join(parts) if parts else "No cards found."

    # -----------------------------------------------------------------------
    # RAG: embedding model and ChromaDB (load only in _load_rag_impl / at server init)
    # -----------------------------------------------------------------------

    def _load_rag_impl(self) -> None:
        """Load embedding model and ChromaDB client. Call under _rag_lock. Idempotent."""
        if self._embedding_model is not None and self._chroma_client is not None:
            return
        t0: float = time.perf_counter()
        LOGGER.debug("Importing torch")
        import torch
        LOGGER.debug("torch imported elapsed=%.3fs", time.perf_counter() - t0)

        t1: float = time.perf_counter()
        LOGGER.debug("Importing SentenceTransformer")
        from sentence_transformers import SentenceTransformer
        LOGGER.debug("SentenceTransformer imported elapsed=%.3fs", time.perf_counter() - t1)

        t2: float = time.perf_counter()
        LOGGER.debug("Importing chromadb")
        import chromadb
        LOGGER.debug("chromadb imported elapsed=%.3fs", time.perf_counter() - t2)

        if self._embedding_model is None:
            t3: float = time.perf_counter()
            device: str = "cuda" if torch.cuda.is_available() else "cpu"
            LOGGER.info("Loading embedding model name=%s device=%s", MODEL_NAME, device)
            self._embedding_model = SentenceTransformer(MODEL_NAME, device=device)
            LOGGER.info("Embedding model loaded name=%s device=%s elapsed=%.3fs", MODEL_NAME, device, time.perf_counter() - t3)

        if self._chroma_client is None:
            t4: float = time.perf_counter()
            LOGGER.info("Opening ChromaDB path=%s", CHROMA_PATH)
            self._chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
            LOGGER.info("ChromaDB client ready path=%s elapsed=%.3fs", CHROMA_PATH, time.perf_counter() - t4)
        self._rag_ready = True

    def is_rag_ready(self) -> bool:
        """Return True if RAG (embedding model + ChromaDB) has been loaded and is ready for semantic search."""
        with self._rag_lock:
            return self._rag_ready

    def load_rag_sync(self) -> None:
        """Load RAG dependencies (embedding model + ChromaDB) in the current thread.
        Intended to be called from a background thread at server startup so the main
        server can start without blocking. Subsequent get_embedding_model / semantic
        search will use the cached instances."""
        with self._rag_lock:
            self._load_rag_impl()

    def get_embedding_model(self):
        """Return the embedding model; load via _load_rag_impl if not yet loaded (under lock)."""
        with self._rag_lock:
            if self._embedding_model is None:
                self._load_rag_impl()
        LOGGER.debug("Using embedding model name=%s", MODEL_NAME)
        return self._embedding_model

    def _get_chroma_collection(self, collection_name: str):
        """Return the named ChromaDB collection; load client via _load_rag_impl if not yet loaded (under lock)."""
        with self._rag_lock:
            if self._chroma_client is None:
                self._load_rag_impl()
        if collection_name not in self._collections:
            t2: float = time.perf_counter()
            self._collections[collection_name] = self._chroma_client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            LOGGER.debug("get_or_create_collection name=%s elapsed=%.3fs", collection_name, time.perf_counter() - t2)
            LOGGER.info("ChromaDB collection ready name=%s path=%s", collection_name, CHROMA_PATH)
        return self._collections[collection_name]

    def get_collection(self):
        """Lazy-load ChromaDB persistent client and mtg_cards collection."""
        return self._get_chroma_collection(COLLECTION_NAME)

    def _semantic_query(self, collection_name: str, query: str, n_results: int) -> list[tuple[str, str]]:
        """Shared encode-and-query against *collection_name*. Returns list of ``(document, card_name)``."""
        model = self.get_embedding_model()
        coll = self._get_chroma_collection(collection_name)
        t_enc: float = time.perf_counter()
        emb = model.encode([query]).tolist()
        LOGGER.debug("Query encoded collection=%s elapsed=%.3fs", collection_name, time.perf_counter() - t_enc)
        t_query: float = time.perf_counter()
        out = coll.query(query_embeddings=emb, n_results=n_results, include=["documents", "metadatas"])
        LOGGER.debug("ChromaDB query done collection=%s elapsed=%.3fs", collection_name, time.perf_counter() - t_query)
        docs = (out.get("documents") or [[]])[0]
        metas = (out.get("metadatas") or [[]])[0]
        results: list[tuple[str, str]] = []
        for doc, meta in zip(docs, metas):
            if not isinstance(meta, dict):
                meta = {}
            if "canonicalName" in meta and isinstance(meta["canonicalName"], str) and meta["canonicalName"].strip():
                name = meta["canonicalName"]
            elif "name" in meta and isinstance(meta["name"], str):
                name = meta["name"]
            else:
                name = ""
            results.append((doc, name))
        return results

    def search_cards(self, query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        LOGGER.info("search_cards started query=%r n_results=%s", query, n_results)
        model = self.get_embedding_model()
        coll = self.get_collection()
        emb = model.encode([query]).tolist()
        out = coll.query(query_embeddings=emb, n_results=n_results, include=["documents", "metadatas"])
        docs = (out.get("documents") or [[]])[0]
        metas = (out.get("metadatas") or [[]])[0]
        LOGGER.debug("ChromaDB returned %s document(s)", len(docs))
        parts: list[str] = []
        for i, (doc, meta) in enumerate(zip(docs, metas), 1):
            card: Card = Card.from_chroma_result(meta, doc)
            LOGGER.debug("Card %s/%s: %s", i, len(docs), card.name)
            parts.append(card.format_display(i, len(docs)))
        LOGGER.info("search_cards finished query=%r returned %s card(s)", query, len(docs))
        return "\n\n".join(parts) if parts else "No cards found."

    def search_triggers(self, query: str, n_results: int = 10) -> str:
        """Find cards whose triggers semantically match *query*.

        Use this to answer: 'which cards trigger on <X>?'
        For example, query='creature enters the battlefield' returns cards that
        trigger when a creature ETBs.
        """
        LOGGER.info("search_triggers started query=%r n_results=%s", query, n_results)
        results = self._semantic_query(TRIGGERS_COLLECTION_NAME, query, n_results)
        parts: list[str] = []
        for i, (doc, name) in enumerate(results, 1):
            parts.append(f"--- {i} of {len(results)} ---\n{doc}")
        LOGGER.info("search_triggers finished query=%r returned %s card(s)", query, len(results))
        return "\n\n".join(parts) if parts else "No cards found."

    def search_effects(self, query: str, n_results: int = 10) -> str:
        """Find cards whose effects semantically match *query*.

        Use this to answer: 'which cards produce <X>?'
        For example, query='create creature token' returns cards whose effects
        produce creature tokens.
        """
        LOGGER.info("search_effects started query=%r n_results=%s", query, n_results)
        results = self._semantic_query(EFFECTS_COLLECTION_NAME, query, n_results)
        parts: list[str] = []
        for i, (doc, name) in enumerate(results, 1):
            parts.append(f"--- {i} of {len(results)} ---\n{doc}")
        LOGGER.info("search_effects finished query=%r returned %s card(s)", query, len(results))
        return "\n\n".join(parts) if parts else "No cards found."

    def semantic_search_structured(
        self, query: str, search_type: str, n_results: int = 10
    ) -> list[dict[str, str]]:
        """Semantic search returning structured list of {name, text} for deck editor API.

        search_type must be one of "general", "trigger", "effect"; maps to the
        corresponding ChromaDB collection. Results are deduplicated by card name
        (first occurrence kept), preserving order.
        """
        if search_type == "general":
            collection_name = COLLECTION_NAME
        elif search_type == "trigger":
            collection_name = TRIGGERS_COLLECTION_NAME
        elif search_type == "effect":
            collection_name = EFFECTS_COLLECTION_NAME
        else:
            raise ValueError(f"search_type must be general/trigger/effect, got {search_type!r}")
        raw = self._semantic_query(collection_name, query, n_results)
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for doc, name in raw:
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "text": doc or ""})
        return out
