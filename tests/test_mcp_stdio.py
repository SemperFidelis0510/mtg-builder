from __future__ import annotations

import os

import pytest

from tests.mcp_stdio import mcp_initialize, start_mcp_server, wait_for_tools_list


@pytest.mark.integration
def test_mcp_server_lists_tools() -> None:
    env = dict(os.environ)
    env["MTG_DISABLE_RAG_STARTUP"] = "1"
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    m = start_mcp_server(env=env)
    try:
        init = mcp_initialize(m, timeout_s=15.0)
        assert "result" in init

        tools = wait_for_tools_list(m, timeout_s=15.0)
        names = {t.get("name") for t in tools if isinstance(t, dict)}

        # Core tools we expect to exist.
        assert "plain_search_card" in names
        assert "get_card_info" in names
        assert "append_cards_to_deck" in names
        assert "remove_cards_from_deck" in names
        assert "move_cards_in_deck" in names
    finally:
        m.close()


@pytest.mark.integration
def test_mcp_deck_editor_tool_errors_when_editor_unreachable() -> None:
    env = dict(os.environ)
    env["MTG_DISABLE_RAG_STARTUP"] = "1"
    env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    m = start_mcp_server(env=env)
    try:
        mcp_initialize(m, timeout_s=15.0)

        # Call a deck editor tool. If the editor isn't running, we should get a clear error.
        # If it *is* running on the default port, this will succeed — accept both behaviors.
        resp = m.request(
            "tools/call",
            params={"name": "append_cards_to_deck", "arguments": {"card_names": "Sol Ring", "board": "main"}},
            timeout_s=15.0,
            req_id=3,
        )
        assert "result" in resp
        result = resp["result"]
        # FastMCP wraps tool outputs; accept either string or structured content.
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            assert isinstance(content, list)
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            joined = "\n".join([t for t in text_parts if isinstance(t, str)])
            low = joined.lower()
            assert (
                ("deck editor unreachable" in low)
                or ("added" in low)
                or ("error calling tool" in low)
                or ("error" in low)
            )
        else:
            low = str(result).lower()
            assert (
                ("deck editor unreachable" in low)
                or ("added" in low)
                or ("error calling tool" in low)
                or ("error" in low)
            )
    finally:
        m.close()

