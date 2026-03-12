/** Modal open/close and postMessage handling for iframe modals. */

import { getSettings } from './settings.js';

export function initAdvSearchModal() {
  const modal = document.getElementById('advSearchModal');
  const iframe = document.getElementById('advSearchIframe');
  const closeBtn = document.getElementById('advSearchModalClose');
  document.getElementById('advancedSearchBtn').addEventListener('click', () => {
    const params = new URLSearchParams({ t: String(Date.now()) });
    const { colors, format, colorlessOnly } = getSettings();
    if (colors && colors.length) params.set('deck_colors', colors.join(','));
    if (format) params.set('deck_format', format);
    if (colorlessOnly) params.set('deck_colorless', 'true');
    iframe.src = '/search?' + params.toString();
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
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

export function initSemanticSearchModal() {
  const modal = document.getElementById('semanticSearchModal');
  const iframe = document.getElementById('semanticSearchIframe');
  const closeBtn = document.getElementById('semanticSearchModalClose');
  const ragPopup = document.getElementById('ragLoadingPopup');
  const ragPopupDismiss = document.getElementById('ragLoadingPopupDismiss');

  function showRagLoadingPopup() {
    if (ragPopup) {
      ragPopup.classList.add('visible');
      ragPopup.setAttribute('aria-hidden', 'false');
    } else {
      alert('RAG still loading');
    }
  }

  function hideRagLoadingPopup() {
    if (ragPopup) {
      ragPopup.classList.remove('visible');
      ragPopup.setAttribute('aria-hidden', 'true');
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

  document.getElementById('semanticSearchBtn').addEventListener('click', () => {
    fetch('/api/rag_ready')
      .then((r) => r.json())
      .then((data) => {
        if (data.ready) {
          iframe.src = '/semantic-search?t=' + String(Date.now());
          modal.classList.add('open');
          modal.setAttribute('aria-hidden', 'false');
        } else {
          showRagLoadingPopup();
        }
      })
      .catch(() => {
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
  const iframe = document.getElementById('importFormatIframe');
  const resultEl = document.getElementById('saveResult');

  document.getElementById('importDeckBtn').addEventListener('click', () => {
    resultEl.textContent = '';
    iframe.src = '/import-modal?t=' + Date.now();
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  });

  function closeImportModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    iframe.src = 'about:blank';
  }

  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'import-done') {
      closeImportModal();
      resultEl.textContent = e.data.message || 'Deck imported.';
      resultEl.style.color = e.data.isError ? '#f88' : '';
    }
  });

  closeBtn.addEventListener('click', closeImportModal);
  modal.addEventListener('click', (e) => {
    if (e.target === modal) closeImportModal();
  });
}
