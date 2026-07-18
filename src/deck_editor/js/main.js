/** Deck editor main entry: inits and event listeners. */

import { createLogger } from './logger.js';
import { TYPE_KEYS } from './constants.js';
import { suggestedSaveName } from './utils.js';
import {
  buildOpenFilePickerOptions,
  buildSaveFilePickerOptions,
  clearLastLoadedDeckFile,
  getLastLoadedFileName,
  rememberSavedFileHandle,
  setLastLoadedDeckFile,
} from './deck-file.js';
import { initCardPreview } from './card-preview.js';
import { initContextMenu } from './context-menu.js';
import { updateSectionHeaderTotal, getDeckMeta, collectState, syncDeckToServer } from './deck.js';
import { renderDeck } from './render.js';
import { initSearch } from './search.js';
import { initAdvSearchModal, initSynergyCheckerModal, initExportModal, initImportModal, initIssueModal } from './modals.js';
import { initSettings, populateSettings } from './settings.js';
import { initAgentChat } from './agent-chat.js';
import { initAgentRules } from './agent-rules.js';
import { initMaybeBoardViewUi } from './maybe-board-view.js';
import { initWishlist } from './wishlist.js';
import { initDeckSort } from './deck-sort.js';
import { initRecommendations } from './recommendations.js';

const log = createLogger('main');

log.info('Deck editor initializing');

// Register core UI interactions before module inits, so basic folding/unfolding
// continues to work even if a later init throws (and blocks the rest of this file).
const deckSectionsZone = document.getElementById('deckSectionsZone');
if (!deckSectionsZone) {
  log.error('Missing #deckSectionsZone; section toggles will not work');
} else {
  deckSectionsZone.addEventListener('click', (e) => {
    const header = e.target.closest('.section-header');
    if (header) {
      const section = header.closest('.section');
      if (section) section.classList.toggle('collapsed');
    }
  });
}

const toolsHeader = document.getElementById('toolsHeader');
const toolsSection = document.getElementById('toolsSection');
if (!toolsHeader || !toolsSection) {
  log.error('Missing tools section DOM (#toolsHeader or #toolsSection)');
} else {
  toolsHeader.addEventListener('click', () => {
    toolsSection.classList.toggle('collapsed');
  });
}

[document.getElementById('section-maybe'), document.getElementById('section-sideboard')].forEach((el) => {
  if (!el) return;
  el.addEventListener('click', (e) => {
    const header = e.target.closest('.section-header');
    if (header) {
      const section = header.closest('.section');
      if (section) section.classList.toggle('collapsed');
    }
  });
});

initCardPreview();
initContextMenu();
initSettings(syncDeckToServer);
initSearch();
initAdvSearchModal();
initSynergyCheckerModal();
initExportModal();
initImportModal();
initIssueModal();
initAgentChat();
initAgentRules();
initMaybeBoardViewUi();
initWishlist();
initDeckSort();
initRecommendations();
log.info('All modules initialized');
document.getElementById('collapseAllBtn').addEventListener('click', () => {
  document.querySelectorAll('#commanderSectionHost .section, #deckSections .section, #section-maybe, #section-sideboard').forEach((s) => s.classList.add('collapsed'));
});
document.getElementById('expandAllBtn').addEventListener('click', () => {
  document.querySelectorAll('#commanderSectionHost .section, #deckSections .section, #section-maybe, #section-sideboard').forEach((s) => s.classList.remove('collapsed'));
});

document.getElementById('clearAllBtn').addEventListener('click', () => {
  if (!confirm('Clear all cards from the deck (including sideboard and maybe board)?')) return;
  log.info('Clearing all cards from deck');
  clearLastLoadedDeckFile();
  populateSettings({ name: '', description: '', colors: [], format: '', commander: '', colorless_only: false });
  TYPE_KEYS.forEach((key) => {
    const listEl = document.getElementById('list-' + key);
    if (listEl) {
      listEl.innerHTML = '';
      updateSectionHeaderTotal(listEl);
    }
  });
  ['maybe', 'sideboard'].forEach((key) => {
    const listEl = document.getElementById('list-' + key);
    if (listEl) {
      listEl.innerHTML = '';
      updateSectionHeaderTotal(listEl);
    }
  });
  const commanderListEl = document.getElementById('list-commander');
  if (commanderListEl) {
    commanderListEl.innerHTML = '';
    updateSectionHeaderTotal(commanderListEl);
  }
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
  fetch('/api/deck', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then((r) => r.ok ? r.json() : Promise.reject(new Error('Clear sync failed')))
    .then((data) => { log.info('Deck cleared and synced'); return renderDeck(data); })
    .catch((err) => { log.error('Clear deck sync failed', err); });
});

function moveAllCards(fromBoard, toBoard) {
  log.info('Moving all cards from %s to %s', fromBoard, toBoard);
  fetch('/api/move_all_cards', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_board: fromBoard, to_board: toBoard }),
  })
    .then((r) => r.ok ? r.json() : Promise.reject(new Error('Move all failed')))
    .then(() => log.info('Moved all cards from %s to %s', fromBoard, toBoard))
    .catch((err) => log.error('Move all cards failed', err));
}

document.getElementById('moveAllToMaybeBtn').addEventListener('click', () => moveAllCards('main', 'maybe'));
document.getElementById('moveAllToMainBtn').addEventListener('click', () => moveAllCards('maybe', 'main'));

document.getElementById('saveBtn').addEventListener('click', () => {
  log.info('Save button clicked');
  const resultEl = document.getElementById('saveResult');
  resultEl.textContent = '';
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
      if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Sync failed'); });
      return fetch('/api/export?format=json');
    })
    .then((r) => {
      if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Export failed'); });
      return r.json();
    })
    .then((data) => {
      const blob = new Blob([data.text], { type: 'application/json' });
      const saveName = suggestedSaveName(state.name, getLastLoadedFileName());
      if (typeof window.showSaveFilePicker === 'function') {
        window
          .showSaveFilePicker(buildSaveFilePickerOptions(saveName))
          .then((handle) => rememberSavedFileHandle(handle).then(() => handle))
          .then((handle) => handle.createWritable())
          .then((writable) => writable.write(blob).then(() => writable.close()))
          .then(() => {
            log.info('Deck saved via file picker');
            resultEl.textContent = 'Saved.';
          })
          .catch((err) => {
            if (err.name === 'AbortError') {
              log.debug('Save cancelled by user');
              resultEl.textContent = 'Cancelled.';
            } else {
              log.error('Save failed', err.message);
              resultEl.textContent = 'Error: ' + (err.message || 'save failed');
              resultEl.style.color = '#f88';
            }
          });
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = saveName;
        a.click();
        URL.revokeObjectURL(url);
        resultEl.textContent = 'Download started (browser may not support save dialog).';
      }
    })
    .catch((err) => {
      log.error('Save flow failed', err.message);
      resultEl.textContent = 'Error: ' + (err.message || 'save failed');
      resultEl.style.color = '#f88';
    });
});

function loadDeckFromJson(json, sourceFileName, sourceFileHandle) {
  return fetch('/api/deck', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(json),
  })
    .then((r) => {
      if (!r.ok) throw new Error('Load failed');
      return fetch('/api/deck');
    })
    .then((r) => r.json())
    .then(renderDeck)
    .then(() => setLastLoadedDeckFile(sourceFileHandle || null, sourceFileName || null))
    .then(() => {
      log.info('Deck loaded from file', sourceFileName || '(unknown)');
      document.getElementById('searchMsg').textContent = 'Deck loaded.';
      document.getElementById('searchMsg').className = 'search-msg';
    });
}

function loadDeckFromFile(file, fileHandle) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => {
      let json;
      try {
        json = JSON.parse(fr.result);
      } catch (e) {
        log.error('Invalid JSON in loaded file', e.message);
        document.getElementById('searchMsg').textContent = 'Invalid JSON file.';
        document.getElementById('searchMsg').className = 'search-msg error';
        reject(e);
        return;
      }
      loadDeckFromJson(json, file.name, fileHandle)
        .then(resolve)
        .catch(reject);
    };
    fr.onerror = () => reject(fr.error || new Error('File read failed'));
    fr.readAsText(file);
  });
}

document.getElementById('loadBtn').addEventListener('click', () => {
  if (typeof window.showOpenFilePicker === 'function') {
    window
      .showOpenFilePicker(buildOpenFilePickerOptions())
      .then((handles) => {
        const handle = handles[0];
        if (!handle) return;
        return handle.getFile().then((file) => loadDeckFromFile(file, handle));
      })
      .catch((err) => {
        if (err.name === 'AbortError') {
          log.debug('Load cancelled by user');
          return;
        }
        log.error('Load deck via file picker failed', err);
        document.getElementById('searchMsg').textContent = 'Load failed.';
        document.getElementById('searchMsg').className = 'search-msg error';
      });
    return;
  }
  document.getElementById('loadFileInput').click();
});
document.getElementById('loadFileInput').addEventListener('change', function () {
  const file = this.files && this.files[0];
  if (!file) return;
  log.info('Loading deck from file', file.name);
  loadDeckFromFile(file, null).catch((err) => {
    if (err && err.message !== 'Load failed') return;
    log.error('Load deck from file failed', err);
    document.getElementById('searchMsg').textContent = 'Load failed.';
    document.getElementById('searchMsg').className = 'search-msg error';
  });
  this.value = '';
});

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
tabButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    const tab = btn.getAttribute('data-tab');
    tabButtons.forEach((b) => b.classList.remove('active'));
    tabPanels.forEach((p) => p.classList.toggle('active', p.id === tab + 'Tab'));
    btn.classList.add('active');
  });
});

log.info('Connecting to SSE /api/events');
const evtSource = new EventSource('/api/events');
evtSource.addEventListener('deck_updated', (e) => {
  log.debug('SSE deck_updated received');
  const data = JSON.parse(e.data);
  renderDeck(data);
  window.dispatchEvent(new CustomEvent('mtg-deck-updated'));
});
evtSource.onerror = () => { log.warn('SSE connection error, will reconnect'); };
