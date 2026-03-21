"""Archidekt adapter – unofficial public REST API (no auth required)."""

from __future__ import annotations

import requests

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

_BASE_URL: str = "https://archidekt.com/api"
_TIMEOUT: int = 15
_HEADERS: dict[str, str] = {
    "Accept": "application/json",
    "User-Agent": "MTGBuilderDeckSearch/1.0",
}

# Archidekt uses numeric format IDs.
_FORMAT_IDS: dict[str, int] = {
    "standard": 1,
    "modern": 2,
    "commander": 3,
    "legacy": 4,
    "vintage": 5,
    "pauper": 6,
    "pioneer": 11,
    "historic": 12,
    "explorer": 13,
}


def _deck_url(deck_id: int) -> str:
    return f"https://archidekt.com/decks/{deck_id}"


def search(
    query: str = "",
    format: str = "",
    colors: str = "",
    commander: str = "",
    n_results: int = 10,
) -> list[DeckSearchResult]:
    """Search Archidekt decks. Returns compact metadata."""
    params: dict[str, str | int] = {"pageSize": min(n_results, 50), "orderBy": "-viewCount"}
    if query:
        params["name"] = query
    if format:
        fmt_lower: str = format.strip().lower()
        if fmt_lower in _FORMAT_IDS:
            params["formats"] = _FORMAT_IDS[fmt_lower]
    if colors:
        color_list: list[str] = [c.strip() for c in colors.split(",") if c.strip()]
        color_map: dict[str, str] = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
        mapped: list[str] = [color_map[c.upper()] for c in color_list if c.upper() in color_map]
        if mapped:
            for color_name in mapped:
                params.setdefault("colors", "")
                params["colors"] = ",".join(mapped)
    if commander:
        params["commanders"] = f'"{commander}"'

    url: str = f"{_BASE_URL}/decks/cards/"
    LOGGER.info("archidekt.search: GET %s params=%s", url, params)
    resp: requests.Response = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    results_list: list[dict] = data if isinstance(data, list) else data.get("results", [])

    out: list[DeckSearchResult] = []
    for deck in results_list[:n_results]:
        deck_id: int = deck["id"]
        owner: dict = deck.get("owner") or {}
        owner_name: str = owner.get("username", "") if isinstance(owner, dict) else str(owner)
        deck_colors: list[str] = []
        for ci in deck.get("colorIdentity", []):
            if isinstance(ci, str):
                deck_colors.append(ci)
            elif isinstance(ci, dict):
                deck_colors.append(ci.get("symbol", ""))
        out.append(DeckSearchResult(
            name=deck.get("name", ""),
            author=owner_name,
            url=_deck_url(deck_id),
            source="Archidekt",
            format=deck.get("format", {}).get("name", "") if isinstance(deck.get("format"), dict) else str(deck.get("format", "")),
            colors=deck_colors,
            date=deck.get("createdAt", "")[:10] if deck.get("createdAt") else "",
            views=int(deck.get("viewCount", 0)),
        ))
    LOGGER.info("archidekt.search: returned %d results", len(out))
    return out


def get_deck(deck_id: int | str) -> DeckDetails:
    """Fetch full deck details from Archidekt by numeric ID."""
    url: str = f"{_BASE_URL}/decks/{deck_id}/"
    LOGGER.info("archidekt.get_deck: GET %s", url)
    resp: requests.Response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    data: dict = resp.json()

    mainboard: dict[str, int] = {}
    sideboard: dict[str, int] = {}
    commander_list: list[str] = []

    for card_entry in data.get("cards", []):
        card_data: dict = card_entry.get("card", {})
        oracle_card: dict = card_data.get("oracleCard", card_data)
        card_name: str = oracle_card.get("name", card_data.get("name", "Unknown"))
        qty: int = int(card_entry.get("quantity", 1))
        categories: list[str] = card_entry.get("categories", [])

        if "Commander" in categories:
            commander_list.append(card_name)
            mainboard[card_name] = mainboard.get(card_name, 0) + qty
        elif "Sideboard" in categories:
            sideboard[card_name] = sideboard.get(card_name, 0) + qty
        elif "Maybeboard" in categories:
            pass
        else:
            mainboard[card_name] = mainboard.get(card_name, 0) + qty

    owner: dict = data.get("owner") or {}
    owner_name: str = owner.get("username", "") if isinstance(owner, dict) else str(owner)
    deck_colors: list[str] = []
    for ci in data.get("colorIdentity", []):
        if isinstance(ci, str):
            deck_colors.append(ci)
        elif isinstance(ci, dict):
            deck_colors.append(ci.get("symbol", ""))

    fmt = data.get("format", "")
    if isinstance(fmt, dict):
        fmt = fmt.get("name", "")

    return DeckDetails(
        name=data.get("name", ""),
        author=owner_name,
        url=_deck_url(int(deck_id)),
        source="Archidekt",
        format=str(fmt),
        colors=deck_colors,
        mainboard=mainboard,
        sideboard=sideboard,
        commander=commander_list,
    )
