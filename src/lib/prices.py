"""Price cache: fetch USD prices from Scryfall, load/save to data/prices.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.lib.config import PRICES_PATH
from src.utils.logger import LOGGER, init_logger

SCRYFALL_COLLECTION_URL: str = "https://api.scryfall.com/cards/collection"
BATCH_SIZE: int = 75
DELAY_BETWEEN_BATCHES_S: float = 0.1


def load_prices() -> dict[str, float]:
    """Read data/prices.json and return {card_name: price_usd}. Missing file or invalid data returns {}."""
    if not PRICES_PATH.is_file():
        return {}
    try:
        with open(PRICES_PATH, "r", encoding="utf-8") as f:
            raw: dict = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("load_prices: failed to read %s: %s", PRICES_PATH, e)
        return {}
    data = raw.get("prices")
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for name, val in data.items():
        if not isinstance(name, str):
            continue
        if val is None:
            continue
        try:
            out[name] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def save_prices(prices: dict[str, float]) -> None:
    """Write prices to data/prices.json with updated_at timestamp."""
    PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prices": {k: v for k, v in prices.items() if v >= 0},
    }
    with open(PRICES_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def fetch_prices_batch(names: list[str]) -> dict[str, float]:
    """Call Scryfall /cards/collection for the given names (max 75). Returns {card_name: price_usd}."""
    if not names:
        return {}
    identifiers: list[dict[str, str]] = [{"name": n} for n in names[:BATCH_SIZE]]
    import requests

    try:
        r = requests.post(
            SCRYFALL_COLLECTION_URL,
            json={"identifiers": identifiers},
            timeout=30,
            headers={"Accept": "application/json", "User-Agent": "MTG-MCP/1.0"},
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        LOGGER.error("fetch_prices_batch: request failed: %s", e)
        raise
    except json.JSONDecodeError as e:
        LOGGER.error("fetch_prices_batch: invalid JSON: %s", e)
        raise ValueError("Scryfall returned invalid JSON") from e

    cards = data.get("data")
    if not isinstance(cards, list):
        return {}
    out: dict[str, float] = {}
    for card in cards:
        name = card.get("name") if isinstance(card, dict) else None
        if not name or not isinstance(name, str):
            continue
        prices_obj = card.get("prices") if isinstance(card, dict) else None
        if not isinstance(prices_obj, dict):
            continue
        usd = prices_obj.get("usd")
        if usd is None or usd == "":
            continue
        try:
            out[name] = float(usd)
        except (TypeError, ValueError):
            continue
    return out


def update_all_prices() -> dict[str, float]:
    """Load all card names from CardDB, fetch prices from Scryfall in batches, save to prices.json, return the dict."""
    from src.lib.cardDB import CardDB

    cards = CardDB.inst().get_card_data()
    names: list[str] = list({c.name for c in cards})
    LOGGER.info("update_all_prices: fetching prices for %s unique names", len(names))
    result: dict[str, float] = load_prices()
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i : i + BATCH_SIZE]
        try:
            batch_prices = fetch_prices_batch(batch)
        except Exception:
            raise
        for k, v in batch_prices.items():
            result[k] = v
        if i + BATCH_SIZE < len(names):
            time.sleep(DELAY_BETWEEN_BATCHES_S)
    save_prices(result)
    LOGGER.info("update_all_prices: saved %s prices to %s", len(result), PRICES_PATH)
    return result


def prices_age_hours() -> float | None:
    """Return hours since last update, or None if file missing or no updated_at."""
    if not PRICES_PATH.is_file():
        return None
    try:
        with open(PRICES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        LOGGER.warning("prices_age_hours: failed to read %s: %s", PRICES_PATH, e)
        return None
    updated = raw.get("updated_at")
    if not updated or not isinstance(updated, str):
        return None
    try:
        from datetime import datetime, timezone

        s = updated.replace("Z", "+00:00")
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
        now = datetime.now(timezone.utc)
        delta = now - dt
        return delta.total_seconds() / 3600.0
    except (ValueError, TypeError) as e:
        LOGGER.warning("prices_age_hours: invalid updated_at in %s: %s", PRICES_PATH, e)
        return None


if __name__ == "__main__":
    init_logger("prices")
    update_all_prices()
