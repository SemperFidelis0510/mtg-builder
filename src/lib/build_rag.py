#!/usr/bin/env python3
"""
Build the MTG RAG space: ingest AtomicCards.json and index cards into ChromaDB.
Run via: python -m src.lib.build_rag (or install.bat build).
Install deps and download data first using src.lib.setup / install.bat install and install.bat download.
"""

import json

from src.lib.config import ATOMIC_CARDS_PATH, CHROMA_PATH, COLLECTION_NAME, MODEL_NAME
from src.lib.cardDB import CardDB
from src.obj.card import Card

# ---------------------------------------------------------------------------
# Build-specific constants
# ---------------------------------------------------------------------------
BATCH_SIZE: int = 500


def do_build() -> None:
    """Parse AtomicCards.json, generate embeddings on GPU, batch upsert to ChromaDB."""
    if not ATOMIC_CARDS_PATH.exists():
        raise FileNotFoundError(
            f"Data not found: {ATOMIC_CARDS_PATH}. Run with --download first."
        )

    import torch
    from sentence_transformers import SentenceTransformer
    import chromadb
    from tqdm import tqdm

    with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data")
    if not data:
        raise ValueError("AtomicCards.json has no 'data' key")

    rows: list[tuple[str, str, dict]] = []
    for card_name, faces in data.items():
        if not isinstance(faces, list):
            continue
        for i, face in enumerate(faces):
            if not isinstance(face, dict):
                continue
            card: Card = Card.from_json_face(face, card_name)
            uid: str = CardDB.make_id(card_name, i)
            rows.append((uid, card.to_rag_document(), card.to_chroma_metadata()))

    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")
    model = SentenceTransformer(MODEL_NAME, device=device)

    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    n: int = len(rows)
    
    for start in tqdm(range(0, n, BATCH_SIZE), desc="Building index", unit="batch"):
        batch = rows[start : start + BATCH_SIZE]
        ids_batch: list[str] = [r[0] for r in batch]
        docs_batch: list[str] = [r[1] for r in batch]
        metas_batch: list[dict] = [r[2] for r in batch]
        emb = model.encode(docs_batch, device=device, show_progress_bar=False)
        emb_list = emb.tolist()
        collection.upsert(
            ids=ids_batch,
            documents=docs_batch,
            embeddings=emb_list,
            metadatas=metas_batch,
        )
    print(f"Indexed {n} card faces in {COLLECTION_NAME}.")


if __name__ == "__main__":
    do_build()
