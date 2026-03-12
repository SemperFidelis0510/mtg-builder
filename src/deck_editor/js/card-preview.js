/** Card hover preview tooltip. */

import { scryfallImageUrlLarge } from './utils.js';

export function showCardPreview(name, x, y) {
  const el = document.getElementById('cardPreview');
  if (!el || !name) return;
  const img = el.querySelector('img');
  img.src = scryfallImageUrlLarge(name);
  img.alt = name;
  el.style.left = (x + 16) + 'px';
  el.style.top = (y + 16) + 'px';
  el.classList.add('visible');
  el.setAttribute('aria-hidden', 'false');
}

export function hideCardPreview() {
  const el = document.getElementById('cardPreview');
  if (el) {
    el.classList.remove('visible');
    el.setAttribute('aria-hidden', 'true');
  }
}

export function initCardPreview() {
  document.addEventListener('mouseover', (e) => {
    const preview = document.getElementById('cardPreview');
    if (e.target === preview || (preview && preview.contains(e.target))) return;
    const img = e.target.closest('.card-img');
    if (img) {
      const stack = img.closest('.card-stack');
      if (stack && stack.dataset.name) showCardPreview(stack.dataset.name, e.clientX, e.clientY);
    }
  });
  document.addEventListener('mouseout', (e) => {
    const img = e.target.closest('.card-img');
    const related = e.relatedTarget;
    const preview = document.getElementById('cardPreview');
    if (img && (!related || !img.contains(related)) && (!preview || !preview.contains(related))) hideCardPreview();
    if (preview && (e.target === preview || preview.contains(e.target)) && (!related || !preview.contains(related))) hideCardPreview();
  });
  document.addEventListener('mousemove', (e) => {
    const preview = document.getElementById('cardPreview');
    if (preview && preview.classList.contains('visible')) {
      const pw = preview.offsetWidth || 248;
      const ph = preview.offsetHeight || 344;
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      let left = e.clientX + 16;
      let top = e.clientY + 16;
      if (left + pw > vw) left = e.clientX - pw - 8;
      if (top + ph > vh) top = e.clientY - ph - 8;
      if (left < 0) left = 0;
      if (top < 0) top = 0;
      preview.style.left = left + 'px';
      preview.style.top = top + 'px';
    }
  });
}
