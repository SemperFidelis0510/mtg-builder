/** Deck rendering and Chart.js statistics. */

import { TYPE_KEYS, TYPE_LABELS, SIDE_LABELS } from './constants.js';
import { collapseToStacks } from './utils.js';
import {
  makeCardStackEl,
  makeMaybeBoardCardEl,
  updateTotalsPanel,
} from './deck.js';
import { initSortable } from './sortable.js';
import { populateSettings } from './settings.js';

let statsPieChartInstance = null;
let statsMvChartInstance = null;

function renderStatsCharts(stats) {
  if (typeof Chart === 'undefined') return;
  const dist = stats.color_distribution || {};
  let mvDist = stats.mana_value_distribution || [];
  while (mvDist.length < 8) mvDist.push(0);

  const pieEl = document.getElementById('statsPieChart');
  if (pieEl) {
    let colorColors = ['#f0e6d3', '#0e4b8c', '#2d2d2d', '#c41e3a', '#2d5016'];
    let pieData = ['W', 'U', 'B', 'R', 'G'].map((c) => (dist[c] != null ? dist[c] : 0));
    const hasAny = pieData.some((v) => v > 0);
    let pieLabels = ['W', 'U', 'B', 'R', 'G'];
    if (!hasAny) {
      pieData = [1];
      pieLabels = ['No colored mana'];
      colorColors = ['#555'];
    }
    statsPieChartInstance = new Chart(pieEl, {
      type: 'pie',
      data: {
        labels: pieLabels,
        datasets: [{ data: pieData, backgroundColor: colorColors, borderColor: 'var(--border)', borderWidth: 1 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: { legend: { position: 'bottom', labels: { color: '#fff', padding: 8 } } },
      },
    });
  }
  const mvEl = document.getElementById('statsMvChart');
  if (mvEl) {
    const mvLabels = ['0', '1', '2', '3', '4', '5', '6', '7+'];
    let mvCreatures = (stats.mana_value_distribution && stats.mana_value_distribution.creatures)
      ? stats.mana_value_distribution.creatures.slice(0, 8)
      : [];
    let mvNonCreatures = (stats.mana_value_distribution && stats.mana_value_distribution.non_creatures)
      ? stats.mana_value_distribution.non_creatures.slice(0, 8)
      : [];
    while (mvCreatures.length < 8) mvCreatures.push(0);
    while (mvNonCreatures.length < 8) mvNonCreatures.push(0);
    statsMvChartInstance = new Chart(mvEl, {
      type: 'bar',
      data: {
        labels: mvLabels,
        datasets: [
          { label: 'Creatures', data: mvCreatures, backgroundColor: '#7cb342', borderColor: 'var(--border)', borderWidth: 1, stack: 'mv' },
          { label: 'Non-creatures', data: mvNonCreatures, backgroundColor: '#d8d8d8', borderColor: 'var(--border)', borderWidth: 1, stack: 'mv' },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        scales: {
          x: { title: { display: true, text: 'Mana value', color: '#fff' }, ticks: { color: '#fff' }, grid: { color: '#fff' }, stacked: true },
          y: { beginAtZero: true, ticks: { color: '#fff', stepSize: 1 }, grid: { color: '#fff' }, stacked: true },
        },
        plugins: { legend: { display: true, position: 'bottom', labels: { color: '#fff', padding: 8 } } },
      },
    });
  }
}

export function renderDeck(data) {
  const container = document.getElementById('deckSections');
  const statsContainer = document.getElementById('statisticsContainer');
  if (statsPieChartInstance) {
    statsPieChartInstance.destroy();
    statsPieChartInstance = null;
  }
  if (statsMvChartInstance) {
    statsMvChartInstance.destroy();
    statsMvChartInstance = null;
  }
  const existingSections = container.querySelectorAll('.section[data-type]');
  const expandedTypes = new Set(
    [...existingSections].filter((s) => !s.classList.contains('collapsed')).map((s) => s.dataset.type)
  );
  container.innerHTML = '';
  if (statsContainer) statsContainer.innerHTML = '';
  const deck = data.deck || data;
  const stats = data.stats || {};
  const maybeByType = deck.maybe_by_type || {};
  const sideboardByType = deck.sideboard_by_type || {};
  window._deckPrices = deck.prices != null && typeof deck.prices === 'object' ? deck.prices : {};
  window._lastStats = stats;

  TYPE_KEYS.forEach((key) => {
    const cards = Array.isArray(deck[key]) ? deck[key] : [];
    const stacks = collapseToStacks(cards);
    const section = document.createElement('div');
    section.className = 'section collapsed';
    section.dataset.type = key;
    const total = cards.length;
    section.innerHTML =
      '<div class="section-header"><span class="section-header-label">' + (TYPE_LABELS[key] || key) + ' (' + total + ')</span></div>' +
      '<div class="section-body"><ul class="card-list" id="list-' + key + '"></ul></div>';
    const list = section.querySelector('.card-list');
    stacks.forEach((s) => {
      list.appendChild(makeCardStackEl(s.name, s.count));
    });
    if (expandedTypes.has(key)) {
      section.classList.remove('collapsed');
    }
    container.appendChild(section);
  });

  const statsSection = document.createElement('div');
  statsSection.id = 'deckStatisticsSection';
  statsSection.className = 'section section-statistics';
  const dist = stats.color_distribution || {};
  const colorLine = ['W', 'U', 'B', 'R', 'G']
    .map((c) => {
      const pct = dist[c] != null ? dist[c] : 0;
      return c + ' ' + pct + '%';
    })
    .join(' · ');
  statsSection.innerHTML =
    '<div class="section-header">Statistics</div>' +
    '<div class="section-body">' +
    '<div class="deck-stats-totals"></div>' +
    '<div class="deck-stats-colors"><strong>Color distribution</strong> (colored mana in costs):<br><span class="deck-stats-color-line">' +
    colorLine +
    '</span></div>' +
    '<div class="deck-stats-charts">' +
    '<div class="deck-stats-chart-wrap"><h4>Color distribution</h4><canvas id="statsPieChart" aria-label="Pie chart of colored mana symbols"></canvas></div>' +
    '<div class="deck-stats-chart-wrap"><h4>Mana value (non-land)</h4><canvas id="statsMvChart" aria-label="Histogram of mana values"></canvas></div>' +
    '</div>' +
    '</div>';
  if (statsContainer) statsContainer.appendChild(statsSection);
  renderStatsCharts(stats);

  const sideKeys = ['maybe', 'sideboard'];
  sideKeys.forEach((key) => {
    const names = Array.isArray(deck[key + '_names']) ? deck[key + '_names'] : (Array.isArray(deck[key]) ? deck[key] : []);
    const listEl = document.getElementById('list-' + key);
    if (listEl) {
      listEl.innerHTML = '';
      collapseToStacks(names).forEach((s) => {
        listEl.appendChild(makeMaybeBoardCardEl(s.name, s.count));
      });
      const sectionEl = document.getElementById('section-' + key);
      if (sectionEl) {
        const hdr = sectionEl.querySelector('.section-header');
        if (hdr) {
          const labelSpan = hdr.querySelector('.section-header-label');
          if (labelSpan) labelSpan.textContent = SIDE_LABELS[key] + ' (' + names.length + ')';
          else hdr.textContent = SIDE_LABELS[key] + ' (' + names.length + ')';
        }
      }
    }
  });

  document.getElementById('saveBtn').disabled = false;
  populateSettings(deck);
  updateTotalsPanel();
  initSortable();
}
