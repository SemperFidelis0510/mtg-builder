#!/usr/bin/env python3
"""
Update the MTG card database end to end.

Run via: python -m src.lib.update_card_database  (or install.bat update).

Steps:
1. Force-download the latest MTGJSON AtomicCards.json into data/.
2. Refresh USD prices from Scryfall into data/prices.json.
3. Cleanly rebuild all ChromaDB semantic-search collections so removed
   or renamed cards do not leave stale rows behind.

The MCP server (server.bat) and deck editor (editor.bat) cache card JSON,
name indexes, and ChromaDB client handles in memory. Restart both after this
routine completes so they pick up the new data.
"""

from src.lib.build_rag import do_build_all
from src.lib.prices import update_all_prices
from src.lib.setup import do_download
from src.utils.logger import LOGGER, init_logger


def do_update() -> None:
    """Run the full card database update: download, prices, clean rebuild.

    Any failure in a step propagates as an exception; there is no partial
    'safe' state. Callers should ensure the MCP server and deck editor are
    stopped (or will be restarted) before/after invoking this.
    """
    LOGGER.info("update_card_database: starting")

    LOGGER.info("update_card_database: step 1/3 downloading AtomicCards.json (force=True)")
    do_download(force=True)

    LOGGER.info("update_card_database: step 2/3 refreshing prices from Scryfall")
    update_all_prices()

    LOGGER.info("update_card_database: step 3/3 rebuilding Chroma collections (clean=True)")
    do_build_all(clean=True)

    LOGGER.info(
        "update_card_database: complete. Restart the MCP server and deck editor "
        "to pick up the new card data."
    )


def main() -> None:
    init_logger("update_card_database")
    do_update()


if __name__ == "__main__":
    main()
