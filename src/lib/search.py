"""Embedding model, ChromaDB access, and semantic search for the MTG MCP project."""

import time

from src.lib.config import CHROMA_PATH, COLLECTION_NAME, MODEL_NAME
from src.obj.card import Card
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------
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
    parts: list[str] = []
    for i, (doc, meta) in enumerate(zip(docs, metas), 1):
        card: Card = Card.from_chroma_result(meta, doc)
        LOGGER.debug("Card %s/%s: %s", i, len(docs), card.name)
        parts.append(card.format_display(i, len(docs)))
    result_count: int = len(docs)
    LOGGER.debug("Building response text for %s card(s)", result_count)
    LOGGER.info("search_cards finished query=%r returned %s card(s)", query, result_count)
    return "\n\n".join(parts) if parts else "No cards found."
