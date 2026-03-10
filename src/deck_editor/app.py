"""FastAPI application for the deck editor: serves HTML and deck API."""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from src.lib.card_data import filter_cards_list
from src.lib.config import DECK_EDITOR_SAVE_DIR
from src.obj.deck import Deck
from src.utils.logger import LOGGER

app = FastAPI(title="MTG Deck Editor")

# ---------------------------------------------------------------------------
# In-memory state (always a deck; starts empty; POST replaces it)
# ---------------------------------------------------------------------------
_current_deck: Deck = Deck()
_removed: list[str] = []

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
            pass


def _notify_deck_updated() -> None:
    """Broadcast current deck state to all SSE clients."""
    _broadcast("deck_updated", _deck_to_response(_current_deck))

# Type-group keys used by the client (order for display)
TYPE_KEYS: list[str] = [
    "creatures",
    "artifacts",
    "enchantments",
    "planeswalkers",
    "instants",
    "sorceries",
    "lands",
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


def _compute_deck_stats(deck: Deck) -> dict:
    """Compute total cards, non_land, lands, and W/U/B/R/G symbol distribution as percentages."""
    from src.lib.card_data import get_card_data

    total_cards: int = 0
    land_count: int = 0
    all_names: list[str] = []
    for key in TYPE_KEYS:
        lst: list[str] = getattr(deck, key, None) or []
        if not isinstance(lst, list):
            continue
        total_cards += len(lst)
        if key == "lands":
            land_count = len(lst)
        all_names.extend(lst)
    non_land: int = total_cards - land_count

    color_counts: dict[str, int] = {c: 0 for c in _COLOR_SYMBOLS}
    data: list = get_card_data()
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
    creature_names: list[str] = list(getattr(deck, "creatures", None) or [])
    non_creature_non_land: list[str] = []
    for key in TYPE_KEYS:
        if key in ("lands", "creatures"):
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

    return {
        "total_cards": total_cards,
        "non_land": non_land,
        "lands": land_count,
        "color_distribution": color_distribution,
        "mana_value_distribution": mana_value_distribution,
    }


def _deck_to_response(deck: Deck) -> dict:
    """Build API response with deck dict, removed list, and stats."""
    out: dict = deck.to_dict()
    out["removed"] = list(_removed)
    resp: dict = {"deck": out, "removed": _removed, "stats": _compute_deck_stats(deck)}
    return resp


def _sanitize_filename(name: str) -> str:
    """Replace unsafe characters for use in filenames."""
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "deck"


def _type_line_to_key(type_line: str) -> str:
    """Map MTG type_line string to TYPE_KEYS section (e.g. 'Instant' -> 'instants')."""
    if not type_line or not isinstance(type_line, str):
        return "sorceries"
    t: str = type_line.lower()
    if "land" in t:
        return "lands"
    if "creature" in t:
        return "creatures"
    if "planeswalker" in t:
        return "planeswalkers"
    if "artifact" in t:
        return "artifacts"
    if "enchantment" in t:
        return "enchantments"
    if "instant" in t:
        return "instants"
    if "sorcery" in t:
        return "sorceries"
    return "sorceries"


def _resolve_type_key(card_name: str) -> tuple[str, str]:
    """Look up card_name in local data; return (canonical_name, type_key). Raises ValueError if not found."""
    from src.lib.card_data import get_card_data

    name_clean: str = (card_name or "").strip()
    if not name_clean:
        raise ValueError("add_card: card name is empty")
    data: list = get_card_data()
    name_lower: str = name_clean.lower()
    for c in data:
        if c.name.lower() == name_lower:
            key: str = _type_line_to_key(c.type_line)
            return (c.name, key)
    LOGGER.error(0, "add_card: card not found: %s", name_clean)
    raise ValueError(f"add_card: card not found: {name_clean!r}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def serve_editor() -> FileResponse:
    """Serve the deck editor HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    index_path: Path = static_dir / "index.html"
    if not index_path.is_file():
        LOGGER.error(0, "Deck editor static file not found: %s", index_path)
        raise FileNotFoundError(f"Static file not found: {index_path}")
    return FileResponse(index_path)


@app.get("/search")
async def serve_search() -> FileResponse:
    """Serve the advanced search popup HTML page."""
    static_dir: Path = Path(__file__).resolve().parent / "static"
    search_path: Path = static_dir / "search.html"
    if not search_path.is_file():
        LOGGER.error(0, "Deck editor static file not found: %s", search_path)
        raise FileNotFoundError(f"Static file not found: {search_path}")
    return FileResponse(search_path)


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
        results = filter_cards_list(
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
    """Load a deck from JSON. Replaces current deck and clears removed zone."""
    global _current_deck, _removed
    if "deck" in body:
        body = body["deck"]
    try:
        _current_deck = Deck.from_dict(body)
    except (KeyError, TypeError) as e:
        LOGGER.error(0, "load_deck: invalid deck payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid deck payload: {e}") from e
    # If type lists are empty but body has "cards", populate type lists from cards (e.g. Arena/other export)
    total_in_types: int = sum(
        len(getattr(_current_deck, k, None) or [])
        for k in TYPE_KEYS
    )
    if total_in_types == 0 and "cards" in body and isinstance(body["cards"], list):
        for raw_name in _names_from_cards_array(body["cards"]):
            try:
                canonical_name, type_key = _resolve_type_key(raw_name)
                list_attr: list[str] = getattr(_current_deck, type_key)
                list_attr.append(canonical_name)
            except ValueError:
                pass
    _removed = []
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
            pass
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
    """Add one or more cards by name. Resolves type from local card data. Broadcasts deck_updated via SSE."""
    global _current_deck
    names_to_add: list[str] = _parse_add_card_names(body)
    for raw_name in names_to_add:
        try:
            canonical_name, type_key = _resolve_type_key(raw_name)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        list_attr: list[str] = getattr(_current_deck, type_key)
        list_attr.append(canonical_name)
    _notify_deck_updated()
    return _deck_to_response(_current_deck)


@app.get("/api/deck")
async def get_deck() -> dict:
    """Return current deck and removed list (empty deck if none loaded yet)."""
    return _deck_to_response(_current_deck)


@app.put("/api/deck")
async def update_deck(body: dict) -> dict:
    """Update deck and removed zone from client state."""
    global _current_deck, _removed

    if "removed" in body and isinstance(body["removed"], list):
        _removed = list(body["removed"])
    else:
        _removed = []

    name: str = _current_deck.name
    if "name" in body and isinstance(body["name"], str):
        name = body["name"]
    colors: list[str] = list(_current_deck.colors)
    if "colors" in body and isinstance(body["colors"], list):
        colors = body["colors"]
    description: str = _current_deck.description
    if "description" in body and isinstance(body["description"], str):
        description = body["description"]

    creatures: list[str] = body["creatures"] if "creatures" in body and isinstance(body["creatures"], list) else []
    artifacts: list[str] = body["artifacts"] if "artifacts" in body and isinstance(body["artifacts"], list) else []
    enchantments: list[str] = (
        body["enchantments"] if "enchantments" in body and isinstance(body["enchantments"], list) else []
    )
    planeswalkers: list[str] = (
        body["planeswalkers"] if "planeswalkers" in body and isinstance(body["planeswalkers"], list) else []
    )
    lands: list[str] = body["lands"] if "lands" in body and isinstance(body["lands"], list) else []
    instants: list[str] = body["instants"] if "instants" in body and isinstance(body["instants"], list) else []
    sorceries: list[str] = body["sorceries"] if "sorceries" in body and isinstance(body["sorceries"], list) else []
    spells_legacy: list[str] = body["spells"] if "spells" in body and isinstance(body["spells"], list) else []
    sorceries_merged: list[str] = sorceries + spells_legacy

    # Deck expects cards as list[Card] or list[dict]; editor only sends type lists (names). Leave cards empty.
    _current_deck = Deck(
        name=name,
        colors=colors,
        description=description,
        cards=None,
        creatures=creatures,
        artifacts=artifacts,
        enchantments=enchantments,
        planeswalkers=planeswalkers,
        lands=lands,
        instants=instants,
        sorceries=sorceries_merged,
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
