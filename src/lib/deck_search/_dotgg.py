"""DotGG (playingmtg.com) adapter – official documented public API, no auth."""

from __future__ import annotations

import json
from urllib.parse import quote

import requests

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

_BASE_URL: str = "https://api.dotgg.gg"
_TIMEOUT: int = 15
_GAME: str = "magic"


def _deck_url(slug: str) -> str:
    return f"https://playingmtg.com/decks/{slug}"


def search(
    query: str = "",
    format: str = "",
    colors: str = "",
    n_results: int = 10,
) -> list[DeckSearchResult]:
    """Search DotGG decks via /cgfw/getdecks."""
    color_list: list[str] = []
    if colors:
        color_list = [c.strip().upper() for c in colors.split(",") if c.strip()]

    rq: dict = {
        "page": 1,
        "limit": min(n_results, 30),
        "srt": "views",
        "direct": "desc",
        "type": "",
        "my": 0,
        "myarchive": 0,
        "fav": 0,
        "getdecks": {
            "hascrd": [],
            "nothascrd": [],
            "youtube": 0,
            "smartsrch": query,
            "date": "",
            "color": color_list,
            "collection": 0,
            "topset": "",
            "at": 0,
            "format": format.lower() if format else "",
            "is_tournament": "",
        },
    }
    url: str = f"{_BASE_URL}/cgfw/getdecks?game={_GAME}&rq={quote(json.dumps(rq))}"
    LOGGER.info("dotgg.search: GET %s", url[:200])
    resp: requests.Response = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    decks_list: list[dict] = data if isinstance(data, list) else data.get("results", data.get("decks", []))

    out: list[DeckSearchResult] = []
    for deck in decks_list[:n_results]:
        slug: str = deck.get("name", deck.get("slug", ""))
        human_name: str = deck.get("humanname", slug)
        out.append(DeckSearchResult(
            name=human_name,
            author=deck.get("authornick", ""),
            url=_deck_url(slug),
            source="DotGG",
            format=deck.get("format", ""),
            colors=[],
            date=str(deck.get("date", ""))[:10],
            views=int(deck.get("views", 0) or 0),
        ))
    LOGGER.info("dotgg.search: returned %d results", len(out))
    return out


def get_deck(slug: str) -> DeckDetails:
    """Fetch full deck from DotGG by slug, using boards mode."""
    url: str = f"{_BASE_URL}/cgfw/getdeck?game={_GAME}&slug={quote(slug)}&mode=boards"
    LOGGER.info("dotgg.get_deck: GET %s", url)
    resp: requests.Response = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    data: dict = resp.json()

    mainboard: dict[str, int] = {}
    sideboard: dict[str, int] = {}

    # boards mode returns {"boards": [mainboard_dict, sideboard_dict, ...]}
    # non-boards mode returns {"deck": {"card_id": qty, ...}}
    boards: list[dict] = data.get("boards", [])
    if boards:
        main_raw: dict = boards[0] if len(boards) > 0 else {}
        side_raw: dict = boards[1] if len(boards) > 1 else {}
    else:
        main_raw = data.get("deck", {})
        side_raw = {}

    # DotGG uses card IDs as keys; we need to resolve them to names.
    # Fetch card data to build an ID->name map for the cards in this deck.
    all_ids: list[str] = list(main_raw.keys()) + list(side_raw.keys())
    id_to_name: dict[str, str] = _resolve_card_ids(all_ids) if all_ids else {}

    for card_id, qty in main_raw.items():
        card_name: str = id_to_name.get(card_id, card_id)
        mainboard[card_name] = mainboard.get(card_name, 0) + int(qty)
    for card_id, qty in side_raw.items():
        card_name = id_to_name.get(card_id, card_id)
        sideboard[card_name] = sideboard.get(card_name, 0) + int(qty)

    slug_val: str = data.get("slug", data.get("name", slug))
    return DeckDetails(
        name=data.get("humanname", data.get("name", "")),
        author=data.get("authornick", ""),
        url=_deck_url(slug_val),
        source="DotGG",
        format=data.get("format", ""),
        colors=[],
        mainboard=mainboard,
        sideboard=sideboard,
    )


def _resolve_card_ids(card_ids: list[str]) -> dict[str, str]:
    """Resolve DotGG card IDs to card names via /cgfw/getcards with ids filter."""
    ids_str: str = ",".join(card_ids)
    url: str = f"{_BASE_URL}/cgfw/getcards?game={_GAME}&ids={quote(ids_str)}&mode=plain"
    LOGGER.debug("dotgg._resolve_card_ids: GET %s", url[:200])
    try:
        resp: requests.Response = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
        cards: list[dict] = resp.json()
        return {str(c["id"]): c["name"] for c in cards if "id" in c and "name" in c}
    except Exception as exc:
        LOGGER.warning("dotgg._resolve_card_ids: failed to resolve IDs: %s", exc)
        return {}
