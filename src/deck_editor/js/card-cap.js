/** Per-card copy limits and bypass list — JS mirror of src/config/card_cap.py. */

import { TYPE_KEYS } from './constants.js';
import { isCommanderEnabledFormat, getSettings } from './settings.js';

export const DEFAULT_CARD_CAP = 4;
export const COMMANDER_CARD_CAP = 1;

const UNCAPPED_CARD_NAMES = new Set([
  // Basic lands
  'Plains', 'Island', 'Swamp', 'Mountain', 'Forest',
  // Snow-Covered basics
  'Snow-Covered Plains', 'Snow-Covered Island', 'Snow-Covered Swamp',
  'Snow-Covered Mountain', 'Snow-Covered Forest',
  // Colorless basic
  'Wastes',
  // Cards with "a deck can have any number of cards named ..."
  'Relentless Rats', 'Rat Colony', 'Shadowborn Apostle',
  "Dragon's Approach", 'Persistent Petitioners', 'Seven Dwarves',
  'Slime Against Humanity', 'Hare Apparent',
]);

const _UNCAPPED_LOWER = new Set([...UNCAPPED_CARD_NAMES].map((n) => n.toLowerCase()));

export function isUncapped(cardName) {
  return _UNCAPPED_LOWER.has(cardName.toLowerCase());
}

/**
 * Count total copies of a card across main deck + sideboard + commander in the DOM.
 * Maybe board is intentionally excluded (exempt from cap).
 */
export function countCardCopiesInDeck(cardName) {
  let total = 0;
  TYPE_KEYS.forEach((key) => {
    const list = document.getElementById('list-' + key);
    if (!list) return;
    list.querySelectorAll('.card-stack[data-name]').forEach((el) => {
      if (el.getAttribute('data-name') === cardName) {
        total += parseInt(el.getAttribute('data-count') || '1', 10);
      }
    });
  });
  const sideList = document.getElementById('list-sideboard');
  if (sideList) {
    sideList.querySelectorAll('.card-stack[data-name]').forEach((el) => {
      if (el.getAttribute('data-name') === cardName) {
        total += parseInt(el.getAttribute('data-count') || '1', 10);
      }
    });
  }
  const cmdList = document.getElementById('list-commander');
  if (cmdList) {
    cmdList.querySelectorAll('.card-stack[data-name]').forEach((el) => {
      if (el.getAttribute('data-name') === cardName) {
        total += 1;
      }
    });
  }
  return total;
}

/** Return the active copy cap based on the current deck format. */
export function getActiveCardCap() {
  const fmt = getSettings().format;
  return isCommanderEnabledFormat(fmt) ? COMMANDER_CARD_CAP : DEFAULT_CARD_CAP;
}

/** Return true if adding one more copy of cardName would stay within the cap. */
export function canAddCopy(cardName) {
  if (isUncapped(cardName)) return true;
  return countCardCopiesInDeck(cardName) < getActiveCardCap();
}
