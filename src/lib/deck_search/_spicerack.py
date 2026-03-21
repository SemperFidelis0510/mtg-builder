"""Spicerack adapter – official documented API, requires API key. Tournament results only."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

_BASE_URL: str = "https://api.spicerack.gg/api"
_TIMEOUT: int = 20

_FORMAT_MAP: dict[str, str] = {
    "standard": "STANDARD",
    "modern": "MODERN",
    "pioneer": "PIONEER",
    "legacy": "LEGACY",
    "vintage": "VINTAGE",
    "commander": "COMMANDER2",
    "pauper": "PAUPER",
    "historic": "HISTORIC",
    "explorer": "EXPLORER",
    "timeless": "TIMELESS",
    "duel": "DUEL",
    "oathbreaker": "OATHBREAKER",
    "premodern": "PREMODERN",
}


def search(
    format: str = "",
    num_days: int = 14,
    n_results: int = 10,
    api_key: str = "",
) -> list[DeckSearchResult]:
    """Search Spicerack tournament decklists. Requires API key."""
    if not api_key:
        LOGGER.info("spicerack.search: skipped (no API key)")
        return []

    params: dict[str, str | int | bool] = {
        "num_days": num_days,
        "decklist_as_text": "false",
    }
    if format:
        fmt_upper: str = _FORMAT_MAP.get(format.strip().lower(), format.strip().upper())
        params["event_format"] = fmt_upper

    url: str = f"{_BASE_URL}/export-decklists/"
    headers: dict[str, str] = {"X-API-Key": api_key}
    LOGGER.info("spicerack.search: GET %s params=%s", url, params)
    resp: requests.Response = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT)
    resp.raise_for_status()
    tournaments: list[dict] = resp.json()

    out: list[DeckSearchResult] = []
    for tournament in tournaments:
        t_name: str = tournament.get("tournamentName", "")
        t_format: str = tournament.get("format", "")
        bracket_url: str = tournament.get("bracketUrl", "")
        start_ts: int | None = tournament.get("startDate")
        date_str: str = ""
        if start_ts:
            date_str = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        for standing in tournament.get("standings", []):
            player: str = standing.get("name", "")
            deck_url: str = standing.get("decklist", bracket_url) or bracket_url
            wins_swiss: int = int(standing.get("winsSwiss", 0))
            losses_swiss: int = int(standing.get("lossesSwiss", 0))
            record: str = f"{wins_swiss}-{losses_swiss}"
            deck_name: str = f"{t_name} ({record})"

            out.append(DeckSearchResult(
                name=deck_name,
                author=player,
                url=deck_url,
                source="Spicerack",
                format=t_format,
                colors=[],
                date=date_str,
                views=0,
            ))
            if len(out) >= n_results:
                break
        if len(out) >= n_results:
            break

    LOGGER.info("spicerack.search: returned %d results", len(out))
    return out


def get_deck(url: str, api_key: str = "") -> DeckDetails:
    """Spicerack doesn't host individual deck pages — re-fetch the tournament data with decklist text.

    The url should be a bracket or Moxfield link from search results.
    For Moxfield links, delegate to the Moxfield adapter instead.
    For bracket URLs, refetch the tournament with decklist_as_text=true and find the matching standing.
    """
    LOGGER.error("spicerack.get_deck: not directly supported (url=%s)", url)
    raise NotImplementedError(
        "Spicerack does not host individual deck pages. "
        "Use the Moxfield URL from the search results to fetch the full deck via Moxfield, "
        "or re-search with decklist_as_text=true."
    )
