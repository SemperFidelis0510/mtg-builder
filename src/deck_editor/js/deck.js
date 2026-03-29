/** Card DOM elements, section headers, deck state, and server sync. */

import { TYPE_KEYS, TYPE_LABELS, SIDE_LABELS } from './constants.js';
import {
  splitCardFaces,
  scryfallImageUrlForSide,
} from './utils.js';
import { getSettings } from './settings.js';

let syncToServerTimer = null;
const cardFaceIndexByName = new Map();

export function resetCardFaceState() {
  cardFaceIndexByName.clear();
}

export function getCardFaceIndex(name) {
  if (!name) return 0;
  if (!cardFaceIndexByName.has(name)) return 0;
  return cardFaceIndexByName.get(name);
}

function setCardFaceIndex(name, faceIndex) {
  cardFaceIndexByName.set(name, faceIndex);
}

export function updateSectionHeaderTotal(listEl) {
  if (!listEl) return;
  const section = listEl.closest('.section');
  const typeKey = section ? section.dataset.type : null;
  let total = 0;
  listEl.querySelectorAll('.card-stack[data-count]').forEach((el) => {
    total += parseInt(el.getAttribute('data-count') || '1', 10);
  });
  if (section && typeKey) {
    const label = typeKey === 'commander'
      ? 'Commander'
      : (TYPE_LABELS[typeKey] || SIDE_LABELS[typeKey] || typeKey);
    const header = section.querySelector('.section-header');
    if (header) {
      const labelSpan = header.querySelector('.section-header-label');
      if (labelSpan) labelSpan.textContent = label + ' (' + total + ')';
      else header.textContent = label + ' (' + total + ')';
    }
  }
  updateTotalsPanel();
  syncDeckToServer();
}

/**
 * Wires -/+ to update data-count on wrap; at 0 removes li and syncs list header.
 * @returns {HTMLDivElement} .card-stack-controls (append after badge)
 */
function attachStackQuantityControls(wrap, li, badge) {
  function setCount(c) {
    c = Math.max(0, c);
    wrap.setAttribute('data-count', String(c));
    badge.textContent = String(c);
    if (c === 0) {
      const list = li.parentNode;
      li.remove();
      if (list && list.id && list.id.indexOf('list-') === 0) updateSectionHeaderTotal(list);
      return;
    }
    updateSectionHeaderTotal(li.parentNode);
  }

  const controls = document.createElement('div');
  controls.className = 'card-stack-controls';
  const btnPlus = document.createElement('button');
  btnPlus.textContent = '+';
  btnPlus.type = 'button';
  const btnMinus = document.createElement('button');
  btnMinus.textContent = '-';
  btnMinus.type = 'button';

  btnPlus.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
  btnPlus.addEventListener('click', (e) => {
    e.stopPropagation();
    setCount(parseInt(wrap.getAttribute('data-count'), 10) + 1);
  });
  btnMinus.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
  btnMinus.addEventListener('click', (e) => {
    e.stopPropagation();
    setCount(parseInt(wrap.getAttribute('data-count'), 10) - 1);
  });

  controls.appendChild(btnMinus);
  controls.appendChild(btnPlus);
  return controls;
}

export function makeCardStackEl(name, count) {
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
  setCardFaceIndex(name, currentFaceIndex);
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
  }
  img.onerror = function () {
    this.style.background = '#333';
    this.style.minWidth = '150px';
    this.style.minHeight = '210px';
  };
  if (twoSided) {
    img.addEventListener('click', (e) => {
      e.stopPropagation();
      let faceIdx = getCardFaceIndex(name);
      faceIdx = (faceIdx + 1) % faces.length;
      setCardFaceIndex(name, faceIdx);
      const currentFaceName = faces[faceIdx];
      wrap.dataset.currentFaceIndex = String(faceIdx);
      wrap.dataset.currentFaceName = currentFaceName;
      img.src = scryfallImageUrlForSide(name, faceIdx);
      img.alt = currentFaceName;
    });
  }

  const badge = document.createElement('span');
  badge.className = 'card-stack-badge';
  badge.textContent = String(count);

  const controls = attachStackQuantityControls(wrap, li, badge);
  const banner = document.createElement('div');
  banner.className = 'card-stack-banner';
  banner.appendChild(badge);
  banner.appendChild(controls);
  const priceSpan = document.createElement('span');
  priceSpan.className = 'card-price';
  const prices = window._deckPrices || {};
  const priceVal = prices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '—';
  banner.appendChild(priceSpan);
  wrap.appendChild(img);
  wrap.appendChild(banner);
  li.appendChild(wrap);
  return li;
}

export function makeMaybeBoardCardEl(name, count, options) {
  const quantityControls = options != null && options.quantityControls === true;
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
  const prices = window._deckPrices || {};
  const priceVal = prices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '—';
  wrap.appendChild(nameSpan);
  if (quantityControls) {
    const qtyWrap = document.createElement('span');
    qtyWrap.className = 'maybe-board-qty-wrap';
    qtyWrap.appendChild(badge);
    qtyWrap.appendChild(attachStackQuantityControls(wrap, li, badge));
    wrap.appendChild(qtyWrap);
  } else {
    wrap.appendChild(badge);
  }
  wrap.appendChild(priceSpan);
  li.appendChild(wrap);
  return li;
}

function sumListPriceUsd(listEl) {
  if (!listEl) return 0;
  const prices = window._deckPrices || {};
  let sum = 0;
  listEl.querySelectorAll('.card-stack[data-name]').forEach((el) => {
    const name = el.getAttribute('data-name');
    const count = parseInt(el.getAttribute('data-count') || '1', 10);
    const p = prices[name];
    if (p != null && typeof p === 'number' && p >= 0) sum += p * count;
  });
  return sum;
}

export function updateTotalsPanel() {
  const prices = window._deckPrices || {};
  let mainAndSideboardTotal = 0;
  TYPE_KEYS.forEach((key) => {
    mainAndSideboardTotal += sumListPriceUsd(document.getElementById('list-' + key));
  });
  mainAndSideboardTotal += sumListPriceUsd(document.getElementById('list-sideboard'));
  const maybeTotal = sumListPriceUsd(document.getElementById('list-maybe'));

  const bannerEl = document.getElementById('deckBannerPrice');
  if (bannerEl) {
    bannerEl.textContent = '$' + Number(mainAndSideboardTotal).toFixed(2);
  }
  const maybePriceEl = document.getElementById('maybeBoardPrice');
  if (maybePriceEl) {
    maybePriceEl.textContent = '$' + Number(maybeTotal).toFixed(2);
  }

  const statsSection = document.getElementById('deckStatisticsSection');
  if (!statsSection) return;
  let total = 0;
  let landCount = 0;
  TYPE_KEYS.forEach((key) => {
    const list = document.getElementById('list-' + key);
    if (list) {
      let n = 0;
      list.querySelectorAll('.card-stack[data-count]').forEach((el) => {
        n += parseInt(el.getAttribute('data-count') || '1', 10);
      });
      total += n;
      if (key === 'land') landCount = n;
    }
  });
  const nonLand = total - landCount;
  const totalsEl = statsSection.querySelector('.deck-stats-totals');
  if (totalsEl) {
    totalsEl.innerHTML =
      '<span class="deck-total-item"><strong>Total cards:</strong> ' + total + '</span>' +
      '<span class="deck-total-item"><strong>Non-land:</strong> ' + nonLand + '</span>' +
      '<span class="deck-total-item"><strong>Lands:</strong> ' + landCount + '</span>' +
      '<span class="deck-total-item"><strong>Total price (USD):</strong> $' + Number(mainAndSideboardTotal).toFixed(2) + '</span>';
  }
}

export function addCardToDeck(cardName, typeKey) {
  const listId = 'list-' + typeKey;
  const list = document.getElementById(listId);
  if (!list) return;
  let existing = null;
  list.querySelectorAll('.card-stack[data-name]').forEach((el) => {
    if (el.getAttribute('data-name') === cardName) existing = el;
  });
  if (existing) {
    let c = parseInt(existing.getAttribute('data-count'), 10) || 1;
    existing.setAttribute('data-count', String(c + 1));
    existing.querySelector('.card-stack-badge').textContent = String(c + 1);
    updateSectionHeaderTotal(list);
  } else {
    list.appendChild(makeCardStackEl(cardName, 1));
    updateSectionHeaderTotal(list);
  }
  const section = list.closest('.section');
  if (section) section.classList.remove('section-hidden');
}

export function syncDeckToServer() {
  if (syncToServerTimer) clearTimeout(syncToServerTimer);
  syncToServerTimer = setTimeout(() => {
    syncToServerTimer = null;
    const state = collectState();
    const body = {
      name: state.name,
      colors: state.colors,
      description: state.description,
      format: state.format,
      commander: state.commander,
      colorless_only: state.colorless_only,
      maybe: state.maybe,
      sideboard: state.sideboard,
    };
    TYPE_KEYS.forEach((key) => {
      body[key] = state[key];
    });
    fetch('/api/deck', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then((r) => {
        if (r.ok) return r.json();
      })
      .catch(() => {});
  }, 400);
}

export function collectState() {
  function expandList(listEl) {
    const arr = [];
    if (!listEl) return arr;
    listEl.querySelectorAll('.card-stack[data-name][data-count]').forEach((el) => {
      const name = el.getAttribute('data-name');
      const count = parseInt(el.getAttribute('data-count'), 10) || 1;
      for (let i = 0; i < count; i++) arr.push(name);
    });
    return arr;
  }
  const meta = getSettings();
  const commanderList = document.getElementById('list-commander');
  let commanderName = '';
  if (commanderList) {
    const commanderEl = commanderList.querySelector('.card-stack[data-name]');
    if (commanderEl) {
      const raw = commanderEl.getAttribute('data-name');
      commanderName = raw != null ? String(raw).trim() : '';
    }
  }
  const deck = {
    name: meta.name,
    colors: meta.colors,
    description: meta.description,
    format: meta.format,
    commander: commanderName,
    colorless_only: meta.colorlessOnly,
  };
  TYPE_KEYS.forEach((key) => {
    deck[key] = expandList(document.getElementById('list-' + key));
  });
  deck.maybe = expandList(document.getElementById('list-maybe'));
  deck.sideboard = expandList(document.getElementById('list-sideboard'));
  return deck;
}

export function getDeckMeta() {
  return fetch('/api/deck/meta')
    .then((r) => {
      if (!r.ok) return { name: '', colors: [], description: '', format: '', commander: '', colorless_only: false };
      return r.json();
    })
    .then((data) => ({
      name: data.name != null ? data.name : '',
      colors: Array.isArray(data.colors) ? data.colors : [],
      description: data.description != null ? data.description : '',
      format: data.format != null ? data.format : '',
      commander: data.commander != null ? data.commander : '',
      colorless_only: data.colorless_only === true,
    }))
    .catch(() => ({ name: '', colors: [], description: '', format: '', commander: '', colorless_only: false }));
}
