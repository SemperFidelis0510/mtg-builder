"""FastAPI routes for the Gemini deck-building agent."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.deck_editor.agent import (
    add_user_rule,
    chat_stream,
    create_conversation,
    delete_conversation,
    delete_user_rule,
    get_resolved_model,
    list_conversations,
    load_api_key,
    load_conversation,
    load_user_rules,
    save_api_key,
)
from src.utils.logger import LOGGER

agent_router = APIRouter(prefix="/api/agent")


def _get_deck_state() -> dict:
    """Import deck state from app module (lazy to avoid circular imports)."""
    from src.deck_editor.app import _current_deck, _deck_to_response

    return _deck_to_response(_current_deck)


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------


@agent_router.get("/key/status")
async def key_status() -> dict:
    """Return whether an API key is configured and the resolved model name."""
    has_key: bool = load_api_key() is not None
    return {"has_key": has_key, "model": get_resolved_model()}


@agent_router.post("/key")
async def save_key(body: dict) -> dict:
    """Save a Gemini API key."""
    if "key" not in body or not isinstance(body["key"], str) or not body["key"].strip():
        raise HTTPException(status_code=400, detail="'key' must be a non-empty string")
    save_api_key(body["key"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# User rules
# ---------------------------------------------------------------------------


@agent_router.get("/rules")
async def get_rules() -> dict:
    """Return the user-configured agent rules."""
    return {"rules": load_user_rules()}


@agent_router.post("/rules")
async def add_rule(body: dict) -> dict:
    """Add a new user rule."""
    if "rule" not in body or not isinstance(body["rule"], str) or not body["rule"].strip():
        raise HTTPException(status_code=400, detail="'rule' must be a non-empty string")
    rules: list[str] = add_user_rule(body["rule"])
    return {"rules": rules}


@agent_router.delete("/rules/{index}")
async def remove_rule(index: int) -> dict:
    """Delete a user rule by index."""
    try:
        rules: list[str] = delete_user_rule(index)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"rules": rules}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@agent_router.get("/conversations")
async def get_conversations() -> dict:
    """List all saved conversations (metadata only)."""
    return {"conversations": list_conversations()}


@agent_router.post("/conversation")
async def new_conversation() -> dict:
    """Create and return a new empty conversation."""
    return create_conversation()


@agent_router.get("/conversation/{conv_id}")
async def get_conversation(conv_id: str) -> dict:
    """Load a full conversation by ID."""
    try:
        return load_conversation(conv_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@agent_router.delete("/conversation/{conv_id}")
async def remove_conversation(conv_id: str) -> dict:
    """Delete a conversation by ID."""
    try:
        delete_conversation(conv_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"ok": True}


# ---------------------------------------------------------------------------
# Streaming chat
# ---------------------------------------------------------------------------


@agent_router.post("/chat")
async def agent_chat(request: Request) -> StreamingResponse:
    """Send a message and receive a streaming SSE response."""
    try:
        body: dict = await request.json()
    except Exception as e:
        LOGGER.error("agent_chat: invalid JSON body: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None

    conversation_id: str | None = body.get("conversation_id")
    message: str | None = body.get("message")

    if not message or not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=400, detail="'message' must be a non-empty string")

    if conversation_id:
        try:
            conv: dict = load_conversation(conversation_id)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    else:
        conv = create_conversation()

    deck_state: dict = _get_deck_state()

    async def stream():
        async for event in chat_stream(conv, message.strip(), deck_state):
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
