/** Agent rules tab: CRUD for user-configured agent rules + API key management. */

import { createLogger } from './logger.js';

const log = createLogger('agent-rules');

const rulesList = document.getElementById('agentRulesList');
const ruleInput = document.getElementById('agentRuleInput');
const ruleAddBtn = document.getElementById('agentRuleAddBtn');

const tabKeyStatus = document.getElementById('agentTabKeyStatus');
const tabKeyInput = document.getElementById('agentTabKeyInput');
const tabKeySaveBtn = document.getElementById('agentTabKeySaveBtn');
const tabKeyError = document.getElementById('agentTabKeyError');

async function _fetchRules() {
  const r = await fetch('/api/agent/rules');
  const data = await r.json();
  return data.rules;
}

function _renderRules(rules) {
  rulesList.innerHTML = '';
  if (!rules.length) {
    const empty = document.createElement('div');
    empty.className = 'agent-rules-empty';
    empty.textContent = 'No rules configured yet. Add rules to customize the AI assistant\'s behavior.';
    rulesList.appendChild(empty);
    return;
  }
  rules.forEach((text, idx) => {
    const item = document.createElement('div');
    item.className = 'agent-rule-item';

    const span = document.createElement('span');
    span.className = 'agent-rule-text';
    span.textContent = text;
    item.appendChild(span);

    const del = document.createElement('button');
    del.className = 'agent-rule-delete-btn';
    del.type = 'button';
    del.title = 'Delete rule';
    del.textContent = '\u00d7';
    del.addEventListener('click', () => _confirmDeleteRule(idx, text));
    item.appendChild(del);

    rulesList.appendChild(item);
  });
}

async function _addRule() {
  const text = ruleInput.value.trim();
  if (!text) return;
  log.info('Adding rule', text.slice(0, 60));
  ruleInput.value = '';
  try {
    const r = await fetch('/api/agent/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rule: text }),
    });
    const data = await r.json();
    _renderRules(data.rules);
  } catch (err) { log.error('_addRule failed', err); }
}

async function _confirmDeleteRule(index, text) {
  const preview = text.length > 60 ? text.slice(0, 60) + '...' : text;
  if (!confirm(`Delete this rule?\n\n"${preview}"`)) return;
  log.info('Deleting rule', index);
  try {
    const r = await fetch(`/api/agent/rules/${index}`, { method: 'DELETE' });
    const data = await r.json();
    _renderRules(data.rules);
  } catch (err) { log.error('_confirmDeleteRule failed', err); }
}

ruleAddBtn.addEventListener('click', _addRule);
ruleInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); _addRule(); }
});

async function _refreshKeyStatus() {
  try {
    const r = await fetch('/api/agent/key/status');
    const data = await r.json();
    if (data.has_key) {
      tabKeyStatus.textContent = 'Key configured' + (data.model ? ` (model: ${data.model})` : '');
      tabKeyStatus.className = 'agent-key-status has-key';
    } else {
      tabKeyStatus.textContent = 'No API key configured';
      tabKeyStatus.className = 'agent-key-status no-key';
    }
  } catch {
    tabKeyStatus.textContent = 'Unable to check key status';
    tabKeyStatus.className = 'agent-key-status no-key';
  }
}

tabKeySaveBtn.addEventListener('click', async () => {
  const key = tabKeyInput.value.trim();
  if (!key) { tabKeyError.textContent = 'Please enter an API key.'; return; }
  tabKeyError.textContent = '';
  try {
    const r = await fetch('/api/agent/key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    if (!r.ok) { tabKeyError.textContent = 'Failed to save key.'; return; }
    tabKeyInput.value = '';
    tabKeyError.textContent = '';
    await _refreshKeyStatus();
  } catch {
    tabKeyError.textContent = 'Network error saving key.';
  }
});

export async function initAgentRules() {
  log.info('initAgentRules');
  try {
    const rules = await _fetchRules();
    log.debug('Loaded %d rules', rules.length);
    _renderRules(rules);
  } catch (err) { log.error('initAgentRules: failed to load rules', err); }
  await _refreshKeyStatus();
}
