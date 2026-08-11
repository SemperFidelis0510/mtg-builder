"""Shared constants for the MTG MCP project."""

from pathlib import Path

from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Paths and model configuration
# ---------------------------------------------------------------------------
_MODULE_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = _MODULE_DIR.parent.parent
DATA_DIR: Path = REPO_ROOT / "data"
DECK_EDITOR_SAVE_DIR: Path = REPO_ROOT / "decks"
DECK_EDITOR_BASE_URL: str = "http://127.0.0.1:8000"
GRAPHRAG_DIR: Path = DATA_DIR / "graphrag"
GRAPHRAG_INPUT_DIR: Path = GRAPHRAG_DIR / "output"
GRAPHRAG_LANCEDB_DIR: Path = GRAPHRAG_INPUT_DIR / "lancedb"
GRAPHRAG_SETTINGS_PATH: Path = GRAPHRAG_DIR / "settings.yaml"
GRAPHRAG_MANIFEST_PATH: Path = GRAPHRAG_INPUT_DIR / "manifest.json"
GRAPHRAG_COMPLETION_MODEL: str = "gemini-3.1-flash-lite"
GRAPHRAG_EMBEDDING_MODEL: str = "gemini-embedding-001"
GEMINI_API_KEY_PATH: Path = Path.home() / ".mtgbuilder" / "agent" / ".key"
GEMINI_API_KEY_URL: str = "https://aistudio.google.com/apikey"
ATOMIC_CARDS_PATH: Path = DATA_DIR / "AtomicCards.json"
PRICES_PATH: Path = DATA_DIR / "prices.json"
CARD_FACES_DIR: Path = DATA_DIR / "faces"
CONFIG_DIR: Path = REPO_ROOT / "src" / "config"
THRESHOLDS_INI_PATH: Path = CONFIG_DIR / "thresholds.ini"
KEYWORD_EXPLANATIONS_PATH: Path = CONFIG_DIR / "keyword_explanations.json"
DECK_SITES_KEYS_PATH: Path = CONFIG_DIR / "deck_sites_keys.json"


def load_required_gemini_api_key() -> str:
    """Load the shared Gemini key for operations that require model access."""
    if not GEMINI_API_KEY_PATH.is_file():
        LOGGER.error("Gemini API key file not found: %s", GEMINI_API_KEY_PATH)
        raise FileNotFoundError(f"Gemini API key file not found: {GEMINI_API_KEY_PATH}")
    key = GEMINI_API_KEY_PATH.read_text(encoding="utf-8").strip()
    if not key:
        LOGGER.error("Gemini API key file is empty: %s", GEMINI_API_KEY_PATH)
        raise ValueError(f"Gemini API key file is empty: {GEMINI_API_KEY_PATH}")
    return key


def load_optional_gemini_api_key() -> str | None:
    """Return the saved Gemini key, or None when it is absent or empty."""
    if not GEMINI_API_KEY_PATH.is_file():
        return None
    key = GEMINI_API_KEY_PATH.read_text(encoding="utf-8").strip()
    return key if key else None


def save_gemini_api_key(key: str) -> None:
    """Persist the shared Gemini key, creating the agent directory if needed."""
    stripped = key.strip()
    if not stripped:
        LOGGER.error("save_gemini_api_key: refusing to save an empty key")
        raise ValueError("Gemini API key must be non-empty")
    GEMINI_API_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_API_KEY_PATH.write_text(stripped, encoding="utf-8")
    LOGGER.info("Gemini API key saved to %s", GEMINI_API_KEY_PATH)


_MTGJSON_LEGALITY_ALIASES: dict[str, str] = {
    "historicbrawl": "brawl",
}


def mtgjson_legality_key(format_name: str) -> str:
    """Return the normalized MTGJSON legality key for an application format."""
    normalized: str = format_name.strip().lower()
    if normalized in _MTGJSON_LEGALITY_ALIASES:
        return _MTGJSON_LEGALITY_ALIASES[normalized]
    return normalized
