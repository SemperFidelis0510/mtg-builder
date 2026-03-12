#!/usr/bin/env python3
"""
Build the MTG RAG spaces: ingest AtomicCards.json and index cards into ChromaDB.
Run via: python -m src.lib.build_rag (or install.bat build).
Install deps and download data first using src.lib.setup / install.bat install and install.bat download.
"""

import json
from typing import Callable

from src.lib.config import (
    ATOMIC_CARDS_PATH,
    CHROMA_PATH,
    COLLECTION_NAME,
    EFFECTS_COLLECTION_NAME,
    MODEL_NAME,
    TRIGGERS_COLLECTION_NAME,
)
from src.lib.cardDB import CardDB
from src.lib.prices import load_prices
from src.obj.card import Card

# ---------------------------------------------------------------------------
# Build-specific constants
# ---------------------------------------------------------------------------
BATCH_SIZE: int = 500


def _load_cards() -> list[Card]:
    """Load all Card faces from AtomicCards.json with prices attached."""
    if not ATOMIC_CARDS_PATH.exists():
        raise FileNotFoundError(
            f"Data not found: {ATOMIC_CARDS_PATH}. Run with --download first."
        )
    with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data")
    if not data:
        raise ValueError("AtomicCards.json has no 'data' key")
    price_map: dict[str, float] = load_prices()
    cards: list[Card] = []
    for card_name, faces in data.items():
        if not isinstance(faces, list):
            continue
        for face in faces:
            if not isinstance(face, dict):
                continue
            card: Card = Card.from_json_face(face, card_name)
            card.price_usd = price_map.get(card_name, -1.0)
            cards.append(card)
    return cards


def _build_collection(
    collection_name: str,
    rows: list[tuple[str, str, dict]],
    label: str,
) -> None:
    """Encode *rows* and upsert them into ChromaDB collection *collection_name*.

    Heavy imports (torch, sentence_transformers, chromadb, tqdm) happen here
    so they are only loaded when building.
    """
    import torch
    from sentence_transformers import SentenceTransformer
    import chromadb
    from tqdm import tqdm

    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[{label}] Using device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)

    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    n: int = len(rows)
    for start in tqdm(range(0, n, BATCH_SIZE), desc=f"Building {label}", unit="batch"):
        batch = rows[start : start + BATCH_SIZE]
        ids_batch: list[str] = [r[0] for r in batch]
        docs_batch: list[str] = [r[1] for r in batch]
        metas_batch: list[dict] = [r[2] for r in batch]
        emb = model.encode(docs_batch, device=device, show_progress_bar=False)
        collection.upsert(
            ids=ids_batch,
            documents=docs_batch,
            embeddings=emb.tolist(),
            metadatas=metas_batch,
        )
    print(f"[{label}] Indexed {n} card faces in {collection_name}.")


def _prepare_rows(
    cards: list[Card],
    doc_fn: Callable[[Card], str],
    meta_fn: Callable[[Card], dict],
) -> list[tuple[str, str, dict]]:
    """Build (id, document, metadata) triples from *cards* using *doc_fn* and *meta_fn*."""
    rows: list[tuple[str, str, dict]] = []
    for i, card in enumerate(cards):
        uid: str = CardDB.make_id(card.name, i)
        rows.append((uid, doc_fn(card), meta_fn(card)))
    return rows


def _name_only_meta(card: Card) -> dict:
    """Minimal metadata for triggers/effects collections: just the card name."""
    return {"name": card.name}


# ---------------------------------------------------------------------------
# Public build functions
# ---------------------------------------------------------------------------

def do_build() -> None:
    """Build the main card RAG collection (semantic search over full card text)."""
    cards = _load_cards()
    rows = _prepare_rows(cards, Card.to_rag_document, Card.to_chroma_metadata)
    _build_collection(COLLECTION_NAME, rows, "cards")


def do_build_triggers() -> None:
    """Build the triggers RAG collection (semantic search over card trigger phrases)."""
    cards = _load_cards()
    rows = _prepare_rows(cards, Card.to_triggers_document, _name_only_meta)
    _build_collection(TRIGGERS_COLLECTION_NAME, rows, "triggers")


def do_build_effects() -> None:
    """Build the effects RAG collection (semantic search over card effect phrases)."""
    cards = _load_cards()
    rows = _prepare_rows(cards, Card.to_effects_document, _name_only_meta)
    _build_collection(EFFECTS_COLLECTION_NAME, rows, "effects")


def do_build_all() -> None:
    """Build all three RAG collections, sharing one card load pass."""
    cards = _load_cards()

    card_rows = _prepare_rows(cards, Card.to_rag_document, Card.to_chroma_metadata)
    _build_collection(COLLECTION_NAME, card_rows, "cards")

    trigger_rows = _prepare_rows(cards, Card.to_triggers_document, _name_only_meta)
    _build_collection(TRIGGERS_COLLECTION_NAME, trigger_rows, "triggers")

    effect_rows = _prepare_rows(cards, Card.to_effects_document, _name_only_meta)
    _build_collection(EFFECTS_COLLECTION_NAME, effect_rows, "effects")


if __name__ == "__main__":
    do_build_all()
