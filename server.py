#!/usr/bin/env python3
"""
MTG RAG MCP Server: semantic search over the Magic: The Gathering card database.
Run the server via: python server.py (or main.bat serve).
Build the RAG index first using build_rag.py (or main.bat install / download / build).
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (must match build_rag.py for ChromaDB path and collection)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
CHROMA_PATH = SCRIPT_DIR / "chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "mtg_cards"

# Lazy singletons
_embedding_model = None
_chroma_client = None
_collection = None


def get_embedding_model():
    """Lazy-load SentenceTransformer and move to GPU if available."""
    global _embedding_model
    if _embedding_model is None:
        import sys
        import torch
        from sentence_transformers import SentenceTransformer
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[mtg-rag] Loading model on {device}", file=sys.stderr)
        _embedding_model = SentenceTransformer(MODEL_NAME, device=device)
        print(f"[mtg-rag] Model loaded on {device}", file=sys.stderr)
    return _embedding_model


def get_collection():
    """Lazy-load ChromaDB persistent client and mtg_cards collection."""
    global _chroma_client, _collection
    if _chroma_client is None:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def search_cards(query: str, n_results: int = 5) -> str:
    """Search for Magic: The Gathering cards by semantic meaning.
    Returns card names and rules text matching the query."""
    model = get_embedding_model()
    coll = get_collection()
    emb = model.encode([query]).tolist()
    out = coll.query(query_embeddings=emb, n_results=n_results, include=["documents", "metadatas"])
    docs = (out.get("documents") or [[]])[0]
    metas = (out.get("metadatas") or [[]])[0]
    parts = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        name = (meta or {}).get("name", "Unknown")
        mana = (meta or {}).get("manaCost", "")
        type_line = (meta or {}).get("type", "")
        text = doc.split("Oracle Text:", 1)[-1].strip() if doc else ""
        parts.append(
            f"--- Card {i} of {len(docs)} ---\n"
            f"Name: {name}\nMana Cost: {mana}\nType: {type_line}\nOracle Text: {text}"
        )
    return "\n\n".join(parts) if parts else "No cards found."


def run_server() -> None:
    """Launch FastMCP server with search_cards tool, stdio transport."""
    from fastmcp import FastMCP

    mcp = FastMCP("MTG Card Search")

    @mcp.tool()
    def search_cards_tool(query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        return search_cards(query=query, n_results=n_results)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
