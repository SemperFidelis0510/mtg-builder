/** Right-click context menu on card elements: copy name, open in MTGMintCard. */

const MTGMINTCARD_SEARCH_BASE = 'https://www.mtgmintcard.com/mtg/singles/search?action=normal_search&ed=0&keywords=';

let menuEl = null;
let currentCardName = null;

function createMenuElement() {
  const div = document.createElement('div');
  div.id = 'cardContextMenu';
  div.setAttribute('aria-hidden', 'true');
  div.innerHTML = [
    '<button type="button" class="context-menu-item" data-action="copy">Copy card name</button>',
    '<button type="button" class="context-menu-item" data-action="triggers">Extract triggers</button>',
    '<button type="button" class="context-menu-item" data-action="effects">Extract effects</button>',
    '<button type="button" class="context-menu-item" data-action="mtgmintcard">Open in MTGMintCard</button>',
  ].join('');
  document.body.appendChild(div);
  return div;
}

function showMenu(x, y, cardName) {
  if (!menuEl) menuEl = createMenuElement();
  currentCardName = cardName;
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
    showMenu(e.clientX, e.clientY, name);
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
