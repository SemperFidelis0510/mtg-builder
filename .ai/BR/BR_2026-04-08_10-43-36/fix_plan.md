# Fix Plan

## Bug Summary

Right-clicking a card in the main deck shows "Move to main deck" (in addition to the correct "Move to maybe board"). Similarly, right-clicking a card in the maybe board shows "Move to maybe board" (in addition to "Move to main deck"). Both board-move buttons are always visible regardless of which board the card is on.

## Root Cause

The JavaScript in `src/deck_editor/js/context-menu.js` correctly sets `button.hidden = true/false` in `showMenu()` (lines 41-42) based on the card's current board context. However, the CSS in `src/deck_editor/styles/deck-editor.css` (lines 1127-1137) defeats this:

```css
#cardContextMenu .context-menu-item {
  display: block;
  /* ... */
}
```

The browser's built-in rule for the `hidden` attribute is:
```css
[hidden] { display: none; }
```

The selector `#cardContextMenu .context-menu-item` (specificity: 1 ID + 1 class = 0-1-1-0) has higher specificity than `[hidden]` (specificity: 1 attribute = 0-0-1-0). The explicit `display: block` always wins, so the `hidden` attribute has no visual effect — both move buttons are always shown.

## Proposed Fix

**File: `src/deck_editor/styles/deck-editor.css` (after line 1137)**

Add a rule that explicitly hides context menu items with the `hidden` attribute, using a selector with sufficient specificity to override the existing rule:

```css
#cardContextMenu .context-menu-item[hidden] {
  display: none;
}
```

This selector has specificity 0-1-2-0 (1 ID + 1 class + 1 attribute), which beats the existing 0-1-1-0 rule.

No JavaScript changes needed — the `showMenu()` logic is already correct.

## Risks & Side Effects

- None. The new rule only applies when `hidden` is present on a `.context-menu-item` inside `#cardContextMenu`. All other context menu items (Copy card name, Extract triggers, etc.) do not use the `hidden` attribute and are unaffected.

## Verification

1. Right-click a card in the **main deck** — confirm "Move to maybe board" is shown, "Move to main deck" is **not** shown.
2. Right-click a card in the **maybe board** — confirm "Move to main deck" is shown, "Move to maybe board" is **not** shown.
3. Right-click a card in the **sideboard** or **commander** slot — confirm neither move button is shown (both hidden, since `getStackBoardContext` returns `null` for those boards).
