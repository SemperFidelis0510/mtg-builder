/** SortableJS drag-and-drop for deck lists. */

import { TYPE_KEYS } from './constants.js';
import { makeMaybeBoardCardEl } from './deck.js';

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

export function initSortable() {
  sortables.forEach((s) => {
    if (s.destroy) s.destroy();
  });
  sortables = [];

  const options = {
    group: { name: 'cards', pull: true, put: true },
    handle: '.card-img, .maybe-board-name',
    animation: 150,
    ghostClass: 'sortable-ghost',
    dragClass: 'sortable-drag',
  };

  TYPE_KEYS.forEach((key) => {
    const el = document.getElementById('list-' + key);
    if (el) sortables.push(Sortable.create(el, options));
  });

  const sideZoneIds = ['list-maybe', 'list-sideboard'];
  sideZoneIds.forEach((zoneId) => {
    const el = document.getElementById(zoneId);
    if (el) {
      sortables.push(
        Sortable.create(el, {
          group: { name: 'cards', pull: true, put: true },
          handle: '.card-img, .maybe-board-name',
          animation: 150,
          ghostClass: 'sortable-ghost',
          dragClass: 'sortable-drag',
          onEnd() {
            setTimeout(() => convertFullCardsInZone(zoneId), 0);
          },
        })
      );
      setTimeout(() => convertFullCardsInZone(zoneId), 50);
    }
  });
}
