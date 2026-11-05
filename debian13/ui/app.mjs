export const AGENT_PROFILES = Object.freeze([
  { id: 'aria', name: 'Aria' },
  { id: 'astyria', name: 'Astyria' },
  { id: 'aurelius', name: 'Aurelius' },
  { id: 'alex', name: 'Alex' },
]);

const SESSION_KEY = 'debian13-chat-session';
const HISTORY_KEY = 'debian13-chat-history';
const SESSION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// Return null for chat messages; only an explicit /cli command reaches the terminal.
export function parseCliCommand(text) {
  if (typeof text !== 'string' || !/^\/cli(?:\s|$)/i.test(text)) return null;
  return text.replace(/^\/cli\s*/i, '').trim();
}

function apiErrorMessage(payload, status) {
  if (status === 401) return 'Brak autoryzacji API (401). Sprawdź token operatora i bezpieczny adres panelu.';
  if (status === 403) return 'API odrzuciło uprawnienia (403). Sprawdź dostęp operatora.';
  if (status === 404) return 'Backend nie udostępnia tego endpointu (404).';
  if (typeof payload?.error === 'string') return payload.error;
  if (typeof payload?.error?.message === 'string' && payload.error.message.length <= 500) {
    return payload.error.message;
  }
  return `Żądanie nie powiodło się (HTTP ${status}).`;
}

export function createApiClient({
  fetchImpl = globalThis.fetch,
  baseUrl = '',
  requestOperatorToken = () => typeof globalThis.prompt === 'function'
    ? globalThis.prompt('Prywatny panel Debian 13 wymaga tokenu operatora. Podaj go tylko na zaufanej stronie HTTPS.')
    : null,
} = {}) {
  // The token is kept in memory only; never put it in URL, storage or logs.
  let operatorToken = '';
  async function fetchRequest(path, options) {
    const call = () => {
      const headers = { ...(options?.headers || {}) };
      if (operatorToken) headers.Authorization = 'Bearer ' + operatorToken;
      return fetchImpl(`${baseUrl}/api/v1/${path}`, { ...options, headers });
    };
    try {
      let response = await call();
      if (response.status === 401 && typeof requestOperatorToken === 'function') {
        operatorToken = '';
        const candidate = requestOperatorToken();
        if (typeof candidate === 'string' && candidate.trim().length >= 32) {
          operatorToken = candidate.trim();
          response = await call();
          if (response.status === 401) operatorToken = '';
        }
      }
      return response;
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw new ApiError('Runtime nieosiągalny — sprawdź połączenie z API.');
    }
  }

  async function request(path, options) {
    const response = await fetchRequest(path, options);
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError('Runtime zwrócił nieprawidłową odpowiedź.', response.status);
    }
    if (!response.ok) {
      throw new ApiError(apiErrorMessage(payload, response.status), response.status);
    }
    return payload;
  }

  function post(path, body) {
    return request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  async function chatStream(message, agent, sessionId, onEvent, signal) {
    const body = { message, agent };
    if (sessionId) body.session_id = sessionId;
    const response = await fetchRequest('chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok) {
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new ApiError('Runtime zwrócił nieprawidłową odpowiedź.', response.status);
      }
      throw new ApiError(apiErrorMessage(payload, response.status), response.status);
    }
    if (!response.headers.get('content-type')?.toLowerCase().includes('text/event-stream') || !response.body) {
      throw new ApiError('Runtime nie zwrócił strumienia odpowiedzi.', response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    const dispatch = (block) => {
      let event = 'message';
      const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
      }
      if (!data.length) return;
      let payload;
      try {
        payload = JSON.parse(data.join('\n'));
      } catch {
        throw new ApiError('Runtime wysłał nieprawidłowe zdarzenie czatu.');
      }
      onEvent({ event, payload });
    };

    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator;
      while ((separator = /\r?\n\r?\n/.exec(buffer))) {
        dispatch(buffer.slice(0, separator.index));
        buffer = buffer.slice(separator.index + separator[0].length);
      }
      if (done) break;
    }
    if (buffer.trim()) dispatch(buffer);
  }

  async function terminalStream(command, onEvent, signal) {
    const response = await fetchRequest('terminal/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
      signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new ApiError(apiErrorMessage(payload, response.status), response.status);
    }
    if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
      throw new ApiError('Brak strumienia danych terminala.', response.status);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = false;
    const dispatch = (frame) => {
      let event = '';
      let payload = null;
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) {
          try { payload = JSON.parse(line.slice(5).trim()); }
          catch { throw new ApiError('Nieprawidłowe zdarzenie terminala.'); }
        }
      }
      if (!payload) return;
      if (event === 'done' || event === 'error') completed = true;
      onEvent({ event, payload });
    };
    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let separator;
        while ((separator = /\r?\n\r?\n/.exec(buffer))) {
          dispatch(buffer.slice(0, separator.index));
          buffer = buffer.slice(separator.index + separator[0].length);
        }
        if (done) break;
      }
      if (buffer.trim()) dispatch(buffer);
      if (!completed) throw new ApiError('Strumień terminala przerwany przed potwierdzeniem zakończenia.');
    } finally {
      reader.releaseLock();
    }
  }

  return Object.freeze({
    getStatus: () => request('status'),
    getAgents: () => request('agents'),
    getOriginalAgents: () => request('original-agents'),
    getIntegrations: () => request('integrations'),
    getTasks: () => request('tasks'),
    getAlexCapabilities: () => request('alex/capabilities'),
    bridge: (message, sessionId = '', useOpenai = true) => post('bridge', { message, sessionId, useOpenai }),
    terminateBridge: (sessionId) => post('bridge/terminate', { sessionId }),
    dispatchToAlex: (taskId) => request(`tasks/${encodeURIComponent(taskId)}/dispatch`, {method: 'POST'}),
    createTask: (task) => post('tasks', task),
    getTask: (taskId) => request(`tasks/${encodeURIComponent(taskId)}`),
    getEvents: () => request('events'),
    getMessages: (sessionId) => request(`sessions/${encodeURIComponent(sessionId)}/messages`),
    cancelTask: (taskId) => request(`tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' }),
    chatStream,
    vision: (agent, message, image) => post('vision', { profile: agent, message, image }),
    terminal: (command) => post('terminal', { command }),
    terminalStream,
    listScripts: () => request('scripts'),
    startScript: (scriptId) => post('scripts/start', { scriptId }),
    respondScript: (sessionId, answer) => post('scripts/respond', { sessionId, answer }),
    cancelScript: (sessionId) => post('scripts/cancel', { sessionId }),
  });
}

export function getInitialAgentStatuses() {
  return AGENT_PROFILES.map((agent) => ({
    ...agent,
    status: 'unknown',
    worker: 'unknown',
  }));
}

function agentStatusLabel(agent) {
  const profile = agent.status === 'profile_configured'
    ? 'Profil skonfigurowany'
    : 'Profil: stan nieznany';
  const worker = agent.worker === 'not_running'
    ? 'Worker nieuruchomiony'
    : agent.worker === 'running'
      ? 'Worker uruchomiony'
      : 'Worker: stan nieznany';
  return { profile, worker };
}

function renderAgentProfiles(list, agents = getInitialAgentStatuses()) {
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const agent of agents) {
    const item = document.createElement('li');
    item.className = 'agent-card';
    item.dataset.agent = agent.id;

    const nameRow = document.createElement('div');
    nameRow.className = 'agent-name-row';
    const dot = document.createElement('span');
    dot.className = 'agent-state-dot';
    dot.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.textContent = agent.name;
    nameRow.append(dot, name);

    const privilege = document.createElement('p');
    privilege.className = 'agent-privilege';
    privilege.textContent = 'Brak narzuconej specjalizacji · uprawnienia ustala środowisko';
    const labels = agentStatusLabel(agent);
    const profile = document.createElement('p');
    profile.className = 'agent-state';
    profile.textContent = labels.profile;
    const worker = document.createElement('p');
    worker.className = 'worker-state';
    worker.textContent = labels.worker;

    item.append(nameRow, privilege, profile, worker);
    fragment.append(item);
  }
  list.replaceChildren(fragment);
}

function setText(id, text) {
  const element = document.getElementById(id);
  if (element) element.textContent = text;
}

function connectionState(state, label) {
  const element = document.getElementById('connection-status');
  if (!element) return;
  element.dataset.state = state;
  const text = element.querySelector('span:last-child');
  if (text) text.textContent = label;
  setText('sidebar-runtime-label', label);
}

function modelStatusLabel(model) {
  const statusLabels = {
    not_configured: 'Brak skonfigurowanego modelu',
    unavailable: 'Model niedostępny',
    configured: 'Model skonfigurowany (połączenie jeszcze niezweryfikowane)',
    model_missing: 'Model niedostępny',
    model_available: 'Model dostępny lokalnie',
  };
  const label = statusLabels[model?.status] ?? 'Stan modelu nieznany';
  const inference = model?.inference === 'verified'
    ? 'inferencja zweryfikowana'
    : 'inferencja niezweryfikowana';
  return `${label} · ${inference}`;
}

// Distinguish a checked service from an unverified or unconfigured one.
// Reachable API does not establish that the selected model is installed or can infer.
export function ollamaStatusLabel(ollama) {
  if (!ollama || typeof ollama !== 'object') return 'Stan Ollamy niezweryfikowany';
  if (ollama.state === 'not_selected') return 'Ollama nie jest skonfigurowana jako dostawca AI';
  const selected = ollama.selectedModel
    ? ` · wybrany model: ${ollama.selectedModel} (niezweryfikowany)`
    : '';
  if (ollama.state === 'unavailable') return `Ollama skonfigurowana, serwer nie odpowiada${selected}`;
  if (ollama.state === 'reachable') {
    const version = ollama.version ? ` (wersja ${ollama.version})` : '';
    return `Serwer Ollama odpowiada${version}${selected} · inferencja niezweryfikowana`;
  }
  return 'Stan Ollamy niezweryfikowany';
}

function renderRuntimeStatus(status) {
  const application = status.application?.status === 'running'
    ? `${status.application.product ?? 'Aplikacja'} działa`
    : 'Aplikacja niedostępna';
  const osRuntime = status.operating_system?.runtime;
  const distribution = osRuntime?.distribution ?? 'dystrybucja nieznana';
  const boot = osRuntime?.debian_13_boot_verified === true
    ? 'boot Debian 13 potwierdzony'
    : 'boot Debian 13 niepotwierdzony';
  setText('hyperspace-api-state', status.application?.status === 'running' ? 'API: dostępne' : 'API: niezweryfikowane');
  setText('hyperspace-runtime-indicator', status.application?.status === 'running' ? 'Odpowiedź API potwierdzona' : 'Brak potwierdzonego połączenia');
  setText('hyperspace-model-state', modelStatusLabel(status.model));
  setText('application-status', application);
  setText('os-status', `${distribution} · ${boot}`);
  setText('model-status', modelStatusLabel(status.model));
  setText('ollama-status', ollamaStatusLabel(status.ollama));
  setText('task-storage-status', status.task_state ?? 'Stan pamięci zadań nieznany');
  setText('status-error', '');
  connectionState('connected', 'API dostępne');
}

function appendTranscript(kind, title, content = '') {
  const transcript = document.getElementById('chat-transcript');
  if (!transcript) return null;
  transcript.querySelector('.empty-state')?.remove();
  const entry = document.createElement('article');
  entry.className = `transcript-entry transcript-${kind}`;
  const heading = document.createElement('p');
  heading.className = 'transcript-heading';
  heading.textContent = title;
  const text = document.createElement('p');
  text.className = 'transcript-text';
  text.textContent = content;
  entry.append(heading, text);
  if (kind === 'user' || kind === 'assistant') {
    // Local operator feedback only; NOT an AI-to-AI reaction.
    const index = transcript.querySelectorAll('.transcript-entry').length;
    const key = ['debian13-reaction-v1', activeSessionId || 'local', index, kind].join(':');
    const controls = document.createElement('div');
    controls.className = 'message-reactions';
    let saved = '';
    try { saved = localStorage.getItem(key) || ''; } catch { /* optional storage */ }
    for (const [value, label] of [['up', '👍'], ['down', '👎']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'message-reaction';
      button.textContent = label;
      button.title = value === 'up' ? 'Oceń pozytywnie' : 'Oceń negatywnie';
      button.setAttribute('aria-label', button.title);
      button.setAttribute('aria-pressed', String(saved === value));
      button.addEventListener('click', () => {
        const next = button.getAttribute('aria-pressed') === 'true' ? '' : value;
        for (const other of controls.querySelectorAll('.message-reaction')) {
          other.setAttribute('aria-pressed', String(next !== '' && other === button));
        }
        try { if (next) localStorage.setItem(key, next); else localStorage.removeItem(key); }
        catch { /* visual feedback remains available */ }
      });
      controls.append(button);
    }
    const note = document.createElement('span');
    note.className = 'reaction-hint';
    note.textContent = 'Ocena użytkownika · lokalnie';
    controls.append(note);
    entry.append(controls);
  }
  transcript.append(entry);
  transcript.scrollTop = transcript.scrollHeight;
  return { entry, heading, text };
}

function renderTranscriptMessages(messages) {
  const transcript = document.getElementById('chat-transcript');
  if (!transcript) return;
  transcript.replaceChildren();
  for (const message of messages) {
    if (!['user', 'assistant'].includes(message.role) || typeof message.content !== 'string') continue;
    const isUser = message.role === 'user';
    const name = AGENT_PROFILES.find((agent) => agent.id === message.agent)?.name ?? 'Agent';
    appendTranscript(
      isUser ? 'user' : 'assistant',
      isUser ? `Ty · ${name}` : `${name} · ${message.source ?? 'API'}`,
      message.content,
    );
  }
  if (!transcript.children.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-state';
    empty.textContent = 'Ta sesja nie ma jeszcze zapisanych wiadomości.';
    transcript.append(empty);
  }
}

function renderTerminal(command, output, isError = false) {
  const terminal = document.getElementById('terminal-output');
  if (!terminal) return;
  terminal.querySelector('.terminal-hint')?.remove();
  const entry = document.createElement('p');
  const prompt = document.createElement('span');
  prompt.className = 'prompt-mark';
  prompt.setAttribute('aria-hidden', 'true');
  prompt.textContent = isError ? '!' : '$';
  const commandText = document.createElement('span');
  commandText.textContent = ` ${command}`;
  entry.append(prompt, commandText);
  const result = document.createElement('pre');
  result.className = isError ? 'terminal-error' : 'terminal-result';
  result.textContent = output;
  terminal.append(entry, result);
  terminal.scrollTop = terminal.scrollHeight;
}

function renderTasks(tasks) {
  const list = document.getElementById('task-list');
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const task of tasks) {
    const item = document.createElement('li');
    item.className = 'task-item';
    item.dataset.taskId = task.id;
    const detail = document.createElement('span');
    detail.textContent = `${task.title || task.kind} · ${task.agent} · ${task.id}`;
    const status = document.createElement('span');
    status.className = `task-state task-${task.status}`;
    status.textContent = task.status;
    const actions = document.createElement('span');
    actions.className = 'task-actions';
    if (typeof task.id === 'string' && SESSION_ID_PATTERN.test(task.id)) {
      const reconnect = document.createElement('button');
      reconnect.type = 'button';
      reconnect.className = 'task-action';
      reconnect.textContent = 'Pobierz status';
      reconnect.setAttribute('aria-label', `Pobierz status zadania ${task.id}`);
      reconnect.addEventListener('click', () => void refreshTaskStatus(task.id, reconnect));
      actions.append(reconnect);
      if (task.agent === 'alex' && task.alexSubmission) {
        const receipt = document.createElement('span');
        receipt.className = 'task-state';
        receipt.textContent = 'Przekazane serwerowi Alexa · praca AI niepotwierdzona';
        actions.append(receipt);
      }
      if (task.agent === 'alex' && task.status === 'queued' && !task.alexSubmission) {
        const dispatch = document.createElement('button');
        dispatch.type = 'button';
        dispatch.className = 'task-action task-dispatch';
        dispatch.textContent = 'Przekaż Alexowi';
        dispatch.disabled = !alexDispatchSupported;
        dispatch.setAttribute('aria-label', `Przekaż zadanie ${task.id} oryginalnemu Alexowi`);
        dispatch.addEventListener('click', () => void dispatchWorkToAlex(task.id, dispatch));
        actions.append(dispatch);
      }
      if (task.status === 'queued' && !task.alexSubmission) {
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'task-action task-cancel';
        cancel.textContent = 'Anuluj';
        cancel.setAttribute('aria-label', `Anuluj zadanie ${task.id}`);
        cancel.addEventListener('click', () => void cancelWorkTask(task.id, cancel));
        actions.append(cancel);
      }
    }
    item.append(detail, status, actions);
    fragment.append(item);
  }
  if (!tasks.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = 'Brak zapisanych zadań do przeglądu.';
    fragment.append(empty);
  }
  list.replaceChildren(fragment);
  setText('tasks-count', `${tasks.length} zadań · zapis SQLite · brak potwierdzonej pracy workerów`);
  setText('tasks-error', '');
}

function renderEvents(events) {
  const list = document.getElementById('event-list');
  if (!list) return;
  const fragment = document.createDocumentFragment();
  for (const event of events) {
    if (
      typeof event?.type !== 'string'
      || typeof event.agent !== 'string'
      || typeof event.task_id !== 'string'
    ) continue;
    const item = document.createElement('li');
    item.className = 'event-item';
    const detail = document.createElement('span');
    const agent = AGENT_PROFILES.find((profile) => profile.id === event.agent)?.name ?? event.agent;
    detail.textContent = `${event.type} · ${agent}`;
    const metadata = document.createElement('span');
    metadata.className = 'event-metadata';
    const time = new Date(event.at);
    const timestamp = Number.isNaN(time.getTime()) ? 'czas nieznany' : time.toLocaleString('pl-PL');
    metadata.textContent = `${timestamp} · ${event.task_id}`;
    item.append(detail, metadata);
    fragment.append(item);
  }
  if (!fragment.childNodes.length) {
    const empty = document.createElement('li');
    empty.className = 'empty-state';
    empty.textContent = events.length
      ? 'Brak zdarzeń o rozpoznanym formacie.'
      : 'API nie zarejestrowało jeszcze zdarzeń.';
    fragment.append(empty);
  }
  list.replaceChildren(fragment);
  setText('events-count', `${events.length} zdarzeń · trwały dziennik SQLite`);
  setText('events-error', '');
}

function readHistory() {
  try {
    const history = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
    return Array.isArray(history)
      ? history.filter((item) => item && SESSION_ID_PATTERN.test(item.id) && typeof item.title === 'string').slice(0, 20)
      : [];
  } catch {
    return [];
  }
}

function writeHistory(history) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20)));
  } catch {
    setText('chat-feedback', 'Indeks historii nie może zostać zapisany w przeglądarce.');
  }
}

function renderHistory(activeId = '') {
  const list = document.getElementById('chat-history');
  if (!list) return;
  const history = readHistory();
  list.replaceChildren();
  if (!history.length) {
    const empty = document.createElement('li');
    empty.className = 'history-empty';
    empty.textContent = 'Brak zapisanych rozmów.';
    list.append(empty);
    return;
  }
  for (const item of history) {
    const row = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'history-button';
    button.textContent = item.title;
    button.dataset.sessionId = item.id;
    if (item.id === activeId) button.setAttribute('aria-current', 'true');
    button.addEventListener('click', () => void loadHistory(item.id));
    row.append(button);
    list.append(row);
  }
}

const api = createApiClient();
let alexDispatchSupported = false;
let cachedWorkTasks = [];
let workRefreshPending = false;
let activeSessionId = '';
let activeChatController = null;
let activeChatTaskId = '';
let activeChatCancelMessage = '';

async function loadStatus() {
  connectionState('loading', 'Ładowanie statusu…');
  setText('status-error', '');
  for (const id of ['application-status', 'os-status', 'model-status', 'task-storage-status']) {
    setText(id, 'Ładowanie…');
  }
  try {
    renderRuntimeStatus(await api.getStatus());
    return true;
  } catch (error) {
    connectionState('error', error.status === 401 ? 'Wymagana autoryzacja' : 'Runtime niedostępny');
    for (const id of ['application-status', 'os-status', 'model-status', 'task-storage-status']) {
      setText(id, 'Niedostępny');
    }
    setText('status-error', error.message);
    return false;
  }
}

async function loadOriginalAgents() {
  const list = document.getElementById('original-agent-list');
  if (!list) return;
  setText('original-agent-error', '');
  try {
    const response = await api.getOriginalAgents();
    if (!Array.isArray(response.agents) || response.agents.length !== 4) {
      throw new ApiError('Źródło nie podało kompletu czterech oryginalnych agentów.');
    }
    const fragment = document.createDocumentFragment();
    for (const agent of response.agents) {
      const item = document.createElement('li');
      item.className = 'agent-card';
      const heading = document.createElement('strong');
      heading.textContent = agent.name;
      const status = document.createElement('p');
      status.className = 'agent-state';
      status.textContent = agent.workerVerified === true
        ? 'Zweryfikowany proces AI'
        : response.registryReachable
          ? 'Rejestr źródłowy dostępny · praca AI niepotwierdzona'
          : 'Oryginalny agent niepodłączony';
      const details = document.createElement('p');
      details.className = 'worker-state';
      details.textContent = `Zadania w rejestrze: ${Number(agent.tasksInRegistry) || 0}; uruchomione workery: niepotwierdzone`;
      item.append(heading, status, details);
      fragment.append(item);
    }
    list.replaceChildren(fragment);
    for (const agent of response.agents) {
      const id = String(agent.id || agent.name || '').toLowerCase();
      const statusElement = document.querySelector(`[data-tile-status="${id}"]`);
      if (statusElement) statusElement.textContent = agent.workerVerified === true
        ? 'Proces zweryfikowany' : response.registryReachable
          ? 'Rejestr źródłowy' : 'Niepodłączony';
    }
  } catch (error) {
    list.replaceChildren();
    for (const element of document.querySelectorAll('[data-tile-status]')) {
      element.textContent = 'API niedostępne';
    }
    setText('original-agent-error', error.message);
  }
}

async function loadAgents() {
  setText('agent-count', 'Ładowanie profili…');
  setText('agent-error', '');
  renderAgentProfiles(document.getElementById('agent-list'));
  try {
    const payload = await api.getAgents();
    if (!Array.isArray(payload.agents)) throw new ApiError('Nieprawidłowy format listy profili.');
    const agents = payload.agents;
    renderAgentProfiles(document.getElementById('agent-list'), agents);
    const workers = agents.filter(({ worker }) => worker === 'running').length;
    setText('agent-count', `${agents.length} profili · ${workers} workerów uruchomionych`);
  } catch (error) {
    setText('agent-count', 'Profile niedostępne');
    setText('agent-error', error.message);
    renderAgentProfiles(document.getElementById('agent-list'));
  }
}

async function loadIntegrations() {
  const list = document.getElementById('integration-list');
  if (!list) return;
  list.replaceChildren();
  try {
    const response = await api.getIntegrations();
    if (!Array.isArray(response.integrations)) throw new ApiError('Nieprawidłowa odpowiedź integracji.');
    for (const item of response.integrations) {
      const entry = document.createElement('li');
      entry.className = 'agent-card';
      const label = document.createElement('strong');
      label.textContent = item.name;
      const status = document.createElement('p');
      status.className = 'agent-state';
      status.textContent = item.connected === true
        ? 'Połączenie potwierdzone'
        : 'Niepołączono · ' + item.status;
      const description = document.createElement('p');
      description.className = 'agent-role';
      description.textContent = item.description;
      entry.append(label, status, description);
      list.append(entry);
    }
    setText('integration-error', '');
  } catch (error) {
    setText('integration-error', error.message);
  }

  // Endpoint MCP is provided by the gateway, but its remote connectivity is
  // not inferred from a local path. Do not call a non-existent mcp-info API.
  setText('mcp-public-healthz', '/healthz (na aktualnym serwerze)');
  setText('mcp-public-endpoint', '/mcp (wymaga autoryzacji Bearer)');
  setText('mcp-bearer-token', 'Sekret operatora pozostaje po stronie serwera.');

}

async function loadTasks() {
  setText('tasks-count', 'Ładowanie…');
  setText('tasks-error', '');
  try {
    const payload = await api.getTasks();
    if (!Array.isArray(payload.tasks)) throw new ApiError('Nieprawidłowy format listy zadań.');
    cachedWorkTasks = payload.tasks;
    renderTasks(payload.tasks);
    return null;
  } catch (error) {
    cachedWorkTasks = [];
    setText('tasks-count', 'Zadania niedostępne');
    setText('tasks-error', error.message);
    const list = document.getElementById('task-list');
    if (list) list.replaceChildren();
    return error;
  }
}

async function loadEvents() {
  setText('events-count', 'Pobieranie zdarzeń…');
  setText('events-error', '');
  const list = document.getElementById('event-list');
  if (list) {
    const loading = document.createElement('li');
    loading.className = 'empty-state';
    loading.textContent = 'Pobieranie zdarzeń z API…';
    list.replaceChildren(loading);
  }
  try {
    const payload = await api.getEvents();
    if (!Array.isArray(payload.events)) throw new ApiError('Nieprawidłowy format dziennika zdarzeń.');
    renderEvents(payload.events);
    return null;
  } catch (error) {
    setText('events-count', 'Zdarzenia niedostępne');
    setText('events-error', error.message);
    if (list) list.replaceChildren();
    return error;
  }
}

async function refreshAlexCapabilities() {
  alexDispatchSupported = false;
  try {
    const response = await api.getAlexCapabilities();
    const state = response.alex;
    alexDispatchSupported = state?.configured === true && state?.supported === true;
    setText('alex-dispatch-state', alexDispatchSupported
      ? 'Oryginalny Alex: protokół przyjęcia zadań dostępny. Wykonanie przez AI wymaga niezależnego potwierdzenia.'
      : 'Oryginalny Alex: przekazywanie zadań niedostępne. Można nadal tworzyć, przeglądać i anulować lokalne zadania.');
    return { checked: true, supported: alexDispatchSupported };
  } catch (error) {
    setText('alex-dispatch-state', 'Nie udało się sprawdzić oryginalnego Alexa: ' + error.message);
    return { checked: false, supported: false };
  }
}

async function dispatchWorkToAlex(taskId, button) {
  if (!alexDispatchSupported || button.disabled) return;
  const approved = window.confirm(
    'Czy przekazać to zadanie do oryginalnego Alexa? Nie oznacza to zgody na zakup, scalanie ani wykonywanie płatności.'
  );
  if (!approved) return;
  button.disabled = true;
  setText('work-connection-state', 'Weryfikacja odbioru zadania przez serwer Alexa…');
  try {
    const result = await api.dispatchToAlex(taskId);
    if (!result.alexSubmission || result.executionStarted !== false) {
      throw new ApiError('Brak potwierdzenia przyjęcia zadania przez oryginalne API Alexa.');
    }
    setText('work-connection-state', 'Oryginalny serwer Alexa przyjął zadanie. Wykonanie przez AI i PR pozostają niepotwierdzone.');
    await refreshWork();
  } catch (error) {
    setText('tasks-error', error.message);
    setText('work-connection-state', error.message);
  } finally {
    button.disabled = false;
  }
}

async function refreshWork() {
  if (workRefreshPending) return false;
  workRefreshPending = true;
  const button = document.getElementById('refresh-tasks');
  if (button) { button.disabled = true; button.textContent = 'Odświeżanie…'; }
  setText('work-connection-state', 'Weryfikuję zadania, zdarzenia i połączenie z Alexem…');
  try {
    const [tasksError, eventsError, alexState] = await Promise.all([
      loadTasks(), loadEvents(), refreshAlexCapabilities(),
    ]);
    // First list render may have happened before the capability response.
    // Repaint from memory, do not issue a duplicate backend request.
    if (!tasksError) renderTasks(cachedWorkTasks);
    const error = tasksError || eventsError;
    const label = error
      ? error.status === 401
        ? 'Work: brak autoryzacji (401). Sprawdź token operatora w konfiguracji API.'
        : error.status === 403
          ? 'Work: dostęp odrzucony (403).'
          : error.status === 404
            ? 'Work: wymagany endpoint nie istnieje w tym backendzie.'
            : 'Work: brak pełnego połączenia z API. Szczegóły w komunikatach zadań i zdarzeń.'
      : alexState.supported
        ? 'Work: zadania i zdarzenia działają; oryginalny Alex przyjmuje zlecenia (praca AI niepotwierdzona).'
        : alexState.checked
          ? 'Work: zadania i zdarzenia działają. Zlecanie pracy oryginalnemu Alexowi jest niedostępne.'
          : 'Work: zadania i zdarzenia działają. Połączenia z oryginalnym Alexem nie udało się zweryfikować.';
    setText('work-connection-state', label);
    if (button) button.textContent = error ? 'Ponów połączenie' : 'Odśwież';
    return !error;
  } catch (error) {
    setText('work-connection-state', 'Work: błąd odświeżania: ' + error.message);
    if (button) button.textContent = 'Ponów połączenie';
    return false;
  } finally {
    workRefreshPending = false;
    if (button) button.disabled = false;
  }
}

async function refreshTaskStatus(taskId, button) {
  button.disabled = true;
  try {
    const payload = await api.getTask(taskId);
    if (!payload.task || payload.task.id !== taskId || typeof payload.task.status !== 'string') {
      throw new ApiError('API zwróciło nieprawidłowy status zadania.');
    }
    const item = button.closest('[data-task-id]');
    const status = item?.querySelector('.task-state');
    if (status) {
      status.textContent = payload.task.status;
      status.className = `task-state task-${payload.task.status}`;
    }
    if (payload.alexSubmission) {
      payload.task.alexSubmission = payload.alexSubmission;
    }
    cachedWorkTasks = cachedWorkTasks.map(task => task.id === taskId ? payload.task : task);
    renderTasks(cachedWorkTasks);
    setText('work-connection-state', `Status zadania pobrany z API: ${payload.task.status}.`);
    setText('tasks-error', '');
  } catch (error) {
    setText('tasks-error', error.message);
    setText('work-connection-state', error.status === 404
      ? 'Szczegółowy status zadania nie jest dostępny w tym backendzie.'
      : error.message);
  } finally {
    button.disabled = false;
  }
}

async function cancelWorkTask(taskId, button) {
  button.disabled = true;
  setText('work-connection-state', 'Wysyłanie żądania anulowania do API…');
  try {
    const payload = await api.cancelTask(taskId);
    if (!payload.task || payload.task.id !== taskId) {
      throw new ApiError('API nie potwierdziło zadania do anulowania.');
    }
    await refreshWork();
    setText('work-connection-state', `API przyjęło żądanie anulowania: ${payload.task.status}.`);
  } catch (error) {
    setText('tasks-error', error.message);
    setText('work-connection-state', error.status === 404
      ? 'Ten backend nie obsługuje anulowania zadań.'
      : error.message);
    button.disabled = false;
  }
}

async function refreshDashboard() {
  // A global refresh pulls all runtime views from the real API; it does not
  // perform a GitHub push or AI Studio import.
  const [statusOk, , , , workOk] = await Promise.all([
    loadStatus(), loadAgents(), loadOriginalAgents(), loadIntegrations(), refreshWork(),
  ]);
  return statusOk === true && workOk === true;
}

let systemSyncPending = false;
async function synchronizeSystemView() {
  if (systemSyncPending) return;
  systemSyncPending = true;
  const button = document.getElementById('sync-system');
  if (button) {
    button.disabled = true;
    button.textContent = '⟳ Odświeżanie…';
  }
  setText('hyperspace-sync-feedback', 'Pobieranie statusu, agentów, integracji, zadań i zdarzeń z API…');
  try {
    const complete = await refreshDashboard();
    setText('hyperspace-sync-feedback', complete
      ? 'Dane API odświeżone. Zmiany kodu z GitHub main pobierzesz osobno w AI Studio → Pull changes.'
      : 'Odświeżanie niepełne — sprawdź diagnostykę i autoryzację API. GitHub → AI Studio synchronizuj osobno.');
  } catch (error) {
    setText('hyperspace-sync-feedback', 'Odświeżanie nieudane: ' + error.message);
  } finally {
    systemSyncPending = false;
    if (button) {
      button.disabled = false;
      button.textContent = '⟳ Odśwież dane systemu';
    }
  }
}

async function loadHistory(sessionId) {
  if (!SESSION_ID_PATTERN.test(sessionId)) return;
  setText('chat-feedback', 'Pobieranie historii rozmowy…');
  try {
    const payload = await api.getMessages(sessionId);
    if (!Array.isArray(payload.messages)) throw new ApiError('Nieprawidłowy format historii rozmowy.');
    activeSessionId = sessionId;
    try {
      sessionStorage.setItem(SESSION_KEY, sessionId);
    } catch {
      setText('chat-feedback', 'Historia dostępna do końca tej karty przeglądarki.');
    }
    renderTranscriptMessages(payload.messages);
    renderHistory(activeSessionId);
    showView('chat');
    setText('chat-feedback', 'Historia pobrana z pamięci API.');
  } catch (error) {
    const history = readHistory().filter((item) => item.id !== sessionId);
    writeHistory(history);
    renderHistory(activeSessionId);
    setText('chat-feedback', error.status === 404
      ? 'Ta sesja wygasła lub backend został uruchomiony ponownie.'
      : error.message);
  }
}

function recordHistory(sessionId, title) {
  const history = readHistory().filter((item) => item.id !== sessionId);
  history.unshift({ id: sessionId, title: title.slice(0, 64) || 'Nowa rozmowa' });
  writeHistory(history);
  renderHistory(sessionId);
  try {
    sessionStorage.setItem(SESSION_KEY, sessionId);
  } catch {
    setText('chat-feedback', 'Sesja działa, ale nie może zostać zapisana w karcie przeglądarki.');
  }
}

function showView(view) {
  // Expose the active view to layout CSS: only chat uses the immersive viewport.
  document.documentElement.dataset.activeView = view;
  const ids = { hyperspace: 'hyperspace-view', chat: 'chat-view', work: 'work-view', diagnostics: 'diagnostics-view', terminal: 'terminal-view', settings: 'settings-view' };
  for (const [name, id] of Object.entries(ids)) {
    const section = document.getElementById(id);
    if (section) section.hidden = name !== view;
  }
  for (const button of document.querySelectorAll('[data-view]')) {
    const active = button.dataset.view === view;
    button.classList.toggle('is-active', active);
    if (button.classList.contains('nav-button')) {
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    } else {
      button.setAttribute('aria-pressed', String(active));
    }
  }
  const titles = { hyperspace: 'Enter the Hyperspace', chat: 'W czym mogę pomóc?', work: 'Work', diagnostics: 'Diagnostyka', terminal: 'Terminal', settings: 'Status systemu' };
  setText('page-title', titles[view] ?? titles.chat);
}

function setMobileSidebar(open) {
  const sidebar = document.getElementById('sidebar');
  const menu = document.getElementById('mobile-menu');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!sidebar || !menu || !backdrop) return;
  const isMobile = window.matchMedia('(max-width: 760px)').matches;
  sidebar.classList.toggle('is-open', isMobile && open);
  sidebar.inert = isMobile && !open;
  sidebar.setAttribute('aria-hidden', String(isMobile && !open));
  menu.setAttribute('aria-expanded', String(isMobile && open));
  menu.setAttribute('aria-label', isMobile && open ? 'Zamknij panel boczny' : 'Otwórz panel boczny');
  backdrop.hidden = !(isMobile && open);
  document.body.classList.toggle('sidebar-open', isMobile && open);
}

function setCollapsedSidebar(collapsed) {
  const layout = document.getElementById('app-layout');
  const button = document.getElementById('sidebar-collapse');
  if (!layout || !button) return;
  layout.dataset.collapsed = String(collapsed);
  button.setAttribute('aria-expanded', String(!collapsed));
  button.setAttribute('aria-label', collapsed ? 'Rozwiń panel boczny' : 'Zwiń panel boczny');
  button.title = collapsed ? 'Rozwiń panel boczny' : 'Zwiń panel boczny';
}

function syncAgentChannel() {
  const select = document.getElementById('chat-agent');
  const channel = select?.value || 'aria';
  const isGroup = channel === 'wszyscy';
  for (const tile of document.querySelectorAll('[data-agent-channel]')) {
    tile.setAttribute('aria-pressed', String(tile.dataset.agentChannel === channel));
  }
  const notice = document.getElementById('group-channel-notice');
  if (notice) notice.hidden = !isGroup;
  const submit = document.getElementById('chat-submit');
  if (submit) submit.disabled = isGroup || Boolean(activeChatController);
  const name = AGENT_PROFILES.find(a => a.id === channel)?.name || channel;
  setText('agent-channel-feedback', isGroup
    ? 'Wszyscy · oczekiwanie na prawdziwe dwukierunkowe API'
    : 'Kanał ' + name + ' · status połączenia w kafelku');
  setText('channel-status', isGroup
    ? 'Wszyscy: wysyłanie zablokowane, dopóki brak API oryginalnych agentów.'
    : 'Wiadomości kierowane do profilu ' + name + ' przez istniejące API.');
}

function bindNavigation() {
  const menu = document.getElementById('mobile-menu');
  const backdrop = document.getElementById('sidebar-backdrop');
  const collapse = document.getElementById('sidebar-collapse');
  const sidebar = document.getElementById('sidebar');
  const media = window.matchMedia('(max-width: 760px)');
  document.querySelectorAll('[data-agent-channel]').forEach(tile => tile.addEventListener('click', () => {
    const select = document.getElementById('chat-agent');
    if (select) { select.value = tile.dataset.agentChannel; syncAgentChannel(); }
    showView('chat');
    setMobileSidebar(false);
    document.getElementById('chat-message')?.focus();
  }));
  document.getElementById('chat-agent')?.addEventListener('change', syncAgentChannel);
  document.getElementById('new-chat-inline')?.addEventListener('click', () =>
    document.getElementById('new-chat')?.click());
  syncAgentChannel();
  menu?.addEventListener('click', () => setMobileSidebar(!sidebar?.classList.contains('is-open')));
  backdrop?.addEventListener('click', () => setMobileSidebar(false));
  collapse?.addEventListener('click', () => {
    const collapsed = document.getElementById('app-layout')?.dataset.collapsed === 'true';
    setCollapsedSidebar(!collapsed);
    try {
      localStorage.setItem('debian13-sidebar-collapsed', String(!collapsed));
    } catch {
      // The panel remains usable when browser storage is disabled.
    }
  });
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.addEventListener('click', () => {
      showView(button.dataset.view);
      if (button.dataset.view === 'work') void refreshWork();
      setMobileSidebar(false);
    });
  });
  document.getElementById('new-chat')?.addEventListener('click', () => {
    activeSessionId = '';
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // Starting a chat does not depend on session storage.
    }
    renderHistory();
    renderTranscriptMessages([]);
    showView('chat');
    setText('chat-feedback', 'Nowa sesja zostanie utworzona przez API po wysłaniu wiadomości.');
    document.getElementById('chat-message')?.focus();
    setMobileSidebar(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sidebar?.classList.contains('is-open')) {
      setMobileSidebar(false);
      menu?.focus();
    }
  });
  media.addEventListener('change', () => setMobileSidebar(false));
  try {
    setCollapsedSidebar(localStorage.getItem('debian13-sidebar-collapsed') === 'true');
  } catch {
    setCollapsedSidebar(false);
  }
  setMobileSidebar(false);
}

async function prepareVisionImage(file) {
  if (!['image/jpeg', 'image/png'].includes(file?.type)) {
    throw new ApiError('Vision obsługuje tylko obrazy JPEG i PNG.');
  }
  if (file.size > 8 * 1024 * 1024) {
    throw new ApiError('Zdjęcie przekracza limit 8 MB. Wybierz mniejszy plik.');
  }
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new ApiError('Nie można odczytać wybranego zdjęcia.'));
      image.src = objectUrl;
    });
    const width = image.naturalWidth;
    const height = image.naturalHeight;
    if (!width || !height) throw new ApiError('Obraz nie zawiera poprawnych wymiarów.');
    const scale = Math.min(1, 1280 / Math.max(width, height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new ApiError('Przeglądarka nie umożliwia przetworzenia zdjęcia.');
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const uri = canvas.toDataURL('image/jpeg', 0.78);
    const encoded = uri.split(',', 2)[1] ?? '';
    if (!encoded || encoded.length > 4 * Math.ceil((2 * 1024 * 1024) / 3)) {
      throw new ApiError('Obraz po przetworzeniu jest za duży. Wybierz mniejszy plik.');
    }
    return { mime_type: 'image/jpeg', base64: encoded };
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function bindChatForm() {
  const form = document.getElementById('chat-form');
  if (!form) return;
  const message = document.getElementById('chat-message');
  const attachment = document.getElementById('chat-image');
  const preview = document.getElementById('chat-image-preview');
  const thumbnail = document.getElementById('chat-image-thumbnail');
  const fileName = document.getElementById('chat-image-name');
  let previewUrl = '';
  let visionBusy = false;
  const clearAttachment = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
    if (attachment) attachment.value = '';
    if (thumbnail) thumbnail.removeAttribute('src');
    if (preview) preview.hidden = true;
  };
  attachment?.addEventListener('change', () => {
    clearAttachmentPreviewOnly();
    const file = attachment.files?.[0];
    if (!file) return;
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 8 * 1024 * 1024) {
      setText('chat-feedback', 'Wybierz obraz JPEG/PNG o rozmiarze do 8 MB.');
      attachment.value = '';
      return;
    }
    previewUrl = URL.createObjectURL(file);
    if (thumbnail) thumbnail.src = previewUrl;
    if (fileName) fileName.textContent = file.name;
    if (preview) preview.hidden = false;
  });
  function clearAttachmentPreviewOnly() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
    if (thumbnail) thumbnail.removeAttribute('src');
    if (preview) preview.hidden = true;
  }
  document.getElementById('chat-image-remove')?.addEventListener('click', clearAttachment);
  message?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const agent = document.getElementById('chat-agent');
    const submit = document.getElementById('chat-submit');
    const stop = document.getElementById('chat-stop');
    const feedback = document.getElementById('chat-feedback');
    const text = message?.value.trim();
    if (!text || !agent || !feedback || activeChatController || visionBusy) return;
    if (agent.value === 'wszyscy') {
      feedback.textContent = 'Czat grupowy wymaga działającego, uwierzytelnionego API czterech agentów. Wiadomości nie wysłano.';
      return;
    }
    // Explicit CLI handoff: chat never silently executes model-generated shell commands.
    // The existing authenticated terminal API remains the sole execution path.
    const cliCommand = parseCliCommand(text);
    if (cliCommand !== null) {
      if (attachment?.files?.length) {
        feedback.textContent = 'Polecenie /cli nie obsługuje załączników. Usuń obraz i spróbuj ponownie.';
        return;
      }
      const command = cliCommand;
      if (!command) {
        feedback.textContent = 'Użycie: /cli pwd lub /cli ls. Dostępne są tylko polecenia dozwolone przez serwer.';
        return;
      }
      const terminalInput = document.getElementById('terminal-command');
      const terminalForm = document.getElementById('terminal-form');
      if (!terminalInput || !terminalForm) {
        feedback.textContent = 'Konsola CLI jest niedostępna.';
        return;
      }
      if (document.getElementById('terminal-submit')?.disabled) {
        feedback.textContent = 'Konsola CLI wykonuje już polecenie. Poczekaj na zakończenie.';
        return;
      }
      appendTranscript('user', 'Ty · polecenie CLI', command);
      appendTranscript('assistant', 'Konsola CLI · przekazanie', 'Polecenie przekazano do terminala. Wynik i kod zakończenia pojawią się w widoku Terminal; to nie jest odpowiedź modelu AI.');
      terminalInput.value = command;
      message.value = '';
      showView('terminal');
      terminalForm.requestSubmit();
      return;
    }
    const file = attachment?.files?.[0] ?? null;
    if (file) {
      visionBusy = true;
      submit.disabled = true;
      const name = AGENT_PROFILES.find((profile) => profile.id === agent.value)?.name ?? agent.value;
      appendTranscript('user', `Ty · ${name} · zdjęcie`, `${text} (dołączono obraz)`);
      const answer = appendTranscript('assistant', `${name} · analizuje zdjęcie`, 'Wysyłanie do Vision…');
      feedback.textContent = 'Analiza obrazu przez model Vision…';
      try {
        const prepared = await prepareVisionImage(file);
        const result = await api.vision(agent.value, text, prepared);
        if (typeof result.content !== 'string') throw new ApiError('Brak tekstu odpowiedzi modelu Vision.');
        if (answer) {
          answer.text.textContent = result.content;
          answer.heading.textContent = `${name} · ${result.model ?? 'Vision'}`;
        }
        message.value = '';
        clearAttachment();
        feedback.textContent = 'Odpowiedź modelu Vision otrzymana z API.';
      } catch (error) {
        answer?.entry.remove();
        appendTranscript('error', 'Błąd Vision', error.message);
        feedback.textContent = error.message;
      } finally {
        visionBusy = false;
        submit.disabled = false;
        await loadTasks();
      }
      return;
    }

    const controller = new AbortController();
    activeChatController = controller;
    activeChatTaskId = '';
    activeChatCancelMessage = '';
    const agentName = AGENT_PROFILES.find((profile) => profile.id === agent.value)?.name ?? agent.value;
    const userMessage = text;
    appendTranscript('user', `Ty · ${agentName}`, userMessage);
    const responseBubble = appendTranscript('assistant', `${agentName} · łączenie`);
    submit.hidden = true;
    stop.hidden = false;
    feedback.textContent = 'Łączenie ze strumieniem API…';
    let responseText = '';
    let receivedDone = false;
    let receivedCancelled = false;
    let responseSource = '';
    let responseAgent = agentName;
    let responseSessionId = '';

    try {
      await api.chatStream(userMessage, agent.value, activeSessionId, ({ event: streamEvent, payload }) => {
        if (streamEvent === 'meta' && typeof payload.session_id === 'string') {
          responseSessionId = payload.session_id;
          if (typeof payload.task_id === 'string' && SESSION_ID_PATTERN.test(payload.task_id)) {
            activeChatTaskId = payload.task_id;
          }
        } else if (streamEvent === 'delta' && typeof payload.text === 'string') {
          responseText += payload.text;
          if (responseBubble) responseBubble.text.textContent = responseText;
          const transcript = document.getElementById('chat-transcript');
          if (transcript) transcript.scrollTop = transcript.scrollHeight;
        } else if (streamEvent === 'done') {
          receivedDone = true;
          responseSessionId = payload.session_id ?? responseSessionId;
          responseSource = payload.source ?? '';
          responseAgent = AGENT_PROFILES.find((profile) => profile.id === payload.agent)?.name ?? responseAgent;
        } else if (streamEvent === 'cancelled') {
          receivedCancelled = true;
          responseSessionId = payload.session_id ?? responseSessionId;
          if (responseBubble) {
            responseBubble.heading.textContent = `${agentName} · anulowano przez API`;
            if (!responseText) responseBubble.text.textContent = 'API potwierdziło przerwanie zadania.';
          }
        } else if (streamEvent === 'error') {
          throw new ApiError(typeof payload.error === 'string' ? payload.error : 'Strumień czatu zakończył się błędem.');
        }
      }, controller.signal);
      if (receivedCancelled) {
        if (responseSessionId && SESSION_ID_PATTERN.test(responseSessionId)) {
          activeSessionId = responseSessionId;
          recordHistory(responseSessionId, userMessage);
        }
        feedback.textContent = 'API potwierdziło anulowanie zadania.';
        return;
      }
      if (!receivedDone) throw new ApiError('Strumień zakończył się przed potwierdzeniem odpowiedzi.');
      if (responseBubble) responseBubble.heading.textContent = `${responseAgent} · ${responseSource || 'API'}`;
      if (responseSessionId && SESSION_ID_PATTERN.test(responseSessionId)) {
        activeSessionId = responseSessionId;
        recordHistory(responseSessionId, userMessage);
      }
      message.value = '';
      feedback.textContent = responseSource === 'ollama'
        ? 'Odpowiedź lokalnej Ollamy; API nie zapisuje historii sesji.'
        : responseSource === 'model_provider'
          ? 'Odpowiedź podłączonego dostawcy AI; brak zapisu historii sesji.'
          : 'Odpowiedź API.';
    } catch (error) {
      if (error?.name === 'AbortError') {
        if (responseBubble) responseBubble.heading.textContent = `${agentName} · strumień przerwany lokalnie`;
        feedback.textContent = activeChatCancelMessage
          || 'Zatrzymano odbiór strumienia. Backend nie potwierdził anulowania zadania.';
      } else {
        if (!responseText && responseBubble) responseBubble.entry.remove();
        else if (responseBubble) responseBubble.heading.textContent = `${agentName} · odpowiedź nieukończona`;
        appendTranscript('error', `Błąd API${error.status ? ` · HTTP ${error.status}` : ''}`, error.message);
        feedback.textContent = error.status === 503
          ? 'Model niedostępny — API nie wygenerowało odpowiedzi.'
          : error.message;
      }
    } finally {
      activeChatController = null;
      activeChatTaskId = '';
      submit.hidden = false;
      stop.hidden = true;
      stop.disabled = false;
      stop.textContent = 'Zatrzymaj';
      await loadTasks();
      syncAgentChannel();
    }
  });
  document.getElementById('chat-stop')?.addEventListener('click', async (event) => {
    const stop = event.currentTarget;
    if (!activeChatController || stop.disabled) return;
    const controller = activeChatController;
    const taskId = activeChatTaskId;
    if (!taskId) {
      activeChatCancelMessage = 'Brak ID zadania do anulowania. Zatrzymano odbiór strumienia; anulowanie serwera nie jest potwierdzone.';
      controller.abort();
      return;
    }
    stop.disabled = true;
    stop.textContent = 'Anulowanie…';
    setText('chat-feedback', 'Wysyłanie żądania anulowania do API…');
    try {
      const payload = await api.cancelTask(taskId);
      if (payload.task?.id !== taskId) throw new ApiError('API nie potwierdziło zadania do anulowania.');
      if (activeChatController !== controller) return;
      setText('chat-feedback', `API przyjęło anulowanie (${payload.task.status}); oczekiwanie na zdarzenie SSE.`);
    } catch (error) {
      if (activeChatController !== controller) return;
      controller.abort();
      activeChatCancelMessage = error.status === 404
        ? 'Ten backend nie udostępnia anulowania zadania. Zatrzymano odbiór strumienia lokalnie; anulowanie serwera nie jest potwierdzone.'
        : error.status === 401 || error.status === 403
          ? `${error.message} Zatrzymano odbiór strumienia; anulowanie serwera nie jest potwierdzone.`
          : `${error.message} Zatrzymano odbiór strumienia; anulowanie serwera nie jest potwierdzone.`;
      setText('chat-feedback', activeChatCancelMessage);
    }
  });
}

function bindWorkCreateForm() {
  const form = document.getElementById('work-create-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = document.getElementById('work-create-submit');
    const agent = document.getElementById('work-task-agent')?.value;
    const kind = document.getElementById('work-task-kind')?.value;
    const title = document.getElementById('work-task-title')?.value.trim();
    const description = document.getElementById('work-task-description')?.value.trim();
    if (!agent || !kind || !title || !description || button.disabled) return;
    button.disabled = true;
    setText('work-create-feedback', 'Zapisuję zadanie w bazie API…');
    try {
      const result = await api.createTask({ agent, kind, title, description });
      if (!result.task?.id || result.executionStarted !== false) {
        throw new ApiError('API nie potwierdziło bezpiecznej rejestracji zadania.');
      }
      document.getElementById('work-task-title').value = '';
      document.getElementById('work-task-description').value = '';
      await refreshWork();
      setText('work-create-feedback', 'Zadanie zapisane. Agent AI NIE został uruchomiony; oczekuje na przegląd operatora.');
    } catch (error) {
      setText('work-create-feedback', error.message);
    } finally {
      button.disabled = false;
    }
  });
}

function bindTerminalForm() {
  const form = document.getElementById('terminal-form');
  if (!form) return;
  let activeController = null;
  // Session-only CLI history: do not persist commands or potential secrets.
  const commandHistory = [];
  let historyIndex = 0;
  let unfinishedCommand = '';
  const commandField = document.getElementById('terminal-command');
  commandField?.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    if (event.ctrlKey || event.altKey || event.metaKey || event.isComposing) return;
    if (!commandHistory.length) return;
    const field = event.currentTarget;
    if (event.key === 'ArrowUp' && field.selectionStart !== 0) return;
    if (event.key === 'ArrowDown' && field.selectionStart !== field.value.length) return;
    event.preventDefault();
    if (historyIndex === commandHistory.length) unfinishedCommand = field.value;
    historyIndex = Math.max(0, Math.min(commandHistory.length, historyIndex + (event.key === 'ArrowUp' ? -1 : 1)));
    field.value = historyIndex === commandHistory.length ? unfinishedCommand : commandHistory[historyIndex];
    field.setSelectionRange(field.value.length, field.value.length);
  });
  const stop = document.getElementById('terminal-stop');
  const feedback = document.getElementById('terminal-feedback');
  const liveStatus = document.getElementById('terminal-live-status');
  const setStatus = (text) => { if (liveStatus) liveStatus.textContent = text; };
  document.getElementById('terminal-clear')?.addEventListener('click', () => {
    document.getElementById('terminal-output')?.replaceChildren();
    setStatus('Ekran wyczyszczony · procesy nie zostały zmienione');
  });
  stop?.addEventListener('click', () => {
    if (!activeController) return;
    activeController.abort();
    setStatus('Przerwano odbiór danych · serwer nie potwierdził zatrzymania');
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const commandInput = document.getElementById('terminal-command');
    const submit = document.getElementById('terminal-submit');
    const command = commandInput?.value.trim();
    if (!command || !feedback || activeController) return;
    if (commandHistory[commandHistory.length - 1] !== command) {
      commandHistory.push(command);
      if (commandHistory.length > 100) commandHistory.shift();
    }
    historyIndex = commandHistory.length;
    unfinishedCommand = '';
    const controller = new AbortController();
    activeController = controller;
    submit.disabled = true;
    if (stop) stop.hidden = false;
    setStatus('Łączenie z rzeczywistym terminalem…');
    feedback.textContent = 'Oczekiwanie na odpowiedź API…';
    const terminal = document.getElementById('terminal-output');
    terminal?.querySelector('.terminal-hint')?.remove();
    const heading = document.createElement('p');
    heading.className = 'terminal-prompt';
    heading.textContent = '$ ' + command;
    const output = document.createElement('pre');
    output.className = 'terminal-result';
    output.textContent = '';
    terminal?.append(heading, output);
    let done = false;
    let failed = false;
    try {
      await api.terminalStream(command, ({ event, payload }) => {
        if (event === 'meta') {
          setStatus('Terminal LIVE · ' + (payload.command || 'polecenie'));
          feedback.textContent = 'Odczyt rzeczywistego stdout/stderr…';
        } else if (event === 'delta' && typeof payload.text === 'string') {
          output.textContent += payload.text;
          if (payload.stream === 'stderr') output.classList.add('terminal-error');
          if (output.textContent.length > 1024 * 1024) {
            throw new ApiError('Wynik jest zbyt obszerny.');
          }
          if (terminal) terminal.scrollTop = terminal.scrollHeight;
        } else if (event === 'done') {
          done = true;
          const code = Number(payload.exitCode);
          output.textContent += '\n[kod zakończenia: ' + code + ']';
          if (code !== 0) output.classList.add('terminal-error');
          setStatus('Zakończono · exit ' + code);
        } else if (event === 'error') {
          failed = true;
          output.textContent += '\n[Błąd: ' + (payload.code || 'terminal_error') + ']';
          output.classList.add('terminal-error');
          setStatus('Błąd polecenia');
        }
      }, controller.signal);
      if (!done && !failed) throw new ApiError('Nie otrzymano końcowego statusu polecenia.');
      if (done) {
        commandInput.value = '';
        feedback.textContent = 'Zakończono rzeczywiste polecenie z listy dozwolonych.';
      } else {
        feedback.textContent = 'Backend zgłosił błąd. Nie potwierdzono sukcesu.';
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        output.textContent += '\n[Odbiór anulowany lokalnie · stan serwera niepotwierdzony]';
        feedback.textContent = 'Przerwano odbiór terminala.';
      } else {
        output.textContent += '\n[Błąd: ' + error.message + ']';
        feedback.textContent = error.message;
        setStatus('Niepowodzenie terminala');
      }
      output.classList.add('terminal-error');
    } finally {
      activeController = null;
      submit.disabled = false;
      if (stop) stop.hidden = true;
      void loadTasks();
    }
  });
}

function bindInteractiveScripts() {
  const select = document.getElementById('script-select');
  const startForm = document.getElementById('script-start-form');
  const answerForm = document.getElementById('script-answer-form');
  const output = document.getElementById('script-live-output');
  const question = document.getElementById('script-prompt');
  const answerInput = document.getElementById('script-answer');
  let active = null;
  if (!select || !startForm || !answerForm || !output) return;
  const draw = (state) => {
    active = state.status === 'waiting_input' ? state : null;
    output.textContent = [
      'Skrypt: ' + state.scriptId,
      'Status: ' + state.status,
      'Etap: ' + state.step + '/' + state.stepCount,
      ...(Array.isArray(state.output) ? state.output : []),
    ].join('\n');
    answerForm.hidden = !active;
    if (active && question) question.textContent = state.prompt || 'Odpowiedz skryptowi';
    if (active) answerInput?.focus();
  };
  api.listScripts().then((data) => {
    select.replaceChildren();
    for (const script of data.scripts || []) {
      const option = document.createElement('option');
      option.value = script.id;
      option.textContent = script.name + ' — ' + script.description;
      select.append(option);
    }
    if (!select.children.length) output.textContent = 'API nie udostępnia skryptów.';
  }).catch((err) => {
    select.replaceChildren();
    output.textContent = 'Lista skryptów niedostępna: ' + err.message;
  });
  startForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (active) { output.textContent = 'Najpierw zakończ lub anuluj poprzedni skrypt.'; return; }
    const button = document.getElementById('script-start');
    if (button) button.disabled = true;
    try { draw(await api.startScript(select.value)); }
    catch (err) { output.textContent = 'Błąd uruchomienia: ' + err.message; }
    finally { if (button) button.disabled = false; }
  });
  answerForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!active || !answerInput?.value.trim()) return;
    const id = active.sessionId;
    const button = document.getElementById('script-answer-submit');
    if (button) button.disabled = true;
    try {
      draw(await api.respondScript(id, answerInput.value.trim()));
      answerInput.value = '';
    } catch (err) { output.textContent = 'Błąd odpowiedzi: ' + err.message; }
    finally { if (button) button.disabled = false; }
  });
  document.getElementById('script-cancel')?.addEventListener('click', async () => {
    if (!active) return;
    try { draw(await api.cancelScript(active.sessionId)); }
    catch (err) { output.textContent = 'Błąd anulowania: ' + err.message; }
  });
}

function bindWallpaper() {
  const file = document.getElementById('wallpaper-file');
  let current = '';
  const reset = () => {
    if (current) URL.revokeObjectURL(current);
    current = '';
    document.body.style.removeProperty('--user-wallpaper');
  };
  file?.addEventListener('change', () => {
    const image = file.files?.[0];
    if (!image) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(image.type)
        || image.size > 8 * 1024 * 1024) {
      setText('wallpaper-feedback', 'Wybierz PNG/JPEG/WebP do 8 MB.');
      file.value = '';
      return;
    }
    reset();
    current = URL.createObjectURL(image);
    // Browser-local wallpaper. Not uploaded and not persisted to any server.
    document.body.style.setProperty('--user-wallpaper', 'url("' + current + '")');
    setText('wallpaper-feedback', 'Własna tapeta jest widoczna w bieżącej karcie. Nie wysłano jej na serwer.');
  });
  document.getElementById('wallpaper-reset')?.addEventListener('click', () => {
    reset();
    if (file) file.value = '';
    setText('wallpaper-feedback', 'Przywrócono motyw domyślny.');
  });
}

function bindBridgeForm() {
  const form = document.getElementById('bridge-form');
  const termBtn = document.getElementById('bridge-terminate');
  if (!form) return;
  let activeSessionId = '';
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = document.getElementById('bridge-message');
    const output = document.getElementById('bridge-output');
    const msg = input?.value.trim();
    if (!msg || !output) return;
    output.textContent = 'Wysyłanie zapytania przez AI Bridge (Gemini/OpenAI + MCP)…';
    try {
      const provider = document.getElementById('bridge-provider')?.value || 'openai';
      const res = await api.bridge(msg, activeSessionId, provider === 'openai');
      activeSessionId = res.sessionId;
      output.textContent = `[Sesja: ${res.sessionId.slice(0, 8)} | Provider: ${res.provider}]\n\nOdpowiedź:\n${res.response}\n\n[MCP Diagnostics Status: ${res.mcpToolResult?.content?.[0]?.text ? 'Zaliczony' : 'Brak'}]`;
    } catch (err) {
      output.textContent = 'Błąd Bridge: ' + err.message;
    }
  });
  termBtn?.addEventListener('click', async () => {
    const output = document.getElementById('bridge-output');
    try {
      if (activeSessionId) await api.terminateBridge(activeSessionId);
      activeSessionId = '';
      if (output) output.textContent = 'Sesja została usunięta z pamięci backendu.';
    } catch (err) {
      if (output) output.textContent = 'Nie udało się zakończyć sesji: ' + err.message;
    }
  });
}

// Keep the composer visible as Android's keyboard changes the visual viewport.
function bindChatViewport() {
  const viewport = window.visualViewport;
  if (!viewport) return;
  const syncHeight = () => {
    const height = Math.round(viewport.height);
    if (height > 0) {
      document.documentElement.style.setProperty('--chat-viewport-height', height + 'px');
    }
  };
  viewport.addEventListener('resize', syncHeight);
  syncHeight();
}

if (typeof document !== 'undefined') {
  bindChatViewport();
  renderAgentProfiles(document.getElementById('agent-list'));
  bindNavigation();
  bindChatForm();
  bindTerminalForm();
  bindInteractiveScripts();
  bindWallpaper();
  bindWorkCreateForm();
  bindBridgeForm();
  renderHistory();
  document.getElementById('refresh-status')?.addEventListener('click', refreshDashboard);
  document.getElementById('sync-system')?.addEventListener('click', synchronizeSystemView);
  document.getElementById('refresh-tasks')?.addEventListener('click', refreshWork);
  void refreshDashboard();
  let savedSession = '';
  try {
    savedSession = sessionStorage.getItem(SESSION_KEY) ?? '';
  } catch {
    // The API-backed chat remains available without browser storage.
  }
  if (SESSION_ID_PATTERN.test(savedSession)) void loadHistory(savedSession);
}

