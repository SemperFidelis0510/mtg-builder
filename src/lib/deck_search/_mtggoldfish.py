"""MTGGoldfish adapter – web scraping, no official API. Uses beautifulsoup4."""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from src.lib.deck_search._models import DeckDetails, DeckSearchResult
from src.utils.logger import LOGGER

_BASE_URL: str = "https://www.mtggoldfish.com"
_TIMEOUT: int = 15

_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_FORMAT_SLUGS: dict[str, str] = {
    "standard": "standard",
    "modern": "modern",
    "pioneer": "pioneer",
    "legacy": "legacy",
    "vintage": "vintage",
    "pauper": "pauper",
    "commander": "commander_1v1",
    "historic": "historic",
    "explorer": "explorer",
}


def search(
    format: str = "",
    n_results: int = 10,
) -> list[DeckSearchResult]:
    """Scrape MTGGoldfish metagame page for top decks in a format."""
    fmt_slug: str = _FORMAT_SLUGS.get(format.strip().lower(), format.strip().lower()) if format else "modern"
    url: str = f"{_BASE_URL}/metagame/{fmt_slug}/full"
    LOGGER.info("mtggoldfish.search: GET %s", url)

    resp: requests.Response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()

    soup: BeautifulSoup = BeautifulSoup(resp.text, "html.parser")

    out: list[DeckSearchResult] = []

    # Metagame tiles contain archetype links and meta share info.
    tiles = soup.select(".archetype-tile")
    for tile in tiles[:n_results]:
        link_tag = tile.select_one(".archetype-tile-title a, .deck-price-paper a, a[href*='/archetype/']")
        if not link_tag:
            link_tag = tile.find("a")
        if not link_tag:
            continue

        deck_name: str = link_tag.get_text(strip=True)
        href: str = link_tag.get("href", "")
        deck_url: str = href if href.startswith("http") else f"{_BASE_URL}{href}"

        meta_share: str = ""
        share_el = tile.select_one(".archetype-tile-statistic-value, .percentage")
        if share_el:
            meta_share = share_el.get_text(strip=True)

        out.append(DeckSearchResult(
            name=f"{deck_name} ({meta_share})" if meta_share else deck_name,
            author="MTGGoldfish Meta",
            url=deck_url,
            source="MTGGoldfish",
            format=format or fmt_slug,
            colors=[],
            date="",
            views=0,
        ))

    # Fallback: try table rows if tiles not found
    if not out:
        rows = soup.select("table tr")
        for row in rows[:n_results]:
            link_tag = row.select_one("a[href*='/archetype/'], a[href*='/deck/']")
            if not link_tag:
                continue
            deck_name = link_tag.get_text(strip=True)
            href = link_tag.get("href", "")
            deck_url = href if href.startswith("http") else f"{_BASE_URL}{href}"
            out.append(DeckSearchResult(
                name=deck_name,
                author="MTGGoldfish Meta",
                url=deck_url,
                source="MTGGoldfish",
                format=format or fmt_slug,
                colors=[],
                date="",
                views=0,
            ))

    LOGGER.info("mtggoldfish.search: returned %d results", len(out))
    return out


def get_deck(deck_id: str) -> DeckDetails:
    """Download a MTGGoldfish deck by numeric ID. Tries the text download endpoint first."""
    download_url: str = f"{_BASE_URL}/deck/download/{deck_id}"
    LOGGER.info("mtggoldfish.get_deck: GET %s", download_url)
    resp: requests.Response = requests.get(download_url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()

    mainboard: dict[str, int] = {}
    sideboard: dict[str, int] = {}
    in_sideboard: bool = False

    for line in resp.text.splitlines():
        stripped: str = line.strip()
        if not stripped:
            in_sideboard = True
            continue
        match = re.match(r"^(\d+)\s+(.+)$", stripped)
        if not match:
            continue
        qty: int = int(match.group(1))
        card_name: str = match.group(2).strip()
        if in_sideboard:
            sideboard[card_name] = sideboard.get(card_name, 0) + qty
        else:
            mainboard[card_name] = mainboard.get(card_name, 0) + qty

    deck_url: str = f"{_BASE_URL}/deck/{deck_id}"

    # Try to get the deck name from the deck page
    deck_name: str = f"MTGGoldfish Deck #{deck_id}"
    try:
        page_resp: requests.Response = requests.get(deck_url, headers=_HEADERS, timeout=_TIMEOUT)
        if page_resp.status_code == 200:
            page_soup: BeautifulSoup = BeautifulSoup(page_resp.text, "html.parser")
            title_tag = page_soup.select_one("h1.title, .deck-view-title, h2.deck-view-title")
            if title_tag:
                deck_name = title_tag.get_text(strip=True)
    except Exception as exc:
        LOGGER.debug("mtggoldfish.get_deck: failed to fetch deck name: %s", exc)

    return DeckDetails(
        name=deck_name,
        author="MTGGoldfish",
        url=deck_url,
        source="MTGGoldfish",
        mainboard=mainboard,
        sideboard=sideboard,
    )


def extract_deck_id_from_url(url: str) -> str:
    """Extract a numeric deck ID or archetype slug from a MTGGoldfish URL."""
    # /deck/12345 or /deck/download/12345
    match = re.search(r"/deck(?:/download)?/(\d+)", url)
    if match:
        return match.group(1)
    # /archetype/burn-modern#paper -> need to go to the archetype page and find a sample deck
    match = re.search(r"/archetype/([^#?/]+)", url)
    if match:
        return _get_sample_deck_from_archetype(match.group(1))
    LOGGER.error("mtggoldfish: cannot extract deck ID from URL: %s", url)
    raise ValueError(f"Cannot extract MTGGoldfish deck ID from URL: {url}")


def _get_sample_deck_from_archetype(archetype_slug: str) -> str:
    """Load an archetype page and find the first sample deck ID."""
    url: str = f"{_BASE_URL}/archetype/{archetype_slug}"
    LOGGER.info("mtggoldfish._get_sample_deck_from_archetype: GET %s", url)
    resp: requests.Response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    soup: BeautifulSoup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("a[href*='/deck/']")
    if link:
        href: str = link.get("href", "")
        match = re.search(r"/deck/(\d+)", href)
        if match:
            return match.group(1)
    LOGGER.error("mtggoldfish: no sample deck found for archetype %s", archetype_slug)
    raise ValueError(f"No sample deck found for MTGGoldfish archetype: {archetype_slug}")
