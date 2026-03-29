/** Agent chat panel: toggle, streaming SSE, markdown, tool calls, conversations. */

let _currentConvId = null;
let _sending = false;

const panel = document.getElementById('agentPanel');
const toggleBtn = document.getElementById('agentPanelToggle');
const closeBtn = document.getElementById('agentPanelClose');
const messagesEl = document.getElementById('agentChatMessages');
const welcomeMsg = document.getElementById('agentWelcomeMsg');
const inputArea = document.getElementById('agentChatInputArea');
const chatInput = document.getElementById('agentChatInput');
const sendBtn = document.getElementById('agentSendBtn');
const convSelect = document.getElementById('agentConversationSelect');
const newConvBtn = document.getElementById('agentNewConvBtn');
const deleteConvBtn = document.getElementById('agentDeleteConvBtn');
const modelBadge = document.getElementById('agentModelBadge');
const keySetup = document.getElementById('agentKeySetup');
const keySaveBtn = document.getElementById('agentKeySaveBtn');
const keyInput = document.getElementById('agentKeyInput');
const keyError = document.getElementById('agentKeyError');

// -----------------------------------------------------------------------
// Panel toggle
// -----------------------------------------------------------------------

function openPanel() {
  panel.classList.add('open');
  panel.setAttribute('aria-hidden', 'false');
  document.body.classList.add('agent-panel-open');
}

function closePanel() {
  panel.classList.remove('open');
  panel.setAttribute('aria-hidden', 'true');
  document.body.classList.remove('agent-panel-open');
}

toggleBtn.addEventListener('click', () => openPanel());
closeBtn.addEventListener('click', closePanel);

// -----------------------------------------------------------------------
// API key check
// -----------------------------------------------------------------------

let _hasKey = false;

async function checkApiKey() {
  try {
    const r = await fetch('/api/agent/key/status');
    const data = await r.json();
    _hasKey = data.has_key;
    if (data.model) modelBadge.textContent = data.model;
    _showKeySetup(!_hasKey);
  } catch {
    _showKeySetup(true);
  }
}

function _showKeySetup(show) {
  keySetup.style.display = show ? 'flex' : 'none';
  messagesEl.style.display = show ? 'none' : 'flex';
  inputArea.style.display = show ? 'none' : 'flex';
}

keySaveBtn.addEventListener('click', async () => {
  const key = keyInput.value.trim();
  if (!key) { keyError.textContent = 'Please enter an API key.'; return; }
  keyError.textContent = '';
  try {
    const r = await fetch('/api/agent/key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    if (!r.ok) { keyError.textContent = 'Failed to save key.'; return; }
    _hasKey = true;
    keyInput.value = '';
    _showKeySetup(false);
    await checkApiKey();
  } catch {
    keyError.textContent = 'Network error saving key.';
  }
});

// -----------------------------------------------------------------------
// Conversation management
// -----------------------------------------------------------------------

async function loadConversationList() {
  try {
    const r = await fetch('/api/agent/conversations');
    const data = await r.json();
    convSelect.innerHTML = '<option value="">New conversation</option>';
    for (const c of data.conversations) {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.title || 'Untitled';
      if (c.id === _currentConvId) opt.selected = true;
      convSelect.appendChild(opt);
    }
  } catch { /* silent */ }
}

async function loadConversation(convId) {
  if (!convId) { _startNewConversation(); return; }
  try {
    const r = await fetch(`/api/agent/conversation/${convId}`);
    if (!r.ok) { _startNewConversation(); return; }
    const conv = await r.json();
    _currentConvId = conv.id;
    deleteConvBtn.disabled = false;
    if (conv.model) modelBadge.textContent = conv.model;
    _clearMessages();
    for (const msg of conv.messages) {
      if (msg.role === 'user') {
        _appendUserMessage(msg.content);
      } else if (msg.role === 'assistant') {
        if (msg.tool_calls) {
          for (const tc of msg.tool_calls) {
            _appendToolCall(tc.name, tc.args, tc.result);
          }
        }
        if (msg.content) _appendAssistantMessage(msg.content);
      }
    }
    _scrollToBottom();
  } catch { _startNewConversation(); }
}

function _startNewConversation() {
  _currentConvId = null;
  _clearMessages();
  welcomeMsg.style.display = 'block';
  convSelect.value = '';
  deleteConvBtn.disabled = true;
}

convSelect.addEventListener('change', () => {
  loadConversation(convSelect.value);
});

newConvBtn.addEventListener('click', () => {
  _startNewConversation();
  loadConversationList();
});

deleteConvBtn.addEventListener('click', async () => {
  if (!_currentConvId) return;
  if (!confirm('Delete this conversation? This cannot be undone.')) return;
  try {
    await fetch(`/api/agent/conversation/${_currentConvId}`, { method: 'DELETE' });
  } catch { /* silent */ }
  _startNewConversation();
  await loadConversationList();
});

// -----------------------------------------------------------------------
// Message rendering
// -----------------------------------------------------------------------

function _clearMessages() {
  messagesEl.innerHTML = '';
  messagesEl.appendChild(welcomeMsg);
  welcomeMsg.style.display = 'block';
}

function _appendUserMessage(text) {
  welcomeMsg.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'agent-msg agent-msg-user';
  el.textContent = text;
  messagesEl.appendChild(el);
}

function _appendAssistantMessage(html) {
  welcomeMsg.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'agent-msg agent-msg-assistant';
  el.innerHTML = _renderMarkdown(html);
  messagesEl.appendChild(el);
  return el;
}

function _createStreamingAssistantMessage() {
  welcomeMsg.style.display = 'none';
  const el = document.createElement('div');
  el.className = 'agent-msg agent-msg-assistant';
  messagesEl.appendChild(el);
  return el;
}

function _appendToolCall(name, args, result) {
  welcomeMsg.style.display = 'none';
  const wrap = document.createElement('div');
  wrap.className = 'agent-tool-call';

  const header = document.createElement('div');
  header.className = 'agent-tool-call-header';
  const label = document.createElement('span');
  label.className = 'agent-tool-call-label';
  label.textContent = _toolCallLabel(name, args);
  header.appendChild(label);
  wrap.appendChild(header);

  const body = document.createElement('div');
  body.className = 'agent-tool-call-body';
  let bodyText = `Tool: ${name}\nArgs: ${JSON.stringify(args, null, 2)}`;
  if (result !== undefined) bodyText += `\n\nResult:\n${result}`;
  body.textContent = bodyText;
  wrap.appendChild(body);

  header.addEventListener('click', () => wrap.classList.toggle('expanded'));
  messagesEl.appendChild(wrap);
  return wrap;
}

function _showTypingIndicator() {
  const el = document.createElement('div');
  el.className = 'agent-typing';
  el.id = 'agentTypingIndicator';
  el.innerHTML = '<div class="agent-typing-dots"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(el);
  _scrollToBottom();
}

function _removeTypingIndicator() {
  const el = document.getElementById('agentTypingIndicator');
  if (el) el.remove();
}

function _scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function _renderMarkdown(text) {
  if (typeof marked !== 'undefined' && marked.parse) {
    return marked.parse(text);
  }
  return text.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
}

function _toolCallLabel(name, args) {
  const labels = {
    plain_search_card: (a) =>
      (a.semantic_query || '').trim()
        ? `Filtered cards + semantic: "${(a.semantic_query || '').trim()}"`
        : 'Filtered cards by attributes',
    get_card_info: (a) => `Looked up: ${a.card_names || ''}`,
    extract_card_mechanics: (a) => `Extracted ${a.extract_type || 'mechanics'} for ${a.card_name || ''}`,
    append_cards_to_deck: (a) => `Added to deck: ${a.card_names || ''}`,
    search_triggers: (a) => `Searched triggers: "${a.query || ''}"`,
    search_effects: (a) => `Searched effects: "${a.query || ''}"`,
    search_online_decks: (a) => `Searched online decks: "${a.query || a.format || ''}"`,
    get_online_deck: (a) => `Fetched deck: ${a.url || ''}`,
    import_online_deck: (a) => `Imported deck: ${a.url || ''}`,
  };
  const fn = labels[name];
  return fn ? fn(args) : `Tool: ${name}`;
}

// -----------------------------------------------------------------------
// Auto-resize textarea
// -----------------------------------------------------------------------
chatInput.addEventListener('input', () => {
  chatInput.style.height = 'auto';
  chatInput.style.height = Math.min(chatInput.scrollHeight, 100) + 'px';
});

// -----------------------------------------------------------------------
// Send message (streaming SSE)
// -----------------------------------------------------------------------

async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text || _sending) return;

  _sending = true;
  sendBtn.disabled = true;
  chatInput.value = '';
  chatInput.style.height = 'auto';

  _appendUserMessage(text);
  _showTypingIndicator();
  _scrollToBottom();

  const body = { message: text };
  if (_currentConvId) body.conversation_id = _currentConvId;

  try {
    const resp = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!resp.ok) {
      _removeTypingIndicator();
      const err = await resp.json().catch(() => ({ detail: 'Request failed' }));
      _appendAssistantMessage(`Error: ${err.detail || resp.statusText}`);
      _sending = false;
      sendBtn.disabled = false;
      return;
    }

    _removeTypingIndicator();

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let streamingEl = null;
    let accumulatedText = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop();

      let eventType = null;
      for (const line of lines) {
        if (line.trim() === '') {
          eventType = null;
          continue;
        }
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ') && eventType) {
          let data;
          try { data = JSON.parse(line.slice(6)); } catch { continue; }

          if (eventType === 'text_delta') {
            if (!streamingEl) {
              accumulatedText = '';
            }
            accumulatedText += data.content;
            if (accumulatedText.trim()) {
              if (!streamingEl) {
                streamingEl = _createStreamingAssistantMessage();
              }
              streamingEl.innerHTML = _renderMarkdown(accumulatedText);
              _scrollToBottom();
            }
          } else if (eventType === 'tool_call') {
            if (!data.name) { eventType = null; continue; }
            streamingEl = null;
            _appendToolCall(data.name, data.args || {});
            _scrollToBottom();
          } else if (eventType === 'tool_result') {
            const toolEls = messagesEl.querySelectorAll('.agent-tool-call');
            const lastTool = toolEls[toolEls.length - 1];
            if (lastTool) {
              const body = lastTool.querySelector('.agent-tool-call-body');
              if (body) body.textContent += `\n\nResult:\n${data.result}`;
            }
          } else if (eventType === 'done') {
            if (data.conversation_id) {
              _currentConvId = data.conversation_id;
              deleteConvBtn.disabled = false;
            }
            if (data.model) modelBadge.textContent = data.model;
            await loadConversationList();
          } else if (eventType === 'error') {
            if (!streamingEl) {
              _appendAssistantMessage(`Error: ${data.message}`);
            } else {
              accumulatedText += `\n\n**Error:** ${data.message}`;
              streamingEl.innerHTML = _renderMarkdown(accumulatedText);
            }
          }
          eventType = null;
        }
      }
    }
  } catch (e) {
    _removeTypingIndicator();
    _appendAssistantMessage(`Network error: ${e.message}`);
  }

  _sending = false;
  sendBtn.disabled = false;
  _scrollToBottom();
}

sendBtn.addEventListener('click', sendMessage);
chatInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// -----------------------------------------------------------------------
// Init
// -----------------------------------------------------------------------

export async function initAgentChat() {
  await checkApiKey();
  await loadConversationList();
}
