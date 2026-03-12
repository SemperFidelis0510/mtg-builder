/** SortableJS drag-and-drop for deck lists. */

import { TYPE_KEYS } from './constants.js';
import { makeMaybeBoardCardEl, makeCardStackEl, updateSectionHeaderTotal } from './deck.js';

let sortables = [];

function convertFullCardsInZone(zoneId) {
  const zone = document.getElementById(zoneId);
  if (!zone) return;
  const toReplace = [];
  for (let i = 0; i < zone.children.length; i++) {
    const li = zone.children[i];
    const stack = li.querySelector('.card-stack');
    const hasImg = li.querySelector('.card-img');
    if (stack && hasImg && !stack.classList.contains('maybe-board-item')) {
      const name = stack.getAttribute('data-name');
      const count = parseInt(stack.getAttribute('data-count'), 10) || 1;
      toReplace.push({ li, name, count });
    }
  }
  toReplace.forEach((r) => {
    const newLi = makeMaybeBoardCardEl(r.name, r.count);
    r.li.parentNode.replaceChild(newLi, r.li);
  });
}

/** Replace any minimized (maybe-board-item) cards in a deck section list with full card-stack elements. */
function convertMinimizedToFullInDeckList(listEl) {
  if (!listEl) return;
  const toReplace = [];
  for (let i = 0; i < listEl.children.length; i++) {
    const li = listEl.children[i];
    const stack = li.querySelector('.card-stack.maybe-board-item');
    if (stack) {
      const name = stack.getAttribute('data-name');
      const count = parseInt(stack.getAttribute('data-count'), 10) || 1;
      toReplace.push({ li, name, count });
    }
  }
  toReplace.forEach((r) => {
    const newLi = makeCardStackEl(r.name, r.count);
    r.li.parentNode.replaceChild(newLi, r.li);
  });
}

function isDeckSectionList(listId) {
  return listId && listId.startsWith('list-') && TYPE_KEYS.includes(listId.replace(/^list-/, ''));
}

function handleDeckSectionDrop(evt) {
  const listEl = evt.to;
  const item = evt.item;
  const stack = item.querySelector('.card-stack.maybe-board-item');
  if (!stack) {
    return;
  }
  const name = stack.getAttribute('data-name');
  if (!name) return;
  fetch('/api/card_type?name=' + encodeURIComponent(name))
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('card type lookup failed'))))
    .then((data) => {
      const typeKey = data.type_key;
      const currentKey = listEl.id.replace(/^list-/, '');
      if (typeKey !== currentKey) {
        const targetList = document.getElementById('list-' + typeKey);
        if (targetList) {
          listEl.removeChild(item);
          targetList.appendChild(item);
          convertMinimizedToFullInDeckList(targetList);
          updateSectionHeaderTotal(listEl);
          updateSectionHeaderTotal(targetList);
          return;
        }
      }
      convertMinimizedToFullInDeckList(listEl);
      updateSectionHeaderTotal(listEl);
    })
    .catch(() => {
      convertMinimizedToFullInDeckList(listEl);
      updateSectionHeaderTotal(listEl);
    });
}

export function initSortable() {
  sortables.forEach((s) => {
    if (s.destroy) s.destroy();
  });
  sortables = [];

  const deckColumnEl = document.querySelector('.deck-column');
  function removeDeckColumnHighlight() {
    if (deckColumnEl) deckColumnEl.classList.remove('deck-column-drag-from-side');
  }

  const deckSectionOptions = {
    group: { name: 'cards', pull: true, put: true },
    handle: '.card-img, .maybe-board-item',
    animation: 150,
    ghostClass: 'sortable-ghost',
    dragClass: 'sortable-drag',
    onEnd(evt) {
      removeDeckColumnHighlight();
      if (isDeckSectionList(evt.to.id)) {
        handleDeckSectionDrop(evt);
      }
    },
  };

  TYPE_KEYS.forEach((key) => {
    const el = document.getElementById('list-' + key);
    if (el) sortables.push(Sortable.create(el, deckSectionOptions));
  });

  const sideZoneIds = ['list-maybe', 'list-sideboard'];
  sideZoneIds.forEach((zoneId) => {
    const el = document.getElementById(zoneId);
    if (el) {
      sortables.push(
        Sortable.create(el, {
          group: { name: 'cards', pull: true, put: true },
          handle: '.card-img, .maybe-board-item',
          animation: 150,
          ghostClass: 'sortable-ghost',
          dragClass: 'sortable-drag',
          onStart() {
            if (deckColumnEl) deckColumnEl.classList.add('deck-column-drag-from-side');
          },
          onEnd() {
            removeDeckColumnHighlight();
            setTimeout(() => convertFullCardsInZone(zoneId), 0);
          },
        })
      );
      setTimeout(() => convertFullCardsInZone(zoneId), 50);
    }
  });
}
