"""FastAPI application for the deck editor: serves HTML and deck API."""

import asyncio
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.lib.cardDB import CardDB
from src.lib.config import DECK_EDITOR_SAVE_DIR
from src.lib.prices import prices_age_hours, update_all_prices
from src.obj.card import Card
from src.obj.deck import Deck, _cards_from_names, _normalize_cards_arg
from src.utils.logger import LOGGER

app = FastAPI(title="MTG Deck Editor")

# ---------------------------------------------------------------------------
# In-memory state (always a deck; starts empty; POST replaces it)
# ---------------------------------------------------------------------------
_current_deck: Deck = Deck()

# ---------------------------------------------------------------------------
# SSE event bus
# ---------------------------------------------------------------------------
_sse_clients: set[asyncio.Queue[str]] = set()


def _broadcast(event_type: str, data_dict: dict) -> None:
    """Push an SSE-formatted message to all connected clients."""
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
    if age is None or age > 24:
        thread: threading.Thread = threading.Thread(target=_run_price_update_then_notify, daemon=True)
        thread.start()


def _startup_load_rag() -> None:
    """Load RAG (embedding model + ChromaDB) in background so semantic search is ready without blocking startup."""
    CardDB.inst().load_rag_sync()


@app.on_event("startup")
def _startup_rag_async() -> None:
    """Start RAG loading in a background thread at server init; heavy deps are not imported in the main process until then."""
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
    data: list = CardDB.inst().get_card_data()
    name_lower_to_card: dict[str, Any] = {}
    for c in data:
        k: str = c.name.lower()
        existing = name_lower_to_card.get(k)
        if existing is None or (
            (c.mana_cost or "").strip() and not (getattr(existing, "mana_cost", None) or "").strip()
        ):
            name_lower_to_card[k] = c
    for name in all_names:
        key: str = (name or "").strip().lower()
        card = name_lower_to_card.get(key)
        if card is None and " // " in name:
            key = name.split(" // ", 1)[0].strip().lower()
            card = name_lower_to_card.get(key)
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
        key = (name or "").strip().lower()
        card = name_lower_to_card.get(key)
        if card is None and " // " in name:
            key = name.split(" // ", 1)[0].strip().lower()
            card = name_lower_to_card.get(key)
        if card is None:
            continue
        mv: float = getattr(card, "mana_value", -1.0) if hasattr(card, "mana_value") else -1.0
        if mv < 0:
            mv = 0.0
        idx = min(7, int(mv))
        mv_creatures[idx] += 1
    for name in non_creature_non_land:
        key = (name or "").strip().lower()
        card = name_lower_to_card.get(key)
        if card is None and " // " in name:
            key = name.split(" // ", 1)[0].strip().lower()
            card = name_lower_to_card.get(key)
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
    out["maybe_names"] = [c.name for c in deck.maybe]
    out["sideboard_names"] = [c.name for c in deck.sideboard]
    # Per-type lists for maybe/sideboard so client can show only sections that have cards
    maybe_by_type: dict[str, list[str]] = {k: [] for k in TYPE_KEYS}
    for c in deck.maybe:
        key = _type_line_to_key(getattr(c, "type_line", "") or "")
        if key in maybe_by_type:
            maybe_by_type[key].append(c.name)
    sideboard_by_type: dict[str, list[str]] = {k: [] for k in TYPE_KEYS}
    for c in deck.sideboard:
        key = _type_line_to_key(getattr(c, "type_line", "") or "")
        if key in sideboard_by_type:
            sideboard_by_type[key].append(c.name)
    out["maybe_by_type"] = maybe_by_type
    out["sideboard_by_type"] = sideboard_by_type
    seen_names: set[str] = set()
    out["prices"] = {}
    for c in deck.cards:
        if c.name not in seen_names:
            seen_names.add(c.name)
            out["prices"][c.name] = c.price_usd if c.price_usd >= 0 else None
    for c in deck.maybe:
        if c.name not in seen_names:
            seen_names.add(c.name)
            price = getattr(c, "price_usd", -1.0)
            out["prices"][c.name] = price if price >= 0 else None
    for c in deck.sideboard:
        if c.name not in seen_names:
            seen_names.add(c.name)
            price = getattr(c, "price_usd", -1.0)
            out["prices"][c.name] = price if price >= 0 else None
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
    name_clean: str = (card_name or "").strip()
    if not name_clean:
        raise ValueError("add_card: card name is empty")
    data: list = CardDB.inst().get_card_data()
    name_lower: str = name_clean.lower()
    for c in data:
        if c.name.lower() == name_lower:
            key: str = _type_line_to_key(c.type_line)
            return (c.name, key)
    LOGGER.error( "add_card: card not found: %s", name_clean)
    raise ValueError(f"add_card: card not found: {name_clean!r}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def serve_editor() -> FileResponse:
    """Serve the deck editor HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    main_path: Path = static_dir / "main.html"
    if not main_path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", main_path)
        raise FileNotFoundError(f"Static file not found: {main_path}")
    return FileResponse(main_path)


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


@app.get("/semantic-search")
async def serve_semantic_search() -> FileResponse:
    """Serve the semantic search popup HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    path: Path = static_dir / "semantic-search.html"
    if not path.is_file():
        LOGGER.error( "Deck editor static file not found: %s", path)
        raise FileNotFoundError(f"Static file not found: {path}")
    return FileResponse(
        path,
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
    """Advanced search: same filters as filter_cards. Returns JSON list of card dicts."""
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"results": [c.to_dict() for c in results]}


@app.get("/api/rag_ready")
async def rag_ready() -> dict:
    """Return whether RAG (embedding model + ChromaDB) is loaded and semantic search is available."""
    return {"ready": CardDB.inst().is_rag_ready()}


@app.post("/api/semantic_search")
async def semantic_search_api(body: dict) -> dict:
    """Semantic search by query and type (general/trigger/effect). Returns list of {name, text}."""
    query: str = (body["query"] or "").strip() if "query" in body and isinstance(body["query"], str) else ""
    if not query:
        raise HTTPException(status_code=400, detail="query is required and must be non-empty")
    search_type: str = (
        body["search_type"] if "search_type" in body and isinstance(body["search_type"], str) else "general"
    ).strip().lower()
    if search_type not in ("general", "trigger", "effect"):
        raise HTTPException(status_code=400, detail="search_type must be general, trigger, or effect")
    n_results: int = int(body["n_results"]) if "n_results" in body and body["n_results"] is not None else 10
    n_results = max(1, min(50, n_results))
    try:
        results = CardDB.inst().semantic_search_structured(query, search_type, n_results)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"results": results}


@app.get("/api/autocomplete")
async def autocomplete(
    q: str = Query("", min_length=0),
    colors: str = Query(""),
    deck_format: str = Query("", alias="format"),
    colorless_only: bool = Query(False),
) -> dict:
    """Autocomplete card names by substring; optionally filter by color identity and format legality. Returns { data: [names] }."""
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
    if "deck" in body:
        body = body["deck"]
    try:
        _current_deck = Deck.from_dict(body)
    except (KeyError, TypeError) as e:
        LOGGER.error( "load_deck: invalid deck payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid deck payload: {e}") from e
    _notify_deck_updated()
    return _deck_to_response(_current_deck)


@app.get("/api/events")
async def sse_events() -> StreamingResponse:
    """SSE stream: sends deck_updated when the deck changes. Sends current state on connect."""
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
    """Add one or more cards by name. Resolves to Card from local card data and appends to deck.cards. Broadcasts deck_updated via SSE."""
    global _current_deck
    names_to_add: list[str] = _parse_add_card_names(body)
    try:
        cards_to_append: list[Card] = _cards_from_names(names_to_add)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    for card in cards_to_append:
        _current_deck.cards.append(card)
    card_colors = _compute_deck_card_colors(_current_deck)
    existing = set(_current_deck.colors)
    _current_deck.colors = list(existing | card_colors)
    _notify_deck_updated()
    return _deck_to_response(_current_deck)


@app.get("/api/deck")
async def get_deck() -> dict:
    """Return current deck and removed list (empty deck if none loaded yet)."""
    return _deck_to_response(_current_deck)


@app.get("/api/deck/meta")
async def get_deck_meta() -> dict:
    """Return only deck metadata (name, colors, description, format, colorless_only) without card lists."""
    return {
        "name": _current_deck.name,
        "colors": list(_current_deck.colors),
        "description": _current_deck.description,
        "format": _current_deck.format,
        "colorless_only": _current_deck.colorless_only,
    }


@app.get("/api/card_type")
async def get_card_type(name: str = Query(..., min_length=1)) -> dict:
    """Return the type key for a card name (e.g. creature, instant, land)."""
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
    try:
        result: str = CardDB.inst().get_card_mechanics(name=name, extract_type=type)
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e)) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"card": name, "type": type, "result": result}


def _run_price_update_then_notify() -> None:
    """Background: run full price update, reload CardDB prices, broadcast deck_updated."""
    try:
        update_all_prices()
        CardDB.inst().reload_prices()
        _notify_deck_updated()
    except Exception as e:
        LOGGER.error( "Price update failed: %s", e)


@app.post("/api/refresh_prices")
async def refresh_prices() -> dict:
    """Start a background update of all card prices from Scryfall. Returns immediately. When done, deck_updated is broadcast via SSE."""
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
    _notify_deck_updated()
    resp = _deck_to_response(_current_deck)
    LOGGER.debug("import_deck: returning response; deck keys in out: %s", list(resp.get("deck", {}).keys())[:10])
    return resp


@app.put("/api/deck")
async def update_deck(body: dict) -> dict:
    """Update deck from client state."""
    global _current_deck

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
        colorless_only=colorless_only,
        cards=cards_list,
        maybe=maybe_cards,
        sideboard=sideboard_cards,
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


# Static file mounts for JS and CSS (must be after specific routes)
_deck_editor_root: Path = Path(__file__).resolve().parent
app.mount("/js", StaticFiles(directory=str(_deck_editor_root / "js")), name="js")
app.mount("/styles", StaticFiles(directory=str(_deck_editor_root / "styles")), name="styles")
