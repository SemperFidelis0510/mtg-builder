/** Pure utility functions for Scryfall URLs, stack conversion, and naming. */

export function scryfallImageUrl(name) {
  return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(name) + '&format=image&version=normal';
}

export function scryfallImageUrlLarge(name) {
  return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(name) + '&format=image&version=large';
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
  if (!typeLine || typeof typeLine !== 'string') return 'spells';
  const t = typeLine.toLowerCase();
  if (t.indexOf('land') !== -1) return 'lands';
  if (t.indexOf('creature') !== -1) return 'creatures';
  if (t.indexOf('planeswalker') !== -1 || t.indexOf('artifact') !== -1 || t.indexOf('enchantment') !== -1) return 'non_creatures';
  if (t.indexOf('instant') !== -1 || t.indexOf('sorcery') !== -1) return 'spells';
  return 'spells';
}
