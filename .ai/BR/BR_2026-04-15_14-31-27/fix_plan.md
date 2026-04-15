# Fix Plan

## Bug Summary

The user reported that tool calls in the agent chat "still show all arguments when collapsed." The screenshot confirms: a collapsed `plain_search_card` tool call displays its full multi-line `Result:` text (15 cards with full details) beneath the header, and the header summary itself enumerates every filter argument. Collapsing a tool call should reduce it to a compact single-line summary; instead the result text and verbose header make it look like nothing was hidden.

## Root Cause

Two issues in the tool-call collapse mechanism:

### 1. Result text always visible (primary)

In [`src/deck_editor/js/agent-chat.js`](src/deck_editor/js/agent-chat.js) (`_appendToolCall`, lines 360-365), the `.agent-tool-call-outcome` element is a sibling of `.agent-tool-call-details-wrap`, not a child. The CSS collapse rules in [`src/deck_editor/styles/deck-editor.css`](src/deck_editor/styles/deck-editor.css) only toggle `.agent-tool-call-details-wrap` (lines 1486-1497). The `.agent-tool-call-outcome` has no hide/show rules at all, so the full `Result: ...` text is always rendered regardless of collapse state.

**DOM structure today:**
```
.agent-tool-call
  ├── .agent-tool-call-header       (always visible -- summary label)
  ├── .agent-tool-call-toolbar      (approval buttons, if pending)
  ├── .agent-tool-call-outcome      <-- ALWAYS VISIBLE (the bug)
  └── .agent-tool-call-details-wrap <-- hidden when collapsed (args <dl>)
```

### 2. Header summary includes all arguments (secondary)

`format_tool_call_summary` in [`src/deck_editor/agent.py`](src/deck_editor/agent.py) (lines 376-447) concatenates every filter into the summary. For `plain_search_card`, this produces lines like:

> Search cards (oracle text filter, identity W, semantic: "payoff for gaining life or excellent lifegain enabler" (general), legal in standard), up to 15 results.

For `append_cards_to_deck` with many cards, it lists every card name.

## Proposed Fix

### Fix 1 -- Hide outcome when collapsed (CSS)

In `src/deck_editor/styles/deck-editor.css`, add `display: none` to `.agent-tool-call-outcome` by default, and show it only when the parent has `.expanded`:

**File:** `src/deck_editor/styles/deck-editor.css`, lines 1477-1485

Change:
```css
.agent-tool-call-outcome {
  padding: 0.35rem 0.6rem 0.5rem;
  background: var(--panel);
  color: #a8a8a8;
  font-size: 0.78rem;
  line-height: 1.4;
  white-space: pre-wrap;
  font-family: inherit;
}
```

To:
```css
.agent-tool-call-outcome {
  display: none;
  padding: 0.35rem 0.6rem 0.5rem;
  background: var(--panel);
  color: #a8a8a8;
  font-size: 0.78rem;
  line-height: 1.4;
  white-space: pre-wrap;
  font-family: inherit;
}
.agent-tool-call.expanded .agent-tool-call-outcome {
  display: block;
}
```

This follows the exact same pattern already used for `.agent-tool-call-details-wrap` (lines 1486-1497).

### Fix 2 -- Shorten header summaries for verbose tools (Python)

In `src/deck_editor/agent.py`, `format_tool_call_summary`:

- **`append_cards_to_deck` / `remove_cards_from_deck` / `move_cards_in_deck`:** When the card list exceeds a threshold (e.g. 4 unique names), show a count instead of listing every name. Example: `"Add to main deck: 24 cards"` instead of `"Add to main deck: Ajani's Pridemate, Ajani's Pridemate, Ajani's Pridemate, ..."`.
- **`plain_search_card`:** Cap the summary at a maximum of ~3 filter fragments. If there are more, append `+ N more filters` instead of listing all. Example: `"Search cards (oracle text filter, identity W, + 2 more filters), up to 15 results."`.
- **`get_card_info`:** When the card names string is very long, truncate after 3 names and append `+ N more`.

## Risks & Side Effects

- **Fix 1** changes the default collapsed appearance for all tool calls. Users who relied on reading results without clicking will now need to expand. This is the intended behavior.
- **Fix 2** reduces information density in the summary. Users who want full details can expand the tool call to see the `<dl>` args and result text.
- No server-side logic changes are required for Fix 1. Fix 2 only changes the summary string generator, which is display-only and not persisted in conversation history (the full `args` dict is persisted separately).

## Verification

1. Start the deck editor (`main.bat deck-editor`).
2. Open the agent chat and send a query that triggers `plain_search_card`, `search_triggers`, and `append_cards_to_deck` (e.g. "Build me a lifegain deck in standard").
3. After tool calls complete, verify:
   - **Collapsed (default):** only the one-line header summary is visible; no result text or args details are shown.
   - **Expanded (click header):** chevron rotates, full args `<dl>` and `Result:` text appear.
   - **Re-collapse (click header again):** result and args hide again.
4. Load a previous conversation from the sidebar and verify the same collapsed/expanded behavior.
5. Verify approval-requiring tools (`append_cards_to_deck`) still show the approval toolbar when pending, and hide it after approval.
