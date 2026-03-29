/** Maybe board: full card view toggle and list rebuild. */

import { isMaybeFullCardView, setMaybeFullCardView } from './maybe-board-prefs.js';
import {
  makeCardStackEl,
  makeMaybeBoardCardEl,
  updateSectionHeaderTotal,
} from './deck.js';
import { initSortable } from './sortable.js';

export { isMaybeFullCardView, setMaybeFullCardView } from './maybe-board-prefs.js';

export function syncMaybeViewToggleButton() {
  const btn = document.getElementById('maybeViewToggleBtn');
  if (!btn) return;
  const full = isMaybeFullCardView();
  btn.textContent = full ? 'Compact' : 'Card images';
  btn.setAttribute('aria-pressed', full ? 'true' : 'false');
}

export function rebuildMaybeList() {
  const list = document.getElementById('list-maybe');
  if (!list) return;
  const stacks = [];
  list.querySelectorAll(':scope > li .card-stack').forEach((el) => {
    const name = el.getAttribute('data-name');
    const count = parseInt(el.getAttribute('data-count') || '1', 10);
    if (name) stacks.push({ name, count: Math.max(1, count) });
  });
  list.innerHTML = '';
  const full = isMaybeFullCardView();
  if (full) {
    list.classList.add('maybe-full-card-view');
    stacks.forEach(({ name, count }) => {
      list.appendChild(makeCardStackEl(name, count));
    });
  } else {
    list.classList.remove('maybe-full-card-view');
    stacks.forEach(({ name, count }) => {
      list.appendChild(makeMaybeBoardCardEl(name, count, { quantityControls: true }));
    });
  }
  updateSectionHeaderTotal(list);
  initSortable();
}

export function toggleMaybeBoardView() {
  setMaybeFullCardView(!isMaybeFullCardView());
  rebuildMaybeList();
  syncMaybeViewToggleButton();
}

export function initMaybeBoardViewUi() {
  syncMaybeViewToggleButton();
  const btn = document.getElementById('maybeViewToggleBtn');
  if (!btn) return;
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleMaybeBoardView();
  });
  btn.addEventListener('mousedown', (e) => {
    e.stopPropagation();
  });
}
