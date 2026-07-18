"""Runtime semantic retrieval and graph reasoning over the MTG GraphRAG index."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.lib.config import (
    GRAPHRAG_COMPLETION_MODEL,
    GRAPHRAG_EMBEDDING_MODEL,
    GRAPHRAG_INPUT_DIR,
    GRAPHRAG_LANCEDB_DIR,
    GRAPHRAG_MANIFEST_PATH,
    GRAPHRAG_SETTINGS_PATH,
    load_required_gemini_api_key,
)
from src.lib.graphrag_build import card_graph_id, mechanic_graph_id
from src.obj.card import Card, MechanicSignature
from src.utils.logger import LOGGER


@dataclass(frozen=True)
class GraphRecommendation:
    """A deterministic deck-addition candidate with graph provenance."""

    name: str
    score: float
    reasons: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class GraphAnalysis:
    """A complete cached deck analysis tied to one deck/index/model fingerprint."""

    recommendations: tuple[GraphRecommendation, ...]
    explanation: str | None
    fingerprint: str


_QUERY_MECHANIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mana acceleration", "produce_mana"),
    ("mana ramp", "produce_mana"),
    ("graveyard recursion", "recursion"),
    ("recursion", "recursion"),
    ("protect", "protect_permanents"),
    ("indestructible", "protect_permanents"),
    ("creature enters", "creature_etb"),
    ("creature dies", "creature_dies"),
    ("instant or sorcery", "instant_sorcery_cast"),
    ("creature token", "create_tokens"),
    ("tokens", "create_tokens"),
    ("draw cards", "draw_cards"),
    ("draw card", "draw_cards"),
    ("destroy target", "remove_permanent"),
    ("remove permanent", "remove_permanent"),
)


def _query_mechanic_tokens(query: str) -> set[str]:
    """Map common natural-language deck queries to the graph vocabulary."""
    normalized = query.casefold()
    return {
        token
        for phrase, token in _QUERY_MECHANIC_PATTERNS
        if phrase in normalized
    }


def _card_popularity_score(node: dict[str, Any]) -> float:
    """Use MTGJSON's EDHREC rank as a stable broad-query quality prior."""
    rank = node["edhrec_rank"]
    if not isinstance(rank, int) or rank < 0:
        LOGGER.error("_card_popularity_score: invalid EDHREC rank: %r", rank)
        raise ValueError(f"Invalid EDHREC rank in graph card node: {rank!r}")
    return 2.0 / (1.0 + math.log10(rank)) if rank > 0 else 0.0


def deck_analysis_fingerprint(
    deck_cards: list[Card],
    candidates: list[Card],
    limit: int,
    deck_context: dict[str, Any],
    manifest_hash: str,
    completion_model: str = GRAPHRAG_COMPLETION_MODEL,
    embedding_model: str = GRAPHRAG_EMBEDDING_MODEL,
) -> str:
    """Hash every input that can change a complete recommendation analysis."""
    if not deck_cards:
        LOGGER.error("deck_analysis_fingerprint: deck_cards must not be empty")
        raise ValueError("deck_analysis_fingerprint: deck_cards must not be empty")
    if limit <= 0 or not manifest_hash or not completion_model or not embedding_model:
        LOGGER.error("deck_analysis_fingerprint: limit, manifest, and models must be valid")
        raise ValueError("deck_analysis_fingerprint: limit, manifest, and models must be valid")
    counts = Counter((card.canonical_name or card.name).casefold() for card in deck_cards)
    return hashlib.sha256(
        json.dumps(
            {
                "cards": sorted(counts.items()),
                "eligible_candidates": sorted(
                    (card.canonical_name or card.name).casefold() for card in candidates
                ),
                "deck_context": deck_context,
                "manifest": manifest_hash,
                "completion_model": completion_model,
                "embedding_model": embedding_model,
                "limit": limit,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class GraphRAGService:
    """Singleton owner of validated GraphRAG data, vectors, traversal, and cache."""

    _instance: GraphRAGService | None = None

    @classmethod
    def inst(cls) -> GraphRAGService:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ready = False
        self._manifest: dict[str, Any] | None = None
        self._nodes: dict[str, dict[str, Any]] = {}
        self._adjacency: dict[str, list[dict[str, Any]]] = {}
        self._search_table: Any = None
        self._entities: Any = None
        self._relationships: Any = None
        self._text_units: Any = None
        self._communities: Any = None
        self._community_reports: Any = None
        self._community_level = 0
        self._graphrag_config: Any = None
        self._analysis_cache: dict[str, GraphAnalysis] = {}

    @staticmethod
    def _read_json_object(path: Path, label: str) -> dict[str, Any]:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            LOGGER.error("%s must be a JSON object: %s", label, path)
            raise TypeError(f"{label} must be a JSON object: {path}")
        return value

    @staticmethod
    def _verify_manifest_content_hash(manifest: dict[str, Any]) -> None:
        expected = manifest["content_hash"]
        if not isinstance(expected, str) or not expected:
            LOGGER.error("GraphRAG manifest content_hash is invalid")
            raise ValueError("GraphRAG manifest content_hash is invalid")
        hash_payload = dict(manifest)
        del hash_payload["content_hash"]
        actual = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if expected != actual:
            LOGGER.error("GraphRAG manifest content hash mismatch")
            raise ValueError("GraphRAG manifest content hash mismatch")

    @staticmethod
    def _verify_artifacts(manifest: dict[str, Any]) -> None:
        files = manifest["files"]
        hashes = manifest["artifact_sha256"]
        if not isinstance(files, dict) or not isinstance(hashes, dict):
            LOGGER.error("GraphRAG manifest files and artifact_sha256 must be objects")
            raise TypeError("GraphRAG manifest files and artifact_sha256 must be objects")
        for key, filename in files.items():
            if not isinstance(filename, str) or not filename:
                LOGGER.error("GraphRAG manifest filename is invalid for %s", key)
                raise ValueError(f"GraphRAG manifest filename is invalid for {key}")
            path = GRAPHRAG_INPUT_DIR / filename
            if not path.is_file():
                LOGGER.error("GraphRAG artifact not found: %s", path)
                raise FileNotFoundError(f"GraphRAG artifact not found: {path}")
            expected_hash = hashes[key]
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if expected_hash != actual_hash:
                LOGGER.error("GraphRAG artifact checksum mismatch: %s", path)
                raise ValueError(f"GraphRAG artifact checksum mismatch: {path}")

    def load_sync(self) -> None:
        """Validate and load every required graph, report, and vector artifact."""
        with self._lock:
            if self._ready:
                return
            if not GRAPHRAG_MANIFEST_PATH.is_file():
                LOGGER.error("GraphRAG manifest not found: %s", GRAPHRAG_MANIFEST_PATH)
                raise FileNotFoundError(f"GraphRAG manifest not found: {GRAPHRAG_MANIFEST_PATH}")
            if not GRAPHRAG_SETTINGS_PATH.is_file():
                LOGGER.error("GraphRAG settings not found: %s", GRAPHRAG_SETTINGS_PATH)
                raise FileNotFoundError(f"GraphRAG settings not found: {GRAPHRAG_SETTINGS_PATH}")
            manifest = self._read_json_object(GRAPHRAG_MANIFEST_PATH, "GraphRAG manifest")
            self._verify_manifest_content_hash(manifest)
            self._verify_artifacts(manifest)

            adjacency_filename = manifest["files"]["adjacency"]
            graph_payload = self._read_json_object(
                GRAPHRAG_INPUT_DIR / adjacency_filename,
                "GraphRAG adjacency",
            )
            nodes = graph_payload["nodes"]
            adjacency = graph_payload["adjacency"]
            if not isinstance(nodes, dict) or not isinstance(adjacency, dict):
                LOGGER.error("GraphRAG adjacency nodes and adjacency must be objects")
                raise TypeError("GraphRAG adjacency nodes and adjacency must be objects")

            import lancedb
            import pandas as pd
            from graphrag.config.load_config import load_config

            db = lancedb.connect(str(GRAPHRAG_LANCEDB_DIR))
            table_names = set(db.table_names())
            if "mtg_search" not in table_names:
                LOGGER.error("GraphRAG LanceDB table mtg_search not found in %s", GRAPHRAG_LANCEDB_DIR)
                raise FileNotFoundError(
                    f"GraphRAG LanceDB table mtg_search not found in {GRAPHRAG_LANCEDB_DIR}"
                )
            entities = pd.read_parquet(GRAPHRAG_INPUT_DIR / manifest["files"]["entities"])
            relationships = pd.read_parquet(GRAPHRAG_INPUT_DIR / manifest["files"]["relationships"])
            text_units = pd.read_parquet(GRAPHRAG_INPUT_DIR / manifest["files"]["text_units"])
            communities = pd.read_parquet(GRAPHRAG_INPUT_DIR / manifest["files"]["communities"])
            reports = pd.read_parquet(GRAPHRAG_INPUT_DIR / manifest["files"]["community_reports"])
            for label, frame in (
                ("entities", entities),
                ("relationships", relationships),
                ("text_units", text_units),
                ("communities", communities),
                ("community_reports", reports),
            ):
                if frame.empty:
                    LOGGER.error("GraphRAG %s artifact is empty", label)
                    raise ValueError(f"GraphRAG {label} artifact is empty")
            if "level" not in communities.columns:
                LOGGER.error("GraphRAG communities artifact has no level column")
                raise ValueError("GraphRAG communities artifact has no level column")
            if "level" not in reports.columns:
                LOGGER.error("GraphRAG community reports artifact has no level column")
                raise ValueError("GraphRAG community reports artifact has no level column")
            report_levels = sorted({int(level) for level in reports["level"].tolist()})
            eligible_report_levels = [level for level in report_levels if level <= 2]
            if not eligible_report_levels:
                LOGGER.error("GraphRAG community reports have no supported level at or below 2")
                raise ValueError("GraphRAG community reports have no supported level at or below 2")

            os.environ["GEMINI_API_KEY"] = load_required_gemini_api_key()
            self._manifest = manifest
            self._nodes = nodes
            self._adjacency = adjacency
            self._search_table = db.open_table("mtg_search")
            self._entities = entities
            self._relationships = relationships
            self._text_units = text_units
            self._communities = communities
            self._community_reports = reports
            self._community_level = max(eligible_report_levels)
            self._graphrag_config = load_config(GRAPHRAG_SETTINGS_PATH.parent)
            self._ready = True
            LOGGER.info(
                "GraphRAG service ready cards=%s combos=%s",
                manifest["card_count"],
                manifest["combo_count"],
            )

    def is_ready(self) -> bool:
        """Return whether all graph artifacts have been validated and loaded."""
        with self._lock:
            return self._ready

    def _require_ready(self) -> None:
        if not self.is_ready():
            LOGGER.error("GraphRAG service is not ready")
            raise RuntimeError("GraphRAG service is not ready")

    def manifest_hash(self) -> str:
        """Return the exact loaded graph/model content hash."""
        self._require_ready()
        assert self._manifest is not None
        value = self._manifest["content_hash"]
        if not isinstance(value, str) or not value:
            LOGGER.error("GraphRAG manifest content_hash is invalid")
            raise ValueError("GraphRAG manifest content_hash is invalid")
        return value

    def _embed_query(self, query: str) -> list[float]:
        if not query or not query.strip():
            LOGGER.error("_embed_query: query must be non-empty")
            raise ValueError("_embed_query: query must be non-empty")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=load_required_gemini_api_key())
        response = client.models.embed_content(
            model=GRAPHRAG_EMBEDDING_MODEL,
            contents=query.strip(),
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=3072,
            ),
        )
        embeddings: Any = response.embeddings
        if not isinstance(embeddings, list) or len(embeddings) != 1:
            LOGGER.error("_embed_query: expected exactly one embedding")
            raise ValueError("_embed_query: expected exactly one embedding")
        values: Any = embeddings[0].values
        if not isinstance(values, list) or not values:
            LOGGER.error("_embed_query: received invalid embedding values")
            raise ValueError("_embed_query: received invalid embedding values")
        return [float(value) for value in values]

    def _vector_rows(self, vector: list[float], scope: str, limit: int) -> list[dict[str, Any]]:
        assert self._search_table is not None
        rows = (
            self._search_table.search(vector)
            .where(f"scope = '{scope}'", prefilter=True)
            .limit(limit)
            .to_list()
        )
        if not isinstance(rows, list):
            LOGGER.error("_vector_rows: LanceDB returned an invalid result")
            raise TypeError("LanceDB returned an invalid result")
        return rows

    def rank_card_names(self, query: str, search_type: str, n_results: int) -> list[str]:
        """Rank cards using card vectors or mechanic-vector traversal."""
        self._require_ready()
        if n_results <= 0:
            LOGGER.error("rank_card_names: n_results must be positive, got %s", n_results)
            raise ValueError(f"rank_card_names: n_results must be positive, got {n_results}")
        if search_type not in ("general", "trigger", "effect"):
            LOGGER.error("rank_card_names: invalid search_type %r", search_type)
            raise ValueError(f"rank_card_names: invalid search_type {search_type!r}")
        vector = self._embed_query(query)
        mechanic_rows = self._vector_rows(vector, "mechanic", max(20, n_results * 4))
        card_rows = self._vector_rows(vector, "general", max(500, n_results * 50))
        card_vector_scores = {
            str(row["entity_id"]): 1.0 / (1.0 + max(0.0, float(row["_distance"])))
            for row in card_rows
        }
        allowed_kinds = (
            {"needs"}
            if search_type == "trigger"
            else {"provides"}
            if search_type == "effect"
            else {"provides", "needs"}
        )
        mechanic_scores: dict[str, float] = {}
        query_tokens = _query_mechanic_tokens(query)
        for token in query_tokens:
            mechanic_id = mechanic_graph_id(token)
            if mechanic_id not in self._adjacency:
                LOGGER.error("rank_card_names: query mechanic is absent from graph: %s", token)
                raise KeyError(f"Query mechanic is absent from graph: {token}")
            mechanic_scores[mechanic_id] = 3.0
        if not query_tokens:
            for mechanic_rank, row in enumerate(mechanic_rows):
                mechanic_id = str(row["entity_id"])
                vector_score = 1.0 / (1.0 + max(0.0, float(row["_distance"])))
                score = vector_score / (1.0 + mechanic_rank * 0.05)
                mechanic_scores[mechanic_id] = max(
                    mechanic_scores[mechanic_id] if mechanic_id in mechanic_scores else 0.0,
                    score,
                )

        card_scores: dict[str, float] = {}
        card_names: dict[str, str] = {}
        if search_type == "general" and not query_tokens:
            for row in card_rows:
                card_id = str(row["entity_id"])
                node = self._nodes[card_id]
                popularity_score = _card_popularity_score(node)
                card_scores[card_id] = 0.5 * card_vector_scores[card_id] + popularity_score
                card_names[card_id] = str(node["display_name"])

        for mechanic_id, mechanic_score in mechanic_scores.items():
            for edge in self._adjacency[mechanic_id]:
                target_id = edge["target"]
                if edge["kind"] not in allowed_kinds:
                    continue
                target_node = self._nodes[target_id]
                if target_node["type"] != "CARD":
                    continue
                popularity_score = _card_popularity_score(target_node)
                base_score = popularity_score
                if target_id in card_vector_scores:
                    base_score += 0.5 * card_vector_scores[target_id]
                if target_id not in card_scores:
                    card_scores[target_id] = base_score
                card_scores[target_id] += mechanic_score * float(edge["weight"])
                card_names[target_id] = str(target_node["display_name"])
        ordered_ids = sorted(
            card_scores,
            key=lambda entity_id: (-card_scores[entity_id], card_names[entity_id].casefold()),
        )
        return [card_names[entity_id] for entity_id in ordered_ids[:n_results]]

    def _combo_ids_for_card(self, card_id: str) -> set[str]:
        return {
            str(edge["target"])
            for edge in self._adjacency[card_id]
            if edge["kind"] == "combo_member" and self._nodes[edge["target"]]["type"] == "COMBO"
        }

    def _typed_edge_weights(self, card_id: str) -> dict[str, dict[str, float]]:
        """Return hub-suppressed semantic weights by relation kind and mechanic."""
        weights: dict[str, dict[str, float]] = {"provides": {}, "needs": {}, "trait": {}}
        for edge in self._adjacency[card_id]:
            kind = edge["kind"]
            if kind not in weights:
                continue
            target = self._nodes[edge["target"]]
            if target["type"] != "MECHANIC":
                LOGGER.error("_typed_edge_weights: typed edge target is not a mechanic: %s", edge)
                raise ValueError("Typed graph edge target is not a mechanic entity")
            weights[kind][str(target["display_name"])] = float(edge["weight"])
        return weights

    def pair_evidence(self, card_a: Card, card_b: Card) -> tuple[float, list[dict[str, Any]]]:
        """Return deterministic synergy from signatures and curated combo paths."""
        self._require_ready()
        signature_a = card_a.mechanic_signature()
        signature_b = card_b.mechanic_signature()
        provides_a_to_b = set(signature_a.provides & signature_b.needs)
        provides_b_to_a = set(signature_b.provides & signature_a.needs)
        traits = set(signature_a.traits & signature_b.traits)
        name_a = card_a.canonical_name or card_a.name
        name_b = card_b.canonical_name or card_b.name
        weights_a = self._typed_edge_weights(card_graph_id(name_a))
        weights_b = self._typed_edge_weights(card_graph_id(name_b))
        score = sum(
            min(weights_a["provides"][token], weights_b["needs"][token])
            for token in provides_a_to_b
        )
        score += sum(
            min(weights_b["provides"][token], weights_a["needs"][token])
            for token in provides_b_to_a
        )
        score += sum(
            min(weights_a["trait"][token], weights_b["trait"][token])
            for token in traits
        )
        evidence: list[dict[str, Any]] = []
        for token in sorted(provides_a_to_b):
            evidence.append(
                {
                    "source": name_a,
                    "target": name_b,
                    "token": token,
                    "kind": "provides",
                    "provenance": "MTGJSON oracle text",
                }
            )
        for token in sorted(provides_b_to_a):
            evidence.append(
                {
                    "source": name_b,
                    "target": name_a,
                    "token": token,
                    "kind": "provides",
                    "provenance": "MTGJSON oracle text",
                }
            )
        for token in sorted(traits):
            evidence.append(
                {
                    "source": name_a,
                    "target": name_b,
                    "token": token,
                    "kind": "trait",
                    "provenance": "MTGJSON card properties",
                }
            )
        common_combos = self._combo_ids_for_card(card_graph_id(name_a)) & self._combo_ids_for_card(
            card_graph_id(name_b)
        )
        for combo_id in sorted(common_combos):
            score += 16.0
            evidence.append(
                {
                    "source": name_a,
                    "target": name_b,
                    "token": str(self._nodes[combo_id]["display_name"]),
                    "kind": "combo",
                    "provenance": "Commander Spellbook",
                }
            )
        return round(score, 3), evidence

    def _analysis_fingerprint(
        self,
        deck_cards: list[Card],
        candidates: list[Card],
        limit: int,
        deck_context: dict[str, Any],
    ) -> str:
        return deck_analysis_fingerprint(
            deck_cards=deck_cards,
            candidates=candidates,
            limit=limit,
            deck_context=deck_context,
            manifest_hash=self.manifest_hash(),
        )

    def recommend_additions(
        self,
        deck_cards: list[Card],
        candidates: list[Card],
        limit: int,
    ) -> list[GraphRecommendation]:
        """Rank additions from needs/provides/traits and complete combo paths."""
        self._require_ready()
        if not deck_cards:
            LOGGER.error("recommend_additions: deck_cards must not be empty")
            raise ValueError("recommend_additions: deck_cards must not be empty")
        if limit <= 0:
            LOGGER.error("recommend_additions: limit must be positive, got %s", limit)
            raise ValueError(f"recommend_additions: limit must be positive, got {limit}")

        deck_names = {(card.canonical_name or card.name).casefold() for card in deck_cards}
        deck_ids = {card_graph_id(card.canonical_name or card.name) for card in deck_cards}
        deck_provides: set[str] = set()
        deck_needs: set[str] = set()
        deck_traits: set[str] = set()
        for card in deck_cards:
            signature = card.mechanic_signature()
            deck_provides.update(signature.provides)
            deck_needs.update(signature.needs)
            deck_traits.update(signature.traits)
        deck_query = (
            "Recommend cards that synergize with this Magic: The Gathering deck. "
            f"Current cards: {', '.join(card.canonical_name or card.name for card in deck_cards)}. "
            f"Provides: {', '.join(sorted(deck_provides)) or '(none)'}. "
            f"Needs and payoffs: {', '.join(sorted(deck_needs)) or '(none)'}. "
            f"Traits: {', '.join(sorted(deck_traits)) or '(none)'}."
        )
        semantic_vector = self._embed_query(deck_query)
        semantic_rows = self._vector_rows(semantic_vector, "general", max(1000, limit * 100))
        semantic_scores = {
            str(row["entity_id"]): 1.0 / (1.0 + max(0.0, float(row["_distance"])))
            for row in semantic_rows
        }

        ranked: list[GraphRecommendation] = []
        for candidate in candidates:
            name = candidate.canonical_name or candidate.name
            if name.casefold() in deck_names:
                continue
            signature = candidate.mechanic_signature()
            fill_needs = set(signature.provides & deck_needs)
            activate_payoffs = set(signature.needs & deck_provides)
            shared_traits = set(signature.traits & deck_traits)
            candidate_id = card_graph_id(name)
            edge_weights = self._typed_edge_weights(candidate_id)
            score = sum(edge_weights["provides"][token] for token in fill_needs)
            score += sum(edge_weights["needs"][token] for token in activate_payoffs)
            score += sum(edge_weights["trait"][token] for token in shared_traits)
            reasons: list[str] = []
            sources: set[str] = set()
            if fill_needs:
                reasons.append(f"Provides deck needs: {', '.join(sorted(fill_needs))}")
                sources.add("MTGJSON oracle text")
            if activate_payoffs:
                reasons.append(f"Uses deck resources: {', '.join(sorted(activate_payoffs))}")
                sources.add("MTGJSON oracle text")
            if shared_traits:
                reasons.append(f"Matches deck traits: {', '.join(sorted(shared_traits)[:3])}")
                sources.add("MTGJSON card properties")
            if candidate_id in semantic_scores:
                score += 2.0 * semantic_scores[candidate_id]
                score += 0.25 * _card_popularity_score(self._nodes[candidate_id])
                reasons.append("Semantically matches the deck's cards and mechanics")
                sources.add("Gemini embedding over MTGJSON oracle text")

            for combo_id in sorted(self._combo_ids_for_card(candidate_id)):
                requires_templates = self._nodes[combo_id]["requires_templates"]
                if not isinstance(requires_templates, list):
                    LOGGER.error("recommend_additions: combo requirements are invalid for %s", combo_id)
                    raise TypeError(f"Graph combo requirements are invalid for {combo_id}")
                if requires_templates:
                    continue
                combo_card_ids = {
                    str(edge["target"])
                    for edge in self._adjacency[combo_id]
                    if edge["kind"] == "combo_member" and self._nodes[edge["target"]]["type"] == "CARD"
                }
                other_members = combo_card_ids - {candidate_id}
                if other_members and other_members.issubset(deck_ids):
                    score += 16.0
                    combo_label = str(self._nodes[combo_id]["display_name"])
                    reasons.append(f"Completes Commander Spellbook combo {combo_label}")
                    sources.add("Commander Spellbook")
            if score <= 0:
                continue
            ranked.append(
                GraphRecommendation(
                    name=name,
                    score=round(score, 3),
                    reasons=tuple(reasons),
                    sources=tuple(sorted(sources)),
                )
            )
        result = sorted(ranked, key=lambda item: (-item.score, item.name.casefold()))[:limit]
        return result

    async def analyze_deck(
        self,
        deck_cards: list[Card],
        candidates: list[Card],
        limit: int,
        deck_context: dict[str, Any],
    ) -> GraphAnalysis:
        """Return and cache a complete deterministic + local-search deck analysis."""
        self._require_ready()
        if not isinstance(deck_context, dict):
            LOGGER.error("analyze_deck: deck_context must be an object")
            raise TypeError("analyze_deck: deck_context must be an object")
        fingerprint = self._analysis_fingerprint(deck_cards, candidates, limit, deck_context)
        with self._lock:
            cached = self._analysis_cache[fingerprint] if fingerprint in self._analysis_cache else None
        if cached is not None:
            return cached
        recommendations = self.recommend_additions(deck_cards, candidates, limit)
        explanation = (
            await self.explain_recommendations(deck_cards, recommendations)
            if recommendations
            else None
        )
        analysis = GraphAnalysis(
            recommendations=tuple(recommendations),
            explanation=explanation,
            fingerprint=fingerprint,
        )
        with self._lock:
            self._analysis_cache[fingerprint] = analysis
        return analysis

    async def explain_recommendations(
        self,
        deck_cards: list[Card],
        recommendations: list[GraphRecommendation],
    ) -> str:
        """Use one GraphRAG local-search call to summarize recommendation evidence."""
        self._require_ready()
        if not deck_cards:
            LOGGER.error("explain_recommendations: deck_cards must not be empty")
            raise ValueError("explain_recommendations: deck_cards must not be empty")
        if not recommendations:
            LOGGER.error("explain_recommendations: recommendations must not be empty")
            raise ValueError("explain_recommendations: recommendations must not be empty")
        from graphrag.api import local_search

        deck_names = ", ".join(card.canonical_name or card.name for card in deck_cards)
        recommendation_names = ", ".join(item.name for item in recommendations)
        query = (
            "Using only the graph evidence and cited sources, explain in one concise paragraph why these "
            f"recommended cards fit the deck. Deck: {deck_names}. Recommendations: {recommendation_names}."
        )
        response, _context = await local_search(
            config=self._graphrag_config,
            entities=self._entities,
            communities=self._communities,
            community_reports=self._community_reports,
            text_units=self._text_units,
            relationships=self._relationships,
            covariates=None,
            community_level=self._community_level,
            response_type="One concise paragraph with data citations",
            query=query,
        )
        if not isinstance(response, str) or not response.strip():
            LOGGER.error("explain_recommendations: GraphRAG local search returned invalid text")
            raise ValueError("GraphRAG local search returned invalid recommendation explanation")
        return response.strip()
