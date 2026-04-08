/** Structured frontend logger with backend relay for warnings and errors. */

function log(level, module, message, ...args) {
  const tag = `[${module}]`;
  if (level === 'DEBUG') console.debug(tag, message, ...args);
  else if (level === 'INFO') console.log(tag, message, ...args);
  else if (level === 'WARN') console.warn(tag, message, ...args);
  else console.error(tag, message, ...args);

  if (level === 'WARN' || level === 'ERROR') {
    const detail = args.length ? message + ' ' + args.map(String).join(' ') : String(message);
    fetch('/api/frontend_log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level, module, message: detail }),
    }).catch(() => {});
  }
}

export function createLogger(module) {
  return {
    debug: (msg, ...a) => log('DEBUG', module, msg, ...a),
    info: (msg, ...a) => log('INFO', module, msg, ...a),
    warn: (msg, ...a) => log('WARN', module, msg, ...a),
    error: (msg, ...a) => log('ERROR', module, msg, ...a),
  };
}
