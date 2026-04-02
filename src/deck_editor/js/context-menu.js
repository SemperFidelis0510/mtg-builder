/** Right-click context menu on card elements: copy name, open in MTGMintCard, board moves. */

import {
  getStackBoardContext,
  moveStackFromMainToMaybe,
  moveStackFromMaybeToMain,
} from './board-move.js';

const MTGMINTCARD_SEARCH_BASE = 'https://www.mtgmintcard.com/mtg/singles/search?action=normal_search&ed=0&keywords=';

let menuEl = null;
let currentCardName = null;
let currentContextStack = null;

function createMenuElement() {
  const div = document.createElement('div');
  div.id = 'cardContextMenu';
  div.setAttribute('aria-hidden', 'true');
  div.innerHTML = [
    '<button type="button" class="context-menu-item" data-action="copy">Copy card name</button>',
    '<button type="button" class="context-menu-item" data-action="triggers">Extract triggers</button>',
    '<button type="button" class="context-menu-item" data-action="effects">Extract effects</button>',
    '<button type="button" class="context-menu-item" data-action="mtgmintcard">Open in MTGMintCard</button>',
    '<button type="button" class="context-menu-item context-menu-board-move" data-action="move-to-maybe" hidden>Move to maybe board</button>',
    '<button type="button" class="context-menu-item context-menu-board-move" data-action="move-to-main" hidden>Move to main deck</button>',
  ].join('');
  document.body.appendChild(div);
  return div;
}

function showMenu(x, y, cardName, stack) {
  if (!menuEl) menuEl = createMenuElement();
  currentCardName = cardName;
  currentContextStack = stack;
  const ctx = stack ? getStackBoardContext(stack) : null;
  const moveMaybeBtn = menuEl.querySelector('[data-action="move-to-maybe"]');
  const moveMainBtn = menuEl.querySelector('[data-action="move-to-main"]');
  if (moveMaybeBtn) moveMaybeBtn.hidden = ctx !== 'main';
  if (moveMainBtn) moveMainBtn.hidden = ctx !== 'maybe';
  menuEl.style.left = x + 'px';
  menuEl.style.top = y + 'px';
  menuEl.classList.add('visible');
  menuEl.setAttribute('aria-hidden', 'false');
}

function hideMenu() {
  if (menuEl) {
    menuEl.classList.remove('visible');
    menuEl.setAttribute('aria-hidden', 'true');
    currentCardName = null;
    currentContextStack = null;
  }
}

function copyCardName(name) {
  if (typeof navigator.clipboard !== 'undefined' && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(name).catch(() => {});
  }
}

function openInMTGMintCard(name) {
  const encoded = encodeURIComponent(name).replace(/%20/g, '+');
  window.open(MTGMINTCARD_SEARCH_BASE + encoded, '_blank', 'noopener,noreferrer');
}

async function extractAndCopy(cardName, type) {
  const params = new URLSearchParams({ name: cardName, type });
  const url = `/api/card_mechanics?${params.toString()}`;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const msg = err.detail || res.statusText || String(res.status);
      if (typeof navigator.clipboard !== 'undefined' && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(`Error: ${msg}`).catch(() => {});
      }
      return;
    }
    const data = await res.json();
    const text = data.result != null ? String(data.result) : '(none)';
    if (typeof navigator.clipboard !== 'undefined' && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
    }
  } catch (e) {
    if (typeof navigator.clipboard !== 'undefined' && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(`Error: ${e.message || String(e)}`).catch(() => {});
    }
  }
}

function handleMenuAction(e) {
  const btn = e.target.closest('.context-menu-item');
  if (!btn || !currentCardName) return;
  e.preventDefault();
  e.stopPropagation();
  const action = btn.getAttribute('data-action');
  if (action === 'move-to-maybe') {
    const stack = currentContextStack;
    hideMenu();
    if (stack) moveStackFromMainToMaybe(stack);
    return;
  }
  if (action === 'move-to-main') {
    const stack = currentContextStack;
    hideMenu();
    if (stack) {
      moveStackFromMaybeToMain(stack).catch((err) => {
        console.error(err);
        window.alert(err.message != null ? String(err.message) : String(err));
      });
    }
    return;
  }
  if (action === 'copy') copyCardName(currentCardName);
  if (action === 'mtgmintcard') openInMTGMintCard(currentCardName);
  if (action === 'triggers') extractAndCopy(currentCardName, 'triggers');
  if (action === 'effects') extractAndCopy(currentCardName, 'effects');
  hideMenu();
}

export function initContextMenu() {
  document.addEventListener('contextmenu', (e) => {
    const stack = e.target.closest('.card-stack[data-name]');
    if (!stack) {
      hideMenu();
      return;
    }
    const name = stack.getAttribute('data-name');
    if (!name) return;
    e.preventDefault();
    showMenu(e.clientX, e.clientY, name, stack);
  });

  document.addEventListener('click', (e) => {
    if (menuEl && menuEl.classList.contains('visible')) {
      if (menuEl.contains(e.target)) {
        handleMenuAction(e);
      } else {
        hideMenu();
      }
    }
  });

  document.addEventListener('scroll', hideMenu, true);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideMenu();
  });
  window.addEventListener('blur', hideMenu);

  if (!menuEl) menuEl = createMenuElement();
  menuEl.addEventListener('click', handleMenuAction);
}
