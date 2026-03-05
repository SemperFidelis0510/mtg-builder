#!/usr/bin/env python3
"""
MTG RAG MCP Server: semantic search over the Magic: The Gathering card database.
Run the server via: python server.py (or main.bat serve).
Build the RAG index first using build_rag.py (or main.bat install / download / build).
"""

import os
import time
from pathlib import Path

from logger import LOGGER

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
        t0: float = time.perf_counter()
        LOGGER.debug("Importing torch")
        import torch
        LOGGER.debug("torch imported elapsed=%.3fs", time.perf_counter() - t0)

        t1: float = time.perf_counter()
        LOGGER.debug("Importing SentenceTransformer")
        from sentence_transformers import SentenceTransformer
        LOGGER.debug("SentenceTransformer imported elapsed=%.3fs", time.perf_counter() - t1)

        t2: float = time.perf_counter()
        LOGGER.debug("Checking CUDA availability")
        device: str = "cuda" if torch.cuda.is_available() else "cpu"
        LOGGER.debug("CUDA check done device=%s elapsed=%.3fs", device, time.perf_counter() - t2)

        LOGGER.info("Loading embedding model name=%s device=%s", MODEL_NAME, device)
        t3: float = time.perf_counter()
        _embedding_model = SentenceTransformer(MODEL_NAME, device=device)
        LOGGER.info("Embedding model loaded name=%s device=%s elapsed=%.3fs", MODEL_NAME, device, time.perf_counter() - t3)
    else:
        LOGGER.debug("Using cached embedding model name=%s", MODEL_NAME)
    return _embedding_model


def get_collection():
    """Lazy-load ChromaDB persistent client and mtg_cards collection."""
    global _chroma_client, _collection
    if _chroma_client is None:
        t0: float = time.perf_counter()
        LOGGER.debug("Importing chromadb")
        import chromadb
        LOGGER.debug("chromadb imported elapsed=%.3fs", time.perf_counter() - t0)

        LOGGER.info("Opening ChromaDB path=%s", CHROMA_PATH)
        t1: float = time.perf_counter()
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        LOGGER.debug("PersistentClient created elapsed=%.3fs", time.perf_counter() - t1)

        t2: float = time.perf_counter()
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        LOGGER.debug("get_or_create_collection done elapsed=%.3fs", time.perf_counter() - t2)
        LOGGER.info("ChromaDB collection ready name=%s path=%s", COLLECTION_NAME, CHROMA_PATH)
    else:
        LOGGER.debug("Using cached ChromaDB client path=%s", CHROMA_PATH)
    return _collection


def search_cards(query: str, n_results: int = 5) -> str:
    """Search for Magic: The Gathering cards by semantic meaning.
    Returns card names and rules text matching the query."""
    LOGGER.info("search_cards started query=%r n_results=%s", query, n_results)
    LOGGER.debug("Resolving embedding model")
    t_model: float = time.perf_counter()
    model = get_embedding_model()
    LOGGER.debug("Embedding model resolved elapsed=%.3fs", time.perf_counter() - t_model)
    LOGGER.debug("Resolving ChromaDB collection")
    t_coll: float = time.perf_counter()
    coll = get_collection()
    LOGGER.debug("ChromaDB collection resolved elapsed=%.3fs", time.perf_counter() - t_coll)
    LOGGER.debug("Encoding query to vector (n_results=%s)", n_results)
    t_enc: float = time.perf_counter()
    emb = model.encode([query]).tolist()
    LOGGER.debug("Query encoded elapsed=%.3fs", time.perf_counter() - t_enc)
    LOGGER.debug("Querying ChromaDB n_results=%s", n_results)
    t_query: float = time.perf_counter()
    out = coll.query(query_embeddings=emb, n_results=n_results, include=["documents", "metadatas"])
    LOGGER.debug("ChromaDB query done elapsed=%.3fs", time.perf_counter() - t_query)
    docs = (out.get("documents") or [[]])[0]
    metas = (out.get("metadatas") or [[]])[0]
    LOGGER.debug("ChromaDB returned %s document(s)", len(docs))
    parts = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        name = (meta or {}).get("name", "Unknown")
        mana = (meta or {}).get("manaCost", "")
        type_line = (meta or {}).get("type", "")
        text = doc.split("Oracle Text:", 1)[-1].strip() if doc else ""
        LOGGER.debug("Card %s/%s: %s", i, len(docs), name)
        parts.append(
            f"--- Card {i} of {len(docs)} ---\n"
            f"Name: {name}\nMana Cost: {mana}\nType: {type_line}\nOracle Text: {text}"
        )
    result_count = len(docs)
    LOGGER.debug("Building response text for %s card(s)", result_count)
    LOGGER.info("search_cards finished query=%r returned %s card(s)", query, result_count)
    return "\n\n".join(parts) if parts else "No cards found."


def run_server() -> None:
    """Launch FastMCP server with search_cards tool, stdio transport."""
    from fastmcp import FastMCP

    LOGGER.info(
        "Server startup script_dir=%s chroma_path=%s collection=%s model=%s transport=stdio",
        SCRIPT_DIR, CHROMA_PATH, COLLECTION_NAME, MODEL_NAME,
    )

    LOGGER.debug("Eager import: torch")
    t0: float = time.perf_counter()
    import torch  # noqa: F401
    LOGGER.debug("torch imported elapsed=%.3fs", time.perf_counter() - t0)

    LOGGER.debug("Eager import: SentenceTransformer")
    t1: float = time.perf_counter()
    from sentence_transformers import SentenceTransformer  # noqa: F401
    LOGGER.debug("SentenceTransformer imported elapsed=%.3fs", time.perf_counter() - t1)

    LOGGER.debug("Eager import: chromadb")
    t2: float = time.perf_counter()
    import chromadb  # noqa: F401
    LOGGER.debug("chromadb imported elapsed=%.3fs", time.perf_counter() - t2)

    mcp = FastMCP("MTG Card Search")

    @mcp.tool()
    def search_cards_tool(query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        LOGGER.info("Request received tool=search_cards_tool query=%r n_results=%s", query, n_results)
        result = search_cards(query=query, n_results=n_results)
        LOGGER.info("Request completed tool=search_cards_tool query=%r", query)
        return result

    LOGGER.info("Tool registered: search_cards_tool; entering mcp.run(transport=stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
