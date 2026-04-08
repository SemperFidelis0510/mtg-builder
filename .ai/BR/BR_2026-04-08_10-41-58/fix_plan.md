# Fix Plan

## Bug Summary

When importing a Brawl/Historic Brawl deck from Arena export text, the commander card is not separated into the commander slot. Instead, it is treated as a regular main-deck card. The log confirms: after `load_deck` the deck had `commander='A-Vivi Ornitier'` with 99 cards, but after `import_deck` via `from_export_text` it had 100 cards and no commander. Arena exports for Brawl decks include a `Commander` section header before the commander card line, but the parser does not recognize it.

## Root Cause

`Deck.from_export_text()` in `src/obj/deck.py` (lines 447-492, the `arena` format branch) only recognizes two section headers:

- `Deck` — skipped (cards go to main by default)
- `Sideboard` / `Sideboard:` — sets `parsing_sideboard = True`

Arena Brawl exports have a third section:

```
Commander
1 A-Vivi Ornitier (SLD) 1478

Deck
1 Card A (SET) 123
...
```

The bare `Commander` header line has only one token after `split(None, 1)`, so `len(parts) < 2` is true and the line is silently skipped (line 464). The card line following it (`1 A-Vivi Ornitier ...`) is then parsed as a normal main-deck card. No `commander` name is ever passed to the `cls(...)` constructor (line 492), so `deck.commander` stays `""`.

The `import_deck` route handler in `src/deck_editor/app.py` (lines 964-1004) also does nothing to extract a commander after calling `from_export_text` — it trusts whatever the parser returns.

## Proposed Fix

**File: `src/obj/deck.py`, `from_export_text`, arena branch (lines 447-492)**

1. Add a `commander_name_arena: str = ""` variable alongside `sideboard_names_arena` and `parsing_sideboard` (around line 450-451).
2. Add a `parsing_commander: bool = False` flag.
3. In the header-detection block (lines 456-462), recognize `commander` and `commander:` as section headers (same pattern as `sideboard`):
   ```python
   if head_lower in ("commander", "commander:"):
       parsing_commander = True
       parsing_sideboard = False
       continue
   ```
4. When the `deck` header is encountered, reset both flags:
   ```python
   if head_lower == "deck" or head_lower == "deck:":
       parsing_commander = False
       parsing_sideboard = False
       continue
   ```
5. Also reset `parsing_commander` when entering the sideboard section (add `parsing_commander = False` to the existing sideboard branch).
6. In the card-line processing block (lines 474-484), add a branch for `parsing_commander` that captures the first card name:
   ```python
   if parsing_commander:
       commander_name_arena = canonical_name
       parsing_commander = False
   elif parsing_sideboard:
       ...
   ```
   Only the first card is taken as commander (Brawl has exactly one).
7. In the return statement (line 492), pass the commander name:
   ```python
   return cls(cards=cards_arena, sideboard=sb_cards_arena, commander=commander_name_arena)
   ```

## Risks & Side Effects

- Non-Brawl Arena exports (Standard, Modern, etc.) never include a `Commander` header, so the new branch is inert for those formats — no risk of false detection.
- If a future Arena export has multiple commander cards (e.g., Partner commanders), only the first would be captured. This is acceptable for now since the `Deck.commander` field is a single string; partner support would be a separate feature.
- The `goldfish` and `moxfield` parsers have their own section-header logic and are unaffected.

## Verification

1. Import a Brawl/Historic Brawl Arena export that includes a `Commander` section. Confirm `deck.commander` is set to the commander name and `deck.cards` has N-1 cards (not N).
2. Import a non-Brawl Arena export (e.g., Standard). Confirm behavior is unchanged (no commander set, all cards in main).
3. Import a Brawl deck with a sideboard. Confirm commander, main deck, and sideboard are all correctly separated.
