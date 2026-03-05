"""Shared constants for the MTG MCP project."""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and model configuration
# ---------------------------------------------------------------------------
_MODULE_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = _MODULE_DIR.parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
CHROMA_PATH: Path = REPO_ROOT / "chroma_db"
MODEL_NAME: str = "all-MiniLM-L6-v2"
COLLECTION_NAME: str = "mtg_cards"
ATOMIC_CARDS_PATH: Path = DATA_DIR / "AtomicCards.json"
