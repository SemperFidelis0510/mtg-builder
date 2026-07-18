"""Public behavior tests for deterministic GraphRAG primitives."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pandas as pd
import pytest


def test_card_mechanic_signature_describes_oracle_evidence() -> None:
    """A card exposes normalized provides, needs, and controlled traits."""
    from src.obj.card import Card

    card = Card(
        name="Witness",
        canonical_name="Witness",
        type_line="Creature - Elf",
        types=["Creature"],
        subtypes=["Elf"],
        keywords=["Prowess"],
        text="Whenever another creature enters the battlefield, draw a card.",
    )

    signature = card.mechanic_signature()

    assert "creature_etb" in signature.needs
    assert "draw_cards" in signature.provides
    assert "type:creature" in signature.traits
    assert "keyword:prowess" in signature.traits


def test_spellbook_variant_normalizes_cards_results_and_provenance() -> None:
    """The public normalizer produces typed combo source data."""
    from src.lib.spellbook import normalize_combo_variant

    combo = normalize_combo_variant(
        {
            "id": "one-two",
            "uses": [
                {"card": {"name": "One"}},
                {"card": {"name": "Two"}},
            ],
            "produces": [
                {"feature": {"name": "Infinite mana"}},
                {"feature": {"name": "Win the game"}},
            ],
            "description": "Perform the loop.",
            "legalities": {"commander": True, "modern": False},
            "requires": [],
        }
    )

    assert combo.combo_id == "one-two"
    assert combo.card_names == ("One", "Two")
    assert combo.produces == ("Infinite mana", "Win the game")
    assert combo.legalities == ("commander",)


def test_spellbook_variant_rejects_malformed_required_data() -> None:
    """Malformed curated combo data fails visibly instead of being skipped."""
    from src.lib.spellbook import normalize_combo_variant

    with pytest.raises(ValueError, match="non-empty"):
        normalize_combo_variant(
            {
                "id": "broken",
                "uses": [{"card": {"name": ""}}, {"card": {"name": "Two"}}],
                "produces": [{"feature": {"name": "Result"}}],
                "description": "Broken.",
                "legalities": {},
                "requires": [],
            }
        )


def test_spellbook_variant_allows_source_record_without_classified_result() -> None:
    """An empty produces list is valid source data, not a malformed response."""
    from src.lib.spellbook import normalize_combo_variant

    combo = normalize_combo_variant(
        {
            "id": "unclassified",
            "uses": [{"card": {"name": "One"}}, {"card": {"name": "Two"}}],
            "produces": [],
            "description": "A valid interaction awaiting result classification.",
            "legalities": {"commander": True},
            "requires": [],
        }
    )

    assert combo.produces == ()


def test_spellbook_variant_allows_one_exact_card_plus_template_requirement() -> None:
    """Spellbook can model a combo with one exact card and one required template."""
    from src.lib.spellbook import normalize_combo_variant

    combo = normalize_combo_variant(
        {
            "id": "card-template",
            "uses": [{"card": {"name": "One"}}],
            "produces": [{"feature": {"name": "Infinite mana"}}],
            "description": "Use the matching template permanent.",
            "legalities": {"commander": True},
            "requires": [{"template": {"name": "A mana-producing permanent"}}],
        }
    )

    assert combo.card_names == ("One",)
    assert combo.requirements == ("A mana-producing permanent",)


def test_spellbook_fetch_follows_server_offset_and_normalizes_pages(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination advances by the actual server page and validates each variant."""
    from src.lib import spellbook

    def variant(combo_id: str, first: str, second: str) -> dict:
        return {
            "id": combo_id,
            "uses": [{"card": {"name": first}}, {"card": {"name": second}}],
            "produces": [{"feature": {"name": "Infinite mana"}}],
            "description": "Repeat.",
            "legalities": {"commander": True},
            "requires": [],
        }

    payloads = [
        {
            "results": [variant("one", "A", "B")],
            "next": "https://backend.commanderspellbook.com/variants/?limit=100&offset=1",
        },
        {"results": [variant("two", "C", "D")], "next": None},
    ]
    offsets: list[int] = []

    def fake_get(url, *, params, timeout, headers):
        offsets.append(params["offset"])
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = payloads[len(offsets) - 1]
        return response

    monkeypatch.setattr(spellbook.requests, "get", fake_get)
    monkeypatch.setattr(spellbook.time, "sleep", lambda seconds: None)

    combos = spellbook.fetch_combo_variants(checkpoint_path=tmp_path / "checkpoint.json")

    assert offsets == [0, 1]
    assert [combo.combo_id for combo in combos] == ["one", "two"]
    assert (tmp_path / "checkpoint.json").exists()
    assert [combo.combo_id for combo in spellbook.fetch_combo_variants(
        checkpoint_path=tmp_path / "checkpoint.json"
    )] == ["one", "two"]
    assert offsets == [0, 1]


def test_byog_artifacts_use_titles_links_weights_and_combo_entities(tmp_path) -> None:
    """The generated Parquet tables satisfy BYOG links without dense combo cliques."""
    from src.lib.graphrag_build import build_graph_artifacts
    from src.lib.spellbook import normalize_combo_variant
    from src.obj.card import Card

    cards = [
        Card(
            name="Payoff",
            canonical_name="Payoff",
            type_line="Creature",
            types=["Creature"],
            text="Whenever another creature enters the battlefield, draw a card.",
        ),
        Card(
            name="Maker",
            canonical_name="Maker",
            type_line="Creature",
            types=["Creature"],
            text="Create a 1/1 creature token.",
        ),
    ]
    combo = normalize_combo_variant(
        {
            "id": "payoff-maker",
            "uses": [
                {"card": {"name": "Payoff"}},
                {"card": {"name": "Maker"}},
            ],
            "produces": [{"feature": {"name": "Draw the deck"}}],
            "description": "Repeat the creature loop.",
            "legalities": {"commander": True},
            "requires": [],
        }
    )

    result = build_graph_artifacts(cards, tmp_path, [combo])
    entities = pd.read_parquet(result.entities_path)
    relationships = pd.read_parquet(result.relationships_path)
    text_units = pd.read_parquet(result.text_units_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert {"id", "title", "description", "text_unit_ids"}.issubset(entities.columns)
    assert {"id", "source", "target", "description", "weight", "text_unit_ids"}.issubset(
        relationships.columns
    )
    assert {"id", "text", "entity_ids", "relationship_ids"}.issubset(text_units.columns)
    assert relationships["source"].str.startswith(("CARD:", "COMBO:")).all()
    combo_edges = relationships[relationships["kind"] == "combo_member"]
    assert len(combo_edges) == 2
    assert not (
        (relationships["source"] == "CARD: PAYOFF")
        & (relationships["target"] == "CARD: MAKER")
    ).any()
    assert relationships.loc[relationships["kind"] == "trait", "weight"].max() < relationships.loc[
        relationships["kind"].isin(["provides", "needs"]), "weight"
    ].max()
    assert manifest["schema_version"] == 3
    assert manifest["combo_count"] == 1


def test_byog_preserves_spellbook_card_missing_from_pinned_mtgjson(tmp_path) -> None:
    """Upstream-only card references become attributed external graph entities."""
    from src.lib.graphrag_build import build_graph_artifacts
    from src.lib.spellbook import normalize_combo_variant
    from src.obj.card import Card

    combo = normalize_combo_variant(
        {
            "id": "external-card",
            "uses": [
                {"card": {"name": "Known"}},
                {"card": {"name": "_____ Goblin"}},
            ],
            "produces": [{"feature": {"name": "A combo result"}}],
            "description": "Use both cards.",
            "legalities": {"commander": True},
            "requires": [],
        }
    )

    result = build_graph_artifacts(
        [Card(name="Known", canonical_name="Known")],
        tmp_path,
        [combo],
    )
    entities = pd.read_parquet(result.entities_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    external = entities[entities["type"] == "EXTERNAL_CARD"]
    assert external["display_name"].tolist() == ["_____ Goblin"]
    assert manifest["external_card_count"] == 1


def test_analysis_fingerprint_invalidates_exactly_on_deck_index_model_or_context() -> None:
    """Any changed deck/index/model/config input creates a different cache key."""
    from src.lib.graphrag_service import deck_analysis_fingerprint
    from src.obj.card import Card

    deck = [Card(name="One", canonical_name="One")]
    candidates = [Card(name="Two", canonical_name="Two")]
    context = {"format": "commander", "colors": ["U"], "main": ["One"]}
    original = deck_analysis_fingerprint(deck, candidates, 12, context, "manifest-a")

    assert original == deck_analysis_fingerprint(deck, candidates, 12, context, "manifest-a")
    assert original != deck_analysis_fingerprint(
        [*deck, Card(name="Three", canonical_name="Three")],
        candidates,
        12,
        context,
        "manifest-a",
    )
    assert original != deck_analysis_fingerprint(deck, candidates, 12, context, "manifest-b")
    assert original != deck_analysis_fingerprint(
        deck,
        candidates,
        12,
        {**context, "format": "modern"},
        "manifest-a",
    )
    assert original != deck_analysis_fingerprint(
        deck,
        candidates,
        12,
        context,
        "manifest-a",
        completion_model="different-model",
    )
