/** On-demand GraphRAG deck recommendations, rendered as a collapsible board section. */

import { createLogger } from './logger.js';
import { collectState } from './deck.js';
import { scryfallImageUrlForSide } from './utils.js';

const log = createLogger('recommendations');
let cachedFingerprint = null;
let cachedManifestHash = null;
let cachedRecommendations = null;

function deckFingerprint() {
  const state = collectState();
  return JSON.stringify({
    colors: [...state.colors].sort(),
    format: state.format,
    commander: state.commander,
    colorless_only: state.colorless_only,
    cards: Object.keys(state)
      .filter((key) => Array.isArray(state[key]))
      .sort()
      .map((key) => [key, [...state[key]].sort()]),
  });
}

function setStatus(message, isError = false) {
  const el = document.getElementById('recommendationsStatus');
  el.textContent = message;
  el.className = 'recommendations-status' + (isError ? ' error' : '');
}

function setHeaderCount(count) {
  const label = document.querySelector('#section-recommendations .section-header-label');
  if (label) label.textContent = 'Recommendations (' + count + ')';
}

function addRecommendationToMainDeck(recommendation, cardEl, addButton) {
  fetch('/api/add_card', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: recommendation.name, board: 'main' }),
  })
    .then((response) => {
      if (!response.ok) {
        return response.json().then((body) => {
          throw new Error(body.detail || 'Could not add card');
        });
      }
      return response.json();
    })
    .then(() => {
      cachedFingerprint = null;
      cachedManifestHash = null;
      cachedRecommendations = null;
      cardEl.classList.add('recommendation-card-added');
      if (addButton) {
        addButton.disabled = true;
        addButton.textContent = 'Added';
      }
      setStatus(recommendation.name + ' was added to the main deck.');
    })
    .catch((error) => {
      if (addButton) addButton.disabled = false;
      log.error('Add recommendation failed', error);
      setStatus(error.message || 'Could not add recommended card.', true);
    });
}

function renderRecommendations(recommendations) {
  const list = document.getElementById('recommendationsList');
  list.innerHTML = '';
  recommendations.forEach((recommendation) => {
    const item = document.createElement('li');
    item.className = 'recommendation-card-wrap';

    const card = document.createElement('div');
    card.className = 'recommendation-card';

    const stack = document.createElement('div');
    stack.className = 'card-stack';
    stack.dataset.name = recommendation.name;
    stack.dataset.currentFaceName = recommendation.name;
    stack.setAttribute('data-name', recommendation.name);

    const img = document.createElement('img');
    img.className = 'card-img';
    img.src = scryfallImageUrlForSide(recommendation.name, 0);
    img.alt = recommendation.name;
    img.loading = 'lazy';
    img.title = 'Double-click to add to the main deck';
    img.onerror = function () {
      this.style.background = '#333';
    };
    img.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      addRecommendationToMainDeck(recommendation, card, addButton);
    });
    stack.appendChild(img);

    const body = document.createElement('div');
    body.className = 'recommendation-body';
    const title = document.createElement('div');
    title.className = 'recommendation-title';
    title.textContent = recommendation.name + ' · score ' + Number(recommendation.score).toFixed(1);
    const price = document.createElement('span');
    price.className = 'card-price recommendation-price';
    const priceVal = recommendation.price_usd;
    price.textContent = (priceVal != null && Number(priceVal) >= 0) ? '$' + Number(priceVal).toFixed(2) : '\u2014';
    const details = document.createElement('p');
    details.className = 'recommendation-details';
    details.textContent = recommendation.reasons.join(' · ');
    const source = document.createElement('p');
    source.className = 'recommendation-source';
    source.textContent = 'Evidence: ' + recommendation.sources.join(', ');

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'recommendation-add-btn';
    addButton.textContent = 'Add to Main Deck';
    addButton.addEventListener('click', () => {
      if (!confirm('Add ' + recommendation.name + ' to the main deck?')) return;
      addButton.disabled = true;
      addRecommendationToMainDeck(recommendation, card, addButton);
    });

    body.append(title, price, details, source, addButton);
    card.append(stack, body);
    item.appendChild(card);
    list.appendChild(item);
  });
}

function analyze() {
  const button = document.getElementById('analyzeRecommendationsBtn');
  const section = document.getElementById('section-recommendations');
  if (section) section.classList.remove('collapsed');
  const currentFingerprint = deckFingerprint();
  if (cachedFingerprint === currentFingerprint && cachedRecommendations !== null) {
    renderRecommendations(cachedRecommendations);
    setHeaderCount(cachedRecommendations.length);
    setStatus('Showing cached results for the current deck and graph index ' + cachedManifestHash.slice(0, 8) + '.');
    return;
  }
  button.disabled = true;
  setStatus('Analyzing deck graph &');
  fetch('/api/recommendations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit: 12 }),
  })
    .then((response) => {
      if (!response.ok) {
        return response.json().then((body) => {
          throw new Error(body.detail || 'Recommendation analysis failed');
        });
      }
      return response.json();
    })
    .then((data) => {
      const recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
      if (recommendations.length && (typeof data.analysis !== 'string' || !data.analysis.trim())) {
        throw new Error('GraphRAG returned recommendations without a local-search explanation.');
      }
      if (typeof data.graph_manifest_hash !== 'string' || !data.graph_manifest_hash) {
        throw new Error('GraphRAG response is missing its index fingerprint.');
      }
      cachedFingerprint = currentFingerprint;
      cachedManifestHash = data.graph_manifest_hash;
      cachedRecommendations = recommendations;
      renderRecommendations(recommendations);
      setHeaderCount(recommendations.length);
      setStatus(
        recommendations.length
          ? 'Showing ' + recommendations.length + ' graph-backed recommendations.'
          : 'No eligible graph-backed additions were found for this deck.'
      );
    })
    .catch((error) => {
      log.error('Recommendation analysis failed', error);
      setStatus(error.message || 'Recommendation analysis failed.', true);
    })
    .finally(() => {
      button.disabled = false;
    });
}

export function initRecommendations() {
  const button = document.getElementById('analyzeRecommendationsBtn');
  button.addEventListener('mousedown', (e) => e.stopPropagation());
  button.addEventListener('click', (e) => {
    e.stopPropagation();
    analyze();
  });
  window.addEventListener('mtg-deck-updated', () => {
    const current = deckFingerprint();
    if (cachedFingerprint !== null && cachedFingerprint !== current) {
      cachedFingerprint = null;
      cachedManifestHash = null;
      cachedRecommendations = null;
      setStatus('Deck changed. Refresh analysis for current recommendations.');
    }
  });
  log.info('Recommendations UI initialized');
}
