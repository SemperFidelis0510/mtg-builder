/** Contenteditable chat prompt: drag deck cards in as inline chips; serialize to quoted names. */

import { createLogger } from './logger.js';

const log = createLogger('prompt-editor');

/** Full card name captured at dragstart; null when no card drag is in progress. */
let draggedCardName = null;

/**
 * Resolve the .card-stack[data-name] involved in a drag, whether the event target is the
 * stack itself, an inner handle (.card-img), or the wrapping <li>.
 * @param {EventTarget} target
 * @returns {HTMLElement|null}
 */
function findDraggedStack(target) {
  if (!(target instanceof Element)) return null;
  const viaClosest = target.closest('.card-stack[data-name]');
  if (viaClosest) return viaClosest;
  return target.querySelector ? target.querySelector('.card-stack[data-name]') : null;
}

/**
 * Caret Range under a screen point, across engines (Chromium vs Firefox).
 * @param {number} x
 * @param {number} y
 * @returns {Range|null}
 */
function caretRangeFromPoint(x, y) {
  if (typeof document.caretRangeFromPoint === 'function') {
    return document.caretRangeFromPoint(x, y);
  }
  if (typeof document.caretPositionFromPoint === 'function') {
    const pos = document.caretPositionFromPoint(x, y);
    if (pos) {
      const range = document.createRange();
      range.setStart(pos.offsetNode, pos.offset);
      range.collapse(true);
      return range;
    }
  }
  return null;
}

/**
 * Build the inline pill element for a card.
 * @param {string} name
 * @returns {HTMLSpanElement}
 */
function buildChip(name) {
  const chip = document.createElement('span');
  chip.className = 'agent-card-chip';
  chip.setAttribute('contenteditable', 'false');
  chip.dataset.cardName = name;

  const nameEl = document.createElement('span');
  nameEl.className = 'agent-card-chip-name';
  nameEl.textContent = name;

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'agent-card-chip-remove';
  removeBtn.setAttribute('aria-label', 'Remove ' + name);
  removeBtn.tabIndex = -1;
  removeBtn.textContent = '\u00D7';
  removeBtn.addEventListener('mousedown', (e) => {
    e.preventDefault();
  });
  removeBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const editor = chip.closest('.agent-chat-input');
    chip.remove();
    if (editor) editor.focus();
    log.debug('removed chip name=%s', name);
  });

  chip.appendChild(nameEl);
  chip.appendChild(removeBtn);
  return chip;
}

/** Whether a separating space is needed immediately before the insertion point. */
function needsLeadingSpace(range) {
  const node = range.startContainer;
  const offset = range.startOffset;
  if (node.nodeType === Node.TEXT_NODE) {
    if (offset === 0) return false;
    const ch = node.nodeValue.charAt(offset - 1);
    return ch !== '' && !/\s/.test(ch);
  }
  if (offset > 0 && node.childNodes[offset - 1]) {
    const prev = node.childNodes[offset - 1];
    if (prev.nodeType === Node.ELEMENT_NODE && prev.classList.contains('agent-card-chip')) {
      return true;
    }
    if (prev.nodeType === Node.TEXT_NODE) {
      const t = prev.nodeValue;
      const ch = t.charAt(t.length - 1);
      return ch !== '' && !/\s/.test(ch);
    }
  }
  return false;
}

/**
 * Insert a card chip at the given range (falling back to the current selection inside the
 * editor, then to the end). Wraps the chip with spaces so the serialized prompt stays
 * separated from surrounding text; the caret ends just after the chip.
 * @param {HTMLElement} editorEl
 * @param {string} name
 * @param {Range|null} range
 */
function insertCardChip(editorEl, name, range) {
  if (!editorEl) throw new Error('insertCardChip: editorEl is required');
  if (!name) throw new Error('insertCardChip: name is required');

  let target = range;
  if (!target || !editorEl.contains(target.startContainer)) {
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && editorEl.contains(sel.getRangeAt(0).startContainer)) {
      target = sel.getRangeAt(0);
    } else {
      target = document.createRange();
      target.selectNodeContents(editorEl);
      target.collapse(false);
    }
  }
  target.deleteContents();

  const frag = document.createDocumentFragment();
  if (needsLeadingSpace(target)) frag.appendChild(document.createTextNode(' '));
  frag.appendChild(buildChip(name));
  const trailing = document.createTextNode(' ');
  frag.appendChild(trailing);
  target.insertNode(frag);

  const after = document.createRange();
  after.setStartAfter(trailing);
  after.collapse(true);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(after);
  editorEl.focus();
  log.debug('insertCardChip: inserted chip name=%s', name);
}

/** Recursively serialize editor content: text as-is, chips as quoted names, breaks as \n. */
function serializeNode(node) {
  let out = '';
  node.childNodes.forEach((child) => {
    if (child.nodeType === Node.TEXT_NODE) {
      out += child.nodeValue;
      return;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) return;
    if (child.classList && child.classList.contains('agent-card-chip')) {
      const cardName = child.dataset.cardName;
      if (!cardName) throw new Error('serializeNode: card chip is missing data-card-name');
      out += '"' + cardName + '"';
      return;
    }
    if (child.tagName === 'BR') {
      out += '\n';
      return;
    }
    if (child.tagName === 'DIV' || child.tagName === 'P') {
      if (out && !out.endsWith('\n')) out += '\n';
      out += serializeNode(child);
      return;
    }
    out += serializeNode(child);
  });
  return out;
}

/**
 * Serialize the prompt editor into the plain string sent to the agent. Each inline card
 * chip becomes its full card name wrapped in double quotes, in place.
 * @param {HTMLElement} editorEl
 * @returns {string}
 */
export function getPromptText(editorEl) {
  if (!editorEl) throw new Error('getPromptText: editorEl is required');
  return serializeNode(editorEl).replace(/\u00A0/g, ' ');
}

/**
 * Replace the editor content with plain text (used for edit/resend and abort-restore).
 * Card chips are intentionally not reconstructed from quoted names.
 * @param {HTMLElement} editorEl
 * @param {string} text
 */
export function setPromptText(editorEl, text) {
  if (!editorEl) throw new Error('setPromptText: editorEl is required');
  clearPrompt(editorEl);
  if (text) editorEl.appendChild(document.createTextNode(text));
}

/**
 * Empty the editor so the CSS placeholder shows again.
 * @param {HTMLElement} editorEl
 */
export function clearPrompt(editorEl) {
  if (!editorEl) throw new Error('clearPrompt: editorEl is required');
  editorEl.replaceChildren();
}

/**
 * Wire drag-and-drop of deck cards into the contenteditable prompt editor. Dropping a card
 * over the editor inserts an inline chip; because the editor is not a SortableJS container,
 * SortableJS reverts the drag and the card stays in the deck.
 * @param {HTMLElement} editorEl
 */
export function initPromptEditor(editorEl) {
  if (!editorEl) throw new Error('initPromptEditor: editorEl is required');
  log.debug('initPromptEditor: wiring drag-and-drop');

  document.addEventListener('dragstart', (e) => {
    const stack = findDraggedStack(e.target);
    draggedCardName = stack ? stack.getAttribute('data-name') : null;
    if (draggedCardName) log.debug('dragstart: card=%s', draggedCardName);
  });

  document.addEventListener('dragend', () => {
    draggedCardName = null;
    editorEl.classList.remove('drag-active');
  });

  editorEl.addEventListener('dragover', (e) => {
    if (!draggedCardName) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    editorEl.classList.add('drag-active');
  });

  editorEl.addEventListener('dragleave', (e) => {
    if (!editorEl.contains(e.relatedTarget)) editorEl.classList.remove('drag-active');
  });

  editorEl.addEventListener('drop', (e) => {
    if (!draggedCardName) return;
    // Block the browser's default "insert dragged HTML" into the editable. We deliberately
    // do NOT stop propagation, so SortableJS still runs its own dragend/drop cleanup and
    // reverts the card back into the deck.
    e.preventDefault();
    const name = draggedCardName;
    draggedCardName = null;
    editorEl.classList.remove('drag-active');
    insertCardChip(editorEl, name, caretRangeFromPoint(e.clientX, e.clientY));
  });

  // Keep the editor truly empty when the user clears it, so the placeholder reappears.
  editorEl.addEventListener('input', () => {
    if (editorEl.childNodes.length === 1 && editorEl.firstChild.nodeName === 'BR') {
      editorEl.replaceChildren();
    }
  });
}
