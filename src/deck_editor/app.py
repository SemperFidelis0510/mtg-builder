"""FastAPI application for the deck editor: serves HTML and deck API."""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

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
    "spells",
    "lands",
]


def _deck_to_response(deck: Deck) -> dict:
    """Build API response with deck dict and removed list."""
    out: dict = deck.to_dict()
    out["removed"] = list(_removed)
    return {"deck": out, "removed": _removed}


def _sanitize_filename(name: str) -> str:
    """Replace unsafe characters for use in filenames."""
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "deck"


def _type_line_to_key(type_line: str) -> str:
    """Map MTG type_line string to TYPE_KEYS section (e.g. 'Instant' -> 'instants')."""
    if not type_line or not isinstance(type_line, str):
        return "spells"
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
    return "spells"


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
    spells: list[str] = body["spells"] if "spells" in body and isinstance(body["spells"], list) else []

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
        sorceries=sorceries,
        spells=spells,
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
