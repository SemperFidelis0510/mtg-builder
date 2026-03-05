#!/usr/bin/env python3
"""
MTG RAG MCP Server: semantic search over the Magic: The Gathering card database.
Run the server via: python server.py (or main.bat serve).
Build the RAG index first using build_rag.py (or main.bat install / download / build).
"""

import json
import os
import time
from pathlib import Path

from logger import LOGGER

# ---------------------------------------------------------------------------
# Constants (must match build_rag.py for ChromaDB path and collection)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)
DATA_DIR = SCRIPT_DIR / "data"
CHROMA_PATH = SCRIPT_DIR / "chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "mtg_cards"
ATOMIC_CARDS_PATH = DATA_DIR / "AtomicCards.json"

# Lazy singletons
_embedding_model = None
_chroma_client = None
_collection = None
_card_data: list[dict] | None = None


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


def get_card_data() -> list[dict]:
    """Lazy-load AtomicCards.json and return a flattened list of card-face dicts."""
    global _card_data
    if _card_data is None:
        if not ATOMIC_CARDS_PATH.is_file():
            LOGGER.error(0, "get_card_data: required file not found: %s", ATOMIC_CARDS_PATH)
            raise FileNotFoundError(f"get_card_data: required file not found: {ATOMIC_CARDS_PATH}")
        with open(ATOMIC_CARDS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        data = raw.get("data")
        if data is None:
            LOGGER.error(0, "get_card_data: AtomicCards.json missing 'data' key")
            raise ValueError("get_card_data: AtomicCards.json missing 'data' key")
        out: list[dict] = []
        for card_name, faces in data.items():
            if not isinstance(faces, list):
                continue
            for face in faces:
                if not isinstance(face, dict):
                    continue
                name = face.get("name") or card_name
                type_line = face.get("type") or ""
                types_list = face.get("types") or []
                subtypes_list = face.get("subtypes") or []
                supertypes_list = face.get("supertypes") or []
                text = face.get("text") or ""
                mana_cost = face.get("manaCost") or ""
                mana_val = face.get("manaValue")
                if mana_val is None:
                    mana_val = face.get("convertedManaCost")
                mana_value = float(mana_val) if mana_val is not None else 0.0
                colors_list = face.get("colors") or []
                color_identity_list = face.get("colorIdentity") or []
                power = face.get("power") or ""
                toughness = face.get("toughness") or ""
                keywords_list = face.get("keywords") or []
                loyalty = face.get("loyalty") or ""
                defense = face.get("defense") or ""
                legalities = face.get("legalities")
                if legalities is None or not isinstance(legalities, dict):
                    legalities = {}
                out.append({
                    "name": name,
                    "type": type_line,
                    "types": types_list,
                    "subtypes": subtypes_list,
                    "supertypes": supertypes_list,
                    "text": text,
                    "manaCost": mana_cost,
                    "manaValue": mana_value,
                    "colors": colors_list,
                    "colorIdentity": color_identity_list,
                    "power": power,
                    "toughness": toughness,
                    "keywords": keywords_list,
                    "loyalty": loyalty,
                    "defense": defense,
                    "legalities": legalities,
                })
        _card_data = out
        LOGGER.info("Card data loaded faces=%s path=%s", len(_card_data), ATOMIC_CARDS_PATH)
    return _card_data


def _parse_colors(colors_str: str) -> set[str]:
    """Parse a comma-separated color string (e.g. 'W,U') into a set of single letters."""
    if not colors_str or not colors_str.strip():
        return set()
    return {c.strip().upper() for c in colors_str.split(",") if c.strip()}


def filter_cards(
    name: str = "",
    oracle_text: str = "",
    type_line: str = "",
    colors: str = "",
    color_identity: str = "",
    mana_value: float = -1.0,
    mana_value_min: float = -1.0,
    mana_value_max: float = -1.0,
    power: str = "",
    toughness: str = "",
    keywords: str = "",
    subtype: str = "",
    supertype: str = "",
    format_legal: str = "",
    n_results: int = 20,
) -> str:
    """Filter MTG cards by exact/filter properties. All filters are AND-combined. At least one filter must be set."""
    has_filter = (
        bool(name.strip())
        or bool(oracle_text.strip())
        or bool(type_line.strip())
        or bool(colors.strip())
        or bool(color_identity.strip())
        or mana_value >= 0
        or mana_value_min >= 0
        or mana_value_max >= 0
        or bool(power.strip())
        or bool(toughness.strip())
        or bool(keywords.strip())
        or bool(subtype.strip())
        or bool(supertype.strip())
        or bool(format_legal.strip())
    )
    if not has_filter:
        LOGGER.error(0, "filter_cards: at least one filter parameter must be set")
        raise ValueError("filter_cards: at least one filter parameter must be set")

    cards = get_card_data()
    name_lower = name.strip().lower() if name else ""
    oracle_lower = oracle_text.strip().lower() if oracle_text else ""
    type_lower = type_line.strip().lower() if type_line else ""
    colors_filter = _parse_colors(colors)
    color_identity_filter = _parse_colors(color_identity)
    power_val = power.strip() if power else ""
    toughness_val = toughness.strip() if toughness else ""
    keywords_lower = keywords.strip().lower() if keywords else ""
    subtype_lower = subtype.strip().lower() if subtype else ""
    supertype_lower = supertype.strip().lower() if supertype else ""
    format_lower = format_legal.strip().lower() if format_legal else ""

    results: list[dict] = []
    for card in cards:
        if name_lower and name_lower not in (card.get("name") or "").lower():
            continue
        if oracle_lower and oracle_lower not in (card.get("text") or "").lower():
            continue
        if type_lower and type_lower not in (card.get("type") or "").lower():
            continue
        if colors_filter:
            card_colors = set((c or "").upper() for c in (card.get("colors") or []))
            if card_colors != colors_filter:
                continue
        if color_identity_filter:
            card_identity = set((c or "").upper() for c in (card.get("colorIdentity") or []))
            if not card_identity.issubset(color_identity_filter):
                continue
        mv = card.get("manaValue")
        if mv is None:
            mv = 0.0
        if mana_value >= 0 and mv != mana_value:
            continue
        if mana_value_min >= 0 and mv < mana_value_min:
            continue
        if mana_value_max >= 0 and mv > mana_value_max:
            continue
        if power_val and (card.get("power") or "").strip() != power_val:
            continue
        if toughness_val and (card.get("toughness") or "").strip() != toughness_val:
            continue
        if keywords_lower:
            card_kw = [ (k or "").lower() for k in (card.get("keywords") or []) ]
            if keywords_lower not in card_kw and not any(keywords_lower in k for k in card_kw):
                continue
        if subtype_lower:
            card_sub = [ (s or "").lower() for s in (card.get("subtypes") or []) ]
            if subtype_lower not in card_sub and not any(subtype_lower in s for s in card_sub):
                continue
        if supertype_lower:
            card_super = [ (s or "").lower() for s in (card.get("supertypes") or []) ]
            if supertype_lower not in card_super and not any(supertype_lower in s for s in card_super):
                continue
        if format_lower:
            leg = card.get("legalities") or {}
            if not isinstance(leg, dict):
                continue
            legal_val = ""
            for k, v in leg.items():
                if k.lower() == format_lower and v:
                    legal_val = (v if isinstance(v, str) else str(v)).lower()
                    break
            if legal_val != "legal":
                continue

        results.append(card)
        if len(results) >= n_results:
            break

    parts: list[str] = []
    for i, card in enumerate(results, 1):
        cname = card.get("name") or "Unknown"
        mana = card.get("manaCost") or ""
        ctype = card.get("type") or ""
        text = (card.get("text") or "").strip() or "(No rules text)"
        parts.append(
            f"--- Card {i} of {len(results)} ---\n"
            f"Name: {cname}\nMana Cost: {mana}\nType: {ctype}\nOracle Text: {text}"
        )
    return "\n\n".join(parts) if parts else "No cards found."


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
    def semantic_search_card(query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        LOGGER.info("Request received tool=semantic_search_card query=%r n_results=%s", query, n_results)
        result = search_cards(query=query, n_results=n_results)
        LOGGER.info("Request completed tool=semantic_search_card query=%r", query)
        return result

    @mcp.tool()
    def plain_search_card(
        name: str = "",
        oracle_text: str = "",
        type_line: str = "",
        colors: str = "",
        color_identity: str = "",
        mana_value: float = -1.0,
        mana_value_min: float = -1.0,
        mana_value_max: float = -1.0,
        power: str = "",
        toughness: str = "",
        keywords: str = "",
        subtype: str = "",
        supertype: str = "",
        format_legal: str = "",
        n_results: int = 20,
    ) -> str:
        """Filter MTG cards by exact properties (name, type, colors, mana value, power/toughness, keywords, etc.).
        All filters are AND-combined. At least one filter must be provided. Returns card names and rules text."""
        LOGGER.info(
            "Request received tool=plain_search_card name=%r type_line=%r colors=%r n_results=%s",
            name, type_line, colors, n_results,
        )
        result = filter_cards(
            name=name,
            oracle_text=oracle_text,
            type_line=type_line,
            colors=colors,
            color_identity=color_identity,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            power=power,
            toughness=toughness,
            keywords=keywords,
            subtype=subtype,
            supertype=supertype,
            format_legal=format_legal,
            n_results=n_results,
        )
        LOGGER.info("Request completed tool=plain_search_card")
        return result

    LOGGER.info("Tool registered: semantic_search_card, plain_search_card; entering mcp.run(transport=stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
