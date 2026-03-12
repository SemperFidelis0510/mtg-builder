"""Shared constants for the MTG MCP project."""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and model configuration
# ---------------------------------------------------------------------------
_MODULE_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = _MODULE_DIR.parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
DECK_EDITOR_SAVE_DIR: Path = REPO_ROOT / "decks"
DECK_EDITOR_BASE_URL: str = "http://127.0.0.1:8000"
CHROMA_PATH: Path = REPO_ROOT / "chroma_db"
MODEL_NAME: str = "all-MiniLM-L6-v2"
COLLECTION_NAME: str = "mtg_cards"
TRIGGERS_COLLECTION_NAME: str = "mtg_triggers"
EFFECTS_COLLECTION_NAME: str = "mtg_effects"
ATOMIC_CARDS_PATH: Path = DATA_DIR / "AtomicCards.json"
PRICES_PATH: Path = DATA_DIR / "prices.json"
CONFIG_DIR: Path = REPO_ROOT / "src" / "config"
THRESHOLDS_INI_PATH: Path = CONFIG_DIR / "thresholds.ini"
KEYWORD_EXPLANATIONS_PATH: Path = CONFIG_DIR / "keyword_explanations.json"
