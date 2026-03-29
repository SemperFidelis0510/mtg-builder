/** Pure utility functions for Scryfall URLs, stack conversion, and naming. */

export function splitCardFaces(name) {
  if (!name || typeof name !== 'string') return [];
  return name
    .split(' // ')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

export function isTwoSidedCardName(name) {
  return splitCardFaces(name).length > 1;
}

function faceNameForIndex(name, faceIndex) {
  const faces = splitCardFaces(name);
  if (faces.length <= 1) return name;
  const idx = Number.isInteger(faceIndex) ? faceIndex : 0;
  if (idx < 0 || idx >= faces.length) return faces[0];
  return faces[idx];
}

export function scryfallImageUrlForSide(name, faceIndex) {
  const exactName = faceNameForIndex(name, faceIndex);
  return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(exactName) + '&format=image&version=normal';
}

export function scryfallImageUrl(name) {
  return scryfallImageUrlForSide(name, 0);
}

export function scryfallImageUrlLargeForSide(name, faceIndex) {
  const exactName = faceNameForIndex(name, faceIndex);
  return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(exactName) + '&format=image&version=large';
}

export function scryfallImageUrlLarge(name) {
  return scryfallImageUrlLargeForSide(name, 0);
}

export function collapseToStacks(arr) {
  if (!Array.isArray(arr)) return [];
  const counts = {};
  for (let i = 0; i < arr.length; i++) {
    const n = arr[i];
    counts[n] = (counts[n] || 0) + 1;
  }
  return Object.keys(counts).map((name) => ({ name, count: counts[name] }));
}

export function expandStacks(stacks) {
  const out = [];
  stacks.forEach((s) => {
    for (let i = 0; i < (s.count || 1); i++) out.push(s.name);
  });
  return out;
}

export function suggestedSaveName() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  const s = String(d.getSeconds()).padStart(2, '0');
  return 'deck_' + y + m + day + '_' + h + min + s + '.json';
}

export function typeLineToSectionKey(typeLine) {
  if (!typeLine || typeof typeLine !== 'string') return 'sorcery';
  const t = typeLine.toLowerCase();
  if (t.indexOf('land') !== -1) return 'land';
  if (t.indexOf('creature') !== -1) return 'creature';
  if (t.indexOf('instant') !== -1) return 'instant';
  if (t.indexOf('sorcery') !== -1) return 'sorcery';
  if (t.indexOf('artifact') !== -1) return 'artifact';
  if (t.indexOf('enchantment') !== -1) return 'enchantment';
  if (t.indexOf('planeswalker') !== -1) return 'planeswalker';
  if (t.indexOf('battle') !== -1) return 'battle';
  return 'sorcery';
}
