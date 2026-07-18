#!/usr/bin/env python3
"""Build the deterministic MTG GraphRAG index and local LanceDB semantic store."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.lib.config import (
    ATOMIC_CARDS_PATH,
    GRAPHRAG_COMPLETION_MODEL,
    GRAPHRAG_DIR,
    GRAPHRAG_EMBEDDING_MODEL,
    GRAPHRAG_INPUT_DIR,
    GRAPHRAG_LANCEDB_DIR,
    GRAPHRAG_MANIFEST_PATH,
    GRAPHRAG_SETTINGS_PATH,
    load_required_gemini_api_key,
)
from src.lib.cardDB import CardDB
from src.lib.graphrag_build import GraphBuildResult, build_graph_artifacts, stable_graph_id
from src.obj.card import Card
from src.utils.logger import LOGGER, init_logger

_EMBED_BATCH_SIZE: int = 50


def _load_cards() -> list[Card]:
    """Use CardDB's canonical/face resolver as the graph's card authority."""
    cards = CardDB.inst().get_canonical_cards()
    if not cards:
        LOGGER.error("build_rag: CardDB produced no canonical cards")
        raise ValueError("build_rag: CardDB produced no canonical cards")
    return cards


def _write_settings(workflows: tuple[str, ...]) -> None:
    """Write GraphRAG configuration without persisting a credential."""
    if not workflows:
        LOGGER.error("_write_settings: workflows must not be empty")
        raise ValueError("_write_settings: workflows must not be empty")
    GRAPHRAG_DIR.mkdir(parents=True, exist_ok=True)
    workflow_names = ", ".join(workflows)
    content: str = f"""completion_models:
  default_completion_model:
    model_provider: gemini
    model: {GRAPHRAG_COMPLETION_MODEL}
    auth_method: api_key
    api_key: ${{GEMINI_API_KEY}}
embedding_models:
  default_embedding_model:
    model_provider: gemini
    model: {GRAPHRAG_EMBEDDING_MODEL}
    auth_method: api_key
    api_key: ${{GEMINI_API_KEY}}
output_storage:
  type: file
  base_dir: output
reporting:
  type: file
  base_dir: logs
cache:
  type: json
  storage:
    type: file
    base_dir: cache
vector_store:
  type: lancedb
  db_uri: output/lancedb
workflows: [{workflow_names}]
embed_text:
  embedding_model_id: default_embedding_model
  names: [entity_description, community_full_content]
cluster_graph:
  max_cluster_size: 250
community_reports:
  completion_model_id: default_completion_model
  max_length: 1200
  max_input_length: 8000
local_search:
  completion_model_id: default_completion_model
  embedding_model_id: default_embedding_model
"""
    GRAPHRAG_SETTINGS_PATH.write_text(content, encoding="utf-8")


def _generate_community_summary(prompt: str) -> str:
    """Generate one evidence-bounded top-level community summary."""
    if not prompt.strip():
        LOGGER.error("_generate_community_summary: prompt must not be empty")
        raise ValueError("_generate_community_summary: prompt must not be empty")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=load_required_gemini_api_key())
    response = client.models.generate_content(
        model=GRAPHRAG_COMPLETION_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=500,
        ),
    )
    text: Any = response.text
    if not isinstance(text, str) or not text.strip():
        LOGGER.error("_generate_community_summary: Gemini returned invalid text")
        raise ValueError("_generate_community_summary: Gemini returned invalid text")
    return text.strip()


def _build_community_reports(result: GraphBuildResult) -> None:
    """Summarize GraphRAG's top-level communities without its all-level O(N²) context build."""
    import pandas as pd

    communities_path = result.manifest_path.parent / "communities.parquet"
    if not communities_path.is_file():
        LOGGER.error("_build_community_reports: communities artifact is missing")
        raise FileNotFoundError(f"GraphRAG communities artifact is missing: {communities_path}")
    communities = pd.read_parquet(communities_path)
    entities = pd.read_parquet(result.entities_path)
    relationships = pd.read_parquet(result.relationships_path)
    top_level = communities[communities["level"] == 0].sort_values("community")
    if top_level.empty:
        LOGGER.error("_build_community_reports: no top-level communities were generated")
        raise ValueError("_build_community_reports: no top-level communities were generated")

    cache_dir = GRAPHRAG_DIR / "cache" / f"community-reports-schema-{3}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for report_index, community in enumerate(top_level.to_dict(orient="records")):
        community_id = int(community["community"])
        entity_ids = list(community["entity_ids"])
        relationship_ids = list(community["relationship_ids"])
        member_entities = entities[entities["id"].isin(entity_ids)]
        member_relationships = relationships[relationships["id"].isin(relationship_ids)]
        if member_entities.empty or member_relationships.empty:
            LOGGER.error("_build_community_reports: community %d has no evidence", community_id)
            raise ValueError(f"GraphRAG community {community_id} has no entity/relationship evidence")

        samples = []
        for entity_type, limit in (("MECHANIC", 15), ("CARD", 15), ("COMBO", 10), ("EXTERNAL_CARD", 5)):
            samples.append(
                member_entities[member_entities["type"] == entity_type]
                .sort_values(["degree", "title"], ascending=[False, True])
                .head(limit)
            )
        sampled_entities = pd.concat(samples, ignore_index=True)
        sampled_relationships = member_relationships.sort_values(
            ["weight", "description"], ascending=[False, True]
        ).head(30)
        entity_context = "\n".join(
            f"- {row['type']} {row['display_name']}: {str(row['description']).replace(chr(10), ' ')[:350]}"
            for row in sampled_entities.to_dict(orient="records")
        )
        relationship_context = "\n".join(
            f"- {row['description']} [source: {row['provenance']}]"
            for row in sampled_relationships.to_dict(orient="records")
        )
        prompt = (
            "Summarize this Magic: The Gathering graph community in one concise paragraph. "
            "Describe its dominant mechanics, card roles, and notable combo themes. "
            "Use only the supplied evidence; do not invent cards or interactions.\n\n"
            f"Community {community_id} contains {len(entity_ids)} entities and "
            f"{len(relationship_ids)} relationships.\nEntities:\n{entity_context}\n"
            f"Relationships:\n{relationship_context}"
        )
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "schema": 3,
                    "model": GRAPHRAG_COMPLETION_MODEL,
                    "community": community_id,
                    "entity_ids": sorted(entity_ids),
                    "relationship_ids": sorted(relationship_ids),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        cache_path = cache_dir / f"{community_id}-{cache_key}.txt"
        if cache_path.is_file():
            summary = cache_path.read_text(encoding="utf-8").strip()
            if not summary:
                LOGGER.error("_build_community_reports: empty cached report %s", cache_path)
                raise ValueError(f"Cached community report is empty: {cache_path}")
        else:
            summary = _generate_community_summary(prompt)
            cache_path.write_text(summary, encoding="utf-8")

        title = f"Community {community_id}"
        finding = {"summary": "Dominant MTG graph theme", "explanation": summary}
        children_value = community["children"]
        children = children_value if isinstance(children_value, list) else list(children_value)
        report_json = {
            "title": title,
            "summary": summary,
            "rating": 5.0,
            "rating_explanation": "Top-level deterministic MTG graph community.",
            "findings": [finding],
        }
        reports.append(
            {
                "id": stable_graph_id("community-report", f"3:{community_id}"),
                "human_readable_id": report_index,
                "community": community_id,
                "level": int(community["level"]),
                "parent": int(community["parent"]),
                "children": children,
                "title": title,
                "summary": summary,
                "full_content": summary,
                "rank": 5.0,
                "rating_explanation": report_json["rating_explanation"],
                "findings": [finding],
                "full_content_json": json.dumps(report_json, ensure_ascii=False),
                "period": str(community["period"]),
                "size": int(community["size"]),
            }
        )
    reports_path = result.manifest_path.parent / "community_reports.parquet"
    pd.DataFrame(reports).to_parquet(reports_path, index=False)
    LOGGER.info("_build_community_reports: generated %d top-level reports", len(reports))


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed documents with the configured Gemini embedding model."""
    if not texts:
        LOGGER.error("_embed_texts: texts must not be empty")
        raise ValueError("_embed_texts: texts must not be empty")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=load_required_gemini_api_key())
    response = client.models.embed_content(
        model=GRAPHRAG_EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=3072,
        ),
    )
    raw_embeddings: Any = response.embeddings
    if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
        LOGGER.error("_embed_texts: expected %d embeddings, got %s", len(texts), type(raw_embeddings).__name__)
        raise ValueError("_embed_texts: embedding response count does not match input count")
    vectors: list[list[float]] = []
    for embedding in raw_embeddings:
        values: Any = embedding.values
        if not isinstance(values, list) or not values:
            LOGGER.error("_embed_texts: embedding values are invalid")
            raise ValueError("_embed_texts: embedding values are invalid")
        vectors.append([float(value) for value in values])
    return vectors


def _build_lancedb(result: GraphBuildResult) -> None:
    """Materialize the runtime card/mechanic search table from GraphRAG embeddings."""
    import lancedb
    import pandas as pd

    entities: Any = pd.read_parquet(result.entities_path)
    if not isinstance(entities, pd.DataFrame) or entities.empty:
        LOGGER.error("_build_lancedb: entities artifact is empty")
        raise ValueError("_build_lancedb: entities artifact is empty")
    required: set[str] = {"id", "type", "display_name", "description"}
    if not required.issubset(entities.columns):
        LOGGER.error("_build_lancedb: entities is missing required columns %s", sorted(required))
        raise ValueError("_build_lancedb: entities is missing required columns")

    source_rows: list[dict[str, Any]] = [
        row
        for row in entities.to_dict(orient="records")
        if row["type"] in ("CARD", "MECHANIC")
    ]
    if not source_rows:
        LOGGER.error("_build_lancedb: no card or mechanic entities were found")
        raise ValueError("_build_lancedb: no card or mechanic entities were found")
    text_units = pd.read_parquet(result.text_units_path)
    card_text_units = {
        str(row["card_id"]): row
        for row in text_units[text_units["scope"] == "card"].to_dict(orient="records")
    }
    card_entity_ids = {str(row["id"]) for row in source_rows if row["type"] == "CARD"}
    if set(card_text_units) != card_entity_ids:
        LOGGER.error("_build_lancedb: card entity/text-unit mapping is incomplete")
        raise ValueError("_build_lancedb: card entity/text-unit mapping is incomplete")
    GRAPHRAG_LANCEDB_DIR.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(GRAPHRAG_LANCEDB_DIR))
    if "entity_description" not in db.table_names():
        LOGGER.error("_build_lancedb: GraphRAG entity_description table is missing")
        raise FileNotFoundError("GraphRAG entity_description LanceDB table is missing")
    embedding_table = db.open_table("entity_description")
    search_table: Any = None
    text_unit_table: Any = None
    copied_count = 0
    copied_text_unit_count = 0
    for start in range(0, len(source_rows), 100):
        batch = source_rows[start : start + 100]
        ids = [str(row["id"]) for row in batch]
        predicate = "id IN (" + ", ".join(f"'{entity_id}'" for entity_id in ids) + ")"
        embeddings = embedding_table.search().where(predicate).limit(len(batch)).to_list()
        vector_by_id = {str(row["id"]): row["vector"] for row in embeddings}
        if set(vector_by_id) != set(ids):
            LOGGER.error("_build_lancedb: missing entity embeddings in batch starting %d", start)
            raise ValueError("_build_lancedb: GraphRAG entity embeddings are incomplete")
        rows = [
            {
                "id": str(row["id"]),
                "entity_id": str(row["id"]),
                "display_name": str(row["display_name"]),
                "scope": "general" if row["type"] == "CARD" else "mechanic",
                "text": str(row["description"]),
                "vector": vector_by_id[str(row["id"])],
            }
            for row in batch
        ]
        if search_table is None:
            search_table = db.create_table("mtg_search", data=rows, mode="overwrite")
        else:
            search_table.add(rows)
        text_unit_rows = [
            {
                "id": str(card_text_units[str(row["id"])]["id"]),
                "vector": vector_by_id[str(row["id"])],
            }
            for row in batch
            if row["type"] == "CARD"
        ]
        if text_unit_rows:
            if text_unit_table is None:
                text_unit_table = db.create_table(
                    "text_unit_text",
                    data=text_unit_rows,
                    mode="overwrite",
                )
            else:
                text_unit_table.add(text_unit_rows)
            copied_text_unit_count += len(text_unit_rows)
        copied_count += len(rows)
    if search_table is None or copied_count != len(source_rows):
        LOGGER.error("_build_lancedb: runtime search table was not fully materialized")
        raise ValueError("_build_lancedb: runtime search table was not fully materialized")
    if text_unit_table is None or copied_text_unit_count != len(card_text_units):
        LOGGER.error("_build_lancedb: card text-unit table was not fully materialized")
        raise ValueError("_build_lancedb: card text-unit table was not fully materialized")
    LOGGER.info(
        "_build_lancedb: indexed %d card/mechanic entities and %d card text units",
        copied_count,
        copied_text_unit_count,
    )


def _run_graphrag_workflows() -> None:
    """Run the workflows currently selected in the generated GraphRAG settings."""
    env: dict[str, str] = dict(os.environ)
    env["GEMINI_API_KEY"] = load_required_gemini_api_key()
    command: list[str] = [sys.executable, "-m", "graphrag", "index", "--root", str(GRAPHRAG_DIR)]
    completed = subprocess.run(
        command,
        cwd=GRAPHRAG_DIR,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        diagnostics = "\n".join(
            value
            for value in (completed.stdout[-2000:], completed.stderr[-2000:])
            if value
        )
        LOGGER.error(
            "_run_graphrag_workflows: command failed code=%s diagnostics=%s",
            completed.returncode,
            diagnostics,
        )
        raise RuntimeError(
            f"GraphRAG indexing failed with exit code {completed.returncode}: {diagnostics}"
        )
    LOGGER.info("_run_graphrag_workflows: GraphRAG workflows completed")


def _run_runtime_embeddings(result: GraphBuildResult) -> None:
    """Embed only entities used by runtime retrieval, then restore the full BYOG table."""
    import pandas as pd

    entities = pd.read_parquet(result.entities_path)
    runtime_entities = entities[entities["type"].isin(("CARD", "MECHANIC"))]
    if runtime_entities.empty:
        LOGGER.error("_run_runtime_embeddings: runtime entity projection is empty")
        raise ValueError("_run_runtime_embeddings: runtime entity projection is empty")
    backup_path = result.entities_path.with_name("entities.full.parquet")
    if backup_path.exists():
        LOGGER.error("_run_runtime_embeddings: stale entity backup exists: %s", backup_path)
        raise FileExistsError(f"Stale GraphRAG entity backup exists: {backup_path}")
    result.entities_path.replace(backup_path)
    try:
        runtime_entities.to_parquet(result.entities_path, index=False)
        _run_graphrag_workflows()
    finally:
        if result.entities_path.exists():
            result.entities_path.unlink()
        backup_path.replace(result.entities_path)


def _validate_generated_index(result: GraphBuildResult) -> None:
    """Validate GraphRAG outputs and the specialized runtime search table."""
    import lancedb
    import pandas as pd

    for filename in ("communities.parquet", "community_reports.parquet"):
        path = result.manifest_path.parent / filename
        if not path.is_file():
            LOGGER.error("_validate_generated_index: required GraphRAG output missing: %s", path)
            raise FileNotFoundError(f"Required GraphRAG output missing: {path}")
        frame = pd.read_parquet(path)
        if frame.empty:
            LOGGER.error("_validate_generated_index: required GraphRAG output is empty: %s", path)
            raise ValueError(f"Required GraphRAG output is empty: {path}")
    db = lancedb.connect(str(GRAPHRAG_LANCEDB_DIR))
    table_names = set(db.table_names())
    required_tables = {"entity_description", "text_unit_text", "mtg_search"}
    missing_tables = sorted(required_tables - table_names)
    if missing_tables:
        LOGGER.error("_validate_generated_index: LanceDB tables missing: %s", missing_tables)
        raise FileNotFoundError(f"Required LanceDB tables missing: {missing_tables}")


def _finalize_manifest(result: GraphBuildResult) -> None:
    """Record exact source and model versions after all graph artifacts exist."""
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    if "content_hash" in manifest:
        del manifest["content_hash"]
    manifest["source_versions"] = {
        "mtgjson_atomic_cards_sha256": hashlib.sha256(ATOMIC_CARDS_PATH.read_bytes()).hexdigest(),
        "commander_spellbook_api": "https://backend.commanderspellbook.com/variants/",
        "commander_spellbook_combo_sha256": manifest["spellbook_combo_sha256"],
        "graphrag": importlib.metadata.version("graphrag"),
        "lancedb": importlib.metadata.version("lancedb"),
    }
    manifest["models"] = {
        "completion": GRAPHRAG_COMPLETION_MODEL,
        "embedding": GRAPHRAG_EMBEDDING_MODEL,
    }
    manifest["embedding_scope"] = {
        "entity_description": ["CARD", "MECHANIC"],
        "text_unit_text": "card",
        "community_full_content": "level-0",
    }
    artifact_hashes: dict[str, str] = {}
    for key, filename in manifest["files"].items():
        path = result.manifest_path.parent / filename
        if not path.is_file():
            LOGGER.error("_finalize_manifest: required artifact missing for %s: %s", key, path)
            raise FileNotFoundError(f"Required GraphRAG artifact missing: {path}")
        artifact_hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["artifact_sha256"] = artifact_hashes
    manifest["content_hash"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def do_build_all(clean: bool = False) -> None:
    """Build all GraphRAG artifacts from authoritative cards and Spellbook combos."""
    spellbook_checkpoint_path = GRAPHRAG_DIR / "spellbook_checkpoint.json"
    if clean:
        for path in (GRAPHRAG_INPUT_DIR, GRAPHRAG_LANCEDB_DIR):
            if path.exists():
                import shutil

                shutil.rmtree(path)
    cards: list[Card] = _load_cards()
    result: GraphBuildResult = build_graph_artifacts(
        cards,
        GRAPHRAG_INPUT_DIR,
        spellbook_checkpoint_path=spellbook_checkpoint_path,
        card_resolver=CardDB.inst().try_resolve_primary_card,
    )
    _write_settings(("create_communities",))
    _run_graphrag_workflows()
    _build_community_reports(result)
    _write_settings(("generate_text_embeddings",))
    _run_runtime_embeddings(result)
    _build_lancedb(result)
    _write_settings(("create_communities", "generate_text_embeddings"))
    _validate_generated_index(result)
    _finalize_manifest(result)
    LOGGER.info(
        "do_build_all: GraphRAG artifacts complete cards=%d combos=%d",
        result.card_count,
        result.combo_count,
    )


if __name__ == "__main__":
    init_logger("build_rag")
    do_build_all(clean=True)
