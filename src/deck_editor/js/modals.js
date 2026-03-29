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
    fetch('/api/rag_ready')
      .then((r) => r.json())
      .then((data) => {
        if (data.ready) {
          iframe.src = '/synergy-checker?t=' + String(Date.now());
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
