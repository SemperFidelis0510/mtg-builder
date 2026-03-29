/** localStorage preference for maybe board full card image layout (no imports). */

const LS_KEY = 'deckEditor.maybeFullCardView';

export function isMaybeFullCardView() {
  return localStorage.getItem(LS_KEY) === '1';
}

export function setMaybeFullCardView(enabled) {
  if (enabled) {
    localStorage.setItem(LS_KEY, '1');
  } else {
    localStorage.removeItem(LS_KEY);
  }
}
