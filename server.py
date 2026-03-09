#!/usr/bin/env python3
"""
MTG RAG MCP Server: semantic search over the Magic: The Gathering card database.
Run the server via: python server.py (or main.bat serve).
Build the RAG index first using build_rag.py (or main.bat install / download / build).
"""

import os
import time

import requests

from src.lib.config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    DECK_EDITOR_BASE_URL,
    MODEL_NAME,
    REPO_ROOT,
)
from src.lib.card_data import filter_cards
from src.lib.search import search_cards
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Ensure CWD is the repo root
# ---------------------------------------------------------------------------
os.chdir(REPO_ROOT)


def run_server() -> None:
    """Launch FastMCP server with search_cards tool, stdio transport."""
    from fastmcp import FastMCP

    LOGGER.info(
        "Server startup repo_root=%s chroma_path=%s collection=%s model=%s transport=stdio",
        REPO_ROOT, CHROMA_PATH, COLLECTION_NAME, MODEL_NAME,
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
        result: str = search_cards(query=query, n_results=n_results)
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
        result: str = filter_cards(
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

    @mcp.tool()
    def append_cards_to_deck(card_names: str) -> str:
        """Append one or more cards to the currently loaded deck in the deck editor server.
        card_names: comma-separated list of card names (e.g. 'Lightning Bolt, Counterspell').
        The deck editor must be running (e.g. python deck_editor.py). Cards are resolved by the editor and appear in the correct section. Returns a short status or error message."""
        names: list[str] = [n.strip() for n in card_names.split(",") if n.strip()]
        if not names:
            return "Error: card_names must contain at least one card name (comma-separated)."
        url: str = f"{DECK_EDITOR_BASE_URL.rstrip('/')}/api/add_card"
        LOGGER.info("Request received tool=append_cards_to_deck names=%s", names)
        try:
            r = requests.post(url, json={"names": names}, timeout=10)
        except requests.RequestException as e:
            LOGGER.error(0, "append_cards_to_deck: request failed: %s", e)
            return f"Error: deck editor unreachable at {url}. Is the deck editor running (e.g. python deck_editor.py)?"
        if r.status_code == 404:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            LOGGER.warning("append_cards_to_deck: card not found: %s", detail)
            return f"Error: {detail}"
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            LOGGER.error(0, "append_cards_to_deck: %s %s", r.status_code, detail)
            return f"Error: {r.status_code} {detail}"
        LOGGER.info("Request completed tool=append_cards_to_deck added=%s", len(names))
        return f"Added {len(names)} card(s) to the deck: {', '.join(names)}."

    LOGGER.info("Tool registered: semantic_search_card, plain_search_card, append_cards_to_deck; entering mcp.run(transport=stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
