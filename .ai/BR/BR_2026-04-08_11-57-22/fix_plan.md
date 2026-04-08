# Fix Plan

## Bug Summary

Card images in the deck editor UI are “broken” (not rendering). The attached server log shows normal startup and deck load activity but **no backend errors**, which strongly suggests the failure is on the client side: the browser is failing to load the `<img>` URLs used for card art.

## Root Cause

The deck editor currently hotlinks card images directly from the Scryfall **API** “named card” endpoint:

- `src/deck_editor/js/utils.js`: `scryfallImageUrlForSide()` and `scryfallImageUrlLargeForSide()` build URLs like:
  - `https://api.scryfall.com/cards/named?exact=<name>&format=image&version=normal`
- `src/deck_editor/js/deck.js`: sets `img.src = scryfallImageUrlForSide(name, currentFaceIndex)`
- `src/deck_editor/js/card-preview.js`: sets `img.src = scryfallImageUrlLarge(imageNameResolved)`

This design is brittle because it depends on a network request per rendered card art using a name-lookup API. When it fails, the browser displays broken images and the UI appears to have “no card art”.

The server log does not show any `/api/...` endpoint activity related to images because images are not proxied through the backend; they are fetched directly by the browser from Scryfall.

## Proposed Fix

### 1) Add a backend image redirect endpoint (stable CDN URLs)

**Add** a new route in `src/deck_editor/app.py`, e.g.:

- `GET /api/card_image?name=<card name>&face=<int>&size=<normal|large>`

Implementation approach:

- Resolve `name` using the existing card database (`CardDB`) so we can access Scryfall identifiers from the AtomicCards payload (e.g. `identifiers.scryfallId`, and for DFC, per-face identifiers).
- Construct a direct CDN image URL on `cards.scryfall.io` (which is intended for image hosting) instead of the API “named” lookup.
- Return a `RedirectResponse` (302) to that CDN URL so the browser loads the image as a normal `<img>` without additional JS changes.

This avoids hammering the “named” endpoint and avoids failure due to exact-name mismatches.

### 2) Switch frontend image helpers to use the backend endpoint

Update `src/deck_editor/js/utils.js`:

- Replace `scryfallImageUrlForSide()` / `scryfallImageUrlLargeForSide()` to return local URLs:
  - `/api/card_image?name=<encoded>&face=<index>&size=normal`
  - `/api/card_image?name=<encoded>&face=<index>&size=large`

This keeps call sites unchanged:

- `src/deck_editor/js/deck.js` continues setting `img.src = scryfallImageUrlForSide(...)`
- `src/deck_editor/js/card-preview.js` continues setting `img.src = scryfallImageUrlLarge(...)`

### 3) Add server-side logging for image failures (strict, visible failures)

In the new `/api/card_image` handler:

- If `name` is missing/invalid → log **error** and return HTTP 400.
- If the card cannot be resolved / missing identifiers → log **error** and return HTTP 404.

This aligns with the repo’s strict error-handling rules and makes future image issues diagnosable via logs.

## Risks & Side Effects

- Requires that `CardDB` provides Scryfall identifiers for the card names being displayed (including special cases like Alchemy names and multi-face cards). If identifiers are missing for some cards, those specific cards will still fail—but now with clear server logs and HTTP status codes.
- Slight increase in backend traffic (image URL requests), but each request is just a redirect and should be cheap compared to the current external name-lookup pattern.
- Must ensure redirects are safe and only point to allowed Scryfall CDN hosts.

## Verification

1. Open the deck editor and load a deck with many cards.
2. Confirm card images render for main deck, maybe board (full-card view), and commander preview.
3. In browser devtools Network tab:
   - Confirm image requests are to `/api/card_image?...` and respond with 302 redirects to `cards.scryfall.io`.
   - Confirm there are no 429/400/404 bursts during initial render.
4. Test a two-sided card:
   - Flip the card face (if supported) and confirm the face image updates correctly.
5. Confirm server log shows a clear error if a card image cannot be resolved (e.g., intentionally request `/api/card_image?name=not-a-card`).

