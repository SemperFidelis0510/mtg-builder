/** On-demand GraphRAG deck recommendations with fingerprint-scoped client cache. */

import { createLogger } from './logger.js';
import { collectState } from './deck.js';

const log = createLogger('recommendations');
let cachedFingerprint = null;
let cachedManifestHash = null;
let cachedRecommendations = null;
let cachedAnalysis = null;

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

function renderRecommendations(recommendations) {
  const list = document.getElementById('recommendationsList');
  list.innerHTML = '';
  recommendations.forEach((recommendation) => {
    const item = document.createElement('li');
    item.className = 'recommendation-item';

    const content = document.createElement('div');
    content.className = 'recommendation-content';
    const title = document.createElement('div');
    title.className = 'recommendation-title';
    title.textContent = recommendation.name + ' · score ' + Number(recommendation.score).toFixed(1);
    const details = document.createElement('p');
    details.className = 'recommendation-details';
    details.textContent = recommendation.reasons.join(' · ');
    const source = document.createElement('p');
    source.className = 'recommendation-source';
    source.textContent = 'Evidence: ' + recommendation.sources.join(', ');
    content.append(title, details, source);

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'recommendation-add-btn';
    addButton.textContent = 'Add to Maybe';
    addButton.addEventListener('click', () => {
      if (!confirm('Add ' + recommendation.name + ' to the Maybe board?')) return;
      addButton.disabled = true;
      fetch('/api/add_card', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: recommendation.name, board: 'maybe' }),
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
          cachedAnalysis = null;
          addButton.textContent = 'Added';
          setStatus(recommendation.name + ' was added to the Maybe board.');
        })
        .catch((error) => {
          addButton.disabled = false;
          log.error('Add recommendation failed', error);
          setStatus(error.message || 'Could not add recommended card.', true);
        });
    });
    item.append(content, addButton);
    list.appendChild(item);
  });
}

function renderAnalysis(analysis) {
  const el = document.getElementById('recommendationsAnalysis');
  el.textContent = analysis || '';
}

function analyze() {
  const button = document.getElementById('analyzeRecommendationsBtn');
  const currentFingerprint = deckFingerprint();
  if (cachedFingerprint === currentFingerprint && cachedRecommendations !== null) {
    renderRecommendations(cachedRecommendations);
    renderAnalysis(cachedAnalysis);
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
      cachedAnalysis = recommendations.length ? data.analysis : null;
      renderRecommendations(recommendations);
      renderAnalysis(cachedAnalysis);
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
  document.getElementById('analyzeRecommendationsBtn').addEventListener('click', analyze);
  window.addEventListener('mtg-deck-updated', () => {
    const current = deckFingerprint();
    if (cachedFingerprint !== null && cachedFingerprint !== current) {
      cachedFingerprint = null;
      cachedManifestHash = null;
      cachedRecommendations = null;
      cachedAnalysis = null;
      renderAnalysis(null);
      setStatus('Deck changed. Refresh analysis for current recommendations.');
    }
  });
  log.info('Recommendations UI initialized');
}
