/** Deck settings panel: name, description, colors, format. Syncs to server on change. */

let settingsDebounceTimer = null;

export function getSettings() {
  const nameEl = document.getElementById('deckName');
  const descEl = document.getElementById('deckDescription');
  const formatEl = document.getElementById('deckFormat');
  const colorChecks = document.querySelectorAll('input[name="deckColor"]:checked');
  const name = nameEl && nameEl.value != null ? String(nameEl.value).trim() : '';
  const description = descEl && descEl.value != null ? String(descEl.value).trim() : '';
  const format = formatEl && formatEl.value != null ? String(formatEl.value).trim() : '';
  const colors = Array.from(colorChecks).map((c) => c.value);
  return { name, colors, description, format };
}

export function populateSettings(deck) {
  const nameEl = document.getElementById('deckName');
  const descEl = document.getElementById('deckDescription');
  const formatEl = document.getElementById('deckFormat');
  if (nameEl) nameEl.value = deck.name != null ? String(deck.name) : '';
  if (descEl) descEl.value = deck.description != null ? String(deck.description) : '';
  if (formatEl) formatEl.value = deck.format != null ? String(deck.format) : '';
  const colors = Array.isArray(deck.colors) ? deck.colors : [];
  document.querySelectorAll('input[name="deckColor"]').forEach((input) => {
    input.checked = colors.indexOf(input.value) !== -1;
  });
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
  const inputs = ['deckName', 'deckDescription', 'deckFormat'];
  inputs.forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', () => fireChange(onChangeCallback));
      el.addEventListener('change', () => fireChange(onChangeCallback));
    }
  });
  document.querySelectorAll('input[name="deckColor"]').forEach((el) => {
    el.addEventListener('change', () => fireChange(onChangeCallback));
  });
}
