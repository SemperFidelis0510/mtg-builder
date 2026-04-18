/** Wishlist tab: persistent buy-list with sort, reorder, and view toggle. */

import { createLogger } from './logger.js';
import { scryfallImageUrlForSide, splitCardFaces } from './utils.js';
import { getCardFaceIndex, makeCardStackEl, updateSectionHeaderTotal } from './deck.js';
import { initSortable } from './sortable.js';

const log = createLogger('wishlist');

const LS_VIEW_KEY = 'deckEditor.wishlistFullCardView';

let wishlistItems = [];
let wishlistPrices = {};
let syncTimer = null;
let sortableInstance = null;

function isFullCardView() {
  return localStorage.getItem(LS_VIEW_KEY) === '1';
}

function setFullCardView(enabled) {
  if (enabled) {
    localStorage.setItem(LS_VIEW_KEY, '1');
  } else {
    localStorage.removeItem(LS_VIEW_KEY);
  }
}

function syncViewToggleButton() {
  const btn = document.getElementById('wishlistViewToggleBtn');
  if (!btn) return;
  const full = isFullCardView();
  btn.textContent = full ? 'Compact' : 'Card images';
  btn.setAttribute('aria-pressed', full ? 'true' : 'false');
}

function updateTotalPrice() {
  const el = document.getElementById('wishlistTotalPrice');
  if (!el) return;
  let total = 0;
  for (const item of wishlistItems) {
    const p = wishlistPrices[item.name];
    if (p != null && typeof p === 'number' && p >= 0) {
      total += p * item.quantity;
    }
  }
  el.textContent = '$' + total.toFixed(2);
}

function makeWishlistCardStack(name, count) {
  count = Math.max(1, parseInt(count, 10) || 1);
  const li = document.createElement('li');
  li.style.listStyle = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'card-stack';
  wrap.dataset.name = name;
  wrap.dataset.count = String(count);
  wrap.setAttribute('data-name', name);
  wrap.setAttribute('data-count', String(count));
  const faces = splitCardFaces(name);
  const twoSided = faces.length > 1;
  let currentFaceIndex = getCardFaceIndex(name);
  if (currentFaceIndex >= faces.length) currentFaceIndex = 0;
  if (currentFaceIndex < 0) currentFaceIndex = 0;
  if (!twoSided) currentFaceIndex = 0;
  wrap.dataset.isTwoSided = twoSided ? 'true' : 'false';
  wrap.classList.toggle('is-two-sided', twoSided);
  wrap.dataset.currentFaceIndex = String(currentFaceIndex);
  wrap.dataset.currentFaceName = twoSided ? faces[currentFaceIndex] : name;

  const img = document.createElement('img');
  img.className = 'card-img';
  img.src = scryfallImageUrlForSide(name, currentFaceIndex);
  img.alt = wrap.dataset.currentFaceName;
  img.loading = 'lazy';
  if (twoSided) {
    img.title = 'Click to flip card face';
    img.addEventListener('click', (e) => {
      e.stopPropagation();
      let faceIdx = parseInt(wrap.dataset.currentFaceIndex, 10) || 0;
      faceIdx = (faceIdx + 1) % faces.length;
      wrap.dataset.currentFaceIndex = String(faceIdx);
      wrap.dataset.currentFaceName = faces[faceIdx];
      img.src = scryfallImageUrlForSide(name, faceIdx);
      img.alt = faces[faceIdx];
    });
  }
  img.onerror = function () {
    this.style.background = '#333';
    this.style.minWidth = '150px';
    this.style.minHeight = '210px';
  };

  const badge = document.createElement('span');
  badge.className = 'card-stack-badge';
  badge.textContent = String(count);

  const controls = makeQuantityControls(wrap, li, badge);
  const banner = document.createElement('div');
  banner.className = 'card-stack-banner';
  banner.appendChild(badge);
  banner.appendChild(controls);
  const priceSpan = document.createElement('span');
  priceSpan.className = 'card-price';
  const priceVal = wishlistPrices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '\u2014';
  banner.appendChild(priceSpan);
  wrap.appendChild(img);
  wrap.appendChild(banner);
  li.appendChild(wrap);
  return li;
}

function makeWishlistCompactEl(name, count) {
  count = Math.max(1, parseInt(count, 10) || 1);
  const li = document.createElement('li');
  li.style.listStyle = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'card-stack maybe-board-item';
  wrap.dataset.name = name;
  wrap.dataset.count = String(count);
  wrap.setAttribute('data-name', name);
  wrap.setAttribute('data-count', String(count));
  wrap.dataset.currentFaceName = name;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'maybe-board-name';
  nameSpan.textContent = name;

  const badge = document.createElement('span');
  badge.className = 'card-stack-badge';
  badge.textContent = String(count);

  const priceSpan = document.createElement('span');
  priceSpan.className = 'card-price';
  const priceVal = wishlistPrices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '\u2014';

  wrap.appendChild(nameSpan);
  const qtyWrap = document.createElement('span');
  qtyWrap.className = 'maybe-board-qty-wrap';
  qtyWrap.appendChild(badge);
  qtyWrap.appendChild(makeQuantityControls(wrap, li, badge));
  wrap.appendChild(qtyWrap);
  wrap.appendChild(priceSpan);
  li.appendChild(wrap);
  return li;
}

function makeQuantityControls(wrap, li, badge) {
  function setCount(c) {
    c = Math.max(0, c);
    wrap.setAttribute('data-count', String(c));
    badge.textContent = String(c);
    if (c === 0) {
      li.remove();
      collectAndSync();
      return;
    }
    collectAndSync();
  }
  const controls = document.createElement('div');
  controls.className = 'card-stack-controls';
  const btnPlus = document.createElement('button');
  btnPlus.textContent = '+';
  btnPlus.type = 'button';
  const btnMinus = document.createElement('button');
  btnMinus.textContent = '-';
  btnMinus.type = 'button';
  btnPlus.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); });
  btnPlus.addEventListener('click', (e) => {
    e.stopPropagation();
    setCount(parseInt(wrap.getAttribute('data-count'), 10) + 1);
  });
  btnMinus.addEventListener('mousedown', (e) => { e.preventDefault(); e.stopPropagation(); });
  btnMinus.addEventListener('click', (e) => {
    e.stopPropagation();
    setCount(parseInt(wrap.getAttribute('data-count'), 10) - 1);
  });
  controls.appendChild(btnMinus);
  controls.appendChild(btnPlus);
  return controls;
}

function collectStateFromDom() {
  const list = document.getElementById('list-wishlist');
  if (!list) return [];
  const entries = [];
  list.querySelectorAll('.card-stack[data-name][data-count]').forEach((el) => {
    const name = el.getAttribute('data-name');
    const count = parseInt(el.getAttribute('data-count'), 10) || 1;
    if (name && count > 0) entries.push({ name, quantity: count });
  });
  return entries;
}

function collectAndSync() {
  wishlistItems = collectStateFromDom();
  updateTotalPrice();
  syncToServer();
}

function syncToServer() {
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = setTimeout(() => {
    syncTimer = null;
    log.debug('syncToServer: syncing wishlist');
    fetch('/api/wishlist', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(wishlistItems),
    })
      .then((r) => {
        if (r.ok) { log.debug('syncToServer: synced OK'); return r.json(); }
        log.warn('syncToServer: server returned', r.status);
      })
      .then((data) => {
        if (data && data.items) {
          wishlistPrices = {};
          for (const item of data.items) {
            if (item.price_usd != null) wishlistPrices[item.name] = item.price_usd;
          }
        }
      })
      .catch((err) => { log.error('syncToServer: failed', err); });
  }, 500);
}

function renderWishlist() {
  const list = document.getElementById('list-wishlist');
  if (!list) return;
  list.innerHTML = '';
  const full = isFullCardView();
  if (full) {
    list.classList.add('wishlist-full-card-view');
  } else {
    list.classList.remove('wishlist-full-card-view');
  }
  for (const item of wishlistItems) {
    if (full) {
      list.appendChild(makeWishlistCardStack(item.name, item.quantity));
    } else {
      list.appendChild(makeWishlistCompactEl(item.name, item.quantity));
    }
  }
  updateTotalPrice();
  initWishlistSortable();
}

function initWishlistSortable() {
  if (sortableInstance) {
    sortableInstance.destroy();
    sortableInstance = null;
  }
  const list = document.getElementById('list-wishlist');
  if (!list || typeof Sortable === 'undefined') return;
  const full = isFullCardView();
  sortableInstance = Sortable.create(list, {
    group: { name: 'wishlist', pull: false, put: false },
    handle: full ? '.card-img' : '.card-stack',
    animation: 150,
    ghostClass: 'sortable-ghost',
    dragClass: 'sortable-drag',
    onEnd() {
      collectAndSync();
    },
  });
}

function sortByName() {
  log.info('sortByName');
  wishlistItems.sort((a, b) => a.name.localeCompare(b.name));
  renderWishlist();
  syncToServer();
}

function sortByPrice() {
  log.info('sortByPrice');
  wishlistItems.sort((a, b) => {
    const pa = wishlistPrices[a.name];
    const pb = wishlistPrices[b.name];
    const va = (pa != null && pa >= 0) ? pa : -1;
    const vb = (pb != null && pb >= 0) ? pb : -1;
    return vb - va;
  });
  renderWishlist();
  syncToServer();
}

function toggleView() {
  const newMode = !isFullCardView();
  log.info('toggleView: fullCard=%s', newMode);
  setFullCardView(newMode);
  renderWishlist();
  syncViewToggleButton();
}

export function addToWishlist(cardName) {
  log.info('addToWishlist: %s', cardName);
  fetch('/api/wishlist/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: cardName }),
  })
    .then((r) => {
      if (!r.ok) throw new Error('Failed to add to wishlist');
      return r.json();
    })
    .then((data) => {
      wishlistItems = data.items.map((it) => ({ name: it.name, quantity: it.quantity }));
      wishlistPrices = {};
      for (const item of data.items) {
        if (item.price_usd != null) wishlistPrices[item.name] = item.price_usd;
      }
      renderWishlist();
    })
    .catch((err) => { log.error('addToWishlist failed', err); });
}

export function initWishlist() {
  log.info('initWishlist');
  syncViewToggleButton();

  const sortNameBtn = document.getElementById('wishlistSortNameBtn');
  if (sortNameBtn) sortNameBtn.addEventListener('click', sortByName);

  const sortPriceBtn = document.getElementById('wishlistSortPriceBtn');
  if (sortPriceBtn) sortPriceBtn.addEventListener('click', sortByPrice);

  const viewToggle = document.getElementById('wishlistViewToggleBtn');
  if (viewToggle) {
    viewToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleView();
    });
    viewToggle.addEventListener('mousedown', (e) => {
      e.stopPropagation();
    });
  }

  fetch('/api/wishlist')
    .then((r) => {
      if (!r.ok) throw new Error('Failed to load wishlist');
      return r.json();
    })
    .then((data) => {
      wishlistItems = data.items.map((it) => ({ name: it.name, quantity: it.quantity }));
      wishlistPrices = {};
      for (const item of data.items) {
        if (item.price_usd != null) wishlistPrices[item.name] = item.price_usd;
      }
      renderWishlist();
      log.info('Wishlist loaded: %d items', wishlistItems.length);
    })
    .catch((err) => { log.error('initWishlist: load failed', err); });
}

export function moveWishlistCardToDeck(cardName) {
  log.info('moveWishlistCardToDeck: %s', cardName);
  fetch('/api/card_type?name=' + encodeURIComponent(cardName))
    .then((r) => {
      if (!r.ok) throw new Error('card_type lookup failed: ' + r.status);
      return r.json();
    })
    .then((data) => {
      const typeKey = data.type_key;
      let targetList = document.getElementById('list-' + typeKey)
                    || document.getElementById('list-sorcery');
      if (!targetList) throw new Error('No main deck section for type: ' + typeKey);
      const existing = targetList.querySelector(
        '.card-stack[data-name="' + CSS.escape(cardName) + '"]'
      );
      if (existing) {
        const c = parseInt(existing.getAttribute('data-count'), 10) || 1;
        existing.setAttribute('data-count', String(c + 1));
        const badge = existing.querySelector('.card-stack-badge');
        if (badge) badge.textContent = String(c + 1);
      } else {
        targetList.appendChild(makeCardStackEl(cardName, 1));
      }
      updateSectionHeaderTotal(targetList);
      const section = targetList.closest('.section');
      if (section) {
        section.classList.remove('section-hidden');
        section.classList.remove('collapsed');
      }
      initSortable();
      return fetch('/api/wishlist/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: cardName, count: 1 }),
      });
    })
    .then((r) => {
      if (!r.ok) throw new Error('wishlist remove failed: ' + r.status);
      return r.json();
    })
    .then((data) => {
      wishlistItems = data.items.map((it) => ({ name: it.name, quantity: it.quantity }));
      wishlistPrices = {};
      for (const item of data.items) {
        if (item.price_usd != null) wishlistPrices[item.name] = item.price_usd;
      }
      renderWishlist();
    })
    .catch((err) => { log.error('moveWishlistCardToDeck failed', err); throw err; });
}

