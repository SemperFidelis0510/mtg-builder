"""Moxfield adapter – unofficial API at api2.moxfield.com, requires API key."""

from __future__ import annotations

import requests

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

_BASE_URL: str = "https://api2.moxfield.com"
_TIMEOUT: int = 15

_HEADERS_BASE: dict[str, str] = {
    "User-Agent": "MTGBuilderDeckSearch/1.0",
    "Content-Type": "application/json; charset=utf-8",
}

_COLOR_MAP: dict[str, str] = {"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}


def _deck_url(public_id: str) -> str:
    return f"https://www.moxfield.com/decks/{public_id}"


def _auth_headers(api_key: str) -> dict[str, str]:
    headers: dict[str, str] = dict(_HEADERS_BASE)
    headers["Authorization"] = f"Bearer {api_key}"
    return headers


def search(
    query: str = "",
    format: str = "",
    n_results: int = 10,
    api_key: str = "",
) -> list[DeckSearchResult]:
    """Search Moxfield decks. Requires a valid API key."""
    if not api_key:
        LOGGER.info("moxfield.search: skipped (no API key)")
        return []

    params: dict[str, str | int] = {
        "pageNumber": 1,
        "pageSize": min(n_results, 50),
        "sortType": "views",
        "sortDirection": "Descending",
    }
    if query:
        params["q"] = query
    if format:
        params["fmt"] = format.strip().lower()

    url: str = f"{_BASE_URL}/v2/decks/search"
    LOGGER.info("moxfield.search: GET %s params=%s", url, params)
    resp: requests.Response = requests.get(url, params=params, headers=_auth_headers(api_key), timeout=_TIMEOUT)
    resp.raise_for_status()
    data: dict = resp.json()

    pages_data: list[dict] = data.get("data", data.get("decks", []))
    if isinstance(data, list):
        pages_data = data

    out: list[DeckSearchResult] = []
    for deck in pages_data[:n_results]:
        public_id: str = deck.get("publicId", deck.get("id", ""))
        created_by: dict = deck.get("createdByUser", {})
        user_name: str = created_by.get("userName", "") if isinstance(created_by, dict) else ""
        colors: list[str] = deck.get("colorIdentity", []) or []
        out.append(DeckSearchResult(
            name=deck.get("name", ""),
            author=user_name,
            url=_deck_url(public_id),
            source="Moxfield",
            format=deck.get("format", ""),
            colors=[c.upper() for c in colors if isinstance(c, str)],
            date=str(deck.get("createdAtUtc", ""))[:10],
            views=int(deck.get("viewCount", 0) or 0),
        ))
    LOGGER.info("moxfield.search: returned %d results", len(out))
    return out


def get_deck(public_id: str, api_key: str = "") -> DeckDetails:
    """Fetch full deck from Moxfield by public ID."""
    if not api_key:
        LOGGER.error("moxfield.get_deck: no API key configured")
        raise ValueError("Moxfield API key is required to fetch deck details. Configure it in src/config/deck_sites_keys.json.")

    url: str = f"{_BASE_URL}/v2/decks/all/{public_id}"
    LOGGER.info("moxfield.get_deck: GET %s", url)
    resp: requests.Response = requests.get(url, headers=_auth_headers(api_key), timeout=_TIMEOUT)
    resp.raise_for_status()
    data: dict = resp.json()

    mainboard: dict[str, int] = {}
    sideboard: dict[str, int] = {}
    commander_list: list[str] = []

    for board_key, target in [("mainboard", mainboard), ("sideboard", sideboard)]:
        board: dict = data.get(board_key, {})
        for card_key, card_entry in board.items():
            card_data: dict = card_entry.get("card", {})
            card_name: str = card_data.get("name", card_key)
            qty: int = int(card_entry.get("quantity", 1))
            target[card_name] = target.get(card_name, 0) + qty

    commanders_board: dict = data.get("commanders", {})
    for card_key, card_entry in commanders_board.items():
        card_data = card_entry.get("card", {})
        card_name = card_data.get("name", card_key)
        qty = int(card_entry.get("quantity", 1))
        commander_list.append(card_name)
        mainboard[card_name] = mainboard.get(card_name, 0) + qty

    created_by: dict = data.get("createdByUser", {})
    user_name: str = created_by.get("userName", "") if isinstance(created_by, dict) else ""

    return DeckDetails(
        name=data.get("name", ""),
        author=user_name,
        url=_deck_url(public_id),
        source="Moxfield",
        format=data.get("format", ""),
        colors=[c.upper() for c in (data.get("colorIdentity", []) or []) if isinstance(c, str)],
        mainboard=mainboard,
        sideboard=sideboard,
        commander=commander_list,
    )
