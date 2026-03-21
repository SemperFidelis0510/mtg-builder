"""Gemini-based deck building agent: tool definitions, conversation management, streaming chat."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from google import genai
from google.genai import types

from src.lib.cardDB import CardDB
from src.utils.logger import LOGGER

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
AGENT_DIR: Path = Path.home() / ".mtgbuilder" / "agent"
CONVERSATIONS_DIR: Path = AGENT_DIR / "conversations"
RULES_FILE: Path = AGENT_DIR / "rules.json"
KEY_FILE: Path = AGENT_DIR / ".key"
AGENT_PROMPT_PATH: Path = Path(__file__).resolve().parent.parent / "config" / "agent_prompt.md"

PRIMARY_MODEL: str = "gemini-2.5-pro"
FALLBACK_MODEL: str = "gemini-2.0-flash"

_resolved_model: str | None = None

# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


def load_api_key() -> str | None:
    """Read the Gemini API key from ~/.mtgbuilder/agent/.key.  Returns None if absent."""
    if not KEY_FILE.is_file():
        return None
    text: str = KEY_FILE.read_text(encoding="utf-8").strip()
    return text if text else None


def save_api_key(key: str) -> None:
    """Persist a Gemini API key to ~/.mtgbuilder/agent/.key."""
    assert key and key.strip(), "API key must be non-empty"
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.strip(), encoding="utf-8")
    LOGGER.info("Agent API key saved to %s", KEY_FILE)


# ---------------------------------------------------------------------------
# User rules CRUD
# ---------------------------------------------------------------------------


def _ensure_rules_file() -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not RULES_FILE.is_file():
        RULES_FILE.write_text(json.dumps({"rules": []}, indent=2), encoding="utf-8")


def load_user_rules() -> list[str]:
    """Return the list of user-configured agent rules."""
    _ensure_rules_file()
    data: dict = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return list(data["rules"])


def add_user_rule(rule: str) -> list[str]:
    """Append *rule* and return the updated list."""
    assert rule and rule.strip(), "Rule text must be non-empty"
    rules: list[str] = load_user_rules()
    rules.append(rule.strip())
    RULES_FILE.write_text(json.dumps({"rules": rules}, indent=2), encoding="utf-8")
    return rules


def delete_user_rule(index: int) -> list[str]:
    """Delete rule at *index* and return the updated list."""
    rules: list[str] = load_user_rules()
    if index < 0 or index >= len(rules):
        LOGGER.error("delete_user_rule: index %d out of range (len=%d)", index, len(rules))
        raise IndexError(f"Rule index {index} out of range (0..{len(rules) - 1})")
    rules.pop(index)
    RULES_FILE.write_text(json.dumps({"rules": rules}, indent=2), encoding="utf-8")
    return rules


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _load_predefined_prompt() -> str:
    if not AGENT_PROMPT_PATH.is_file():
        LOGGER.error("Predefined agent prompt not found: %s", AGENT_PROMPT_PATH)
        raise FileNotFoundError(f"Predefined agent prompt not found: {AGENT_PROMPT_PATH}")
    return AGENT_PROMPT_PATH.read_text(encoding="utf-8")


def _format_deck_summary(deck_state: dict) -> str:
    """Build a concise text summary of the current deck for the system prompt."""
    deck: dict = deck_state.get("deck") or deck_state
    name: str = deck.get("name") or "(unnamed)"
    fmt: str = deck.get("format") or "(none)"
    colors: list[str] = deck.get("colors") or []

    type_keys: list[str] = [
        "creature", "instant", "sorcery", "artifact",
        "enchantment", "planeswalker", "battle", "land",
    ]
    lines: list[str] = [
        "## Current Deck State",
        f"- **Name**: {name}",
        f"- **Format**: {fmt}",
        f"- **Colors**: {', '.join(colors) if colors else '(none)'}",
    ]
    total: int = 0
    for tk in type_keys:
        cards: list[str] = deck.get(tk) or []
        if cards:
            total += len(cards)
            lines.append(f"- **{tk.capitalize()}** ({len(cards)}): {', '.join(cards)}")
    lines.insert(3, f"- **Total cards**: {total}")

    maybe_names: list[str] = deck.get("maybe_names") or []
    if maybe_names:
        lines.append(f"- **Maybe board** ({len(maybe_names)}): {', '.join(maybe_names)}")
    sideboard_names: list[str] = deck.get("sideboard_names") or []
    if sideboard_names:
        lines.append(f"- **Sideboard** ({len(sideboard_names)}): {', '.join(sideboard_names)}")

    stats: dict | None = deck_state.get("stats")
    if stats:
        price: float = stats.get("total_price_usd") or 0.0
        if price > 0:
            lines.append(f"- **Estimated price**: ${price:.2f}")

    return "\n".join(lines)


def build_system_prompt(deck_state: dict) -> str:
    """Combine predefined prompt + user rules + deck state into one system instruction."""
    parts: list[str] = [_load_predefined_prompt()]

    rules: list[str] = load_user_rules()
    if rules:
        parts.append("\n## User Rules\n")
        for i, r in enumerate(rules, 1):
            parts.append(f"{i}. {r}")

    parts.append("\n" + _format_deck_summary(deck_state))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Gemini tool declarations (mirrors MCP tools from server.py)
# ---------------------------------------------------------------------------

_TOOL_DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name="semantic_search_card",
        description="Search for Magic: The Gathering cards by semantic meaning. Returns card names and rules text matching the query.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query describing the kind of cards you want."},
                "n_results": {"type": "integer", "description": "Number of results to return (default 5)."},
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="plain_search_card",
        description="Filter MTG cards by exact properties. All filters are AND-combined. Returns card names and rules text.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Substring match on card name."},
                "oracle_text": {"type": "string", "description": "Substring match on oracle text."},
                "type_line": {"type": "string", "description": "Substring match on type line."},
                "colors": {"type": "string", "description": "Comma-separated color letters (W,U,B,R,G). Cards must have all listed colors."},
                "color_identity": {"type": "string", "description": "Comma-separated color identity letters."},
                "mana_value": {"type": "number", "description": "Exact mana value."},
                "mana_value_min": {"type": "number", "description": "Minimum mana value."},
                "mana_value_max": {"type": "number", "description": "Maximum mana value."},
                "price_usd_min": {"type": "number", "description": "Minimum price in USD."},
                "price_usd_max": {"type": "number", "description": "Maximum price in USD."},
                "power": {"type": "string", "description": "Power value (e.g. '3', '*')."},
                "toughness": {"type": "string", "description": "Toughness value."},
                "keywords": {"type": "string", "description": "Comma-separated keywords (e.g. 'flying,trample')."},
                "subtype": {"type": "string", "description": "Card subtype (e.g. 'Elf', 'Goblin')."},
                "supertype": {"type": "string", "description": "Card supertype (e.g. 'Legendary')."},
                "format_legal": {"type": "string", "description": "Format legality (e.g. 'standard', 'commander')."},
                "n_results": {"type": "integer", "description": "Max number of results (default 20)."},
            },
            "required": [],
        },
    ),
    types.FunctionDeclaration(
        name="get_card_info",
        description="Get detailed data for one or more MTG cards by exact name. Returns a JSON array.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "card_names": {"type": "string", "description": "Comma-separated card names (e.g. 'Lightning Bolt, Counterspell')."},
                "fields": {"type": "string", "description": "Comma-separated field names to include (default: name,mana_cost,mana_value,type_line,text,colors,color_identity,power,toughness,keywords)."},
            },
            "required": ["card_names"],
        },
    ),
    types.FunctionDeclaration(
        name="extract_card_mechanics",
        description="Extract triggers or effects for a card by exact name.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "card_name": {"type": "string", "description": "Exact card name."},
                "extract_type": {"type": "string", "description": "'triggers' or 'effects'."},
            },
            "required": ["card_name", "extract_type"],
        },
    ),
    types.FunctionDeclaration(
        name="append_cards_to_deck",
        description="Add one or more cards to the user's currently loaded deck. Cards appear in the deck editor immediately.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "card_names": {"type": "string", "description": "Comma-separated card names to add (e.g. 'Sol Ring, Lightning Bolt')."},
                "board": {"type": "string", "description": "Which board to add to: 'main' (default), 'maybe', or 'sideboard'."},
            },
            "required": ["card_names"],
        },
    ),
    types.FunctionDeclaration(
        name="remove_cards_from_deck",
        description="Remove one or more cards from the user's currently loaded deck.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "card_names": {"type": "string", "description": "Comma-separated card names to remove."},
                "board": {"type": "string", "description": "Which board to remove from: 'main' (default), 'maybe', or 'sideboard'."},
                "count": {"type": "integer", "description": "How many copies to remove per card name (default 1)."},
            },
            "required": ["card_names"],
        },
    ),
    types.FunctionDeclaration(
        name="move_cards_in_deck",
        description="Move one or more cards between boards (main deck, maybe board, sideboard) in the user's currently loaded deck.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "card_names": {"type": "string", "description": "Comma-separated card names to move."},
                "from_board": {"type": "string", "description": "Source board: 'main', 'maybe', or 'sideboard'."},
                "to_board": {"type": "string", "description": "Destination board: 'main', 'maybe', or 'sideboard'."},
                "count": {"type": "integer", "description": "How many copies to move per card name (default 1)."},
            },
            "required": ["card_names", "from_board", "to_board"],
        },
    ),
    types.FunctionDeclaration(
        name="search_triggers",
        description="Find cards whose triggers (costs, conditions) semantically match the query.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic query for trigger search."},
                "n_results": {"type": "integer", "description": "Number of results (default 10)."},
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="search_effects",
        description="Find cards whose effects (outcomes they produce) semantically match the query.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic query for effect search."},
                "n_results": {"type": "integer", "description": "Number of results (default 10)."},
            },
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="search_online_decks",
        description=(
            "Search for MTG decklists on popular sites (Archidekt, DotGG, Moxfield, Spicerack, MTGGoldfish). "
            "Returns compact metadata (name, author, format, colors, URL)."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text search for deck name or archetype (e.g. 'burn', 'atraxa superfriends')."},
                "format": {"type": "string", "description": "MTG format filter (standard, modern, pioneer, commander, legacy, vintage, pauper)."},
                "colors": {"type": "string", "description": "Comma-separated color letters (W,U,B,R,G)."},
                "commander": {"type": "string", "description": "Commander card name (Archidekt only)."},
                "source": {"type": "string", "description": "Limit to one site: archidekt, dotgg, moxfield, spicerack, mtggoldfish. Empty for all."},
                "n_results": {"type": "integer", "description": "Max results per source (default 10)."},
            },
            "required": [],
        },
    ),
    types.FunctionDeclaration(
        name="get_online_deck",
        description="Fetch full card list of an MTG deck from a supported site by URL. Returns mainboard and sideboard.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full deck URL from Archidekt, DotGG, Moxfield, or MTGGoldfish."},
            },
            "required": ["url"],
        },
    ),
    types.FunctionDeclaration(
        name="import_online_deck",
        description="Import an MTG deck from a supported site URL into the current deck editor session, replacing the current deck.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full deck URL from Archidekt, DotGG, Moxfield, or MTGGoldfish."},
            },
            "required": ["url"],
        },
    ),
]

GEMINI_TOOL: types.Tool = types.Tool(function_declarations=_TOOL_DECLARATIONS)


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

_DEFAULT_CARD_FIELDS: str = "name,mana_cost,mana_value,type_line,text,colors,color_identity,power,toughness,keywords"


def execute_tool_call(name: str, args: dict[str, Any]) -> str:
    """Execute a tool call by name, return the result as a string."""
    LOGGER.info("Agent tool call: %s args=%s", name, args)
    try:
        if name == "semantic_search_card":
            return CardDB.inst().search_cards(
                query=args["query"],
                n_results=int(args.get("n_results") or 5),
            )
        if name == "plain_search_card":
            return CardDB.inst().filter_cards(
                name=args.get("name") or "",
                oracle_text=args.get("oracle_text") or "",
                type_line=args.get("type_line") or "",
                colors=args.get("colors") or "",
                color_identity=args.get("color_identity") or "",
                mana_value=float(args["mana_value"]) if "mana_value" in args and args["mana_value"] is not None else -1.0,
                mana_value_min=float(args["mana_value_min"]) if "mana_value_min" in args and args["mana_value_min"] is not None else -1.0,
                mana_value_max=float(args["mana_value_max"]) if "mana_value_max" in args and args["mana_value_max"] is not None else -1.0,
                price_usd_min=float(args["price_usd_min"]) if "price_usd_min" in args and args["price_usd_min"] is not None else -1.0,
                price_usd_max=float(args["price_usd_max"]) if "price_usd_max" in args and args["price_usd_max"] is not None else -1.0,
                power=args.get("power") or "",
                toughness=args.get("toughness") or "",
                keywords=args.get("keywords") or "",
                subtype=args.get("subtype") or "",
                supertype=args.get("supertype") or "",
                format_legal=args.get("format_legal") or "",
                n_results=int(args.get("n_results") or 20),
            )
        if name == "get_card_info":
            card_names: str = args["card_names"]
            names: list[str] = [n.strip() for n in card_names.split(",") if n.strip()]
            fields_str: str = args.get("fields") or _DEFAULT_CARD_FIELDS
            card_fields: list[str] = [f.strip() for f in fields_str.split(",") if f.strip()]
            return CardDB.inst().get_cards_info(names=names, card_fields=card_fields)
        if name == "extract_card_mechanics":
            return CardDB.inst().get_card_mechanics(
                name=args["card_name"],
                extract_type=args["extract_type"],
            )
        if name == "append_cards_to_deck":
            card_names_str: str = args["card_names"]
            names_list: list[str] = [n.strip() for n in card_names_str.split(",") if n.strip()]
            if not names_list:
                return "Error: card_names must contain at least one card name."
            board: str = args["board"] if "board" in args and args["board"] else "main"
            from src.deck_editor.app import _current_deck, _notify_deck_updated, _get_board_list, _recompute_and_set_colors
            from src.obj.deck import _cards_from_names
            target_list = _get_board_list(_current_deck, board)
            cards_to_append = _cards_from_names(names_list)
            for card in cards_to_append:
                target_list.append(card)
            _recompute_and_set_colors(_current_deck)
            _notify_deck_updated()
            board_label: str = "main deck" if board == "main" else f"{board} board"
            return f"Added {len(names_list)} card(s) to the {board_label}: {', '.join(names_list)}."
        if name == "remove_cards_from_deck":
            card_names_str = args["card_names"]
            names_list = [n.strip() for n in card_names_str.split(",") if n.strip()]
            if not names_list:
                return "Error: card_names must contain at least one card name."
            board = args["board"] if "board" in args and args["board"] else "main"
            count: int = int(args["count"]) if "count" in args and args["count"] is not None else 1
            from src.deck_editor.app import _current_deck, _notify_deck_updated, _get_board_list, _recompute_and_set_colors
            target_list = _get_board_list(_current_deck, board)
            not_found: list[str] = []
            for card_name in names_list:
                name_lower: str = card_name.strip().lower()
                removed: int = 0
                for _ in range(count):
                    idx: int | None = None
                    for i, c in enumerate(target_list):
                        if c.name.lower() == name_lower:
                            idx = i
                            break
                    if idx is None:
                        break
                    target_list.pop(idx)
                    removed += 1
                if removed == 0:
                    not_found.append(card_name)
            if not_found:
                return f"Error: card(s) not found in {board} board: {', '.join(not_found)}"
            _recompute_and_set_colors(_current_deck)
            _notify_deck_updated()
            board_label = "main deck" if board == "main" else f"{board} board"
            return f"Removed {len(names_list)} card(s) from the {board_label}: {', '.join(names_list)}."
        if name == "move_cards_in_deck":
            card_names_str = args["card_names"]
            names_list = [n.strip() for n in card_names_str.split(",") if n.strip()]
            if not names_list:
                return "Error: card_names must contain at least one card name."
            from_board: str = args["from_board"]
            to_board: str = args["to_board"]
            count = int(args["count"]) if "count" in args and args["count"] is not None else 1
            if from_board == to_board:
                return f"Error: from_board and to_board must differ (both are {from_board!r})"
            from src.deck_editor.app import _current_deck, _notify_deck_updated, _get_board_list, _recompute_and_set_colors
            source_list = _get_board_list(_current_deck, from_board)
            dest_list = _get_board_list(_current_deck, to_board)
            not_found = []
            for card_name in names_list:
                name_lower = card_name.strip().lower()
                moved: int = 0
                for _ in range(count):
                    idx = None
                    for i, c in enumerate(source_list):
                        if c.name.lower() == name_lower:
                            idx = i
                            break
                    if idx is None:
                        break
                    dest_list.append(source_list.pop(idx))
                    moved += 1
                if moved == 0:
                    not_found.append(card_name)
            if not_found:
                return f"Error: card(s) not found in {from_board} board: {', '.join(not_found)}"
            _recompute_and_set_colors(_current_deck)
            _notify_deck_updated()
            from_label: str = "main deck" if from_board == "main" else f"{from_board} board"
            to_label: str = "main deck" if to_board == "main" else f"{to_board} board"
            return f"Moved {len(names_list)} card(s) from {from_label} to {to_label}: {', '.join(names_list)}."
        if name == "search_triggers":
            return CardDB.inst().search_triggers(
                query=args["query"],
                n_results=int(args.get("n_results") or 10),
            )
        if name == "search_effects":
            return CardDB.inst().search_effects(
                query=args["query"],
                n_results=int(args.get("n_results") or 10),
            )
        if name == "search_online_decks":
            from src.lib.deck_search import search_decks as _search_decks
            return _search_decks(
                query=args.get("query") or "",
                format=args.get("format") or "",
                colors=args.get("colors") or "",
                commander=args.get("commander") or "",
                source=args.get("source") or "",
                n_results=int(args.get("n_results") or 10),
            )
        if name == "get_online_deck":
            from src.lib.deck_search import get_deck as _get_deck
            return _get_deck(url=args["url"])
        if name == "import_online_deck":
            from src.lib.deck_search import get_deck_as_card_list
            from src.deck_editor.app import _current_deck, _notify_deck_updated, _compute_deck_card_colors
            from src.obj.deck import Deck

            mainboard_raw, sideboard_raw = get_deck_as_card_list(url=args["url"])
            main_names: list[str] = []
            for cname, qty in mainboard_raw.items():
                for _ in range(qty):
                    main_names.append(cname)
            sb_names: list[str] = []
            for cname, qty in sideboard_raw.items():
                for _ in range(qty):
                    sb_names.append(cname)

            new_deck: Deck = Deck()
            new_deck.add_cards(main_names)
            if sb_names:
                from src.obj.deck import _cards_from_names
                new_deck.sideboard = _cards_from_names(sb_names)

            _current_deck.cards = new_deck.cards
            _current_deck.sideboard = new_deck.sideboard
            card_colors = _compute_deck_card_colors(_current_deck)
            _current_deck.colors = list(card_colors)
            _notify_deck_updated()
            return f"Imported deck with {len(main_names)} mainboard and {len(sb_names)} sideboard cards from {args['url']}."
        LOGGER.error("Unknown agent tool: %s", name)
        raise ValueError(f"Unknown tool: {name}")
    except Exception as e:
        LOGGER.error("Agent tool execution error for %s: %s", name, e)
        return f"Error executing {name}: {e}"


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------


def _ensure_conversations_dir() -> None:
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def create_conversation() -> dict:
    """Create and persist a new empty conversation. Returns the conversation dict."""
    _ensure_conversations_dir()
    conv_id: str = str(uuid.uuid4())
    now: str = datetime.now(timezone.utc).isoformat()
    conv: dict = {
        "id": conv_id,
        "title": "New conversation",
        "created_at": now,
        "updated_at": now,
        "model": _resolved_model or PRIMARY_MODEL,
        "messages": [],
    }
    (CONVERSATIONS_DIR / f"{conv_id}.json").write_text(json.dumps(conv, indent=2), encoding="utf-8")
    return conv


def list_conversations() -> list[dict]:
    """Return metadata for all saved conversations, newest first."""
    _ensure_conversations_dir()
    result: list[dict] = []
    for p in CONVERSATIONS_DIR.glob("*.json"):
        try:
            data: dict = json.loads(p.read_text(encoding="utf-8"))
            result.append({
                "id": data["id"],
                "title": data["title"],
                "created_at": data["created_at"],
                "updated_at": data["updated_at"],
                "message_count": len(data["messages"]),
            })
        except Exception as e:
            LOGGER.warning("Skipping corrupt conversation file %s: %s", p, e)
    result.sort(key=lambda c: c["updated_at"], reverse=True)
    return result


def load_conversation(conv_id: str) -> dict:
    """Load a conversation by ID. Raises FileNotFoundError if not found."""
    path: Path = CONVERSATIONS_DIR / f"{conv_id}.json"
    if not path.is_file():
        LOGGER.error("Conversation not found: %s", conv_id)
        raise FileNotFoundError(f"Conversation not found: {conv_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_conversation(conv: dict) -> None:
    """Persist a conversation dict to disk."""
    _ensure_conversations_dir()
    conv_id: str = conv["id"]
    conv["updated_at"] = datetime.now(timezone.utc).isoformat()
    (CONVERSATIONS_DIR / f"{conv_id}.json").write_text(json.dumps(conv, indent=2), encoding="utf-8")


def delete_conversation(conv_id: str) -> None:
    """Delete a conversation by ID. Raises FileNotFoundError if not found."""
    path: Path = CONVERSATIONS_DIR / f"{conv_id}.json"
    if not path.is_file():
        LOGGER.error("Conversation not found for deletion: %s", conv_id)
        raise FileNotFoundError(f"Conversation not found: {conv_id}")
    path.unlink()
    LOGGER.info("Deleted conversation %s", conv_id)


# ---------------------------------------------------------------------------
# Gemini contents conversion
# ---------------------------------------------------------------------------


def _messages_to_contents(messages: list[dict]) -> list[types.Content]:
    """Convert our stored messages to Gemini Content objects."""
    contents: list[types.Content] = []
    for msg in messages:
        role: str = msg["role"]
        if role == "user":
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=msg["content"])],
            ))
        elif role == "assistant":
            tool_calls: list[dict] = msg.get("tool_calls") or []
            if tool_calls:
                fc_parts: list[types.Part] = [
                    types.Part.from_function_call(name=tc["name"], args=tc["args"])
                    for tc in tool_calls
                ]
                contents.append(types.Content(role="model", parts=fc_parts))
                fr_parts: list[types.Part] = [
                    types.Part.from_function_response(name=tc["name"], response={"result": tc["result"]})
                    for tc in tool_calls
                ]
                contents.append(types.Content(role="tool", parts=fr_parts))
            text: str = msg.get("content") or ""
            if text:
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))
    return contents


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def _get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def _is_model_not_found(exc: Exception) -> bool:
    """Return True if the exception indicates the model doesn't exist (404), as opposed to quota/auth/other errors."""
    msg: str = str(exc).lower()
    return "404" in msg or "not found" in msg or "not_found" in msg


def resolve_model() -> str:
    """Return the cached resolved model, or PRIMARY_MODEL if not yet resolved."""
    return _resolved_model or PRIMARY_MODEL


def set_resolved_model(model_name: str) -> None:
    """Cache the resolved model after a successful API call."""
    global _resolved_model
    if _resolved_model != model_name:
        _resolved_model = model_name
        LOGGER.info("Resolved agent model: %s", model_name)


def get_resolved_model() -> str | None:
    """Return the cached resolved model name, or None if not yet resolved."""
    return _resolved_model


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------

MAX_TOOL_ROUNDS: int = 10


async def chat_stream(
    conv: dict,
    user_message: str,
    deck_state: dict,
) -> AsyncGenerator[dict, None]:
    """Stream agent response. Yields dicts with keys: type, and type-specific data.

    Event types:
      text_delta  -> {"type": "text_delta", "content": "..."}
      tool_call   -> {"type": "tool_call", "name": "...", "args": {...}}
      tool_result -> {"type": "tool_result", "name": "...", "result": "..."}
      done        -> {"type": "done", "conversation_id": "...", "model": "..."}
      error       -> {"type": "error", "message": "..."}
    """
    api_key: str | None = load_api_key()
    if not api_key:
        yield {"type": "error", "message": "No API key configured."}
        return

    model_name: str = resolve_model()
    client: genai.Client = _get_client(api_key)

    conv["messages"].append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    if len(conv["messages"]) == 1:
        conv["title"] = user_message[:60].strip() or "New conversation"

    system_prompt: str = build_system_prompt(deck_state)
    contents: list[types.Content] = _messages_to_contents(conv["messages"])

    config: types.GenerateContentConfig = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=[GEMINI_TOOL],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    accumulated_text: str = ""
    all_tool_calls: list[dict] = []

    for _round in range(MAX_TOOL_ROUNDS):
        function_calls_this_round: list[types.FunctionCall] = []
        model_content_parts: list[types.Part] = []
        last_candidate_content: types.Content | None = None

        try:
            async for chunk in await client.aio.models.generate_content_stream(
                model=model_name,
                contents=contents,
                config=config,
            ):
                if chunk.text:
                    accumulated_text += chunk.text
                    yield {"type": "text_delta", "content": chunk.text}

                if chunk.candidates:
                    for candidate in chunk.candidates:
                        if candidate.content:
                            last_candidate_content = candidate.content

            if last_candidate_content and last_candidate_content.parts:
                for part in last_candidate_content.parts:
                    if part.function_call:
                        function_calls_this_round.append(part.function_call)
                        model_content_parts.append(part)

            set_resolved_model(model_name)
        except Exception as e:
            if _round == 0 and model_name == PRIMARY_MODEL and _is_model_not_found(e):
                LOGGER.warning("Model %s not found, falling back to %s", model_name, FALLBACK_MODEL)
                model_name = FALLBACK_MODEL
                continue
            LOGGER.error("Gemini streaming error: %s", e)
            err_str: str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                yield {"type": "error", "message": "Gemini API rate limit exceeded. Please wait a minute and try again, or check your API key quota at https://ai.dev/rate-limit"}
            else:
                yield {"type": "error", "message": f"Gemini API error: {err_str}"}
            conv["messages"].pop()
            return

        if not function_calls_this_round:
            break

        contents.append(types.Content(role="model", parts=model_content_parts))

        fr_parts: list[types.Part] = []
        for fc in function_calls_this_round:
            fc_name: str = fc.name
            fc_args: dict = dict(fc.args) if fc.args else {}
            yield {"type": "tool_call", "name": fc_name, "args": fc_args}

            result_str: str = execute_tool_call(fc_name, fc_args)
            yield {"type": "tool_result", "name": fc_name, "result": result_str}

            all_tool_calls.append({"name": fc_name, "args": fc_args, "result": result_str})
            fr_parts.append(types.Part.from_function_response(
                name=fc_name,
                response={"result": result_str},
            ))

        contents.append(types.Content(role="tool", parts=fr_parts))

    conv["messages"].append({
        "role": "assistant",
        "content": accumulated_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_calls": all_tool_calls if all_tool_calls else None,
    })
    conv["model"] = model_name
    save_conversation(conv)

    yield {"type": "done", "conversation_id": conv["id"], "model": model_name}
