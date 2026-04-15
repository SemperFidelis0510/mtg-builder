/** Modal open/close and postMessage handling for iframe modals. */

import { createLogger } from './logger.js';
import { getSettings } from './settings.js';

const log = createLogger('modals');

export function initAdvSearchModal() {
  const modal = document.getElementById('advSearchModal');
  const iframe = document.getElementById('advSearchIframe');
  const closeBtn = document.getElementById('advSearchModalClose');
  let loaded = false;

  function ensureLoaded() {
    if (loaded) return;
    loaded = true;
    iframe.src = '/search';
  }

  function pushSettings() {
    if (!iframe.contentWindow) return;
    const { colors, format, colorlessOnly } = getSettings();
    iframe.contentWindow.postMessage({
      type: 'update-deck-settings',
      deck_colors: colors && colors.length ? colors.join(',') : '',
      deck_format: format || '',
      deck_colorless: !!colorlessOnly,
    }, '*');
  }

  ensureLoaded();

  document.getElementById('advancedSearchBtn').addEventListener('click', () => {
    log.info('Opening advanced search modal');
    ensureLoaded();
    pushSettings();
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  });
  function closeModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
  });
}

export function initSynergyCheckerModal() {
  const modal = document.getElementById('synergyCheckerModal');
  const iframe = document.getElementById('synergyCheckerIframe');
  const closeBtn = document.getElementById('synergyCheckerModalClose');
  const ragPopup = document.getElementById('ragLoadingPopup');
  const ragPopupDismiss = document.getElementById('ragLoadingPopupDismiss');

  function showRagLoadingPopup() {
    if (ragPopup) {
      ragPopup.classList.add('visible');
      ragPopup.setAttribute('aria-hidden', 'false');
      ragPopup.style.cssText =
        'position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:1rem;box-sizing:border-box;';
      const backdrop = ragPopup.querySelector('.rag-loading-popup-backdrop');
      if (backdrop) {
        backdrop.style.cssText = 'position:absolute;inset:0;background:rgba(0,0,0,0.7);z-index:1;';
      }
      const content = ragPopup.querySelector('.rag-loading-popup-content');
      if (content) {
        content.style.cssText =
          'position:relative;z-index:2;background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:1.5rem 2rem;max-width:360px;box-shadow:0 8px 32px rgba(0,0,0,0.4);color:#e8e8e8;';
      }
    } else {
      alert('RAG still loading. Synergy check requires the embedding model. Please try again in a moment.');
    }
  }

  function hideRagLoadingPopup() {
    if (ragPopup) {
      ragPopup.classList.remove('visible');
      ragPopup.setAttribute('aria-hidden', 'true');
      ragPopup.style.cssText = '';
      const content = ragPopup.querySelector('.rag-loading-popup-content');
      if (content) content.style.cssText = '';
      const backdrop = ragPopup.querySelector('.rag-loading-popup-backdrop');
      if (backdrop) backdrop.style.cssText = '';
    }
  }

  if (ragPopupDismiss) {
    ragPopupDismiss.addEventListener('click', hideRagLoadingPopup);
  }
  if (ragPopup) {
    const backdrop = ragPopup.querySelector('.rag-loading-popup-backdrop');
    if (backdrop) {
      backdrop.addEventListener('click', hideRagLoadingPopup);
    }
  }

  document.getElementById('synergyCheckerBtn').addEventListener('click', () => {
    log.info('Synergy checker requested, checking RAG readiness');
    fetch('/api/rag_ready')
      .then((r) => r.json())
      .then((data) => {
        if (data.ready) {
          log.info('RAG ready, opening synergy checker');
          iframe.src = '/synergy-checker?t=' + String(Date.now());
          modal.classList.add('open');
          modal.setAttribute('aria-hidden', 'false');
        } else {
          log.warn('RAG not ready for synergy checker');
          showRagLoadingPopup();
        }
      })
      .catch((err) => {
        log.error('RAG readiness check failed', err);
        showRagLoadingPopup();
      });
  });

  function closeModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    iframe.src = 'about:blank';
  }
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeModal();
  });
}

export function initExportModal() {
  const modal = document.getElementById('exportFormatModal');
  const closeBtn = document.getElementById('exportFormatModalClose');
  const iframe = document.getElementById('exportFormatIframe');
  const resultEl = document.getElementById('saveResult');

  document.getElementById('exportDecklistBtn').addEventListener('click', () => {
    log.info('Opening export modal');
    resultEl.textContent = '';
    iframe.src = '/export-modal?t=' + Date.now();
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  });

  function closeExportModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    iframe.src = 'about:blank';
  }

  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'export-done') {
      closeExportModal();
      resultEl.textContent = e.data.message || 'Copied to clipboard.';
      resultEl.style.color = e.data.isError ? '#f88' : '';
    }
  });

  closeBtn.addEventListener('click', closeExportModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeExportModal();
  });
}

export function initImportModal() {
  const modal = document.getElementById('importFormatModal');
  const closeBtn = document.getElementById('importFormatModalClose');
  const textarea = document.getElementById('importTextarea');
  const selectEl = document.getElementById('importFormatSelect');
  const mergeCheckbox = document.getElementById('importMergeCheckbox');
  const submitBtn = document.getElementById('importSubmitBtn');
  const cancelBtn = document.getElementById('importCancelBtn');
  const errorEl = document.getElementById('importError');
  const resultEl = document.getElementById('saveResult');

  function openImportModal() {
    log.info('Opening import modal');
    resultEl.textContent = '';
    errorEl.textContent = '';
    textarea.value = '';
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    textarea.focus();
  }

  function closeImportModal(message, isError) {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    if (message) {
      resultEl.textContent = message;
      resultEl.style.color = isError ? '#f88' : '';
    }
  }

  document.getElementById('importDeckBtn').addEventListener('click', openImportModal);
  closeBtn.addEventListener('click', () => closeImportModal());
  cancelBtn.addEventListener('click', () => closeImportModal('Cancelled.', false));
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeImportModal();
  });

  submitBtn.addEventListener('click', () => {
    const text = textarea.value.trim();
    const format = selectEl.value;
    errorEl.textContent = '';
    if (!format) {
      errorEl.textContent = 'Select a format.';
      return;
    }
    fetch('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, format, merge: mergeCheckbox.checked }),
    })
      .then((r) => {
        if (!r.ok) {
          return r.json().then((err) => {
            let msg = 'Import failed.';
            if (typeof err.detail === 'string') msg = err.detail;
            else if (Array.isArray(err.detail) && err.detail[0] && err.detail[0].msg)
              msg = err.detail[0].msg;
            throw new Error(msg);
          });
        }
        return r.json();
      })
      .then(() => closeImportModal('Deck imported.', false))
      .catch((err) => {
        errorEl.textContent = err.message || 'Import failed.';
      });
  });
}

export function initBugReportModal() {
  const modal = document.getElementById('bugReportModal');
  const backdrop = modal.querySelector('.bug-report-modal-backdrop');
  const textarea = document.getElementById('bugReportTextarea');
  const submitBtn = document.getElementById('bugReportSubmit');
  const cancelBtn = document.getElementById('bugReportCancel');
  const statusEl = document.getElementById('bugReportStatus');

  function openModal() {
    textarea.value = '';
    statusEl.textContent = '';
    statusEl.className = 'bug-report-modal-status';
    submitBtn.disabled = false;
    modal.classList.add('visible');
    modal.setAttribute('aria-hidden', 'false');
    textarea.focus();
  }

  function closeModal() {
    modal.classList.remove('visible');
    modal.setAttribute('aria-hidden', 'true');
  }

  document.getElementById('bugReportBtn').addEventListener('click', () => { log.info('Opening bug report modal'); openModal(); });
  cancelBtn.addEventListener('click', closeModal);
  backdrop.addEventListener('click', closeModal);

  submitBtn.addEventListener('click', () => {
    const description = textarea.value.trim();
    if (!description) {
      statusEl.textContent = 'Please enter a description.';
      statusEl.className = 'bug-report-modal-status error';
      return;
    }
    submitBtn.disabled = true;
    statusEl.textContent = 'Submitting...';
    statusEl.className = 'bug-report-modal-status';

    fetch('/api/bug_report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ description }),
    })
      .then((r) => {
        if (!r.ok) return r.json().then((err) => { throw new Error(err.detail || 'Failed'); });
        return r.json();
      })
      .then((data) => {
        log.info('Bug report filed at', data.path);
        statusEl.textContent = 'Bug report filed: ' + data.path;
        statusEl.className = 'bug-report-modal-status success';
        setTimeout(closeModal, 2000);
      })
      .catch((err) => {
        log.error('Bug report submission failed', err.message);
        statusEl.textContent = 'Error: ' + (err.message || 'submission failed');
        statusEl.className = 'bug-report-modal-status error';
        submitBtn.disabled = false;
      });
  });
}
