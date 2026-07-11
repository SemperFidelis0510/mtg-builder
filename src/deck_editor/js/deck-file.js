/** Tracks last loaded deck file for save-dialog defaults (name and directory). */

let lastLoadedFileHandle = null;
let lastLoadedFileName = null;
let lastLoadedDirHandle = null;

export function getLastLoadedFileName() {
  return lastLoadedFileName;
}

export function clearLastLoadedDeckFile() {
  lastLoadedFileHandle = null;
  lastLoadedFileName = null;
  lastLoadedDirHandle = null;
}

export async function setLastLoadedDeckFile(fileHandle, fileName) {
  lastLoadedFileHandle = fileHandle || null;
  lastLoadedFileName = fileName || null;
  lastLoadedDirHandle = null;
  if (fileHandle && typeof fileHandle.getParent === 'function') {
    try {
      lastLoadedDirHandle = await fileHandle.getParent();
    } catch (_err) {
      // Optional: directory hint for save dialog; save still works without it.
    }
  }
}

const JSON_FILE_TYPES = [{ description: 'JSON deck', accept: { 'application/json': ['.json'] } }];

export function buildSaveFilePickerOptions(suggestedName) {
  const opts = {
    suggestedName,
    types: JSON_FILE_TYPES,
  };
  if (lastLoadedDirHandle) {
    opts.startIn = lastLoadedDirHandle;
  }
  return opts;
}

export function buildOpenFilePickerOptions() {
  return { types: JSON_FILE_TYPES, multiple: false };
}

export async function rememberSavedFileHandle(handle) {
  await setLastLoadedDeckFile(handle, handle.name);
}