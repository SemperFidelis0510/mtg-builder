"""FastAPI application for the deck editor: serves HTML and deck API."""

import asyncio
import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
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
# Wishlist persistence
# ---------------------------------------------------------------------------
WISHLIST_FILE: Path = Path.home() / ".mtgbuilder" / "wishlist.json"

# ---------------------------------------------------------------------------
# In-memory state (always a deck; starts empty; POST replaces it)
# ---------------------------------------------------------------------------
_current_deck: Deck = Deck()

# ---------------------------------------------------------------------------
# SSE event bus
# ---------------------------------------------------------------------------
_sse_clients: set[asyncio.Queue[str]] = set()


def _download_and_cache_image(face_name: str, url: str, size: str) -> None:
    """Download image bytes from *url* and save to the face disk cache. Best-effort."""
    import requests

    if CardDB._face_image_path(face_name, size).is_file():
        return
    try:
        r = None
        for attempt in range(4):
            r = requests.get(
                url, timeout=15, allow_redirects=True,
                headers={"Accept": "*/*", "User-Agent": "MTG-MCP/1.0"},
            )
            if r.status_code == 429:
                LOGGER.error("_download_and_cache_image: 429 face_name=%r attempt=%d", face_name, attempt + 1)
                CardDB._sleep_for_retry_after(r.headers, attempt)
                continue
            break
        assert r is not None, "requests.get must return a response"
        if not r.ok:
            LOGGER.error("_download_and_cache_image: HTTP %s face_name=%r url=%s", r.status_code, face_name, url)
            return
        if not r.content:
            LOGGER.error("_download_and_cache_image: empty body face_name=%r url=%s", face_name, url)
            return
        CardDB.inst().save_face_image(face_name, size, r.content)
    except Exception as e:
        LOGGER.error("_download_and_cache_image: failed face_name=%r size=%s err=%s", face_name, size, e)


def _prefetch_deck_images(deck: Deck) -> None:
    """Prefetch and disk-cache card face images for all cards in *deck*.

    Uses the Scryfall ``/cards/collection`` batch endpoint to discover image URLs,
    then downloads and saves each face image via ``CardDB.save_face_image``.
    Best-effort: logs errors but does not raise (deck load should still succeed).
    """
    try:
        import requests

        names: set[str] = set()
        for c in deck.cards:
            names.add(c.name)
        for c in deck.maybe:
            names.add(c.name)
        for c in deck.sideboard:
            names.add(c.name)
        if deck.commander:
            names.add(deck.commander)

        face_entries: list[tuple[str, int]] = []
        for n in sorted(names):
            try:
                faces: list[Card] = CardDB.inst().resolve_faces(n)
            except Exception as e:
                LOGGER.error("_prefetch_deck_images: resolve_faces failed name=%r err=%s", n, e)
                continue
            for i, f in enumerate(faces):
                face_entries.append((f.name, i))

        if not face_entries:
            return

        for i in range(0, len(face_entries), BATCH_SIZE):
            batch = face_entries[i : i + BATCH_SIZE]
            identifiers: list[dict[str, str]] = [{"name": face_name} for (face_name, _idx) in batch]
            r = None
            for attempt in range(4):
                r = requests.post(
                    SCRYFALL_COLLECTION_URL,
                    json={"identifiers": identifiers},
                    timeout=30,
                    headers={"Accept": "application/json", "User-Agent": "MTG-MCP/1.0"},
                )
                if r.status_code == 429:
                    LOGGER.error("_prefetch_deck_images: rate limited (429); backing off attempt=%d", attempt + 1)
                    CardDB._sleep_for_retry_after(r.headers, attempt)
                    continue
                break
            if r is None or not r.ok:
                status = r.status_code if r is not None else "n/a"
                LOGGER.error("_prefetch_deck_images: scryfall collection failed status=%s", status)
                return
            payload = r.json()
            data = payload["data"] if isinstance(payload, dict) and "data" in payload else None
            if not isinstance(data, list):
                LOGGER.error("_prefetch_deck_images: invalid scryfall payload (missing data list)")
                return

            # Collect (face_name, url, size) tuples to download.
            to_download: list[tuple[str, str, str]] = []

            for card_obj in data:
                if not isinstance(card_obj, dict):
                    continue
                card_name = card_obj["name"] if "name" in card_obj and isinstance(card_obj["name"], str) else ""
                if not card_name:
                    continue

                if "image_uris" in card_obj and isinstance(card_obj["image_uris"], dict):
                    image_uris = card_obj["image_uris"]
                    for sz in ("normal", "large"):
                        if sz in image_uris and isinstance(image_uris[sz], str):
                            to_download.append((card_name, image_uris[sz], sz))

                if "card_faces" in card_obj and isinstance(card_obj["card_faces"], list):
                    for face_obj in card_obj["card_faces"]:
                        if not isinstance(face_obj, dict):
                            continue
                        fn = face_obj["name"] if "name" in face_obj and isinstance(face_obj["name"], str) else ""
                        if not fn:
                            continue
                        fi_uris = face_obj["image_uris"] if "image_uris" in face_obj else None
                        if not isinstance(fi_uris, dict):
                            continue
                        for sz in ("normal", "large"):
                            if sz in fi_uris and isinstance(fi_uris[sz], str):
                                to_download.append((fn, fi_uris[sz], sz))

            for fn, url, sz in to_download:
                _download_and_cache_image(fn, url, sz)

            if i + BATCH_SIZE < len(face_entries):
                time.sleep(DELAY_BETWEEN_BATCHES_S)
    except Exception as e:
        LOGGER.error("_prefetch_deck_images: unexpected failure: %s", e)


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


@app.on_event("startup")
def _startup_ensure_wishlist_file() -> None:
    """Create ~/.mtgbuilder/wishlist.json with an empty list if it does not exist."""
    if WISHLIST_FILE.is_file():
        LOGGER.info("_startup_ensure_wishlist_file: wishlist file exists at %s", WISHLIST_FILE)
        return
    WISHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WISHLIST_FILE.write_text("[]", encoding="utf-8")
    LOGGER.info("_startup_ensure_wishlist_file: created empty wishlist at %s", WISHLIST_FILE)


def _read_wishlist() -> list[dict[str, Any]]:
    """Read and return the wishlist entries from disk."""
    if not WISHLIST_FILE.is_file():
        return []
    raw: str = WISHLIST_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    entries: list[dict[str, Any]] = json.loads(raw)
    if not isinstance(entries, list):
        LOGGER.error("_read_wishlist: expected list, got %s", type(entries).__name__)
        raise TypeError(f"_read_wishlist: expected list in wishlist file, got {type(entries).__name__}")
    return entries


def _write_wishlist(entries: list[dict[str, Any]]) -> None:
    """Atomically write wishlist entries to disk."""
    WISHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WISHLIST_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def _enrich_wishlist_with_prices(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach price_usd to each wishlist entry from the card database."""
    card_db: CardDB = CardDB.inst()
    enriched: list[dict[str, Any]] = []
    for entry in entries:
        name: str = entry["name"]
        quantity: int = entry["quantity"]
        price_usd: float | None = None
        card: Card | None = card_db.try_resolve_primary_card(name)
        if card is not None:
            p: float = getattr(card, "price_usd", -1.0)
            if p >= 0:
                price_usd = p
        enriched.append({"name": name, "quantity": quantity, "price_usd": price_usd})
    return enriched


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


_BOARD_ATTRS: tuple[str, ...] = ("cards", "sideboard", "maybe")


def _count_cards_per_board(deck: Deck) -> dict[str, dict[str, int]]:
    """Return {canonical_name: {board_attr: count}} for main, sideboard, and maybe."""
    result: dict[str, dict[str, int]] = {}
    for attr in _BOARD_ATTRS:
        for card in getattr(deck, attr):
            n: str = CardDB.inst().card_display_name(card)
            if n not in result:
                result[n] = {}
            result[n][attr] = result[n].get(attr, 0) + 1
    return result


def _remove_n_copies(board: list[Card], card_name: str, n: int) -> int:
    """Remove up to *n* copies of *card_name* from *board*. Return count actually removed."""
    indices: list[int] = []
    for i, card in enumerate(board):
        if CardDB.inst().card_display_name(card) == card_name:
            indices.append(i)
            if len(indices) >= n:
                break
    remove_cards_at_indices(board, indices)
    return len(indices)


def merge_deck_into(current: Deck, imported: Deck) -> None:
    """Merge *imported* into *current* in-place.

    - Cards in *current* that are absent from *imported* are kept (never removed).
    - For each card in *imported*, copies on boards the imported deck does NOT use
      are moved to the board(s) the imported deck specifies.  Only the remaining
      deficit (after moves) causes new cards to be created.
    - Commander: set from *imported* only when *current* has none.
    - Colors: union of both decks' color lists.
    """
    cur_per_board: dict[str, dict[str, int]] = _count_cards_per_board(current)
    imp_per_board: dict[str, dict[str, int]] = _count_cards_per_board(imported)

    total_moved: int = 0
    total_created: int = 0

    for card_name, imp_boards in imp_per_board.items():
        cur_boards: dict[str, int] = cur_per_board.get(card_name, {})

        per_target_deficit: dict[str, int] = {}
        for attr, imp_qty in imp_boards.items():
            diff: int = imp_qty - cur_boards.get(attr, 0)
            if diff > 0:
                per_target_deficit[attr] = diff

        total_deficit: int = sum(per_target_deficit.values())
        if total_deficit == 0:
            continue

        freed: int = 0
        for attr in _BOARD_ATTRS:
            if cur_boards.get(attr, 0) > 0 and imp_boards.get(attr, 0) == 0:
                can_free: int = min(cur_boards[attr], total_deficit - freed)
                if can_free > 0:
                    _remove_n_copies(getattr(current, attr), card_name, can_free)
                    freed += can_free
                    total_moved += can_free
                if freed >= total_deficit:
                    break

        for attr, deficit in per_target_deficit.items():
            new_cards: list[Card] = _cards_from_names([card_name] * deficit)
            getattr(current, attr).extend(new_cards)

        total_created += total_deficit - freed

    if not current.commander and imported.commander:
        current.commander = imported.commander

    merged_colors: set[str] = set(current.colors) | set(imported.colors)
    current.colors = list(merged_colors)

    LOGGER.info(
        "merge_deck_into: moved %d, created %d cards; commander=%r; colors=%s",
        total_moved,
        total_created,
        current.commander,
        current.colors,
    )


_VALID_BOARDS: frozenset[str] = frozenset({"main", "maybe", "sideboard", "commander"})


def _valid_boards_detail() -> str:
    return "'main', 'maybe', 'sideboard', or 'commander'"


def _is_commander_enabled_format(format_value: str) -> bool:
    """Return True when format uses commander color-identity deckbuilding rules."""
    fmt: str = (format_value or "").strip().lower()
    return fmt == "duel" or "commander" in fmt or "brawl" in fmt


def _effective_identity_filters(
    *,
    format_legal: str,
    colors: str,
    colorless_only: bool,
    requested_color_identity: str,
    requested_color_identity_colorless: bool,
) -> tuple[str, bool]:
    """Resolve effective color-identity filters for search calls.

    In commander-family formats, regular color criterion is mirrored onto color identity:
    - selected colors => color_identity=colors
    - colorless-only => color_identity_colorless=True
    """
    color_identity: str = (requested_color_identity or "").strip()
    color_identity_colorless: bool = requested_color_identity_colorless
    if not _is_commander_enabled_format(format_legal):
        return color_identity, color_identity_colorless
    colors_clean: str = (colors or "").strip()
    if colors_clean:
        return colors_clean, False
    if colorless_only:
        return "", True
    return color_identity, color_identity_colorless


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


def _apply_copy_cap(
    valid_cards: list[Card],
    valid_names: list[str],
    rejected: list[dict[str, str]],
    deck: Deck,
) -> tuple[list[Card], list[str], list[dict[str, str]]]:
    """Filter out cards that would exceed the per-card copy cap.

    Counts existing copies across main + sideboard + commander (maybe is excluded).
    Uses ``COMMANDER_CARD_CAP`` for commander-family formats, ``DEFAULT_CARD_CAP`` otherwise.
    Cards in ``UNCAPPED_CARD_NAMES`` bypass the limit entirely.
    """
    from collections import Counter

    from src.config.card_cap import COMMANDER_CARD_CAP, DEFAULT_CARD_CAP, is_uncapped

    cap: int = COMMANDER_CARD_CAP if _is_commander_enabled_format(deck.format or "") else DEFAULT_CARD_CAP

    display_name = CardDB.inst().card_display_name
    existing: Counter[str] = Counter()
    for card in deck.cards:
        existing[display_name(card)] += 1
    for card in deck.sideboard:
        existing[display_name(card)] += 1
    if deck.commander:
        existing[deck.commander] += 1

    batch: Counter[str] = Counter()
    capped_cards: list[Card] = []
    capped_names: list[str] = []

    for card, name in zip(valid_cards, valid_names):
        canonical: str = display_name(card)
        if is_uncapped(canonical):
            capped_cards.append(card)
            capped_names.append(name)
            continue

        total: int = existing[canonical] + batch[canonical]
        if total >= cap:
            rejected.append({
                "name": name,
                "reason": f"Deck already contains {total} {'copy' if total == 1 else 'copies'} (max {cap} for this format)",
            })
        else:
            batch[canonical] += 1
            capped_cards.append(card)
            capped_names.append(name)

    return capped_cards, capped_names, rejected


def validate_cards_for_deck(
    cards: list[Card],
    card_names: list[str],
    deck: Deck,
    board: str,
) -> tuple[list[Card], list[str], list[dict[str, str]]]:
    """Validate cards against deck color-identity, format-legality, and copy-cap restrictions.

    Returns ``(valid_cards, valid_names, rejected)`` where *rejected* is a list of
    ``{"name": ..., "reason": ...}`` dicts.  Validation is skipped for the *maybe*
    board (all cards pass).
    """
    if board == "maybe":
        return list(cards), list(card_names), []

    deck_colors: set[str] = set(deck.colors) if deck.colors else set()
    deck_format: str = (deck.format or "").strip().lower()

    valid_cards: list[Card] = []
    valid_names: list[str] = []
    rejected: list[dict[str, str]] = []

    for card, name in zip(cards, card_names):
        reasons: list[str] = []

        if deck_colors:
            card_identity: set[str] = set(card.color_identity) if card.color_identity else set()
            if card_identity and not card_identity.issubset(deck_colors):
                reasons.append(
                    f"Color identity [{', '.join(sorted(card_identity))}] "
                    f"is not within deck colors [{', '.join(sorted(deck_colors))}]"
                )

        if deck_format:
            legal_val: str = ""
            for k, v in card.legalities.items():
                if k.lower() == deck_format and v:
                    legal_val = (v if isinstance(v, str) else str(v)).lower()
                    break
            if legal_val != "legal":
                status_str: str = legal_val if legal_val else "not found"
                reasons.append(f"Not legal in {deck.format} format (status: {status_str})")

        if reasons:
            rejected.append({"name": name, "reason": "; ".join(reasons)})
        else:
            valid_cards.append(card)
            valid_names.append(name)

    return _apply_copy_cap(valid_cards, valid_names, rejected, deck)


def _recompute_and_set_colors(deck: Deck) -> None:
    """Recompute deck colors from all boards (main + maybe + sideboard + commander) and update deck.colors.

    If *deck.colors* is already non-empty (user-set), the existing value is
    preserved and no recomputation is performed.
    """
    if deck.colors:
        return
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
    effective_color_identity, effective_color_identity_colorless = _effective_identity_filters(
        format_legal=format_legal,
        colors=colors,
        colorless_only=colorless_only,
        requested_color_identity=color_identity,
        requested_color_identity_colorless=color_identity_colorless,
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
            color_identity=effective_color_identity,
            color_identity_colorless=effective_color_identity_colorless,
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
    colors_clean: str = colors.strip()
    color_identity_arg, color_identity_colorless_arg = _effective_identity_filters(
        format_legal=deck_format,
        colors=colors_clean,
        colorless_only=colorless_only,
        requested_color_identity="",
        requested_color_identity_colorless=False,
    )
    try:
        results = CardDB.inst().filter_cards_list(
            name=q_clean,
            colors=colors_clean,
            color_identity=color_identity_arg,
            color_identity_colorless=color_identity_colorless_arg,
            colorless_only=colorless_only,
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
        LOGGER.error("load_deck: invalid deck payload: %s", e)
        raise HTTPException(status_code=400, detail=f"Invalid deck payload: {e}") from e
    LOGGER.info(
        "load_deck: deck loaded name=%r format=%r commander=%r cards=%d",
        _current_deck.name,
        _current_deck.format,
        _current_deck.commander,
        len(_current_deck.cards),
    )
    threading.Thread(target=_prefetch_deck_images, args=(_current_deck,), daemon=True).start()
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
    resolved_cards: list[Card] = []
    resolved_names: list[str] = []
    not_found: list[str] = []
    for name in names_to_add:
        try:
            cards = _cards_from_names([name])
            resolved_cards.extend(cards)
            resolved_names.extend([name] * len(cards))
        except ValueError:
            not_found.append(name)
    if not resolved_cards:
        raise HTTPException(status_code=404, detail=f"Card(s) not found: {', '.join(not_found)}")

    cards_to_append, valid_names, rejected = validate_cards_for_deck(
        resolved_cards, resolved_names, _current_deck, board,
    )

    if not cards_to_append:
        reasons = "; ".join(f"{r['name']}: {r['reason']}" for r in rejected)
        detail = f"No cards could be added. {reasons}"
        if not_found:
            detail += f". Card(s) not found: {', '.join(not_found)}"
        raise HTTPException(status_code=400, detail=detail)

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
    if not_found or rejected:
        LOGGER.warning(
            "add_card: added=%s requested=%s board=%s not_found=%s rejected=%s",
            len(cards_to_append),
            len(names_to_add),
            board,
            not_found,
            [r["name"] for r in rejected],
        )
    _recompute_and_set_colors(_current_deck)
    _notify_deck_updated()
    LOGGER.info("add_card: added %d cards to %s", len(cards_to_append), board)
    response: dict = _deck_to_response(_current_deck)
    if not_found:
        response["not_found"] = not_found
    if rejected:
        response["rejected"] = rejected
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


_MOVE_ALL_BOARDS: frozenset[str] = frozenset({"main", "maybe", "sideboard"})


@app.post("/api/move_all_cards")
async def move_all_cards(body: dict) -> dict:
    """Move every card from one board to another in one shot. Broadcasts deck_updated via SSE.

    Body: {"from_board": "main"|"maybe"|"sideboard", "to_board": "main"|"maybe"|"sideboard"}
    """
    if "from_board" not in body or not isinstance(body["from_board"], str):
        raise HTTPException(status_code=400, detail=f"'from_board' is required (string: {', '.join(sorted(_MOVE_ALL_BOARDS))})")
    if "to_board" not in body or not isinstance(body["to_board"], str):
        raise HTTPException(status_code=400, detail=f"'to_board' is required (string: {', '.join(sorted(_MOVE_ALL_BOARDS))})")
    from_board: str = body["from_board"]
    to_board: str = body["to_board"]
    if from_board not in _MOVE_ALL_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid from_board: {from_board!r}.")
    if to_board not in _MOVE_ALL_BOARDS:
        raise HTTPException(status_code=400, detail=f"Invalid to_board: {to_board!r}.")
    if from_board == to_board:
        raise HTTPException(status_code=400, detail=f"from_board and to_board must differ (both are {from_board!r})")
    source: list[Card] = _get_board_list(_current_deck, from_board)
    dest: list[Card] = _get_board_list(_current_deck, to_board)
    moved_count: int = len(source)
    if moved_count == 0:
        return _deck_to_response(_current_deck)
    dest.extend(source)
    source.clear()
    _recompute_and_set_colors(_current_deck)
    _notify_deck_updated()
    LOGGER.info("move_all_cards: moved %d cards from %s to %s", moved_count, from_board, to_board)
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
) -> FileResponse:
    """Serve a card face image from the local disk cache, fetching from Scryfall on first request."""
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
    try:
        path: Path = CardDB.inst().get_face_image(face_name, size)
    except Exception as e:
        LOGGER.error("card_image: failed to get face image face_name=%r size=%s err=%s", face_name, size, e)
        raise HTTPException(status_code=502, detail="Failed to fetch card image") from e

    return FileResponse(path, media_type="image/jpeg")


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
    """Import a deck from pasted text. Body: {"text": str, "format": str, "merge"?: bool}.

    When *merge* is true (the default) and a deck is already loaded, imported cards
    are merged into the current deck instead of replacing it.
    """
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
    want_merge: bool = body.get("merge", True) is not False
    LOGGER.debug("import_deck: format=%r merge=%s text_len=%d text_preview=%r", fmt, want_merge, len(text), (text[:80] + "..." if len(text) > 80 else text))
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
    deck_has_cards: bool = bool(_current_deck.cards or _current_deck.sideboard or _current_deck.maybe)
    if want_merge and deck_has_cards:
        LOGGER.info("import_deck: merging into existing deck (cards=%d)", len(_current_deck.cards))
        merge_deck_into(_current_deck, deck)
    else:
        _current_deck = deck
    card_colors = _compute_deck_card_colors(_current_deck)
    existing = set(_current_deck.colors)
    _current_deck.colors = list(existing | card_colors)
    threading.Thread(target=_prefetch_deck_images, args=(_current_deck,), daemon=True).start()
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


# ---------------------------------------------------------------------------
# Wishlist routes
# ---------------------------------------------------------------------------


@app.get("/api/wishlist")
async def get_wishlist() -> dict:
    """Return the full wishlist with prices attached."""
    entries: list[dict[str, Any]] = _read_wishlist()
    enriched: list[dict[str, Any]] = _enrich_wishlist_with_prices(entries)
    total_price: float = 0.0
    for item in enriched:
        p = item["price_usd"]
        if p is not None and p >= 0:
            total_price += p * item["quantity"]
    return {"items": enriched, "total_price_usd": round(total_price, 2)}


@app.put("/api/wishlist")
async def put_wishlist(request: Request) -> dict:
    """Replace the entire wishlist (used after reorder, sort, quantity edits)."""
    body: Any = await request.json()
    if not isinstance(body, list):
        raise HTTPException(status_code=400, detail="Body must be a JSON array of {name, quantity} objects")
    entries: list[dict[str, Any]] = []
    for item in body:
        if not isinstance(item, dict) or "name" not in item:
            raise HTTPException(status_code=400, detail="Each item must have a 'name' field")
        qty: int = max(1, int(item["quantity"]) if "quantity" in item else 1)
        entries.append({"name": str(item["name"]), "quantity": qty})
    _write_wishlist(entries)
    LOGGER.info("put_wishlist: saved %d entries", len(entries))
    enriched: list[dict[str, Any]] = _enrich_wishlist_with_prices(entries)
    total_price: float = 0.0
    for it in enriched:
        p = it["price_usd"]
        if p is not None and p >= 0:
            total_price += p * it["quantity"]
    return {"items": enriched, "total_price_usd": round(total_price, 2)}


@app.post("/api/wishlist/add")
async def add_to_wishlist(request: Request) -> dict:
    """Append card(s) to the wishlist; increment quantity if already present."""
    body: dict = await request.json()
    names_raw: Any = body["names"] if "names" in body else None
    name_single: Any = body["name"] if "name" in body else None
    if names_raw is None and name_single is None:
        raise HTTPException(status_code=400, detail="Provide 'name' (string) or 'names' (list of strings)")
    names: list[str] = []
    if names_raw is not None:
        if not isinstance(names_raw, list):
            raise HTTPException(status_code=400, detail="'names' must be a list of strings")
        names = [str(n) for n in names_raw]
    elif name_single is not None:
        names = [str(name_single)]
    entries: list[dict[str, Any]] = _read_wishlist()
    name_to_idx: dict[str, int] = {e["name"].lower(): i for i, e in enumerate(entries)}
    for n in names:
        key: str = n.strip().lower()
        if not key:
            continue
        card_db: CardDB = CardDB.inst()
        resolved: Card | None = card_db.try_resolve_primary_card(n.strip())
        display_name: str = card_db.card_display_name(resolved) if resolved is not None else n.strip()
        existing_key: str = display_name.lower()
        if existing_key in name_to_idx:
            idx: int = name_to_idx[existing_key]
            entries[idx]["quantity"] = entries[idx]["quantity"] + 1
        else:
            entries.append({"name": display_name, "quantity": 1})
            name_to_idx[existing_key] = len(entries) - 1
    _write_wishlist(entries)
    LOGGER.info("add_to_wishlist: added %s, total entries=%d", names, len(entries))
    enriched: list[dict[str, Any]] = _enrich_wishlist_with_prices(entries)
    total_price: float = 0.0
    for it in enriched:
        p = it["price_usd"]
        if p is not None and p >= 0:
            total_price += p * it["quantity"]
    return {"items": enriched, "total_price_usd": round(total_price, 2)}


@app.post("/api/wishlist/remove")
async def remove_from_wishlist(request: Request) -> dict:
    """Remove card(s) from the wishlist or decrement quantity."""
    body: dict = await request.json()
    names_raw: Any = body["names"] if "names" in body else None
    name_single: Any = body["name"] if "name" in body else None
    if names_raw is None and name_single is None:
        raise HTTPException(status_code=400, detail="Provide 'name' (string) or 'names' (list of strings)")
    names: list[str] = []
    if names_raw is not None:
        if not isinstance(names_raw, list):
            raise HTTPException(status_code=400, detail="'names' must be a list of strings")
        names = [str(n) for n in names_raw]
    elif name_single is not None:
        names = [str(name_single)]
    count: int = max(1, int(body["count"]) if "count" in body else 1)
    entries: list[dict[str, Any]] = _read_wishlist()
    for n in names:
        key: str = n.strip().lower()
        if not key:
            continue
        for i, e in enumerate(entries):
            if e["name"].lower() == key:
                e["quantity"] -= count
                if e["quantity"] <= 0:
                    entries.pop(i)
                break
    _write_wishlist(entries)
    LOGGER.info("remove_from_wishlist: removed %s (count=%d), total entries=%d", names, count, len(entries))
    enriched: list[dict[str, Any]] = _enrich_wishlist_with_prices(entries)
    total_price: float = 0.0
    for it in enriched:
        p = it["price_usd"]
        if p is not None and p >= 0:
            total_price += p * it["quantity"]
    return {"items": enriched, "total_price_usd": round(total_price, 2)}


# Static file mounts for JS and CSS (must be after specific routes)
_deck_editor_root: Path = Path(__file__).resolve().parent
app.mount("/js", StaticFiles(directory=str(_deck_editor_root / "js")), name="js")
app.mount("/styles", StaticFiles(directory=str(_deck_editor_root / "styles")), name="styles")
