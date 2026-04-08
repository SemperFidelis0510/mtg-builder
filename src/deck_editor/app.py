"""FastAPI application for the deck editor: serves HTML and deck API."""

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.lib.cardDB import CardDB
from src.lib.config import DECK_EDITOR_SAVE_DIR, REPO_ROOT
from src.lib.deck_board_ops import collect_matching_indices_asc, move_cards_at_indices, remove_cards_at_indices
from src.lib.deck_name_match import commander_string_matches_request, requested_name_matches_deck_card
from src.lib.prices import BATCH_SIZE, DELAY_BETWEEN_BATCHES_S, SCRYFALL_COLLECTION_URL, prices_age_hours, update_all_prices
from src.obj.card import Card
from src.obj.deck import Deck, _cards_from_names, _normalize_cards_arg, _resolve_name_to_type_key
from src.utils.logger import LOGGER


class DeckEditorError(Exception):
    """Deck mutation error with HTTP-like status (used by API routes and in-process agent)."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code: int = status_code
        self.detail: str = detail
        super().__init__(detail)


app = FastAPI(title="MTG Deck Editor")

from src.deck_editor.agent_routes import agent_router  # noqa: E402  (after app creation to avoid circular)

app.include_router(agent_router)


@app.middleware("http")
async def add_no_cache_for_static_assets(request: Request, call_next):
    """Disable browser caching for editor JS/CSS so frontend changes are always picked up."""
    response = await call_next(request)
    if request.url.path.startswith("/js/") or request.url.path.startswith("/styles/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

# ---------------------------------------------------------------------------
# In-memory state (always a deck; starts empty; POST replaces it)
# ---------------------------------------------------------------------------
_current_deck: Deck = Deck()

# ---------------------------------------------------------------------------
# SSE event bus
# ---------------------------------------------------------------------------
_sse_clients: set[asyncio.Queue[str]] = set()

# ---------------------------------------------------------------------------
# Card image redirect cache (name+face+size -> final CDN URL)
# ---------------------------------------------------------------------------
_CARD_IMAGE_CACHE: dict[tuple[str, int, str], str] = {}


def _sleep_for_retry_after(headers: Any, attempt: int) -> None:
    """Sleep for Retry-After if present; otherwise exponential backoff."""
    retry_after_s: float | None = None
    try:
        if headers is not None and hasattr(headers, "get"):
            ra = headers.get("Retry-After")
            if isinstance(ra, str) and ra.strip():
                retry_after_s = float(ra.strip())
    except Exception:
        retry_after_s = None
    delay: float = retry_after_s if retry_after_s is not None else min(8.0, 0.5 * (2 ** attempt))
    time.sleep(max(0.25, delay))


def _prefetch_deck_image_redirects(deck: Deck) -> None:
    """Prefetch Scryfall CDN redirect targets for all card faces in *deck*.

    This avoids a burst of per-card image lookups that can hit Scryfall rate limits (429).
    Best-effort: logs errors but does not raise (deck load should still succeed).
    """
    try:
        import requests

        # Collect unique display names from all boards + commander.
        names: set[str] = set()
        for c in deck.cards:
            names.add(c.name)
        for c in deck.maybe:
            names.add(c.name)
        for c in deck.sideboard:
            names.add(c.name)
        if deck.commander:
            names.add(deck.commander)

        # Expand to per-face names so DFC faces can be cached individually.
        face_entries: list[tuple[str, int]] = []
        for n in sorted(names):
            try:
                faces: list[Card] = CardDB.inst().resolve_faces(n)
            except Exception as e:
                LOGGER.error("_prefetch_deck_image_redirects: resolve_faces failed name=%r err=%s", n, e)
                continue
            for i, f in enumerate(faces):
                face_entries.append((f.name, i))

        if not face_entries:
            return

        # Query Scryfall in batches using /cards/collection.
        # We request by name (not scryfall_id) because names are what the UI uses.
        for i in range(0, len(face_entries), BATCH_SIZE):
            batch = face_entries[i : i + BATCH_SIZE]
            identifiers: list[dict[str, str]] = [{"name": face_name} for (face_name, _idx) in batch]
            # Retry on 429 with backoff; otherwise fail fast (best-effort overall).
            r = None
            for attempt in range(4):
                r = requests.post(
                    SCRYFALL_COLLECTION_URL,
                    json={"identifiers": identifiers},
                    timeout=30,
                    headers={"Accept": "application/json", "User-Agent": "MTG-MCP/1.0"},
                )
                if r.status_code == 429:
                    LOGGER.error("_prefetch_deck_image_redirects: rate limited (429); backing off attempt=%d", attempt + 1)
                    _sleep_for_retry_after(r.headers, attempt)
                    continue
                break
            if r is None or not r.ok:
                status = r.status_code if r is not None else "n/a"
                LOGGER.error("_prefetch_deck_image_redirects: scryfall collection failed status=%s", status)
                return
            payload = r.json()
            data = payload["data"] if isinstance(payload, dict) and "data" in payload else None
            if not isinstance(data, list):
                LOGGER.error("_prefetch_deck_image_redirects: invalid scryfall payload (missing data list)")
                return

            # Build quick lookup from returned card data.
            for card_obj in data:
                if not isinstance(card_obj, dict):
                    continue
                card_name = card_obj["name"] if "name" in card_obj and isinstance(card_obj["name"], str) else ""
                if not card_name:
                    continue

                # For single-faced cards, images are in image_uris.
                if "image_uris" in card_obj and isinstance(card_obj["image_uris"], dict):
                    image_uris = card_obj["image_uris"]
                    if "normal" in image_uris and isinstance(image_uris["normal"], str):
                        _CARD_IMAGE_CACHE[(card_name, 0, "normal")] = image_uris["normal"]
                    if "large" in image_uris and isinstance(image_uris["large"], str):
                        _CARD_IMAGE_CACHE[(card_name, 0, "large")] = image_uris["large"]

                # For multi-faced cards, images are per face.
                if "card_faces" in card_obj and isinstance(card_obj["card_faces"], list):
                    faces_list = card_obj["card_faces"]
                    for face_idx, face_obj in enumerate(faces_list):
                        if not isinstance(face_obj, dict):
                            continue
                        face_name = face_obj["name"] if "name" in face_obj and isinstance(face_obj["name"], str) else ""
                        if not face_name:
                            continue
                        image_uris = face_obj["image_uris"] if "image_uris" in face_obj else None
                        if not isinstance(image_uris, dict):
                            continue
                        if "normal" in image_uris and isinstance(image_uris["normal"], str):
                            _CARD_IMAGE_CACHE[(face_name, face_idx, "normal")] = image_uris["normal"]
                        if "large" in image_uris and isinstance(image_uris["large"], str):
                            _CARD_IMAGE_CACHE[(face_name, face_idx, "large")] = image_uris["large"]

            if i + BATCH_SIZE < len(face_entries):
                time.sleep(DELAY_BETWEEN_BATCHES_S)
    except Exception as e:
        LOGGER.error("_prefetch_deck_image_redirects: unexpected failure: %s", e)


def _broadcast(event_type: str, data_dict: dict) -> None:
    """Push an SSE-formatted message to all connected clients."""
    LOGGER.debug("_broadcast: event=%s clients=%d", event_type, len(_sse_clients))
    payload: str = json.dumps(data_dict)
    lines: str = f"event: {event_type}\ndata: {payload}\n\n"
    for q in _sse_clients:
        try:
            q.put_nowait(lines)
        except asyncio.QueueFull:
            LOGGER.warning("_broadcast: SSE queue full, dropping event %s", event_type)


def _notify_deck_updated() -> None:
    """Broadcast current deck state to all SSE clients."""
    _broadcast("deck_updated", _deck_to_response(_current_deck))


@app.on_event("startup")
def _startup_refresh_prices() -> None:
    """If prices are missing or older than 24h, start a background price update."""
    age: float | None = prices_age_hours()
    LOGGER.info("_startup_refresh_prices: price age=%s hours", age)
    if age is None or age > 24:
        LOGGER.info("_startup_refresh_prices: starting background price update")
        thread: threading.Thread = threading.Thread(target=_run_price_update_then_notify, daemon=True)
        thread.start()


def _startup_load_rag() -> None:
    """Load RAG (embedding model + ChromaDB) in background so semantic search is ready without blocking startup."""
    LOGGER.info("_startup_load_rag: starting RAG load")
    CardDB.inst().load_rag_sync()
    LOGGER.info("_startup_load_rag: RAG loaded successfully")


@app.on_event("startup")
def _startup_rag_async() -> None:
    """Start RAG loading in a background thread at server init; heavy deps are not imported in the main process until then."""
    disable: str = (os.environ.get("MTG_DISABLE_RAG_STARTUP") or "").strip().lower()
    if disable in ("1", "true", "yes", "on"):
        LOGGER.info("_startup_rag_async: MTG_DISABLE_RAG_STARTUP set; skipping RAG background load")
        return
    LOGGER.info("_startup_rag_async: launching RAG background thread")
    thread: threading.Thread = threading.Thread(target=_startup_load_rag, daemon=True)
    thread.start()


# Type-group keys used by the client (order: creature, instant, sorcery, artifact, enchantment, planeswalker, battle, land)
TYPE_KEYS: list[str] = [
    "creature",
    "instant",
    "sorcery",
    "artifact",
    "enchantment",
    "planeswalker",
    "battle",
    "land",
]


_COLOR_SYMBOLS: str = "WUBRG"
_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


def _count_colored_mana_in_cost(mana_cost: str) -> dict[str, int]:
    """Parse mana cost string and count W, U, B, R, G (only colored symbols). Hybrid counts for each color."""
    counts: dict[str, int] = {c: 0 for c in _COLOR_SYMBOLS}
    if not mana_cost or not isinstance(mana_cost, str):
        return counts
    for sym_match in _MANA_SYMBOL_RE.finditer(mana_cost):
        inner: str = sym_match.group(1)
        for c in _COLOR_SYMBOLS:
            if c in inner.upper():
                counts[c] += 1
    return counts


def _compute_deck_card_colors(deck: Deck) -> set[str]:
    """Return set of WUBRG colors present in any card's color_identity."""
    colors: set[str] = set()
    for card in deck.cards:
        for c in getattr(card, "color_identity", []) or []:
            if c in "WUBRG":
                colors.add(c)
    return colors


_VALID_BOARDS: frozenset[str] = frozenset({"main", "maybe", "sideboard", "commander"})


def _valid_boards_detail() -> str:
    return "'main', 'maybe', 'sideboard', or 'commander'"


def _get_board_list(deck: Deck, board: str) -> list[Card]:
    """Return the card list for the given board name. Raises ValueError for unknown boards."""
    if board == "main":
        return deck.cards
    if board == "maybe":
        return deck.maybe
    if board == "sideboard":
        return deck.sideboard
    raise ValueError(f"Unknown board: {board!r}. Must be {_valid_boards_detail()}.")


def _push_previous_commander_to_main(deck: Deck) -> None:
    """If deck.commander is set, resolve it to a Card and append one copy to main. Logs on resolve failure."""
    prev: str = (deck.commander or "").strip()
    if not prev:
        return
    try:
        prev_cards: list[Card] = _cards_from_names([prev])
    except ValueError:
        LOGGER.warning("push_previous_commander_to_main: could not resolve previous commander %r", prev)
        return
    if prev_cards:
        deck.cards.append(prev_cards[0])


def _assign_commander_card(deck: Deck, card: Card) -> None:
    """Set commander to *card*'s canonical name; previous commander (if any) is appended to main as one copy."""
    LOGGER.debug("_assign_commander_card: new=%r prev=%r", card.name, deck.commander)
    _push_previous_commander_to_main(deck)
    deck.commander = card.name


def _commander_name_lower(deck: Deck) -> str | None:
    c: str = (deck.commander or "").strip()
    return c.lower() if c else None


def _move_cards_between_boards(
    deck: Deck,
    names_to_move: list[str],
    from_board: str,
    to_board: str,
    count: int,
) -> None:
    """Move cards between boards including commander slot. Raises DeckEditorError on failure."""
    LOGGER.debug("_move_cards_between_boards: names=%s from=%s to=%s count=%d", names_to_move, from_board, to_board, count)
    if from_board not in _VALID_BOARDS:
        raise DeckEditorError(400, f"Invalid from_board: {from_board!r}. Must be {_valid_boards_detail()}.")
    if to_board not in _VALID_BOARDS:
        raise DeckEditorError(400, f"Invalid to_board: {to_board!r}. Must be {_valid_boards_detail()}.")
    if from_board == to_board:
        raise DeckEditorError(400, f"from_board and to_board must differ (both are {from_board!r})")
    if count < 1:
        raise DeckEditorError(400, "count must be >= 1")

    if to_board == "commander":
        if len(names_to_move) != 1 or count != 1:
            raise DeckEditorError(400, "Moving to commander requires exactly one card name and count=1")
        card_name: str = names_to_move[0]
        source_list: list[Card] = _get_board_list(deck, from_board)
        idx_move: int | None = None
        for i, c in enumerate(source_list):
            if requested_name_matches_deck_card(c, card_name):
                idx_move = i
                break
        if idx_move is None:
            raise DeckEditorError(404, f"Card(s) not found in {from_board} board: {card_name}")
        moved_card: Card = source_list.pop(idx_move)
        _assign_commander_card(deck, moved_card)
    elif from_board == "commander":
        if len(names_to_move) != 1 or count != 1:
            raise DeckEditorError(400, "Moving from commander requires exactly one card name and count=1")
        cur: str = (deck.commander or "").strip()
        if not cur:
            raise DeckEditorError(404, "No commander set")
        if not commander_string_matches_request(cur, names_to_move[0]):
            raise DeckEditorError(404, f"Card(s) not found in commander slot: {names_to_move[0]!r}")
        try:
            cmd_cards: list[Card] = _cards_from_names([cur])
        except ValueError:
            raise DeckEditorError(404, f"Commander not found in card DB: {deck.commander!r}") from None
        cmd_card: Card = cmd_cards[0]
        deck.commander = ""
        dest_list: list[Card] = _get_board_list(deck, to_board)
        dest_list.append(cmd_card)
    else:
        source_list = _get_board_list(deck, from_board)
        dest_list = _get_board_list(deck, to_board)
        idx_asc, not_found = collect_matching_indices_asc(source_list, names_to_move, count)
        if idx_asc is None:
            assert not_found
            raise DeckEditorError(404, f"Card(s) not found in {from_board} board: {', '.join(not_found)}")
        move_cards_at_indices(source_list, dest_list, idx_asc)


def _recompute_and_set_colors(deck: Deck) -> None:
    """Recompute deck colors from all boards (main + maybe + sideboard + commander) and update deck.colors."""
    colors: set[str] = set()
    for card in deck.cards:
        for c in getattr(card, "color_identity", []) or []:
            if c in "WUBRG":
                colors.add(c)
    for card in deck.maybe:
        for c in getattr(card, "color_identity", []) or []:
            if c in "WUBRG":
                colors.add(c)
    for card in deck.sideboard:
        for c in getattr(card, "color_identity", []) or []:
            if c in "WUBRG":
                colors.add(c)
    cmd_lower: str | None = _commander_name_lower(deck)
    if cmd_lower:
        try:
            cmd_card: Card = _cards_from_names([(deck.commander or "").strip()])[0]
            for c in getattr(cmd_card, "color_identity", []) or []:
                if c in "WUBRG":
                    colors.add(c)
        except ValueError:
            LOGGER.warning("_recompute_and_set_colors: commander %r not found in card DB", deck.commander)
    deck.colors = list(colors)


def _compute_deck_stats(deck: Deck) -> dict:
    """Compute total cards, non_land, lands, and W/U/B/R/G symbol distribution as percentages."""
    total_cards: int = 0
    land_count: int = 0
    all_names: list[str] = []
    for key in TYPE_KEYS:
        lst: list[str] = getattr(deck, key, None) or []
        if not isinstance(lst, list):
            continue
        total_cards += len(lst)
        if key == "land":
            land_count = len(lst)
        all_names.extend(lst)
    non_land: int = total_cards - land_count

    color_counts: dict[str, int] = {c: 0 for c in _COLOR_SYMBOLS}
    card_db = CardDB.inst()

    def _resolve_for_stats(name: str) -> Card | None:
        try:
            return card_db.resolve_primary_card(name)
        except ValueError:
            return None

    for name in all_names:
        card = _resolve_for_stats(name)
        if card is None:
            continue
        cost_counts: dict[str, int] = _count_colored_mana_in_cost(card.mana_cost)
        for c in _COLOR_SYMBOLS:
            color_counts[c] += cost_counts[c]
    total_colored: int = sum(color_counts.values())
    if total_colored == 0:
        color_distribution: dict[str, float] = {c: 0.0 for c in _COLOR_SYMBOLS}
    else:
        color_distribution = {c: round(100.0 * color_counts[c] / total_colored, 1) for c in _COLOR_SYMBOLS}

    # Mana value histogram for non-land cards: creatures vs non-creatures (buckets 0..6, 7+)
    mv_creatures: list[int] = [0] * 8
    mv_non_creatures: list[int] = [0] * 8
    creature_names: list[str] = list(getattr(deck, "creature", None) or [])
    non_creature_non_land: list[str] = []
    for key in TYPE_KEYS:
        if key in ("land", "creature"):
            continue
        lst = getattr(deck, key, None) or []
        if isinstance(lst, list):
            non_creature_non_land.extend(lst)
    for name in creature_names:
        card = _resolve_for_stats(name)
        if card is None:
            continue
        mv: float = getattr(card, "mana_value", -1.0) if hasattr(card, "mana_value") else -1.0
        if mv < 0:
            mv = 0.0
        idx = min(7, int(mv))
        mv_creatures[idx] += 1
    for name in non_creature_non_land:
        card = _resolve_for_stats(name)
        if card is None:
            continue
        mv = getattr(card, "mana_value", -1.0) if hasattr(card, "mana_value") else -1.0
        if mv < 0:
            mv = 0.0
        idx = min(7, int(mv))
        mv_non_creatures[idx] += 1
    mana_value_distribution = {"creatures": mv_creatures, "non_creatures": mv_non_creatures}

    total_price_usd: float = 0.0
    for c in deck.cards:
        if getattr(c, "price_usd", -1.0) >= 0:
            total_price_usd += c.price_usd

    return {
        "total_cards": total_cards,
        "non_land": non_land,
        "lands": land_count,
        "color_distribution": color_distribution,
        "mana_value_distribution": mana_value_distribution,
        "total_price_usd": round(total_price_usd, 2),
    }


def _deck_to_response(deck: Deck) -> dict:
    """Build API response with deck dict and stats.

    The deck dict includes the computed type lists (creature, instant, sorcery, artifact,
    enchantment, planeswalker, land), maybe/sideboard name lists, and maybe_by_type /
    sideboard_by_type for section visibility (counts per type in maybe/sideboard).
    """
    out: dict = deck.to_dict()
    for key in TYPE_KEYS:
        out[key] = list(getattr(deck, key, None) or [])
    card_db = CardDB.inst()
    out["maybe_names"] = [card_db.card_display_name(c) for c in deck.maybe]
    out["sideboard_names"] = [card_db.card_display_name(c) for c in deck.sideboard]
    # Per-type lists for maybe/sideboard so client can show only sections that have cards
    maybe_by_type: dict[str, list[str]] = {k: [] for k in TYPE_KEYS}
    for c in deck.maybe:
        key = _type_line_to_key(getattr(c, "type_line", "") or "")
        if key in maybe_by_type:
            maybe_by_type[key].append(card_db.card_display_name(c))
    sideboard_by_type: dict[str, list[str]] = {k: [] for k in TYPE_KEYS}
    for c in deck.sideboard:
        key = _type_line_to_key(getattr(c, "type_line", "") or "")
        if key in sideboard_by_type:
            sideboard_by_type[key].append(card_db.card_display_name(c))
    out["maybe_by_type"] = maybe_by_type
    out["sideboard_by_type"] = sideboard_by_type
    seen_names: set[str] = set()
    out["prices"] = {}
    for c in deck.cards:
        display_name: str = card_db.card_display_name(c)
        if display_name not in seen_names:
            seen_names.add(display_name)
            out["prices"][display_name] = c.price_usd if c.price_usd >= 0 else None
    for c in deck.maybe:
        display_name = card_db.card_display_name(c)
        if display_name not in seen_names:
            seen_names.add(display_name)
            price = getattr(c, "price_usd", -1.0)
            out["prices"][display_name] = price if price >= 0 else None
    for c in deck.sideboard:
        display_name = card_db.card_display_name(c)
        if display_name not in seen_names:
            seen_names.add(display_name)
            price = getattr(c, "price_usd", -1.0)
            out["prices"][display_name] = price if price >= 0 else None
    resp: dict = {"deck": out, "stats": _compute_deck_stats(deck)}
    return resp


def _sanitize_filename(name: str) -> str:
    """Replace unsafe characters for use in filenames."""
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "deck"


def _type_line_to_key(type_line: str) -> str:
    """Map MTG type_line to one of TYPE_KEYS. Priority: land > creature > instant > sorcery > artifact > enchantment > planeswalker > battle."""
    if not type_line or not isinstance(type_line, str):
        return "sorcery"
    t: str = type_line.lower()
    if "land" in t:
        return "land"
    if "creature" in t:
        return "creature"
    if "instant" in t:
        return "instant"
    if "sorcery" in t:
        return "sorcery"
    if "artifact" in t:
        return "artifact"
    if "enchantment" in t:
        return "enchantment"
    if "planeswalker" in t:
        return "planeswalker"
    if "battle" in t:
        return "battle"
    return "sorcery"


def _resolve_type_key(card_name: str) -> tuple[str, str]:
    """Look up card_name in local data; return (canonical_name, type_key). Raises ValueError if not found."""
    return _resolve_name_to_type_key(card_name)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def serve_editor() -> FileResponse:
    """Serve the deck editor HTML page."""
    LOGGER.info("serve_editor: GET /")
    static_dir: Path = Path(__file__).resolve().parent / "static"
    main_path: Path = static_dir / "main.html"
    if not main_path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", main_path)
        raise FileNotFoundError(f"Static file not found: {main_path}")
    return FileResponse(
        main_path,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/search")
async def serve_search() -> FileResponse:
    """Serve the advanced search popup HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    search_path: Path = static_dir / "search.html"
    if not search_path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", search_path)
        raise FileNotFoundError(f"Static file not found: {search_path}")
    return FileResponse(
        search_path,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/export-modal")
async def serve_export_modal() -> FileResponse:
    """Serve the export format modal iframe page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    path: Path = static_dir / "export-modal.html"
    if not path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", path)
        raise FileNotFoundError(f"Static file not found: {path}")
    return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"})


@app.get("/synergy-checker")
async def serve_synergy_checker() -> FileResponse:
    """Serve the synergy checker popup HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    path: Path = static_dir / "synergy-checker.html"
    if not path.is_file():
        LOGGER.error("Deck editor static file not found: %s", path)
        raise FileNotFoundError(f"Static file not found: {path}")
    return FileResponse(
        path,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/import-modal")
async def serve_import_modal() -> FileResponse:
    """Serve the import deck modal iframe page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    path: Path = static_dir / "import-modal.html"
    if not path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", path)
        raise FileNotFoundError(f"Static file not found: {path}")
    return FileResponse(path, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"})


@app.post("/api/search")
async def search_cards_api(body: dict) -> dict:
    """Advanced search: structural filters plus optional semantic_query / search_type (RAG-ranked within filters). Returns JSON list of card dicts."""
    LOGGER.debug("search_cards_api: POST /api/search keys=%s", list(body.keys()))
    name: str = body["name"] if "name" in body and isinstance(body["name"], str) else ""
    oracle_text: str | list[str] = ""
    if "oracle_text" in body:
        if isinstance(body["oracle_text"], list):
            oracle_text = [s for s in body["oracle_text"] if isinstance(s, str) and s.strip()]
        elif isinstance(body["oracle_text"], str) and body["oracle_text"].strip():
            oracle_text = body["oracle_text"].strip()
    type_line: str = ""
    if "type" in body and isinstance(body["type"], str) and (body["type"] or "").strip():
        type_line = (body["type"] or "").strip()
    elif "type_line" in body and isinstance(body["type_line"], str):
        type_line = body["type_line"] or ""
    colors: str = body["colors"] if "colors" in body and isinstance(body["colors"], str) else ""
    color_identity: str = (
        body["color_identity"] if "color_identity" in body and isinstance(body["color_identity"], str) else ""
    )
    color_identity_colorless: bool = body.get("color_identity_colorless") is True
    colorless_only: bool = body.get("colorless_only") is True
    mana_value: float = float(body["mana_value"]) if "mana_value" in body and body["mana_value"] is not None else -1.0
    mana_value_min: float = (
        float(body["mana_value_min"]) if "mana_value_min" in body and body["mana_value_min"] is not None else -1.0
    )
    mana_value_max: float = (
        float(body["mana_value_max"]) if "mana_value_max" in body and body["mana_value_max"] is not None else -1.0
    )
    price_usd_min: float = (
        float(body["price_usd_min"]) if "price_usd_min" in body and body["price_usd_min"] is not None else -1.0
    )
    price_usd_max: float = (
        float(body["price_usd_max"]) if "price_usd_max" in body and body["price_usd_max"] is not None else -1.0
    )
    power: str = body["power"] if "power" in body and isinstance(body["power"], str) else ""
    toughness: str = body["toughness"] if "toughness" in body and isinstance(body["toughness"], str) else ""
    keywords: str = body["keywords"] if "keywords" in body and isinstance(body["keywords"], str) else ""
    subtype: str = body["subtype"] if "subtype" in body and isinstance(body["subtype"], str) else ""
    supertype: str = body["supertype"] if "supertype" in body and isinstance(body["supertype"], str) else ""
    format_legal: str = (
        body["format_legal"] if "format_legal" in body and isinstance(body["format_legal"], str) else ""
    )
    n_results: int = int(body["n_results"]) if "n_results" in body and body["n_results"] is not None else 20
    n_results = max(1, min(100, n_results))
    offset: int = int(body["offset"]) if "offset" in body and body["offset"] is not None else 0
    offset = max(0, offset)

    semantic_query: str = ""
    if "semantic_query" in body and isinstance(body["semantic_query"], str):
        semantic_query = body["semantic_query"].strip()
    search_type_raw: str = (
        body["search_type"] if "search_type" in body and isinstance(body["search_type"], str) else "general"
    )
    search_type: str = search_type_raw.strip().lower()
    if search_type not in ("general", "trigger", "effect"):
        raise HTTPException(status_code=400, detail="search_type must be general, trigger, or effect")

    if semantic_query and not CardDB.inst().is_rag_ready():
        raise HTTPException(
            status_code=503,
            detail="Semantic search requires RAG; the embedding index is not ready yet.",
        )

    try:
        results = CardDB.inst().filter_cards_list(
            name=name,
            oracle_text=oracle_text,
            type_line=type_line,
            colors=colors,
            color_identity=color_identity,
            color_identity_colorless=color_identity_colorless,
            colorless_only=colorless_only,
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
            offset=offset,
            semantic_query=semantic_query,
            search_type=search_type,
        )
    except ValueError as e:
        msg: str = str(e)
        if "not ready" in msg.lower() and "rag" in msg.lower():
            raise HTTPException(status_code=503, detail=msg) from e
        raise HTTPException(status_code=400, detail=msg) from e
    LOGGER.info("search_cards_api: returning %d results (name=%r semantic=%r)", len(results), name, semantic_query[:40] if semantic_query else "")
    return {"results": [c.to_dict() for c in results]}


@app.get("/api/rag_ready")
async def rag_ready() -> dict:
    """Return whether RAG (embedding model + ChromaDB) is loaded and semantic search is available."""
    ready: bool = CardDB.inst().is_rag_ready()
    LOGGER.debug("rag_ready: %s", ready)
    return {"ready": ready}


@app.get("/api/autocomplete")
async def autocomplete(
    q: str = Query("", min_length=0),
    colors: str = Query(""),
    deck_format: str = Query("", alias="format"),
    colorless_only: bool = Query(False),
) -> dict:
    """Autocomplete card names by substring; optionally filter by color identity and format legality. Returns { data: [names] }."""
    LOGGER.debug("autocomplete: q=%r colors=%r format=%r", q, colors, deck_format)
    q_clean: str = (q or "").strip()
    if len(q_clean) < 2:
        return {"data": []}
    color_identity_arg: str = ""
    color_identity_colorless_arg: bool = False
    if colorless_only:
        color_identity_colorless_arg = True
    elif colors.strip():
        color_identity_arg = colors.strip()
    try:
        results = CardDB.inst().filter_cards_list(
            name=q_clean,
            color_identity=color_identity_arg,
            color_identity_colorless=color_identity_colorless_arg,
            format_legal=deck_format.strip() if deck_format else "",
            n_results=15,
            offset=0,
        )
    except ValueError as e:
        LOGGER.warning("autocomplete: filter_cards_list failed: %s", e)
        return {"data": []}
    return {"data": [c.name for c in results]}


def _names_from_cards_array(cards: list) -> list[str]:
    """Extract card names from a 'cards' array (items may be strings or dicts with 'name')."""
    names: list[str] = []
    for item in cards:
        if isinstance(item, str) and (item or "").strip():
            names.append((item or "").strip())
        elif isinstance(item, dict) and "name" in item and isinstance(item["name"], str):
            n = (item["name"] or "").strip()
            if n:
                names.append(n)
    return names


@app.post("/api/deck")
async def load_deck(body: dict) -> dict:
    """Load a deck from JSON. Replaces current deck."""
    global _current_deck
    LOGGER.info("load_deck: POST /api/deck")
    if "deck" in body:
        body = body["deck"]
    try:
        _current_deck = Deck.from_dict(body)
    except (KeyError, TypeError) as e:
        LOGGER.error( "load_deck: invalid deck payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid deck payload: {e}") from e
    LOGGER.info(
        "load_deck: deck loaded name=%r format=%r commander=%r cards=%d",
        _current_deck.name,
        _current_deck.format,
        _current_deck.commander,
        len(_current_deck.cards),
    )
    threading.Thread(target=_prefetch_deck_image_redirects, args=(_current_deck,), daemon=True).start()
    _notify_deck_updated()
    return _deck_to_response(_current_deck)


@app.get("/api/events")
async def sse_events() -> StreamingResponse:
    """SSE stream: sends deck_updated when the deck changes. Sends current state on connect."""
    LOGGER.debug("sse_events: new SSE client connecting (total=%d)", len(_sse_clients) + 1)
    queue: asyncio.Queue[str] = asyncio.Queue()
    _sse_clients.add(queue)

    async def stream() -> None:
        try:
            # Send initial state so client gets deck on connect
            initial: dict = _deck_to_response(_current_deck)
            queue.put_nowait(f"event: deck_updated\ndata: {json.dumps(initial)}\n\n")
            while True:
                msg: str = await queue.get()
                yield msg
        except asyncio.CancelledError:
            LOGGER.debug("SSE stream cancelled (client disconnected)")
            raise
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _parse_add_card_names(body: dict) -> list[str]:
    """Extract list of card names from body: 'name' (single) or 'names' (list). Raises HTTPException if invalid."""
    if "names" in body and isinstance(body["names"], list):
        names = [n for n in body["names"] if isinstance(n, str) and (n or "").strip()]
        if not names:
            raise HTTPException(status_code=400, detail="'names' must be a non-empty list of card name strings")
        return names
    if "name" in body and isinstance(body["name"], str):
        n = (body["name"] or "").strip()
        if not n:
            raise HTTPException(status_code=400, detail="'name' must be a non-empty card name string")
        return [n]
    raise HTTPException(status_code=400, detail="Provide 'name' (string) or 'names' (list of strings)")


@app.post("/api/add_card")
async def add_card(body: dict) -> dict:
    """Add one or more cards by name to a board (default: main deck). Broadcasts deck_updated via SSE."""
    global _current_deck
    names_to_add: list[str] = _parse_add_card_names(body)
    LOGGER.debug("add_card: names=%s board=%s", names_to_add, body.get("board", "main") if isinstance(body, dict) else "?")
    board: str = body["board"] if "board" in body and isinstance(body["board"], str) else "main"
    if board not in _VALID_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid board: {board!r}. Must be {_valid_boards_detail()}.")
    cards_to_append: list[Card] = []
    not_found: list[str] = []
    for name in names_to_add:
        try:
            cards_to_append.extend(_cards_from_names([name]))
        except ValueError:
            not_found.append(name)
    if not cards_to_append:
        raise HTTPException(status_code=404, detail=f"Card(s) not found: {', '.join(not_found)}")
    if board == "commander":
        if len(names_to_add) != 1 or len(cards_to_append) != 1:
            raise HTTPException(
                status_code=400,
                detail="board 'commander' accepts exactly one card name per request",
            )
        _assign_commander_card(_current_deck, cards_to_append[0])
    else:
        target_list: list[Card] = _get_board_list(_current_deck, board)
        for card in cards_to_append:
            target_list.append(card)
    if not_found:
        LOGGER.warning(
            "add_card: partially added=%s requested=%s board=%s not_found=%s",
            len(cards_to_append),
            len(names_to_add),
            board,
            not_found,
        )
    _recompute_and_set_colors(_current_deck)
    _notify_deck_updated()
    LOGGER.info("add_card: added %d cards to %s", len(cards_to_append), board)
    response: dict = _deck_to_response(_current_deck)
    if not_found:
        response["not_found"] = not_found
    return response


@app.post("/api/remove_card")
async def remove_card(body: dict) -> dict:
    """Remove copies of one or more cards from a board (default: main). Broadcasts deck_updated via SSE.

    Body: {"names": [...], "board": "main"|"maybe"|"sideboard"|"commander", "count": 1}
    """
    global _current_deck
    names_to_remove: list[str] = _parse_add_card_names(body)
    LOGGER.debug("remove_card: names=%s board=%s", names_to_remove, body.get("board", "main") if isinstance(body, dict) else "?")
    board: str = body["board"] if "board" in body and isinstance(body["board"], str) else "main"
    if board not in _VALID_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid board: {board!r}. Must be {_valid_boards_detail()}.")
    count: int = int(body["count"]) if "count" in body and body["count"] is not None else 1
    if count < 1:
        raise HTTPException(status_code=400, detail="count must be >= 1")
    if board == "commander":
        if count != 1:
            raise HTTPException(status_code=400, detail="board 'commander' only supports count=1")
        if len(names_to_remove) != 1:
            raise HTTPException(status_code=400, detail="board 'commander' accepts exactly one card name per request")
        cur_cmd: str = (_current_deck.commander or "").strip()
        if not cur_cmd:
            raise HTTPException(status_code=404, detail="No commander set")
        if not commander_string_matches_request(cur_cmd, names_to_remove[0]):
            raise HTTPException(
                status_code=404,
                detail=f"Card not found in commander slot: {names_to_remove[0]!r}",
            )
        _current_deck.commander = ""
    else:
        target_list: list[Card] = _get_board_list(_current_deck, board)
        idx_asc, not_found = collect_matching_indices_asc(target_list, names_to_remove, count)
        if idx_asc is None:
            assert not_found
            raise HTTPException(status_code=404, detail=f"Card(s) not found in {board} board: {', '.join(not_found)}")
        remove_cards_at_indices(target_list, idx_asc)
    _recompute_and_set_colors(_current_deck)
    _notify_deck_updated()
    LOGGER.info("remove_card: removed %s from %s", names_to_remove, board)
    return _deck_to_response(_current_deck)


@app.post("/api/move_card")
async def move_card(body: dict) -> dict:
    """Move copies of one or more cards from one board to another. Broadcasts deck_updated via SSE.

    Body: {"names": [...], "from_board": "main"|"maybe"|"sideboard"|"commander", "to_board": same, "count": 1}
    """
    global _current_deck
    names_to_move: list[str] = _parse_add_card_names(body)
    LOGGER.debug("move_card: names=%s from=%s to=%s", names_to_move, body.get("from_board"), body.get("to_board"))
    if "from_board" not in body or not isinstance(body["from_board"], str):
        raise HTTPException(status_code=400, detail=f"'from_board' is required (string: {_valid_boards_detail()})")
    if "to_board" not in body or not isinstance(body["to_board"], str):
        raise HTTPException(status_code=400, detail=f"'to_board' is required (string: {_valid_boards_detail()})")
    from_board: str = body["from_board"]
    to_board: str = body["to_board"]
    if from_board not in _VALID_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid from_board: {from_board!r}. Must be {_valid_boards_detail()}.")
    if to_board not in _VALID_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid to_board: {to_board!r}. Must be {_valid_boards_detail()}.")
    if from_board == to_board:
        raise HTTPException(status_code=400, detail=f"from_board and to_board must differ (both are {from_board!r})")
    count: int = int(body["count"]) if "count" in body and body["count"] is not None else 1
    if count < 1:
        raise HTTPException(status_code=400, detail="count must be >= 1")

    try:
        _move_cards_between_boards(_current_deck, names_to_move, from_board, to_board, count)
    except DeckEditorError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    _recompute_and_set_colors(_current_deck)
    _notify_deck_updated()
    LOGGER.info("move_card: moved %s from %s to %s", names_to_move, from_board, to_board)
    return _deck_to_response(_current_deck)


@app.get("/api/deck")
async def get_deck() -> dict:
    """Return current deck and removed list (empty deck if none loaded yet)."""
    LOGGER.debug("get_deck: GET /api/deck cards=%d", len(_current_deck.cards))
    return _deck_to_response(_current_deck)


@app.get("/api/deck/meta")
async def get_deck_meta() -> dict:
    """Return only deck metadata (name, colors, description, format, commander, colorless_only) without card lists."""
    return {
        "name": _current_deck.name,
        "colors": list(_current_deck.colors),
        "description": _current_deck.description,
        "format": _current_deck.format,
        "commander": _current_deck.commander,
        "colorless_only": _current_deck.colorless_only,
    }


@app.get("/api/card_type")
async def get_card_type(name: str = Query(..., min_length=1)) -> dict:
    """Return the type key for a card name (e.g. creature, instant, land)."""
    LOGGER.debug("get_card_type: name=%r", name)
    try:
        _, type_key = _resolve_type_key(name)
        return {"type_key": type_key}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/card_mechanics")
async def get_card_mechanics(
    name: str = Query(..., min_length=1),
    type: str = Query(..., pattern="^(triggers|effects)$"),
) -> dict:
    """Return extracted triggers or effects for a card by name. type must be 'triggers' or 'effects'."""
    LOGGER.debug("get_card_mechanics: name=%r type=%s", name, type)
    try:
        result: str = CardDB.inst().get_card_mechanics(name=name, extract_type=type)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"card": name, "type": type, "result": result}


@app.get("/api/synergy")
async def get_synergy(
    name1: str = Query(..., min_length=1),
    name2: str = Query(..., min_length=1),
) -> dict:
    """Return synergy score between two cards by name. Higher score = better synergy. Requires RAG to be loaded."""
    LOGGER.debug("get_synergy: name1=%r name2=%r", name1, name2)
    if not CardDB.inst().is_rag_ready():
        raise HTTPException(
            status_code=503,
            detail="Synergy check requires RAG (embedding model) to be loaded. Please try again in a moment.",
        )
    try:
        score: float = CardDB.inst().get_synergy_score(name_a=name1, name_b=name2)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"card_a": name1, "card_b": name2, "synergy_score": round(score, 4)}


@app.get("/api/card_image")
async def card_image(
    name: str = Query(..., min_length=1),
    face: int = Query(0, ge=0),
    size: str = Query("normal"),
) -> RedirectResponse:
    """Redirect to a card image URL (Scryfall CDN).

    The frontend uses this for <img src=...> so we can apply consistent request
    headers to Scryfall and cache the resulting CDN URL.
    """
    req_name: str = name.strip()
    if not req_name:
        LOGGER.error("card_image: empty name")
        raise HTTPException(status_code=400, detail="name must not be empty")
    if size not in ("normal", "large"):
        LOGGER.error("card_image: invalid size=%r", size)
        raise HTTPException(status_code=400, detail="size must be 'normal' or 'large'")

    try:
        faces: list[Card] = CardDB.inst().resolve_faces(req_name)
    except ValueError as e:
        LOGGER.error("card_image: unknown card name: %r", req_name)
        raise HTTPException(status_code=404, detail=str(e)) from e

    if face >= len(faces):
        LOGGER.error("card_image: face index out of range name=%r face=%d faces=%d", req_name, face, len(faces))
        raise HTTPException(status_code=400, detail=f"face index out of range (faces={len(faces)})")

    face_name: str = faces[face].name
    cache_key: tuple[str, int, str] = (face_name, face, size)
    if cache_key in _CARD_IMAGE_CACHE:
        return RedirectResponse(url=_CARD_IMAGE_CACHE[cache_key], status_code=302)

    scry_url: str = (
        "https://api.scryfall.com/cards/named?exact="
        + urllib.parse.quote(face_name, safe="")
        + "&format=image&version="
        + size
    )
    try:
        import requests

        r = None
        for attempt in range(4):
            r = requests.get(
                scry_url,
                timeout=15,
                allow_redirects=True,
                headers={"Accept": "*/*", "User-Agent": "MTG-MCP/1.0"},
                stream=True,
            )
            if r.status_code == 429:
                LOGGER.error("card_image: rate limited (429) name=%r attempt=%d", face_name, attempt + 1)
                _sleep_for_retry_after(r.headers, attempt)
                r.close()
                continue
            break
        assert r is not None, "requests.get must return a response"
        r.close()
    except Exception as e:
        LOGGER.error("card_image: request failed name=%r url=%s err=%s", face_name, scry_url, e)
        raise HTTPException(status_code=502, detail="Failed to fetch card image") from e

    if not r.ok:
        LOGGER.error("card_image: scryfall error status=%s name=%r url=%s", r.status_code, face_name, scry_url)
        raise HTTPException(status_code=502, detail=f"Scryfall image request failed ({r.status_code})")

    final_url: str = r.url
    if not isinstance(final_url, str) or not final_url:
        LOGGER.error("card_image: missing final url name=%r url=%s", face_name, scry_url)
        raise HTTPException(status_code=502, detail="Scryfall returned empty image URL")
    if "cards.scryfall.io/" not in final_url:
        LOGGER.error("card_image: unexpected redirect target name=%r final_url=%s", face_name, final_url)
        raise HTTPException(status_code=502, detail="Unexpected image host from Scryfall")

    _CARD_IMAGE_CACHE[cache_key] = final_url
    return RedirectResponse(url=final_url, status_code=302)


def _run_price_update_then_notify() -> None:
    """Background: run full price update, reload CardDB prices, broadcast deck_updated."""
    LOGGER.info("_run_price_update_then_notify: starting price update")
    try:
        update_all_prices()
        CardDB.inst().reload_prices()
        _notify_deck_updated()
        LOGGER.info("_run_price_update_then_notify: price update completed")
    except Exception as e:
        LOGGER.error("Price update failed: %s", e)


@app.post("/api/refresh_prices")
async def refresh_prices() -> dict:
    """Start a background update of all card prices from Scryfall. Returns immediately. When done, deck_updated is broadcast via SSE."""
    LOGGER.info("refresh_prices: starting background price refresh")
    thread: threading.Thread = threading.Thread(target=_run_price_update_then_notify, daemon=True)
    thread.start()
    return {"status": "started"}


@app.get("/api/export/formats")
async def get_export_formats() -> dict:
    """Return available export format keys and display names for the format picker."""
    return {"formats": Deck.EXPORT_FORMATS}


@app.get("/api/export")
async def export_deck(format: str) -> dict:
    """Export current deck in the given format. Returns {"text": "..."}. Use format from /api/export/formats."""
    LOGGER.debug("export_deck: format=%r", format)
    fmt: str = (format or "").strip().lower()
    if fmt not in Deck.EXPORT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {format!r}; use one of: {list(Deck.EXPORT_FORMATS.keys())}",
        )
    try:
        text: str = _current_deck.export(fmt)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"text": text}


@app.post("/api/import")
async def import_deck(request: Request) -> dict:
    """Import a deck from pasted text. Body: {"text": str, "format": str}. Replaces current deck."""
    global _current_deck
    LOGGER.debug("import_deck: POST /api/import received")
    try:
        body: dict = await request.json()
        LOGGER.debug("import_deck: body keys: %s", list(body.keys()) if isinstance(body, dict) else type(body))
    except Exception as e:
        LOGGER.error("import_deck: request.json() failed: %s %s", type(e).__name__, e)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None
    if not isinstance(body, dict) or "text" not in body or "format" not in body:
        LOGGER.warning("import_deck: body missing text or format; body type=%s keys=%s", type(body), list(body.keys()) if isinstance(body, dict) else "n/a")
        raise HTTPException(status_code=400, detail="Body must include 'text' and 'format'")
    text: str = body["text"] if isinstance(body["text"], str) else ""
    fmt: str = (body["format"] or "").strip().lower()
    LOGGER.debug("import_deck: format=%r text_len=%d text_preview=%r", fmt, len(text), (text[:80] + "..." if len(text) > 80 else text))
    if fmt not in Deck.EXPORT_FORMATS:
        LOGGER.warning("import_deck: unsupported format: %r allowed: %s", fmt, list(Deck.EXPORT_FORMATS.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {body['format']!r}; use one of: {list(Deck.EXPORT_FORMATS.keys())}",
        )
    try:
        LOGGER.debug("import_deck: calling Deck.from_export_text(...)")
        deck: Deck = Deck.from_export_text(text, fmt)
        LOGGER.info("import_deck: from_export_text ok; deck.cards len=%d", len(deck.cards))
    except ValueError as e:
        LOGGER.warning("import_deck: from_export_text ValueError: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        LOGGER.error("import_deck: from_export_text unexpected: %s %s", type(e).__name__, e)
        raise
    _current_deck = deck
    card_colors = _compute_deck_card_colors(_current_deck)
    existing = set(_current_deck.colors)
    _current_deck.colors = list(existing | card_colors)
    threading.Thread(target=_prefetch_deck_image_redirects, args=(_current_deck,), daemon=True).start()
    _notify_deck_updated()
    resp = _deck_to_response(_current_deck)
    LOGGER.debug("import_deck: returning response; deck keys in out: %s", list(resp.get("deck", {}).keys())[:10])
    return resp


@app.put("/api/deck")
async def update_deck(body: dict) -> dict:
    """Update deck from client state."""
    global _current_deck
    LOGGER.info("update_deck: PUT /api/deck")

    name: str = _current_deck.name
    if "name" in body and isinstance(body["name"], str):
        name = body["name"]
    colors: list[str] = list(_current_deck.colors)
    if "colors" in body and isinstance(body["colors"], list):
        colors = body["colors"]
    description: str = _current_deck.description
    if "description" in body and isinstance(body["description"], str):
        description = body["description"]
    deck_format: str = _current_deck.format
    if "format" in body and isinstance(body["format"], str):
        deck_format = body["format"]
    commander_name: str = _current_deck.commander
    if "commander" in body:
        if not isinstance(body["commander"], str):
            raise HTTPException(status_code=400, detail="'commander' must be a string")
        commander_name = body["commander"]
    colorless_only: bool = _current_deck.colorless_only
    if "colorless_only" in body and isinstance(body["colorless_only"], bool):
        colorless_only = body["colorless_only"]

    all_names: list[str] = []
    for key in TYPE_KEYS:
        lst = body[key] if key in body and isinstance(body[key], list) else []
        all_names.extend(lst)
    # Legacy: accept old 4-type keys if new keys not present
    if not all_names:
        for leg in ("creatures", "non_creatures", "spells", "lands"):
            lst = body[leg] if leg in body and isinstance(body[leg], list) else []
            all_names.extend(lst)
    try:
        cards_list: list[Card] = _cards_from_names(all_names)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    maybe_names: list[str] = body["maybe"] if "maybe" in body and isinstance(body["maybe"], list) else []
    sideboard_names: list[str] = body["sideboard"] if "sideboard" in body and isinstance(body["sideboard"], list) else []
    try:
        maybe_cards: list[Card] = _cards_from_names(maybe_names)
        sideboard_cards: list[Card] = _cards_from_names(sideboard_names)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    _current_deck = Deck(
        name=name,
        colors=colors,
        description=description,
        format=deck_format,
        commander=commander_name,
        colorless_only=colorless_only,
        cards=cards_list,
        maybe=maybe_cards,
        sideboard=sideboard_cards,
    )
    LOGGER.info(
        "update_deck: applied name=%r format=%r commander=%r main=%d maybe=%d sideboard=%d",
        _current_deck.name,
        _current_deck.format,
        _current_deck.commander,
        len(_current_deck.cards),
        len(_current_deck.maybe),
        len(_current_deck.sideboard),
    )
    _notify_deck_updated()
    return _deck_to_response(_current_deck)


@app.post("/api/save")
async def save_deck() -> dict:
    """Write current deck to a JSON file (excluding removed). Return path. Uses Deck.save()."""
    safe_name: str = _sanitize_filename(_current_deck.name)
    timestamp: str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename: str = f"{safe_name}_{timestamp}.json"
    out_path: Path = DECK_EDITOR_SAVE_DIR / filename
    DECK_EDITOR_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    _current_deck.save("json", out_path)
    LOGGER.info("Deck saved to %s", out_path)
    return {"saved_to": str(out_path)}


_FRONTEND_LOG_LEVELS: dict[str, int] = {"WARN": logging.WARNING, "ERROR": logging.ERROR}


@app.post("/api/frontend_log")
async def frontend_log(body: dict) -> dict:
    """Receive a frontend log entry and write it to the server log file."""
    level: str = body["level"] if "level" in body and isinstance(body["level"], str) else "ERROR"
    module: str = body["module"] if "module" in body and isinstance(body["module"], str) else "unknown"
    message: str = body["message"] if "message" in body and isinstance(body["message"], str) else ""
    py_level: int = _FRONTEND_LOG_LEVELS.get(level, logging.ERROR)
    LOGGER.log(py_level, "[frontend:%s] %s", module, message)
    return {"ok": True}


_BUG_REPORT_DIR: Path = REPO_ROOT / ".ai" / "BR"


@app.post("/api/bug_report")
async def file_bug_report(body: dict) -> dict:
    """File a bug report: save user description and a snapshot of the latest logs to .ai/BR/."""
    if "description" not in body or not isinstance(body["description"], str):
        LOGGER.error("file_bug_report: missing or invalid 'description' in body")
        raise HTTPException(status_code=400, detail="'description' (string) is required")
    description: str = body["description"].strip()
    if not description:
        LOGGER.error("file_bug_report: empty description")
        raise HTTPException(status_code=400, detail="'description' must not be empty")

    now: datetime = datetime.now()
    ts: str = now.strftime("%Y-%m-%d_%H-%M-%S")
    br_dir: Path = _BUG_REPORT_DIR / f"BR_{ts}"
    br_dir.mkdir(parents=True, exist_ok=True)

    deck_editor_logs: Path = REPO_ROOT / "logs" / "deck_editor"
    attached: list[str] = []
    if deck_editor_logs.is_dir():
        log_files: list[Path] = sorted(deck_editor_logs.glob("*.log"), key=lambda p: p.stat().st_mtime)
        if log_files:
            latest: Path = log_files[-1]
            shutil.copy2(latest, br_dir / latest.name)
            attached.append(latest.name)

    report_lines: list[str] = [
        "# Bug Report",
        "",
        f"**Date:** {now.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Description",
        "",
        description,
        "",
    ]
    if attached:
        report_lines.extend(["## Attached Logs", ""])
        for name in attached:
            report_lines.append(f"- `{name}`")
        report_lines.append("")

    (br_dir / "bug_report.md").write_text("\n".join(report_lines), encoding="utf-8")

    relative: str = str(br_dir.relative_to(REPO_ROOT))
    LOGGER.info("Bug report filed: %s (logs: %s)", relative, attached)
    return {"path": relative, "logs_attached": attached}


# Static file mounts for JS and CSS (must be after specific routes)
_deck_editor_root: Path = Path(__file__).resolve().parent
app.mount("/js", StaticFiles(directory=str(_deck_editor_root / "js")), name="js")
app.mount("/styles", StaticFiles(directory=str(_deck_editor_root / "styles")), name="styles")
