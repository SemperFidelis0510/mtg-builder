/** SortableJS drag-and-drop for deck lists. */

import { TYPE_KEYS } from './constants.js';
import { isMaybeFullCardView } from './maybe-board-prefs.js';
import { makeMaybeBoardCardEl, makeCardStackEl, updateSectionHeaderTotal } from './deck.js';

let sortables = [];

function convertFullCardsInZone(zoneId) {
  if (zoneId === 'list-maybe' && isMaybeFullCardView()) return;
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
    const newLi =
      zoneId === 'list-maybe'
        ? makeMaybeBoardCardEl(r.name, r.count, { quantityControls: true })
        : makeMaybeBoardCardEl(r.name, r.count);
    r.li.parentNode.replaceChild(newLi, r.li);
  });
}

export function appendCardToList(listEl, name, count) {
  if (!listEl || !name) return;
  const n = Math.max(1, parseInt(count, 10) || 1);
  if (listEl.id === 'list-maybe') {
    if (isMaybeFullCardView()) {
      listEl.appendChild(makeCardStackEl(name, n));
    } else {
      listEl.appendChild(makeMaybeBoardCardEl(name, n, { quantityControls: true }));
    }
    return;
  }
  if (listEl.id === 'list-sideboard') {
    listEl.appendChild(makeMaybeBoardCardEl(name, n));
    return;
  }
  listEl.appendChild(makeCardStackEl(name, n));
}

function commanderSlotPresent() {
  return !!document.getElementById('list-commander');
}

/** Typed main sections: commander hub when commander slot exists; else maybe/sideboard → main only. */
function allowPutIntoMainTypedList(to, from) {
  if (!from) return false;
  const fromEl = from.el || from;
  const fromId = (fromEl && fromEl.id) || '';
  if (commanderSlotPresent()) {
    return fromId === 'list-commander';
  }
  return fromId === 'list-maybe' || fromId === 'list-sideboard';
}

/** Maybe/sideboard: accept any in-group drag when no commander slot; else only from commander. */
function allowPutIntoSideZone(to, from) {
  if (!from) return false;
  if (!commanderSlotPresent()) {
    return true;
  }
  const fromEl = from.el || from;
  const fromId = (fromEl && fromEl.id) || '';
  return fromId === 'list-commander';
}

function allowPutDeckDropTarget(to, from) {
  if (!from || commanderSlotPresent()) return false;
  const fromEl = from.el || from;
  const fromId = (fromEl && fromEl.id) || '';
  return fromId === 'list-maybe' || fromId === 'list-sideboard';
}

function handleDeckDropTargetAdd(evt) {
  const item = evt.item;
  const dropTarget = document.getElementById('deckDropTarget');
  if (!dropTarget || commanderSlotPresent()) return;
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
        group: { name: 'cards', pull: false, put: allowPutDeckDropTarget },
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
          group: { name: 'cards', pull: true, put: allowPutIntoMainTypedList },
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

  const commanderListEl = document.getElementById('list-commander');
  if (commanderListEl) {
    sortables.push(
      Sortable.create(commanderListEl, {
        group: { name: 'cards', pull: true, put: true },
        handle: '.card-img, .maybe-board-item',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        onAdd(evt) {
          const list = evt.to;
          const sourceList = evt.from;
          const item = evt.item;
          const stack = item.querySelector('.card-stack');
          if (!list || !stack) return;
          const name = stack.getAttribute('data-name');
          const count = parseInt(stack.getAttribute('data-count') || '1', 10);
          if (count > 1 && sourceList) {
            appendCardToList(sourceList, name, count - 1);
            updateSectionHeaderTotal(sourceList);
          }
          list.innerHTML = '';
          list.appendChild(makeCardStackEl(name, 1));
          updateSectionHeaderTotal(list);
          const section = list.closest('.section');
          if (section) section.classList.remove('collapsed');
        },
        onEnd() {
          removeDeckSectionsZoneHighlight();
        },
      })
    );
  }

  const sideZoneIds = ['list-maybe', 'list-sideboard'];
  sideZoneIds.forEach((zoneId) => {
    const el = document.getElementById(zoneId);
    if (el) {
      sortables.push(
        Sortable.create(el, {
          group: { name: 'cards', pull: true, put: allowPutIntoSideZone },
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
