"""Deterministic bring-your-own-graph artifact generation for MTG GraphRAG."""

from __future__ import annotations

import hashlib
import html
import json
import math
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.lib.spellbook import SpellbookCombo, fetch_combo_variants
from src.obj.card import Card, MechanicSignature
from src.utils.logger import LOGGER

GRAPH_SCHEMA_VERSION = 3
_GRAPH_NAMESPACE = uuid.UUID("8d00684c-c9ca-4f8c-83c9-d25fbca6ff5d")
_PROVIDE_NEED_WEIGHT = 1.0
_TRAIT_WEIGHT = 0.15
_COMBO_WEIGHT = 1.0


@dataclass(frozen=True)
class GraphBuildResult:
    """Paths and source counts produced by one deterministic graph build."""

    entities_path: Path
    relationships_path: Path
    text_units_path: Path
    adjacency_path: Path
    manifest_path: Path
    card_count: int
    combo_count: int


def stable_graph_id(kind: str, value: str) -> str:
    """Return a stable UUID string for one namespaced graph object."""
    if not kind.strip() or not value.strip():
        LOGGER.error("stable_graph_id: kind and value must be non-empty")
        raise ValueError("stable_graph_id: kind and value must be non-empty")
    return str(uuid.uuid5(_GRAPH_NAMESPACE, f"{kind.strip().casefold()}:{value.strip().casefold()}"))


def card_graph_id(card_name: str) -> str:
    """Return the stable graph entity ID for a canonical card name."""
    return stable_graph_id("card", card_name)


def mechanic_graph_id(mechanic: str) -> str:
    """Return the stable graph entity ID for one normalized mechanic token."""
    return stable_graph_id("mechanic", mechanic)


def combo_graph_id(combo_id: str) -> str:
    """Return the stable graph entity ID for one Commander Spellbook combo."""
    return stable_graph_id("combo", combo_id)


def external_card_graph_id(card_name: str) -> str:
    """Return a stable ID for a Spellbook card absent from pinned MTGJSON."""
    return stable_graph_id("external-card", card_name)


def _graphrag_title(title: str) -> str:
    """Match the node-title normalization used by GraphRAG's stable LCC."""
    return html.unescape(title).upper().strip()


def _validate_canonical_cards(cards: list[Card]) -> list[Card]:
    """Validate CardDB's one-primary-face-per-canonical build contract."""
    seen: set[str] = set()
    for card in cards:
        canonical = (card.canonical_name or card.name).strip()
        if not canonical:
            LOGGER.error("_validate_canonical_cards: card has no canonical name")
            raise ValueError("_validate_canonical_cards: card has no canonical name")
        key = canonical.casefold()
        if key in seen:
            LOGGER.error("_validate_canonical_cards: duplicate canonical card %r", canonical)
            raise ValueError(f"Duplicate canonical card supplied to graph build: {canonical!r}")
        if card.face_index != 0:
            LOGGER.error(
                "_validate_canonical_cards: non-primary face supplied for %r index=%d",
                canonical,
                card.face_index,
            )
            raise ValueError(f"Non-primary face supplied to graph build: {canonical!r}")
        seen.add(key)
    return sorted(cards, key=lambda card: (card.canonical_name or card.name).casefold())


def _write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    """Write one required GraphRAG parquet artifact."""
    import pandas as pd

    if not rows:
        LOGGER.error("_write_parquet: refusing to write empty required table %s", path)
        raise ValueError(f"_write_parquet: refusing to write empty required table {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def _entity(
    *,
    entity_id: str,
    title: str,
    entity_type: str,
    description: str,
    text_unit_ids: list[str],
    degree: int,
    display_name: str,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "title": _graphrag_title(title),
        "type": entity_type,
        "description": description,
        "text_unit_ids": text_unit_ids,
        "frequency": len(text_unit_ids),
        "degree": degree,
        "display_name": display_name,
    }


def _relationship(
    *,
    source_id: str,
    source_title: str,
    target_id: str,
    target_title: str,
    description: str,
    kind: str,
    weight: float,
    semantic_weight: float,
    text_unit_id: str,
    provenance: str,
) -> dict[str, Any]:
    relationship_id = stable_graph_id(
        "relationship",
        f"{source_id}:{kind}:{target_id}:{text_unit_id}",
    )
    return {
        "id": relationship_id,
        "source": _graphrag_title(source_title),
        "target": _graphrag_title(target_title),
        "description": description,
        "weight": float(weight),
        "text_unit_ids": [text_unit_id],
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "semantic_weight": float(semantic_weight),
        "provenance": provenance,
    }


def _mechanic_description(token: str) -> str:
    return f"Normalized Magic: The Gathering mechanic: {token.replace('_', ' ')}."


def build_graph_artifacts(
    cards: list[Card],
    output_dir: Path,
    combo_variants: list[SpellbookCombo] | None = None,
    spellbook_checkpoint_path: Path | None = None,
    card_resolver: Callable[[str], Card | None] | None = None,
) -> GraphBuildResult:
    """Write validated GraphRAG BYOG tables and deterministic runtime adjacency."""
    if not cards:
        LOGGER.error("build_graph_artifacts: cards must not be empty")
        raise ValueError("build_graph_artifacts: cards must not be empty")
    combos = (
        fetch_combo_variants(checkpoint_path=spellbook_checkpoint_path)
        if combo_variants is None
        else combo_variants
    )
    canonical_cards = _validate_canonical_cards(cards)
    canonical_by_name = {
        (card.canonical_name or card.name).casefold(): card for card in canonical_cards
    }
    signatures: dict[str, MechanicSignature] = {}
    mechanic_degree: Counter[str] = Counter()
    card_combo_degree: Counter[str] = Counter()
    combo_by_id: dict[str, SpellbookCombo] = {}
    combo_cards_by_id: dict[str, tuple[Card | str, ...]] = {}
    external_combo_units: dict[str, list[str]] = {}

    for card in canonical_cards:
        canonical = card.canonical_name or card.name
        signature = card.mechanic_signature()
        signatures[canonical] = signature
        mechanic_degree.update(signature.provides)
        mechanic_degree.update(signature.needs)
        mechanic_degree.update(signature.traits)

    for combo in combos:
        if not isinstance(combo, SpellbookCombo):
            LOGGER.error("build_graph_artifacts: combo must be normalized SpellbookCombo")
            raise TypeError("build_graph_artifacts: combo must be normalized SpellbookCombo")
        resolved_combo_cards: list[Card | str] = []
        for name in combo.card_names:
            if name.casefold() in canonical_by_name:
                resolved_combo_cards.append(canonical_by_name[name.casefold()])
                continue
            if card_resolver is None:
                resolved_combo_cards.append(name)
                continue
            resolved = card_resolver(name)
            if resolved is None:
                resolved_combo_cards.append(name)
                continue
            canonical_key = (resolved.canonical_name or resolved.name).casefold()
            if canonical_key not in canonical_by_name:
                LOGGER.error(
                    "build_graph_artifacts: resolver returned canonical absent from build: %s",
                    canonical_key,
                )
                raise ValueError(
                    f"Card resolver returned canonical card absent from graph input: {canonical_key}"
                )
            resolved_combo_cards.append(canonical_by_name[canonical_key])
        if combo.combo_id in combo_by_id:
            LOGGER.error("build_graph_artifacts: duplicate combo id %s", combo.combo_id)
            raise ValueError(f"Duplicate Commander Spellbook combo id: {combo.combo_id}")
        combo_by_id[combo.combo_id] = combo
        combo_cards_by_id[combo.combo_id] = tuple(resolved_combo_cards)
        card_combo_degree.update(
            (card.canonical_name or card.name).casefold()
            for card in resolved_combo_cards
            if isinstance(card, Card)
        )
        combo_unit_id = stable_graph_id("text-unit-combo", combo.combo_id)
        for resolved_card in resolved_combo_cards:
            if isinstance(resolved_card, str):
                external_combo_units.setdefault(resolved_card, []).append(combo_unit_id)

    entities: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    text_units: list[dict[str, Any]] = []
    entity_by_id: dict[str, dict[str, Any]] = {}
    adjacency: dict[str, list[dict[str, Any]]] = {}
    mechanic_text_units: dict[str, list[str]] = {}

    def add_entity(value: dict[str, Any]) -> None:
        entity_id = value["id"]
        if entity_id in entity_by_id:
            LOGGER.error("build_graph_artifacts: duplicate entity id %s", entity_id)
            raise ValueError(f"Duplicate graph entity id: {entity_id}")
        entity_by_id[entity_id] = value
        entities.append(value)

    def add_relationship(value: dict[str, Any]) -> None:
        relationships.append(value)
        forward = {
            "relationship_id": value["id"],
            "target": value["target_id"],
            "kind": value["kind"],
            "description": value["description"],
            "weight": value["semantic_weight"],
            "provenance": value["provenance"],
        }
        reverse = {
            "relationship_id": value["id"],
            "target": value["source_id"],
            "kind": value["kind"],
            "description": value["description"],
            "weight": value["semantic_weight"],
            "provenance": value["provenance"],
        }
        adjacency.setdefault(value["source_id"], []).append(forward)
        adjacency.setdefault(value["target_id"], []).append(reverse)

    for card_index, card in enumerate(canonical_cards):
        canonical = card.canonical_name or card.name
        card_id = card_graph_id(canonical)
        unit_id = stable_graph_id("text-unit-card", canonical)
        card_title = f"Card: {canonical}"
        signature = signatures[canonical]
        relationship_ids: list[str] = []
        entity_ids = [card_id]

        card_entity = _entity(
            entity_id=card_id,
            title=card_title,
            entity_type="CARD",
            description=card.to_graph_description(),
            text_unit_ids=[unit_id],
            degree=len(signature.provides)
            + len(signature.needs)
            + len(signature.traits)
            + card_combo_degree[canonical.casefold()],
            display_name=canonical,
        )
        card_entity["edhrec_rank"] = card.edhrec_rank
        add_entity(card_entity)
        for kind, tokens, base_weight, semantic_weight in (
            ("provides", signature.provides, _PROVIDE_NEED_WEIGHT, 8.0),
            ("needs", signature.needs, _PROVIDE_NEED_WEIGHT, 8.0),
            ("trait", signature.traits, _TRAIT_WEIGHT, 0.5),
        ):
            for token in sorted(tokens):
                mechanic_id = mechanic_graph_id(token)
                mechanic_title = f"Mechanic: {token}"
                entity_ids.append(mechanic_id)
                mechanic_text_units.setdefault(token, []).append(unit_id)
                degree = mechanic_degree[token]
                hub_factor = 1.0 / math.sqrt(max(1, degree))
                relationship = _relationship(
                    source_id=card_id,
                    source_title=card_title,
                    target_id=mechanic_id,
                    target_title=mechanic_title,
                    description=f"{canonical} {kind} {token.replace('_', ' ')}.",
                    kind=kind,
                    weight=base_weight * hub_factor,
                    semantic_weight=semantic_weight * hub_factor,
                    text_unit_id=unit_id,
                    provenance="MTGJSON oracle text",
                )
                add_relationship(relationship)
                relationship_ids.append(relationship["id"])
        text_units.append(
            {
                "id": unit_id,
                "human_readable_id": card_index,
                "text": card.to_graph_description(),
                "n_tokens": len(card.to_graph_description().split()),
                "document_id": stable_graph_id("document", "mtgjson-atomic-cards"),
                "entity_ids": sorted(set(entity_ids)),
                "relationship_ids": relationship_ids,
                "covariate_ids": [],
                "card_id": card_id,
                "scope": "card",
            }
        )

    for token in sorted(mechanic_text_units):
        mechanic_id = mechanic_graph_id(token)
        add_entity(
            _entity(
                entity_id=mechanic_id,
                title=f"Mechanic: {token}",
                entity_type="MECHANIC",
                description=_mechanic_description(token),
                text_unit_ids=mechanic_text_units[token],
                degree=mechanic_degree[token],
                display_name=token,
            )
        )

    for external_name in sorted(external_combo_units, key=str.casefold):
        add_entity(
            _entity(
                entity_id=external_card_graph_id(external_name),
                title=f"External card: {external_name}",
                entity_type="EXTERNAL_CARD",
                description=(
                    f"Commander Spellbook card reference: {external_name}. "
                    "This card is absent from the pinned MTGJSON AtomicCards snapshot."
                ),
                text_unit_ids=external_combo_units[external_name],
                degree=len(external_combo_units[external_name]),
                display_name=external_name,
            )
        )

    card_text_count = len(text_units)
    for combo_index, combo in enumerate(combo_by_id.values(), start=card_text_count):
        combo_id = combo_graph_id(combo.combo_id)
        unit_id = stable_graph_id("text-unit-combo", combo.combo_id)
        combo_title = f"Combo: Commander Spellbook {combo.combo_id}"
        description = (
            f"Commander Spellbook combo using {', '.join(combo.card_names)}. "
            f"Produces: {', '.join(combo.produces) if combo.produces else '(none classified by source)'}. "
            f"Additional requirements: {', '.join(combo.requirements) if combo.requirements else '(none)'}. "
            f"{combo.description}"
        )
        relationship_ids: list[str] = []
        entity_ids = [combo_id]
        combo_entity = _entity(
                entity_id=combo_id,
                title=combo_title,
                entity_type="COMBO",
                description=description,
                text_unit_ids=[unit_id],
                degree=len(combo.card_names),
                display_name=combo.combo_id,
            )
        combo_entity["requires_templates"] = list(combo.requirements)
        combo_entity["produces"] = list(combo.produces)
        combo_entity["legalities"] = list(combo.legalities)
        add_entity(combo_entity)
        for combo_card in combo_cards_by_id[combo.combo_id]:
            if isinstance(combo_card, Card):
                canonical = combo_card.canonical_name or combo_card.name
                target_id = card_graph_id(canonical)
                target_title = f"Card: {canonical}"
            else:
                canonical = combo_card
                target_id = external_card_graph_id(canonical)
                target_title = f"External card: {canonical}"
            entity_ids.append(target_id)
            relationship = _relationship(
                source_id=combo_id,
                source_title=combo_title,
                target_id=target_id,
                target_title=target_title,
                description=f"Commander Spellbook combo {combo.combo_id} uses {canonical}.",
                kind="combo_member",
                weight=_COMBO_WEIGHT,
                semantic_weight=16.0,
                text_unit_id=unit_id,
                provenance="Commander Spellbook",
            )
            add_relationship(relationship)
            relationship_ids.append(relationship["id"])
        text_units.append(
            {
                "id": unit_id,
                "human_readable_id": combo_index,
                "text": description,
                "n_tokens": len(description.split()),
                "document_id": stable_graph_id("document", "commander-spellbook"),
                "entity_ids": entity_ids,
                "relationship_ids": relationship_ids,
                "covariate_ids": [],
                "card_id": "",
                "scope": "combo",
            }
        )

    degree_by_id = {entity_id: len(edges) for entity_id, edges in adjacency.items()}
    title_by_id = {entity["id"]: entity["title"] for entity in entities}
    for entity_index, entity in enumerate(entities):
        entity["human_readable_id"] = entity_index
    for relationship_index, relationship in enumerate(relationships):
        relationship["human_readable_id"] = relationship_index
        relationship["combined_degree"] = (
            degree_by_id[relationship["source_id"]] + degree_by_id[relationship["target_id"]]
        )
        if relationship["source"] != title_by_id[relationship["source_id"]]:
            LOGGER.error("build_graph_artifacts: source title mismatch for %s", relationship["id"])
            raise ValueError("Graph relationship source title does not match entity title")
        if relationship["target"] != title_by_id[relationship["target_id"]]:
            LOGGER.error("build_graph_artifacts: target title mismatch for %s", relationship["id"])
            raise ValueError("Graph relationship target title does not match entity title")

    output_dir.mkdir(parents=True, exist_ok=True)
    entities_path = output_dir / "entities.parquet"
    relationships_path = output_dir / "relationships.parquet"
    text_units_path = output_dir / "text_units.parquet"
    adjacency_path = output_dir / "graph_adjacency.json"
    manifest_path = output_dir / "manifest.json"
    _write_parquet(entities, entities_path)
    _write_parquet(relationships, relationships_path)
    _write_parquet(text_units, text_units_path)

    adjacency_payload = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "nodes": entity_by_id,
        "adjacency": adjacency,
    }
    adjacency_path.write_text(json.dumps(adjacency_payload, ensure_ascii=False), encoding="utf-8")
    adjacency_hash = hashlib.sha256(adjacency_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "card_count": len(canonical_cards),
        "combo_count": len(combo_by_id),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "external_card_count": len(external_combo_units),
        "adjacency_sha256": adjacency_hash,
        "spellbook_combo_sha256": hashlib.sha256(
            json.dumps(
                [
                    {
                        "combo_id": combo.combo_id,
                        "card_names": combo.card_names,
                        "produces": combo.produces,
                        "description": combo.description,
                        "legalities": combo.legalities,
                        "requirements": combo.requirements,
                    }
                    for combo in combo_by_id.values()
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        "files": {
            "entities": entities_path.name,
            "relationships": relationships_path.name,
            "text_units": text_units_path.name,
            "adjacency": adjacency_path.name,
            "communities": "communities.parquet",
            "community_reports": "community_reports.parquet",
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    LOGGER.info(
        "build_graph_artifacts: cards=%d combos=%d entities=%d relationships=%d output=%s",
        len(canonical_cards),
        len(combo_by_id),
        len(entities),
        len(relationships),
        output_dir,
    )
    return GraphBuildResult(
        entities_path=entities_path,
        relationships_path=relationships_path,
        text_units_path=text_units_path,
        adjacency_path=adjacency_path,
        manifest_path=manifest_path,
        card_count=len(canonical_cards),
        combo_count=len(combo_by_id),
    )
