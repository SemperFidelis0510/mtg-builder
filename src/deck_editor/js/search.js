/** Reusable card search + autocomplete widget, used by the Board tab and the Wishlist tab. */

import { createLogger } from './logger.js';
import { renderDeck } from './render.js';
import { getSettings } from './settings.js';
import { addToWishlist } from './wishlist.js';

const log = createLogger('search');

/**
 * Wire up a card-name search box: autocomplete dropdown, keyboard navigation, and
 * Scryfall fuzzy-name resolution for free-typed queries. The actual "add" behavior
 * (which board/list the card lands in, and how the UI refreshes) is supplied by the caller.
 * @param {{
 *   inputId: string,
 *   addBtnId: string,
 *   dropdownId: string,
 *   msgId: string,
 *   addCard: (name: string) => Promise<void>,
 * }} config
 */
export function initCardSearchBox(config) {
  const { inputId, addBtnId, dropdownId, msgId, addCard } = config;
  const input = document.getElementById(inputId);
  const addBtn = document.getElementById(addBtnId);
  const dropdown = document.getElementById(dropdownId);
  const msgEl = document.getElementById(msgId);
  if (!input || !addBtn || !dropdown || !msgEl) {
    log.error('initCardSearchBox: missing DOM for %s', inputId);
    return;
  }

  let autocompleteDebounceTimer = null;
  let autocompleteAbort = null;
  let autocompleteHighlight = -1;

  function setMsg(text, isError = false) {
    msgEl.textContent = text;
    msgEl.className = 'search-msg' + (isError ? ' error' : '');
  }

  function showAutocomplete(names) {
    dropdown.innerHTML = '';
    dropdown.style.display = 'block';
    autocompleteHighlight = -1;
    (names || []).slice(0, 15).forEach((name) => {
      const li = document.createElement('li');
      li.textContent = name;
      li.dataset.name = name;
      li.addEventListener('click', () => selectAutocomplete(name));
      dropdown.appendChild(li);
    });
  }

  function hideAutocomplete() {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    autocompleteHighlight = -1;
  }

  function addResolvedCard(name) {
    setMsg('Adding…');
    return addCard(name)
      .then(() => {
        setMsg('Added: ' + name);
        input.value = '';
        input.focus();
      })
      .catch((error) => {
        log.error('addResolvedCard: failed to add', name, error);
        setMsg(error.message || 'Failed to add card.', true);
      });
  }

  function selectAutocomplete(name) {
    log.info('selectAutocomplete: selected', name);
    hideAutocomplete();
    input.value = '';
    addResolvedCard(name);
  }

  function runAutocomplete(query) {
    if (autocompleteAbort) autocompleteAbort.abort();
    if (!query || query.length < 2) {
      hideAutocomplete();
      return;
    }
    const { colors, format, colorlessOnly } = getSettings();
    const params = new URLSearchParams({ q: query });
    if (colorlessOnly) {
      params.set('colorless_only', 'true');
    } else if (colors && colors.length) {
      params.set('colors', colors.join(','));
    }
    if (format) params.set('format', format);
    autocompleteAbort = new AbortController();
    log.debug('runAutocomplete: query', query);
    fetch('/api/autocomplete?' + params.toString(), { signal: autocompleteAbort.signal })
      .then((r) => r.json())
      .then((data) => {
        log.debug('runAutocomplete: got', (data.data || []).length, 'results');
        if (data.data && data.data.length) showAutocomplete(data.data);
        else hideAutocomplete();
      })
      .catch((err) => {
        if (err.name !== 'AbortError') { log.warn('runAutocomplete: fetch error', err); hideAutocomplete(); }
      });
  }

  function doSearch() {
    const query = (input.value || '').trim();
    log.info('doSearch: query', query);
    if (!query) {
      setMsg('Enter a card name.');
      return;
    }
    setMsg('Searching…');
    fetch('https://api.scryfall.com/cards/named?fuzzy=' + encodeURIComponent(query))
      .then((r) => r.json())
      .then((data) => {
        if (data.object === 'error') {
          setMsg(data.details || 'Card not found.', true);
          return;
        }
        return addResolvedCard(data.name);
      })
      .catch((err) => {
        log.error('doSearch: scryfall fetch failed', err);
        setMsg('Search failed.', true);
      });
  }

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

function addCardToMainDeck(name) {
  return fetch('/api/add_card', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  }).then((r) => {
    if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Add failed'); });
    return r.json();
  }).then((deckData) => {
    renderDeck(deckData);
  });
}

export function initSearch() {
  initCardSearchBox({
    inputId: 'cardSearch',
    addBtnId: 'addCardBtn',
    dropdownId: 'autocompleteDropdown',
    msgId: 'searchMsg',
    addCard: addCardToMainDeck,
  });
}

function addCardToWishlist(name) {
  return new Promise((resolve, reject) => {
    addToWishlist(name, { onSuccess: resolve, onError: reject });
  });
}

export function initWishlistSearch() {
  initCardSearchBox({
    inputId: 'wishlistCardSearch',
    addBtnId: 'wishlistAddCardBtn',
    dropdownId: 'wishlistAutocompleteDropdown',
    msgId: 'wishlistSearchMsg',
    addCard: addCardToWishlist,
  });
}
