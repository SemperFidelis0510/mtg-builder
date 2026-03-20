/** Agent rules tab: CRUD for user-configured agent rules. */

const rulesList = document.getElementById('agentRulesList');
const ruleInput = document.getElementById('agentRuleInput');
const ruleAddBtn = document.getElementById('agentRuleAddBtn');

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
  ruleInput.value = '';
  try {
    const r = await fetch('/api/agent/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rule: text }),
    });
    const data = await r.json();
    _renderRules(data.rules);
  } catch { /* silent */ }
}

async function _confirmDeleteRule(index, text) {
  const preview = text.length > 60 ? text.slice(0, 60) + '...' : text;
  if (!confirm(`Delete this rule?\n\n"${preview}"`)) return;
  try {
    const r = await fetch(`/api/agent/rules/${index}`, { method: 'DELETE' });
    const data = await r.json();
    _renderRules(data.rules);
  } catch { /* silent */ }
}

ruleAddBtn.addEventListener('click', _addRule);
ruleInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); _addRule(); }
});

export async function initAgentRules() {
  try {
    const rules = await _fetchRules();
    _renderRules(rules);
  } catch { /* silent on startup */ }
}
