"""Card database: loading, filtering, and RAG semantic search for the MTG MCP project."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from dataclasses import fields as _dc_fields
from pathlib import Path
from typing import Any

from src.lib.config import (
    ATOMIC_CARDS_PATH,
    CARD_FACES_DIR,
    mtgjson_legality_key,
)
from src.lib.graphrag_service import GraphAnalysis, GraphRAGService
from src.lib.prices import load_prices
from src.obj.card import Card
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# CardDB singleton: AtomicCards + GraphRAG semantic service
# ---------------------------------------------------------------------------

class CardDB:
    """Unified card database: cards, exact filters, and GraphRAG semantic retrieval."""

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
            ordered_faces = sorted(faces, key=lambda c: c.face_index)
            canonical_alias = canonical.casefold()
            alias_to_primary[canonical_alias] = ordered_faces[0]
            alias_to_faces[canonical_alias] = ordered_faces
        for canonical, faces in canonical_to_faces.items():
            ordered_faces: list[Card] = sorted(faces, key=lambda c: c.face_index)
            primary: Card = ordered_faces[0]
            aliases: set[str] = set()
            for face in ordered_faces:
                aliases.add(face.name.casefold())
                aliases.add(face.face_name.casefold())
                for fname in face.face_names:
                    aliases.add(fname.casefold())
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
        key: str = name.strip().casefold()
        name_map: dict[str, Card] = self._get_name_to_card()
        if key not in name_map:
            LOGGER.error("resolve_primary_card: card not found: %r", name)
            raise ValueError(f"Card not found: {name!r}")
        return name_map[key]

    def try_resolve_primary_card(self, name: str) -> Card | None:
        """Resolve name to primary face Card, or None if unknown. Does not log."""
        if not name or not name.strip():
            return None
        key: str = name.strip().casefold()
        name_map: dict[str, Card] = self._get_name_to_card()
        return name_map[key] if key in name_map else None

    def resolve_faces(self, name: str) -> list[Card]:
        """Resolve any alias (front/back/combined) to all faces of the card."""
        if not name or not name.strip():
            LOGGER.error("resolve_faces: name is empty")
            raise ValueError("resolve_faces: name is empty")
        self._build_name_indexes()
        assert self._name_to_faces is not None, "_build_name_indexes must initialize _name_to_faces"
        key: str = name.strip().casefold()
        if key not in self._name_to_faces:
            LOGGER.error("resolve_faces: card not found: %r", name)
            raise ValueError(f"Card not found: {name!r}")
        return list(self._name_to_faces[key])

    def get_canonical_cards(self) -> list[Card]:
        """Return one existing primary face per canonical card, sorted by name."""
        self._build_name_indexes()
        assert self._canonical_to_faces is not None
        primary_cards: list[Card] = []
        for canonical, faces in self._canonical_to_faces.items():
            if not faces:
                LOGGER.error("get_canonical_cards: canonical card %r has no faces", canonical)
                raise ValueError(f"Canonical card {canonical!r} has no faces")
            primary_cards.append(min(faces, key=lambda card: card.face_index))
        return sorted(primary_cards, key=lambda card: (card.canonical_name or card.name).casefold())

    @staticmethod
    def card_display_name(card: Card) -> str:
        if card.canonical_name:
            return card.canonical_name
        return card.name

    @staticmethod
    def _is_split_card(faces: list[Card]) -> bool:
        """True for classic split cards (both faces instant or both sorcery, neither a land)."""
        if len(faces) < 2:
            return False
        type_lines: list[str] = [f.type_line.lower() for f in faces[:2]]
        if "land" in type_lines[0] or "land" in type_lines[1]:
            return False
        both_instant: bool = "instant" in type_lines[0] and "instant" in type_lines[1]
        both_sorcery: bool = "sorcery" in type_lines[0] and "sorcery" in type_lines[1]
        return both_instant or both_sorcery

    def card_arena_export_name(self, card: Card) -> str:
        """Name to use in MTG Arena deck import/export lines.

        Arena accepts full ``//`` names for split cards but expects the front face
        only for MDFCs, transform DFCs, adventures, and similar multi-face cards.
        """
        lookup_name: str = card.canonical_name if card.canonical_name else card.name
        faces: list[Card] = self.resolve_faces(lookup_name)
        if len(faces) >= 2 and self._is_split_card(faces):
            return self.card_display_name(card)
        primary: Card = faces[0]
        if primary.face_name:
            return primary.face_name
        if primary.face_names:
            return primary.face_names[0]
        return self.card_display_name(card)

    @staticmethod
    def _name_query_matches_card(card: Card, query_lower: str) -> bool:
        aliases: list[str] = []
        aliases.append(card.name.casefold())
        aliases.append(card.face_name.casefold())
        if card.canonical_name:
            aliases.append(card.canonical_name.casefold())
        for face_name in card.face_names:
            aliases.append(face_name.casefold())
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
        """Return deterministic graph synergy score between two cards."""
        card_a: Card = self.resolve_primary_card(name_a)
        card_b: Card = self.resolve_primary_card(name_b)
        score, _ = GraphRAGService.inst().pair_evidence(card_a, card_b)
        return score

    def get_synergy_evidence(self, name_a: str, name_b: str) -> tuple[float, list[dict[str, Any]]]:
        """Return the graph score and explainable evidence for two named cards."""
        card_a: Card = self.resolve_primary_card(name_a)
        card_b: Card = self.resolve_primary_card(name_b)
        return GraphRAGService.inst().pair_evidence(card_a, card_b)

    async def analyze_deck(
        self,
        deck_cards: list[Card],
        eligible_candidates: list[Card],
        limit: int,
        deck_context: dict[str, Any],
    ) -> GraphAnalysis:
        """Return a complete cached GraphRAG analysis over prevalidated candidates."""
        if not deck_cards:
            LOGGER.error("analyze_deck: deck_cards must not be empty")
            raise ValueError("analyze_deck: deck_cards must not be empty")
        return await GraphRAGService.inst().analyze_deck(
            deck_cards,
            eligible_candidates,
            limit,
            deck_context,
        )

    def get_graph_manifest_hash(self) -> str:
        """Return the loaded GraphRAG artifact hash for response cache invalidation."""
        return GraphRAGService.inst().manifest_hash()

    @staticmethod
    def _parse_colors(colors_str: str) -> set[str]:
        """Parse a comma-separated color string (e.g. 'W,U') into a set of single letters."""
        if not colors_str or not colors_str.strip():
            return set()
        return {c.strip().upper() for c in colors_str.split(",") if c.strip()}

    @staticmethod
    def _collection_name_for_search_type(search_type: str) -> str:
        """Validate the public GraphRAG search scope."""
        st: str = (search_type or "").strip().lower()
        if st in ("general", "trigger", "effect"):
            return st
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
            if not card_colors.issubset(colors_filter):
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

    def _faces_for_canonical_card_name(self, card_name: str) -> list[Card] | None:
        """Resolve a graph canonical name to all faces for that card, or None if unknown."""
        cn: str = card_name.strip()
        if not cn:
            return None
        self._build_name_indexes()
        assert self._canonical_to_faces is not None
        if cn in self._canonical_to_faces:
            return self._canonical_to_faces[cn]
        al: str = cn.casefold()
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
        card_name: str,
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
        faces: list[Card] | None = self._faces_for_canonical_card_name(card_name)
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
        search_type: str,
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
        """GraphRAG-ranked hits filtered by deterministic structural rules."""
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
        ranked_names: list[str] = GraphRAGService.inst().rank_card_names(
            semantic_query,
            search_type,
            max(100, (offset + n_results) * 4),
        )
        seen_canonical: set[str] = set()
        skipped_qualified: int = 0
        out: list[Card] = []
        for raw_name in ranked_names:
            name_key: str = raw_name.strip()
            if not name_key or name_key in seen_canonical:
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
                break
        return out

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
        """Filter MTG cards by exact/filter properties. All filters are AND-combined. Returns list of Card.

        At least one structural filter or a non-empty *semantic_query* must be set. offset/n_results support pagination.

        If semantic_query is non-empty, results are GraphRAG-ranked within the given search_type
        collection, intersected with the same structural filters (deduped by canonical card).
        """
        _oracle_list: list[str] = (
            [s.strip() for s in oracle_text] if isinstance(oracle_text, list) else [oracle_text.strip()] if oracle_text else []
        )
        _oracle_list = [s for s in _oracle_list if s]
        semantic_stripped: str = (semantic_query or "").strip()
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
            or bool(semantic_stripped)
        )
        if not has_filter:
            LOGGER.error(
                "filter_cards_list: at least one structural filter or semantic_query must be set",
            )
            raise ValueError(
                "filter_cards_list: at least one structural filter or semantic_query must be set",
            )

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
        format_lower: str = mtgjson_legality_key(format_legal) if format_legal else ""

        sem: str = semantic_stripped
        if sem:
            if not self.is_rag_ready():
                LOGGER.error("filter_cards_list: semantic_query set but RAG is not ready")
                raise ValueError("Semantic search requires GraphRAG; the validated index is not ready yet.")
            scope: str = self._collection_name_for_search_type(search_type)
            ranked: list[Card] = self._filter_cards_list_semantic_ranked(
                sem,
                scope,
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
    # RAG: GraphRAG artifacts and local LanceDB
    # -----------------------------------------------------------------------

    def _load_rag_impl(self) -> None:
        """Load and validate the locally built GraphRAG artifacts."""
        GraphRAGService.inst().load_sync()

    def is_rag_ready(self) -> bool:
        """Return True when the validated GraphRAG index is ready for retrieval."""
        return GraphRAGService.inst().is_ready()

    def load_rag_sync(self) -> None:
        """Load GraphRAG dependencies in the current thread."""
        self._load_rag_impl()

    def _semantic_query(self, collection_name: str, query: str, n_results: int) -> list[tuple[str, str]]:
        """Shared GraphRAG semantic retrieval returning descriptions and card names."""
        scope: str = self._collection_name_for_search_type(collection_name)
        names: list[str] = GraphRAGService.inst().rank_card_names(query, scope, n_results)
        return [
            (self.resolve_primary_card(name).to_graph_description(), name)
            for name in names
        ]

    def search_cards(self, query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        LOGGER.info("search_cards started query=%r n_results=%s", query, n_results)
        parts: list[str] = []
        names: list[str] = GraphRAGService.inst().rank_card_names(query, "general", n_results)
        for i, name in enumerate(names, 1):
            card: Card = self.resolve_primary_card(name)
            parts.append(card.format_display(i, len(names)))
        LOGGER.info("search_cards finished query=%r returned %s card(s)", query, len(names))
        return "\n\n".join(parts) if parts else "No cards found."

    def search_triggers(self, query: str, n_results: int = 10) -> str:
        """Find cards whose triggers semantically match *query*.

        Use this to answer: 'which cards trigger on <X>?'
        For example, query='creature enters the battlefield' returns cards that
        trigger when a creature ETBs.
        """
        LOGGER.info("search_triggers started query=%r n_results=%s", query, n_results)
        results = self._semantic_query("trigger", query, n_results)
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
        results = self._semantic_query("effect", query, n_results)
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
        corresponding GraphRAG scope. Results are deduplicated by card name
        (first occurrence kept), preserving order.
        """
        scope: str = self._collection_name_for_search_type(search_type)
        raw = self._semantic_query(scope, query, n_results)
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for doc, name in raw:
            if name and name not in seen:
                seen.add(name)
                out.append({"name": name, "text": doc or ""})
        return out

    # -----------------------------------------------------------------------
    # Card face image disk cache (data/faces/{size}/{safe_name}.jpg)
    # -----------------------------------------------------------------------

    _SCRYFALL_IMAGE_URL: str = "https://api.scryfall.com/cards/named"
    _SCRYFALL_USER_AGENT: str = "MTG-MCP/1.0"
    _FACE_NAME_SANITIZE_RE: re.Pattern[str] = re.compile(r"[^\w-]")
    _FACE_NAME_COLLAPSE_RE: re.Pattern[str] = re.compile(r"_+")

    @staticmethod
    def _sanitize_face_name(name: str) -> str:
        """Convert a card face name to a filesystem-safe lowercase string."""
        safe: str = CardDB._FACE_NAME_SANITIZE_RE.sub("_", name.strip().lower())
        safe = CardDB._FACE_NAME_COLLAPSE_RE.sub("_", safe)
        return safe.strip("_")

    @staticmethod
    def _face_image_path(face_name: str, size: str) -> Path:
        """Return the expected disk path for a cached face image (does not check existence)."""
        return CARD_FACES_DIR / size / f"{CardDB._sanitize_face_name(face_name)}.jpg"

    @staticmethod
    def _sleep_for_retry_after(headers: Any, attempt: int) -> None:
        """Sleep for Retry-After if present; otherwise exponential backoff."""
        retry_after_s: float | None = None
        try:
            if headers is not None and hasattr(headers, "get"):
                ra = headers.get("Retry-After")
                if isinstance(ra, str) and ra.strip():
                    retry_after_s = float(ra.strip())
        except Exception:
            retry_after_s = None
        delay: float = retry_after_s if retry_after_s is not None else min(8.0, 0.5 * (2 ** attempt))
        time.sleep(max(0.25, delay))

    @staticmethod
    def _fetch_face_image_bytes(face_name: str, size: str) -> bytes:
        """Download card face image bytes from Scryfall.

        Raises on network or HTTP errors after retrying 429s.
        """
        import requests

        scry_url: str = (
            CardDB._SCRYFALL_IMAGE_URL
            + "?exact=" + urllib.parse.quote(face_name, safe="")
            + "&format=image&version=" + size
        )
        LOGGER.info("_fetch_face_image_bytes: fetching face_name=%r size=%s", face_name, size)
        r = None
        for attempt in range(4):
            r = requests.get(
                scry_url,
                timeout=15,
                allow_redirects=True,
                headers={"Accept": "*/*", "User-Agent": CardDB._SCRYFALL_USER_AGENT},
            )
            if r.status_code == 429:
                LOGGER.error(
                    "_fetch_face_image_bytes: rate limited (429) face_name=%r attempt=%d",
                    face_name, attempt + 1,
                )
                CardDB._sleep_for_retry_after(r.headers, attempt)
                continue
            break
        assert r is not None, "requests.get must return a response"
        if not r.ok:
            LOGGER.error(
                "_fetch_face_image_bytes: scryfall error status=%s face_name=%r url=%s",
                r.status_code, face_name, scry_url,
            )
            raise RuntimeError(
                f"Scryfall image request failed ({r.status_code}) for {face_name!r}"
            )
        image_bytes: bytes = r.content
        if not image_bytes:
            LOGGER.error("_fetch_face_image_bytes: empty body face_name=%r url=%s", face_name, scry_url)
            raise RuntimeError(f"Scryfall returned empty image body for {face_name!r}")
        LOGGER.info(
            "_fetch_face_image_bytes: downloaded face_name=%r size=%s bytes=%d",
            face_name, size, len(image_bytes),
        )
        return image_bytes

    def get_face_image(self, face_name: str, size: str) -> Path:
        """Return path to a cached card face image, fetching from Scryfall if not yet cached.

        Args:
            face_name: individual face name (e.g. ``"Lightning Bolt"`` or ``"Delver of Secrets"``).
            size: ``"normal"`` or ``"large"``.

        Returns:
            Path to the JPEG file on disk.
        """
        if not face_name or not face_name.strip():
            LOGGER.error("get_face_image: face_name is empty")
            raise ValueError("get_face_image: face_name is empty")
        if size not in ("normal", "large"):
            LOGGER.error("get_face_image: invalid size=%r", size)
            raise ValueError(f"get_face_image: size must be 'normal' or 'large', got {size!r}")

        path: Path = self._face_image_path(face_name, size)
        if path.is_file():
            LOGGER.debug("get_face_image: cache hit face_name=%r size=%s", face_name, size)
            return path

        LOGGER.info("get_face_image: cache miss face_name=%r size=%s — fetching", face_name, size)
        image_bytes: bytes = self._fetch_face_image_bytes(face_name, size)
        return self.save_face_image(face_name, size, image_bytes)

    def save_face_image(self, face_name: str, size: str, image_bytes: bytes) -> Path:
        """Write image bytes to the face cache and return the file path.

        Used by ``get_face_image`` (lazy cache) and by the batch prefetch in ``app.py``.
        """
        if not face_name or not face_name.strip():
            LOGGER.error("save_face_image: face_name is empty")
            raise ValueError("save_face_image: face_name is empty")
        if size not in ("normal", "large"):
            LOGGER.error("save_face_image: invalid size=%r", size)
            raise ValueError(f"save_face_image: size must be 'normal' or 'large', got {size!r}")
        if not image_bytes:
            LOGGER.error("save_face_image: image_bytes is empty for face_name=%r", face_name)
            raise ValueError(f"save_face_image: image_bytes is empty for {face_name!r}")

        path: Path = self._face_image_path(face_name, size)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)
        LOGGER.info("save_face_image: cached face_name=%r size=%s path=%s", face_name, size, path)
        return path
