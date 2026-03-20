#!/usr/bin/env python3
"""
MTG RAG MCP Server: semantic search over the Magic: The Gathering card database.
Run the server via: python -m src.server (or server.bat).
Build the RAG index first using install.bat (install / download / build) or python -m src.lib.build_rag after setup.
"""

import os
import threading

import requests

from src.lib.config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    DECK_EDITOR_BASE_URL,
    MODEL_NAME,
    REPO_ROOT,
)
from src.lib.cardDB import CardDB
from src.utils.logger import LOGGER, init_logger

# ---------------------------------------------------------------------------
# Ensure CWD is the repo root
# ---------------------------------------------------------------------------
os.chdir(REPO_ROOT)


def run_server() -> None:
    """Launch FastMCP server with search_cards tool, stdio transport."""
    init_logger("mcp")
    from fastmcp import FastMCP

    LOGGER.info(
        "Server startup repo_root=%s chroma_path=%s collection=%s model=%s transport=stdio",
        REPO_ROOT, CHROMA_PATH, COLLECTION_NAME, MODEL_NAME,
    )

    def _load_rag() -> None:
        CardDB.inst().load_rag_sync()

    rag_thread: threading.Thread = threading.Thread(target=_load_rag, daemon=True)
    rag_thread.start()
    LOGGER.debug("RAG load started in background thread")

    mcp = FastMCP("MTG Card Search")

    @mcp.tool()
    def semantic_search_card(query: str, n_results: int = 5) -> str:
        """Search for Magic: The Gathering cards by semantic meaning.
        Returns card names and rules text matching the query."""
        LOGGER.info("Request received tool=semantic_search_card query=%r n_results=%s", query, n_results)
        result: str = CardDB.inst().search_cards(query=query, n_results=n_results)
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
        price_usd_min: float = -1.0,
        price_usd_max: float = -1.0,
        power: str = "",
        toughness: str = "",
        keywords: str = "",
        subtype: str = "",
        supertype: str = "",
        format_legal: str = "",
        n_results: int = 20,
    ) -> str:
        """Filter MTG cards by exact properties (name, type, colors, mana value, price range, power/toughness, keywords, etc.).
        All filters are AND-combined. At least one filter must be provided. Returns card names and rules text."""
        LOGGER.info(
            "Request received tool=plain_search_card name=%r type_line=%r colors=%r n_results=%s",
            name, type_line, colors, n_results,
        )
        result: str = CardDB.inst().filter_cards(
            name=name,
            oracle_text=oracle_text,
            type_line=type_line,
            colors=colors,
            color_identity=color_identity,
            mana_value=mana_value,
            mana_value_min=mana_value_min,
            mana_value_max=mana_value_max,
            price_usd_min=price_usd_min,
            price_usd_max=price_usd_max,
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
            LOGGER.error("append_cards_to_deck: request failed: %s", e)
            return f"Error: deck editor unreachable at {url}. Is the deck editor running (e.g. python deck_editor.py)?"
        if r.status_code == 404:
            try:
                detail = r.json().get("detail", r.text)
            except Exception as e:
                LOGGER.debug("append_cards_to_deck: r.json() failed: %s", e)
                detail = r.text
            LOGGER.warning("append_cards_to_deck: card not found: %s", detail)
            return f"Error: {detail}"
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception as e:
                LOGGER.debug("append_cards_to_deck: r.json() failed: %s", e)
                detail = r.text
            LOGGER.error("append_cards_to_deck: %s %s", r.status_code, detail)
            return f"Error: {r.status_code} {detail}"
        LOGGER.info("Request completed tool=append_cards_to_deck added=%s", len(names))
        return f"Added {len(names)} card(s) to the deck: {', '.join(names)}."

    _DEFAULT_CARD_FIELDS: str = "name,mana_cost,mana_value,type_line,text,colors,color_identity,power,toughness,keywords"

    @mcp.tool()
    def get_card_info(
        card_names: str,
        fields: str = _DEFAULT_CARD_FIELDS,
    ) -> str:
        """Get detailed data for one or more MTG cards by exact name.
        card_names: comma-separated card names (e.g. 'Lightning Bolt, Counterspell').
        fields: comma-separated Card field names to include in the response (default covers the most common fields).
        Available fields: name, type_line, types, subtypes, supertypes, text, mana_cost, mana_value, colors, color_identity, power, toughness, keywords, loyalty, defense, legalities, triggers, effects, price_usd.
        Returns a JSON array with the requested fields for each card. Cards not found get an error entry."""
        names: list[str] = [n.strip() for n in card_names.split(",") if n.strip()]
        if not names:
            return "Error: card_names must contain at least one card name (comma-separated)."
        card_fields: list[str] = [f.strip() for f in fields.split(",") if f.strip()]
        if not card_fields:
            return "Error: fields must contain at least one field name (comma-separated)."
        LOGGER.info(
            "Request received tool=get_card_info names=%s fields=%s",
            names, card_fields,
        )
        result: str = CardDB.inst().get_cards_info(names=names, card_fields=card_fields)
        LOGGER.info("Request completed tool=get_card_info names=%s", names)
        return result

    @mcp.tool()
    def extract_card_mechanics(card_name: str, extract_type: str) -> str:
        """Extract triggers or effects for a card by exact name.
        card_name: exact card name (e.g. 'Lightning Bolt').
        extract_type: 'triggers' or 'effects'.
        Returns the extracted list as a semicolon-separated string, or '(none)' if empty."""
        LOGGER.info(
            "Request received tool=extract_card_mechanics card_name=%r extract_type=%s",
            card_name, extract_type,
        )
        result: str = CardDB.inst().get_card_mechanics(name=card_name, extract_type=extract_type)
        LOGGER.info("Request completed tool=extract_card_mechanics card_name=%r", card_name)
        return result

    @mcp.tool()
    def search_triggers(query: str, n_results: int = 10) -> str:
        """Find cards whose triggers (costs, conditions) semantically match the query.
        Use this to find cards that respond to or care about a specific game event or cost.
        Example: query='creature enters the battlefield' returns cards that trigger on creature ETB."""
        LOGGER.info("Request received tool=search_triggers query=%r n_results=%s", query, n_results)
        result: str = CardDB.inst().search_triggers(query=query, n_results=n_results)
        LOGGER.info("Request completed tool=search_triggers query=%r", query)
        return result

    @mcp.tool()
    def search_effects(query: str, n_results: int = 10) -> str:
        """Find cards whose effects (what they produce or provide) semantically match the query.
        Use this to find cards that produce a specific outcome or board state.
        Example: query='create creature token' returns cards that make creature tokens."""
        LOGGER.info("Request received tool=search_effects query=%r n_results=%s", query, n_results)
        result: str = CardDB.inst().search_effects(query=query, n_results=n_results)
        LOGGER.info("Request completed tool=search_effects query=%r", query)
        return result

    # ------------------------------------------------------------------
    # Online deck search tools
    # ------------------------------------------------------------------

    @mcp.tool()
    def search_online_decks(
        query: str = "",
        format: str = "",
        colors: str = "",
        commander: str = "",
        source: str = "",
        n_results: int = 10,
    ) -> str:
        """Search for MTG decklists on popular deck-building sites (Archidekt, DotGG/playingmtg, Moxfield, Spicerack, MTGGoldfish).
        Returns compact metadata (deck name, author, format, colors, URL) for each result.
        query: text search for deck name or archetype (e.g. 'burn', 'atraxa superfriends').
        format: MTG format filter (standard, modern, pioneer, commander, legacy, vintage, pauper, historic, explorer).
        colors: comma-separated color letters to filter (W,U,B,R,G).
        commander: commander card name (Archidekt only, for Commander format).
        source: limit to one site (archidekt, dotgg, moxfield, spicerack, mtggoldfish), or leave empty for all.
        n_results: max results per source (default 10)."""
        LOGGER.info(
            "Request received tool=search_online_decks query=%r format=%r source=%r",
            query, format, source,
        )
        from src.lib.deck_search import search_decks as _search_decks
        result: str = _search_decks(
            query=query, format=format, colors=colors,
            commander=commander, source=source, n_results=n_results,
        )
        LOGGER.info("Request completed tool=search_online_decks")
        return result

    @mcp.tool()
    def get_online_deck(url: str) -> str:
        """Fetch the full card list of an MTG deck from a supported site by URL.
        Supports Archidekt, DotGG/playingmtg, Moxfield, and MTGGoldfish URLs.
        Returns the complete mainboard and sideboard with card names and quantities."""
        LOGGER.info("Request received tool=get_online_deck url=%r", url)
        from src.lib.deck_search import get_deck as _get_deck
        result: str = _get_deck(url=url)
        LOGGER.info("Request completed tool=get_online_deck url=%r", url)
        return result

    @mcp.tool()
    def import_online_deck(url: str) -> str:
        """Import an MTG deck from a supported site URL into the deck editor.
        Fetches the deck, resolves card names, and loads it as the current deck.
        The deck editor must be running. Supports Archidekt, DotGG/playingmtg, Moxfield, MTGGoldfish URLs."""
        LOGGER.info("Request received tool=import_online_deck url=%r", url)
        from src.lib.deck_search import get_deck_as_card_list
        mainboard, sideboard = get_deck_as_card_list(url=url)
        main_names: list[str] = []
        for card_name, qty in mainboard.items():
            for _ in range(qty):
                main_names.append(card_name)
        sb_names: list[str] = []
        for card_name, qty in sideboard.items():
            for _ in range(qty):
                sb_names.append(card_name)
        payload: dict = {"mainboard": main_names, "sideboard": sb_names}
        import_url: str = f"{DECK_EDITOR_BASE_URL.rstrip('/')}/api/import_deck"
        try:
            r = requests.post(import_url, json=payload, timeout=15)
        except requests.RequestException as e:
            LOGGER.error("import_online_deck: request failed: %s", e)
            return f"Error: deck editor unreachable at {import_url}. Is the deck editor running?"
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            LOGGER.error("import_online_deck: %s %s", r.status_code, detail)
            return f"Error: {r.status_code} {detail}"
        LOGGER.info("Request completed tool=import_online_deck main=%d sb=%d", len(main_names), len(sb_names))
        return f"Imported deck with {len(main_names)} mainboard and {len(sb_names)} sideboard cards from {url}."

    LOGGER.info(
        "Tools registered: semantic_search_card, plain_search_card, get_card_info, "
        "extract_card_mechanics, append_cards_to_deck, search_triggers, search_effects, "
        "search_online_decks, get_online_deck, import_online_deck; entering mcp.run(transport=stdio)",
    )
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
