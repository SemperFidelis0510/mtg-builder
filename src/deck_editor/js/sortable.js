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

function allowPutOnlyFromSideZones(to, from) {
  if (!from) return false;
  const fromEl = from.el || from;
  const fromId = (fromEl && fromEl.id) || '';
  return fromId === 'list-maybe' || fromId === 'list-sideboard';
}

function handleDeckDropTargetAdd(evt) {
  const item = evt.item;
  const dropTarget = document.getElementById('deckDropTarget');
  if (!dropTarget) return;
  const stack = item.querySelector('.card-stack.maybe-board-item') || item.querySelector('.card-stack');
  if (!stack) return;
  const name = stack.getAttribute('data-name');
  if (!name) return;
  const count = parseInt(stack.getAttribute('data-count'), 10) || 1;
  dropTarget.removeChild(item);
  fetch('/api/card_type?name=' + encodeURIComponent(name))
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error('card type lookup failed'))))
    .then((data) => {
      const typeKey = data.type_key;
      const targetList = document.getElementById('list-' + typeKey);
      if (!targetList) return;
      const existing = targetList.querySelector('.card-stack[data-name="' + CSS.escape(name) + '"]');
      if (existing) {
        const c = parseInt(existing.getAttribute('data-count'), 10) || 1;
        existing.setAttribute('data-count', String(c + count));
        const badge = existing.closest('li').querySelector('.card-stack-badge');
        if (badge) badge.textContent = String(c + count);
      } else {
        targetList.appendChild(makeCardStackEl(name, count));
      }
      updateSectionHeaderTotal(targetList);
      const section = targetList.closest('.section');
      if (section) {
        section.classList.remove('section-hidden');
        section.classList.remove('collapsed');
      }
    })
    .catch(() => {
      const targetList = document.getElementById('list-sorcery');
      if (targetList) {
        targetList.appendChild(makeCardStackEl(name, count));
        updateSectionHeaderTotal(targetList);
        const section = targetList.closest('.section');
        if (section) {
          section.classList.remove('section-hidden');
          section.classList.remove('collapsed');
        }
      }
    });
}

export function initSortable() {
  sortables.forEach((s) => {
    if (s.destroy) s.destroy();
  });
  sortables = [];

  const deckSectionsZoneEl = document.getElementById('deckSectionsZone');
  function removeDeckSectionsZoneHighlight() {
    if (deckSectionsZoneEl) deckSectionsZoneEl.classList.remove('deck-sections-zone-drag-from-side');
  }

  const dropTargetEl = document.getElementById('deckDropTarget');
  if (dropTargetEl) {
    sortables.push(
      Sortable.create(dropTargetEl, {
        group: { name: 'cards', pull: false, put: allowPutOnlyFromSideZones },
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        onAdd: handleDeckDropTargetAdd,
        onEnd() {
          removeDeckSectionsZoneHighlight();
        },
      })
    );
  }

  TYPE_KEYS.forEach((key) => {
    const el = document.getElementById('list-' + key);
    if (el) {
      sortables.push(
        Sortable.create(el, {
          group: { name: 'cards', pull: true, put: allowPutOnlyFromSideZones },
          handle: '.card-img',
          animation: 150,
          ghostClass: 'sortable-ghost',
          dragClass: 'sortable-drag',
          onAdd() {
            const section = el.closest('.section');
            if (section) section.classList.remove('collapsed');
          },
          onEnd() {
            const list = el;
            if (list) updateSectionHeaderTotal(list);
          },
        })
      );
    }
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
            if (deckSectionsZoneEl) deckSectionsZoneEl.classList.add('deck-sections-zone-drag-from-side');
          },
          onEnd() {
            removeDeckSectionsZoneHighlight();
            setTimeout(() => convertFullCardsInZone(zoneId), 0);
          },
        })
      );
      setTimeout(() => convertFullCardsInZone(zoneId), 50);
    }
  });
}
