/** Shared color palette component: WUBRG chips + Colorless. Reused by deck settings and advanced search. */

const COLORS = [
  { value: 'W', cls: 'color-W', title: 'White' },
  { value: 'U', cls: 'color-U', title: 'Blue' },
  { value: 'B', cls: 'color-B', title: 'Black' },
  { value: 'R', cls: 'color-R', title: 'Red' },
  { value: 'G', cls: 'color-G', title: 'Green' },
];

/**
 * Build a .colors-row element with WUBRG + Colorless chips.
 * @param {object} opts
 * @param {string} opts.name            - checkbox name for WUBRG (e.g. "deckColor", "color", "color_identity")
 * @param {string} opts.colorlessName   - checkbox name for the colorless chip (e.g. "deckColorless", "colorless")
 * @param {string} [opts.idPrefix]      - optional; each input gets id = prefix + value (e.g. "c" → "cW", "cC")
 * @param {function} [opts.onChange]     - optional change callback
 * @returns {HTMLDivElement}
 */
export function createColorPalette({ name, colorlessName, idPrefix, onChange }) {
  const row = document.createElement('div');
  row.className = 'colors-row';

  COLORS.forEach((c) => {
    const label = document.createElement('label');
    label.className = 'color-chip ' + c.cls;
    label.title = c.title;
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = name;
    input.value = c.value;
    if (idPrefix) input.id = idPrefix + c.value;
    label.appendChild(input);
    row.appendChild(label);
    if (onChange) input.addEventListener('change', onChange);
  });

  const clLabel = document.createElement('label');
  clLabel.className = 'color-chip color-C';
  clLabel.title = 'Colorless';
  const clInput = document.createElement('input');
  clInput.type = 'checkbox';
  clInput.name = colorlessName;
  if (idPrefix) clInput.id = idPrefix + 'C';
  clLabel.appendChild(clInput);
  row.appendChild(clLabel);
  if (onChange) clInput.addEventListener('change', onChange);

  return row;
}

/**
 * Read checked colors from a palette container.
 * @param {HTMLElement} container - element containing the .colors-row
 * @returns {{ colors: string[], colorless: boolean }}
 */
export function getColorPaletteValues(container) {
  const colors = [];
  container.querySelectorAll('.color-chip input[type="checkbox"]:checked').forEach((input) => {
    if (input.value && 'WUBRG'.indexOf(input.value) !== -1) colors.push(input.value);
  });
  const clChip = container.querySelector('.color-chip.color-C input[type="checkbox"]');
  return { colors, colorless: clChip ? clChip.checked : false };
}

/**
 * Set checked colors on a palette container.
 * @param {HTMLElement} container - element containing the .colors-row
 * @param {string[]} colors      - array of color values to check (e.g. ["W","U"])
 * @param {boolean} colorless    - whether the colorless chip should be checked
 */
export function setColorPaletteValues(container, colors, colorless) {
  const set = new Set(Array.isArray(colors) ? colors : []);
  container.querySelectorAll('.color-chip input[type="checkbox"]').forEach((input) => {
    if (input.value && 'WUBRG'.indexOf(input.value) !== -1) {
      input.checked = set.has(input.value);
    }
  });
  const clChip = container.querySelector('.color-chip.color-C input[type="checkbox"]');
  if (clChip) clChip.checked = colorless === true;
}
