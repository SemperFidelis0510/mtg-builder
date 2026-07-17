/** Deck-wide sorting popup and session sort-state UI. */

import { createLogger } from './logger.js';

const log = createLogger('deck-sort');

const SORT_LABELS = {
  manual: 'Manual',
  name: 'Name',
  mana_value: 'Mana cost',
  price: 'Price',
};

const DIRECTION_LABELS = {
  ascending: 'Ascending',
  descending: 'Descending',
};

let activeDeckSort = { criterion: 'manual', direction: 'ascending' };

export function isDeckSortManual() {
  return activeDeckSort.criterion === 'manual';
}

function isSortState(value) {
  return value
    && typeof value === 'object'
    && typeof value.criterion === 'string'
    && typeof value.direction === 'string'
    && Object.hasOwn(SORT_LABELS, value.criterion)
    && Object.hasOwn(DIRECTION_LABELS, value.direction);
}

export function initDeckSort() {
  const control = document.getElementById('deckSortControl');
  const toggleBtn = document.getElementById('deckSortBtn');
  const popup = document.getElementById('deckSortPopup');
  const criterionSelect = document.getElementById('deckSortCriterion');
  const directionSelect = document.getElementById('deckSortDirection');
  const errorEl = document.getElementById('deckSortError');
  const cancelBtn = document.getElementById('deckSortCancelBtn');
  const applyBtn = document.getElementById('deckSortApplyBtn');

  if (!control || !toggleBtn || !popup || !criterionSelect || !directionSelect || !errorEl || !cancelBtn || !applyBtn) {
    throw new Error('Deck sort controls are missing from the page');
  }

  function updateDirectionControl() {
    directionSelect.disabled = criterionSelect.value === 'manual';
  }

  function updateButtonLabel() {
    if (activeDeckSort.criterion === 'manual') {
      toggleBtn.textContent = 'Sort';
      return;
    }
    const directionSymbol = activeDeckSort.direction === 'ascending' ? 'Asc' : 'Desc';
    toggleBtn.textContent = `Sort: ${SORT_LABELS[activeDeckSort.criterion]} ${directionSymbol}`;
  }

  function setPopupOpen(open) {
    popup.hidden = !open;
    popup.setAttribute('aria-hidden', open ? 'false' : 'true');
    toggleBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      criterionSelect.value = activeDeckSort.criterion;
      directionSelect.value = activeDeckSort.direction;
      errorEl.textContent = '';
      updateDirectionControl();
      criterionSelect.focus();
    }
  }

  function syncSortState(sortState) {
    if (!isSortState(sortState)) {
      throw new Error('Deck sort response contained an invalid sort state');
    }
    activeDeckSort = { criterion: sortState.criterion, direction: sortState.direction };
    updateButtonLabel();
    if (!popup.hidden) {
      criterionSelect.value = activeDeckSort.criterion;
      directionSelect.value = activeDeckSort.direction;
      updateDirectionControl();
    }
  }

  async function applySort() {
    const criterion = criterionSelect.value;
    const direction = directionSelect.value;
    if (!Object.hasOwn(SORT_LABELS, criterion) || !Object.hasOwn(DIRECTION_LABELS, direction)) {
      throw new Error('Deck sort controls contain an invalid selection');
    }

    applyBtn.disabled = true;
    cancelBtn.disabled = true;
    errorEl.textContent = '';
    try {
      const response = await fetch('/api/deck/sort', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ criterion, direction }),
      });
      if (!response.ok) {
        const errorBody = await response.json();
        if (errorBody && typeof errorBody.detail === 'string') {
          throw new Error(errorBody.detail);
        }
        throw new Error(`Deck sort failed with status ${response.status}`);
      }
      const data = await response.json();
      syncSortState(data.sort);
      setPopupOpen(false);
      log.info('Deck sort applied: %s %s', criterion, direction);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      log.error('Deck sort failed', error);
      errorEl.textContent = message;
      throw error;
    } finally {
      applyBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  }

  criterionSelect.addEventListener('change', updateDirectionControl);
  toggleBtn.addEventListener('click', () => setPopupOpen(popup.hidden));
  cancelBtn.addEventListener('click', () => setPopupOpen(false));
  applyBtn.addEventListener('click', () => { applySort(); });
  document.addEventListener('click', (event) => {
    if (!popup.hidden && !control.contains(event.target)) {
      setPopupOpen(false);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !popup.hidden) {
      setPopupOpen(false);
      toggleBtn.focus();
    }
  });
  window.addEventListener('deck-sort-updated', (event) => {
    syncSortState(event.detail);
  });

  updateDirectionControl();
  updateButtonLabel();
}
