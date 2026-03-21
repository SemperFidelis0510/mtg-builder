/** Deck settings panel: name, description, colors, format, colorless, sideboard toggle. Syncs to server on change. */

import { createColorPalette, getColorPaletteValues, setColorPaletteValues } from './color-palette.js';

let settingsDebounceTimer = null;
let _prevSideboardHasCards = null;

export function getSettings() {
  const nameEl = document.getElementById('deckName');
  const descEl = document.getElementById('deckDescription');
  const formatEl = document.getElementById('deckFormat');
  const container = document.getElementById('deckColorsContainer');
  const name = nameEl && nameEl.value != null ? String(nameEl.value).trim() : '';
  const description = descEl && descEl.value != null ? String(descEl.value).trim() : '';
  const format = formatEl && formatEl.value != null ? String(formatEl.value).trim() : '';
  const palette = container ? getColorPaletteValues(container) : { colors: [], colorless: false };
  return { name, colors: palette.colors, description, format, colorlessOnly: palette.colorless };
}

export function populateSettings(deck) {
  const nameEl = document.getElementById('deckName');
  const descEl = document.getElementById('deckDescription');
  const formatEl = document.getElementById('deckFormat');
  const container = document.getElementById('deckColorsContainer');
  if (nameEl) nameEl.value = deck.name != null ? String(deck.name) : '';
  if (descEl) descEl.value = deck.description != null ? String(deck.description) : '';
  if (formatEl) formatEl.value = deck.format != null ? String(deck.format) : '';
  if (container) setColorPaletteValues(container, deck.colors, deck.colorless_only);

  const sideboardNames = Array.isArray(deck.sideboard_names)
    ? deck.sideboard_names
    : (Array.isArray(deck.sideboard) ? deck.sideboard : []);
  const hasCards = sideboardNames.length > 0;
  const toggle = document.getElementById('sideboardToggle');
  if (toggle) {
    if (_prevSideboardHasCards === null || _prevSideboardHasCards !== hasCards) {
      toggle.checked = hasCards;
    }
    _prevSideboardHasCards = hasCards;
  }
}

function fireChange(callback) {
  if (settingsDebounceTimer) clearTimeout(settingsDebounceTimer);
  settingsDebounceTimer = setTimeout(() => {
    settingsDebounceTimer = null;
    if (typeof callback === 'function') callback();
  }, 400);
}

export function initSettings(onChangeCallback) {
  const section = document.getElementById('deckSettingsSection');
  const header = document.getElementById('deckSettingsHeader');
  if (header && section) {
    header.addEventListener('click', () => section.classList.toggle('collapsed'));
  }

  const container = document.getElementById('deckColorsContainer');
  if (container) {
    container.appendChild(createColorPalette({
      name: 'deckColor',
      colorlessName: 'deckColorless',
      onChange: () => fireChange(onChangeCallback),
    }));
  }

  const inputs = ['deckName', 'deckDescription', 'deckFormat'];
  inputs.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', () => fireChange(onChangeCallback));
      el.addEventListener('change', () => fireChange(onChangeCallback));
    }
  });

}
