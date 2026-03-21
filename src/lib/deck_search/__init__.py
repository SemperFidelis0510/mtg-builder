"""Online deck search facade – unified interface over multiple MTG deck sites.

Public functions:
    search_decks()          -> formatted text with deck metadata from multiple sources
    get_deck()              -> formatted text with full card list for a single deck URL
    get_deck_as_card_list() -> raw (mainboard, sideboard) dicts for programmatic import
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

# Lazy-import adapters to keep startup light and avoid import-time side effects.
from src.lib.deck_search import (
    _archidekt,
    _dotgg,
    _moxfield,
    _mtggoldfish,
    _spicerack,
)

_VALID_SOURCES: frozenset[str] = frozenset(
    {"archidekt", "dotgg", "moxfield", "spicerack", "mtggoldfish"}
)


# ---------------------------------------------------------------------------
# API key loading
# ---------------------------------------------------------------------------

def _load_api_keys() -> dict[str, str]:
    """Load API keys from src/config/deck_sites_keys.json. Returns empty strings for missing keys."""
    from src.lib.config import DECK_SITES_KEYS_PATH

    if not DECK_SITES_KEYS_PATH.is_file():
        LOGGER.debug("deck_search: keys file not found at %s", DECK_SITES_KEYS_PATH)
        return {"moxfield_api_key": "", "spicerack_api_key": ""}
    try:
        data: dict = json.loads(DECK_SITES_KEYS_PATH.read_text(encoding="utf-8"))
        return {
            "moxfield_api_key": data.get("moxfield_api_key", "") or "",
            "spicerack_api_key": data.get("spicerack_api_key", "") or "",
        }
    except Exception as exc:
        LOGGER.warning("deck_search: failed to load keys file: %s", exc)
        return {"moxfield_api_key": "", "spicerack_api_key": ""}


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

def _parse_deck_url(url: str) -> tuple[str, str]:
    """Detect source and extract deck identifier from a URL.

    Returns (source, identifier) where source is one of _VALID_SOURCES
    and identifier is the deck ID / slug / public ID suitable for the adapter.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    url_lower: str = url.lower()

    if "archidekt.com" in url_lower:
        match = re.search(r"/decks?/(\d+)", url)
        if match:
            return ("archidekt", match.group(1))
        raise ValueError(f"Cannot extract Archidekt deck ID from URL: {url}")

    if "playingmtg.com" in url_lower or "dotgg.gg" in url_lower:
        match = re.search(r"/decks?/([^/?#]+)", url)
        if match:
            return ("dotgg", match.group(1))
        raise ValueError(f"Cannot extract DotGG deck slug from URL: {url}")

    if "moxfield.com" in url_lower:
        match = re.search(r"/decks?/([^/?#]+)", url)
        if match:
            return ("moxfield", match.group(1))
        raise ValueError(f"Cannot extract Moxfield public ID from URL: {url}")

    if "mtggoldfish.com" in url_lower:
        deck_id: str = _mtggoldfish.extract_deck_id_from_url(url)
        return ("mtggoldfish", deck_id)

    if "spicerack.gg" in url_lower:
        raise ValueError(
            "Spicerack URLs point to tournament brackets, not individual decks. "
            "Use the Moxfield decklist link from the search results instead."
        )

    raise ValueError(f"Unrecognized deck site URL: {url}")


# ---------------------------------------------------------------------------
# search_decks
# ---------------------------------------------------------------------------

def search_decks(
    query: str = "",
    format: str = "",
    colors: str = "",
    commander: str = "",
    source: str = "",
    n_results: int = 10,
) -> str:
    """Search for decks across online sources. Returns formatted text."""
    if source:
        src_lower: str = source.strip().lower()
        if src_lower not in _VALID_SOURCES:
            raise ValueError(f"Unknown source {source!r}. Valid: {sorted(_VALID_SOURCES)}")
        sources_to_query: list[str] = [src_lower]
    else:
        sources_to_query = list(_VALID_SOURCES)

    keys: dict[str, str] = _load_api_keys()

    tasks: dict[str, Callable[[], list[DeckSearchResult]]] = {}
    for src in sources_to_query:
        if src == "archidekt":
            tasks[src] = lambda: _archidekt.search(
                query=query, format=format, colors=colors, commander=commander, n_results=n_results,
            )
        elif src == "dotgg":
            tasks[src] = lambda: _dotgg.search(
                query=query, format=format, colors=colors, n_results=n_results,
            )
        elif src == "moxfield":
            _mox_key: str = keys["moxfield_api_key"]
            tasks[src] = lambda k=_mox_key: _moxfield.search(
                query=query, format=format, n_results=n_results, api_key=k,
            )
        elif src == "spicerack":
            _sr_key: str = keys["spicerack_api_key"]
            tasks[src] = lambda k=_sr_key: _spicerack.search(
                format=format, n_results=n_results, api_key=k,
            )
        elif src == "mtggoldfish":
            tasks[src] = lambda: _mtggoldfish.search(
                format=format, n_results=n_results,
            )

    all_results: list[DeckSearchResult] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_to_src = {executor.submit(fn): src_name for src_name, fn in tasks.items()}
        for future in as_completed(future_to_src):
            src_name: str = future_to_src[future]
            try:
                results: list[DeckSearchResult] = future.result(timeout=30)
                all_results.extend(results)
            except Exception as exc:
                LOGGER.warning("deck_search: %s failed: %s", src_name, exc)
                errors.append(f"{src_name}: {exc}")

    if not all_results and not errors:
        return "No decks found matching the search criteria."

    lines: list[str] = [r.format_text() for r in all_results]
    if errors:
        lines.append("")
        lines.append("Some sources failed:")
        for err in errors:
            lines.append(f"  - {err}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_deck
# ---------------------------------------------------------------------------

def get_deck(url: str) -> str:
    """Fetch full deck details from a URL. Returns formatted text."""
    source, identifier = _parse_deck_url(url)
    details: DeckDetails = _fetch_deck_details(source, identifier)
    return details.format_text()


def get_deck_as_card_list(url: str) -> tuple[dict[str, int], dict[str, int]]:
    """Fetch a deck and return raw (mainboard, sideboard) card-name-to-quantity dicts."""
    source, identifier = _parse_deck_url(url)
    details: DeckDetails = _fetch_deck_details(source, identifier)
    return (details.mainboard, details.sideboard)


def _fetch_deck_details(source: str, identifier: str) -> DeckDetails:
    """Dispatch to the correct adapter to fetch full deck details."""
    keys: dict[str, str] = _load_api_keys()

    if source == "archidekt":
        return _archidekt.get_deck(int(identifier))
    if source == "dotgg":
        return _dotgg.get_deck(identifier)
    if source == "moxfield":
        return _moxfield.get_deck(identifier, api_key=keys["moxfield_api_key"])
    if source == "mtggoldfish":
        return _mtggoldfish.get_deck(identifier)
    if source == "spicerack":
        return _spicerack.get_deck(identifier, api_key=keys["spicerack_api_key"])
    raise ValueError(f"Unknown source: {source!r}")
