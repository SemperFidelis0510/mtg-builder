/** Card search and autocomplete. */

import { typeLineToSectionKey } from './utils.js';
import { addCardToDeck } from './deck.js';
import { getSettings } from './settings.js';

let autocompleteDebounceTimer = null;
let autocompleteAbort = null;
let autocompleteHighlight = -1;

function showAutocomplete(names) {
  const ul = document.getElementById('autocompleteDropdown');
  ul.innerHTML = '';
  ul.style.display = 'block';
  autocompleteHighlight = -1;
  (names || []).slice(0, 15).forEach((name) => {
    const li = document.createElement('li');
    li.textContent = name;
    li.dataset.name = name;
    li.addEventListener('click', () => selectAutocomplete(name));
    ul.appendChild(li);
  });
}

function hideAutocomplete() {
  const ul = document.getElementById('autocompleteDropdown');
  ul.style.display = 'none';
  ul.innerHTML = '';
  autocompleteHighlight = -1;
}

function selectAutocomplete(name) {
  hideAutocomplete();
  document.getElementById('cardSearch').value = '';
  const msgEl = document.getElementById('searchMsg');
  msgEl.textContent = 'Adding…';
  msgEl.className = 'search-msg';
  fetch('https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(name))
    .then((r) => r.json())
    .then((data) => {
      if (data.object === 'error') {
        msgEl.textContent = data.details || 'Card not found.';
        msgEl.className = 'search-msg error';
        return;
      }
      addCardToDeck(name, typeLineToSectionKey(data.type_line));
      return fetch('/api/add_card', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }).then((r) => {
        if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Add failed'); });
        msgEl.textContent = 'Added: ' + name;
        msgEl.className = 'search-msg';
      });
    })
    .catch(() => {
      msgEl.textContent = 'Failed to add card.';
      msgEl.className = 'search-msg error';
    });
}

function runAutocomplete(query) {
  if (autocompleteAbort) autocompleteAbort.abort();
  if (!query || query.length < 2) {
    hideAutocomplete();
    return;
  }
  const { colors, format } = getSettings();
  const params = new URLSearchParams({ q: query });
  if (colors && colors.length) params.set('colors', colors.join(','));
  if (format) params.set('format', format);
  autocompleteAbort = new AbortController();
  fetch('/api/autocomplete?' + params.toString(), { signal: autocompleteAbort.signal })
    .then((r) => r.json())
    .then((data) => {
      if (data.data && data.data.length) showAutocomplete(data.data);
      else hideAutocomplete();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') hideAutocomplete();
    });
}

function doSearch() {
  const input = document.getElementById('cardSearch');
  const msgEl = document.getElementById('searchMsg');
  const query = (input.value || '').trim();
  if (!query) {
    msgEl.textContent = 'Enter a card name.';
    msgEl.className = 'search-msg';
    return;
  }
  msgEl.textContent = 'Searching…';
  msgEl.className = 'search-msg';
  fetch('https://api.scryfall.com/cards/named?fuzzy=' + encodeURIComponent(query))
    .then((r) => r.json())
    .then((data) => {
      if (data.object === 'error') {
        msgEl.textContent = data.details || 'Card not found.';
        msgEl.className = 'search-msg error';
        return;
      }
      const name = data.name;
      addCardToDeck(name, typeLineToSectionKey(data.type_line));
      return fetch('/api/add_card', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      }).then((r) => {
        if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Add failed'); });
        msgEl.textContent = 'Added: ' + name;
        msgEl.className = 'search-msg';
        input.value = '';
        input.focus();
      }).catch((err) => {
        msgEl.textContent = err.message || 'Add failed';
        msgEl.className = 'search-msg error';
      });
    })
    .catch(() => {
      msgEl.textContent = 'Search failed.';
      msgEl.className = 'search-msg error';
    });
}

export function initSearch() {
  const input = document.getElementById('cardSearch');
  const addBtn = document.getElementById('addCardBtn');
  const dropdown = document.getElementById('autocompleteDropdown');
  addBtn.addEventListener('click', doSearch);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const items = dropdown.querySelectorAll('li');
      if (items.length && autocompleteHighlight >= 0 && items[autocompleteHighlight]) {
        e.preventDefault();
        selectAutocomplete(items[autocompleteHighlight].dataset.name);
        return;
      }
      e.preventDefault();
      doSearch();
    } else if (e.key === 'Escape') {
      hideAutocomplete();
      input.blur();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      const items = dropdown.querySelectorAll('li');
      if (items.length) {
        autocompleteHighlight = (autocompleteHighlight + 1) % items.length;
        items.forEach((el, i) => el.classList.toggle('highlight', i === autocompleteHighlight));
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const items = dropdown.querySelectorAll('li');
      if (items.length) {
        autocompleteHighlight = autocompleteHighlight <= 0 ? items.length - 1 : autocompleteHighlight - 1;
        items.forEach((el, i) => el.classList.toggle('highlight', i === autocompleteHighlight));
      }
    }
  });
  input.addEventListener('input', () => {
    if (autocompleteDebounceTimer) clearTimeout(autocompleteDebounceTimer);
    const q = (input.value || '').trim();
    autocompleteDebounceTimer = setTimeout(() => runAutocomplete(q), 280);
  });
  input.addEventListener('blur', () => {
    setTimeout(hideAutocomplete, 180);
  });
}
