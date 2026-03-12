/** Card DOM elements, section headers, deck state, and server sync. */

import { TYPE_KEYS, TYPE_LABELS, SIDE_LABELS } from './constants.js';
import { scryfallImageUrl } from './utils.js';
import { getSettings } from './settings.js';

let syncToServerTimer = null;

export function updateSectionHeaderTotal(listEl) {
  if (!listEl) return;
  const section = listEl.closest('.section');
  const typeKey = section ? section.dataset.type : null;
  let total = 0;
  listEl.querySelectorAll('.card-stack[data-count]').forEach((el) => {
    total += parseInt(el.getAttribute('data-count') || '1', 10);
  });
  if (section && typeKey) {
    const label = TYPE_LABELS[typeKey] || SIDE_LABELS[typeKey] || typeKey;
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

  const img = document.createElement('img');
  img.className = 'card-img';
  img.src = scryfallImageUrl(name);
  img.alt = name;
  img.title = name;
  img.loading = 'lazy';
  img.onerror = function () {
    this.style.background = '#333';
    this.style.minWidth = '150px';
    this.style.minHeight = '210px';
  };

  const badge = document.createElement('span');
  badge.className = 'card-stack-badge';
  badge.textContent = String(count);

  const controls = document.createElement('div');
  controls.className = 'card-stack-controls';
  const btnPlus = document.createElement('button');
  btnPlus.textContent = '+';
  btnPlus.type = 'button';
  const btnMinus = document.createElement('button');
  btnMinus.textContent = '-';
  btnMinus.type = 'button';

  function setCount(c) {
    c = Math.max(0, c);
    wrap.setAttribute('data-count', String(c));
    badge.textContent = String(c);
    if (c === 0) {
      li.remove();
      const list = li.parentNode;
      if (list && list.id && list.id.indexOf('list-') === 0) updateSectionHeaderTotal(list);
      return;
    }
    updateSectionHeaderTotal(li.parentNode);
  }

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
  const banner = document.createElement('div');
  banner.className = 'card-stack-banner';
  banner.appendChild(badge);
  banner.appendChild(controls);
  wrap.appendChild(img);
  wrap.appendChild(banner);
  const priceSpan = document.createElement('span');
  priceSpan.className = 'card-price';
  const prices = window._deckPrices || {};
  const priceVal = prices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '—';
  wrap.appendChild(priceSpan);
  li.appendChild(wrap);
  return li;
}

export function makeMaybeBoardCardEl(name, count) {
  count = Math.max(1, parseInt(count, 10) || 1);
  const li = document.createElement('li');
  li.style.listStyle = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'card-stack maybe-board-item';
  wrap.dataset.name = name;
  wrap.dataset.count = String(count);
  wrap.setAttribute('data-name', name);
  wrap.setAttribute('data-count', String(count));

  const nameSpan = document.createElement('span');
  nameSpan.className = 'maybe-board-name';
  nameSpan.textContent = name;
  nameSpan.title = name;

  const badge = document.createElement('span');
  badge.className = 'card-stack-badge';
  badge.textContent = String(count);

  const priceSpan = document.createElement('span');
  priceSpan.className = 'card-price';
  const prices = window._deckPrices || {};
  const priceVal = prices[name];
  priceSpan.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '—';
  wrap.appendChild(nameSpan);
  wrap.appendChild(badge);
  wrap.appendChild(priceSpan);
  li.appendChild(wrap);
  return li;
}

export function updateTotalsPanel() {
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
    const totalPrice = (window._lastStats && window._lastStats.total_price_usd != null) ? window._lastStats.total_price_usd : 0;
    totalsEl.innerHTML =
      '<span class="deck-total-item"><strong>Total cards:</strong> ' + total + '</span>' +
      '<span class="deck-total-item"><strong>Non-land:</strong> ' + nonLand + '</span>' +
      '<span class="deck-total-item"><strong>Lands:</strong> ' + landCount + '</span>' +
      '<span class="deck-total-item"><strong>Total price (USD):</strong> $' + Number(totalPrice).toFixed(2) + '</span>';
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
      colorless_only: state.colorless_only,
      creature: state.creature,
      instant: state.instant,
      sorcery: state.sorcery,
      artifact: state.artifact,
      enchantment: state.enchantment,
      planeswalker: state.planeswalker,
      land: state.land,
      maybe: state.maybe,
      sideboard: state.sideboard,
    };
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
  const deck = {
    name: meta.name,
    colors: meta.colors,
    description: meta.description,
    format: meta.format,
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
      if (!r.ok) return { name: '', colors: [], description: '', format: '', colorless_only: false };
      return r.json();
    })
    .then((data) => ({
      name: data.name != null ? data.name : '',
      colors: Array.isArray(data.colors) ? data.colors : [],
      description: data.description != null ? data.description : '',
      format: data.format != null ? data.format : '',
      colorless_only: data.colorless_only === true,
    }))
    .catch(() => ({ name: '', colors: [], description: '', format: '', colorless_only: false }));
}
