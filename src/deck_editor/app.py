"""FastAPI application for the deck editor: serves HTML and deck API."""

import re
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from src.lib.config import DATA_DIR
from src.obj.deck import Deck
from src.utils.logger import LOGGER

app = FastAPI(title="MTG Deck Editor")

# ---------------------------------------------------------------------------
# In-memory state (always a deck; starts empty; POST replaces it)
# ---------------------------------------------------------------------------
_current_deck: Deck = Deck()
_removed: list[str] = []

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


def _build_cards_from_type_lists(data: dict) -> list[str]:
    """Rebuild unified cards list from type lists (for save)."""
    cards: list[str] = []
    for key in TYPE_KEYS:
        if key in data and isinstance(data[key], list):
            cards.extend(data[key])
    return cards


def _sanitize_filename(name: str) -> str:
    """Replace unsafe characters for use in filenames."""
    return re.sub(r"[^\w\-.]", "_", name).strip("_") or "deck"


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

    cards: list[str] = _build_cards_from_type_lists(body)
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

    _current_deck = Deck(
        name=name,
        colors=colors,
        description=description,
        cards=cards,
        creatures=creatures,
        artifacts=artifacts,
        enchantments=enchantments,
        planeswalkers=planeswalkers,
        lands=lands,
        instants=instants,
        sorceries=sorceries,
        spells=spells,
    )
    return _deck_to_response(_current_deck)


@app.post("/api/save")
async def save_deck() -> dict:
    """Write current deck to a JSON file (excluding removed). Return path."""
    safe_name: str = _sanitize_filename(_current_deck.name)
    timestamp: str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename: str = f"{safe_name}_{timestamp}.json"
    out_path: Path = DATA_DIR / filename
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    import json

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_current_deck.to_dict(), f, indent=2)
    LOGGER.info("Deck saved to %s", out_path)
    return {"saved_to": str(out_path)}
