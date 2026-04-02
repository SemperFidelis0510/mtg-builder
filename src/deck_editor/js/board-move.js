/** Move stacks between main typed lists and maybe board (context menu). */

import { TYPE_KEYS } from './constants.js';
import { makeCardStackEl, updateSectionHeaderTotal } from './deck.js';
import { appendCardToList, initSortable } from './sortable.js';

/**
 * @param {Element} stack .card-stack[data-name]
 * @returns {'main' | 'maybe' | null}
 */
export function getStackBoardContext(stack) {
  const ul = stack.closest('ul');
  if (!ul || !ul.id) return null;
  if (ul.id === 'list-maybe') return 'maybe';
  for (let i = 0; i < TYPE_KEYS.length; i++) {
    if (ul.id === 'list-' + TYPE_KEYS[i]) return 'main';
  }
  return null;
}

function mergeOrAppendToMaybe(name, count) {
  const maybeList = document.getElementById('list-maybe');
  if (!maybeList) return;
  const existing = maybeList.querySelector('.card-stack[data-name="' + CSS.escape(name) + '"]');
  if (existing) {
    const c = parseInt(existing.getAttribute('data-count'), 10) || 1;
    const n = c + count;
    existing.setAttribute('data-count', String(n));
    const badge = existing.querySelector('.card-stack-badge');
    if (badge) badge.textContent = String(n);
  } else {
    appendCardToList(maybeList, name, count);
  }
  updateSectionHeaderTotal(maybeList);
  const section = maybeList.closest('.section');
  if (section) section.classList.remove('section-hidden');
}

/**
 * @param {Element} stack .card-stack[data-name]
 */
export function moveStackFromMainToMaybe(stack) {
  const li = stack.closest('li');
  const list = li ? li.parentElement : null;
  if (!li || !list || !stack) return;
  const name = stack.getAttribute('data-name');
  const count = parseInt(stack.getAttribute('data-count'), 10) || 1;
  if (!name) return;
  if (getStackBoardContext(stack) !== 'main') return;
  li.remove();
  updateSectionHeaderTotal(list);
  mergeOrAppendToMaybe(name, count);
  initSortable();
}

/**
 * @param {Element} stack .card-stack[data-name]
 */
export async function moveStackFromMaybeToMain(stack) {
  const li = stack.closest('li');
  const maybeList = li ? li.parentElement : null;
  if (!li || !maybeList || maybeList.id !== 'list-maybe' || !stack) return;
  const name = stack.getAttribute('data-name');
  const count = parseInt(stack.getAttribute('data-count'), 10) || 1;
  if (!name) return;
  if (getStackBoardContext(stack) !== 'maybe') return;

  const res = await fetch('/api/card_type?name=' + encodeURIComponent(name));
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const msg = err.detail || res.statusText || String(res.status);
    console.error('moveStackFromMaybeToMain: card_type failed:', msg);
    throw new Error('Could not resolve card type for main deck: ' + msg);
  }
  const data = await res.json();
  const typeKey = data.type_key;
  let targetList = document.getElementById('list-' + typeKey);
  if (!targetList) {
    targetList = document.getElementById('list-sorcery');
  }
  if (!targetList) {
    console.error('moveStackFromMaybeToMain: no target list for type', typeKey);
    throw new Error('No main deck section for card type');
  }

  li.remove();
  updateSectionHeaderTotal(maybeList);

  const existing = targetList.querySelector('.card-stack[data-name="' + CSS.escape(name) + '"]');
  if (existing) {
    const c = parseInt(existing.getAttribute('data-count'), 10) || 1;
    const n = c + count;
    existing.setAttribute('data-count', String(n));
    const badge = existing.querySelector('.card-stack-badge');
    if (badge) badge.textContent = String(n);
  } else {
    targetList.appendChild(makeCardStackEl(name, count));
  }
  updateSectionHeaderTotal(targetList);
  const section = targetList.closest('.section');
  if (section) {
    section.classList.remove('section-hidden');
    section.classList.remove('collapsed');
  }
  initSortable();
}
