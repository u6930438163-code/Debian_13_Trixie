import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { bindDesignControls, normalizeDesignTheme, parseFocusPreference } from './design.mjs';

function repo(path) {
  return readFileSync(new URL('../../' + path, import.meta.url), 'utf8');
}
test('AI Studio/Vite root points at the same canonical UI as server.ts', () => {
  const root = repo('index.html');
  const server = repo('server.ts');
  assert.match(root, /url=\.\/debian13\/ui\//);
  assert.match(server, /const uiDir = path\.join\(__dirname, 'debian13\/ui'\)/);
  assert.match(server, /Cache-Control', 'no-store'/);
});
test('canonical modern UI exposes navigation, real terminal, diagnostics and agent tiles', () => {
  const html = repo('debian13/ui/index.html');
  const js = repo('debian13/ui/app.mjs');
  const css = repo('debian13/ui/styles.css');
  for (const marker of ['data-view="diagnostics"', 'id="diagnostics-view"', 'id="terminal-view"',
                        'id="chat-form"', 'id="work-view"', 'id="agent-tiles"',
                        'id="script-start"', 'id="wallpaper-file"']) {
    assert.ok(html.includes(marker), 'Missing visible component: ' + marker);
  }
  for (const marker of ['bindInteractiveScripts()', 'bindNavigation()', 'message-reactions']) {
    assert.ok(js.includes(marker), 'Missing interaction: ' + marker);
  }
  assert.match(css, /@media \(max-width:\s*360px\)/);
  assert.match(css, /--accent:\s*#38bdf8/);
  assert.match(repo('debian13/ui/dev_server.py'), /Cache-Control.*no-store/);
});

test('live GUI loads Original palette, Cosmic palette and browser-only design module', () => {
  const html = repo('debian13/ui/index.html');
  const css = repo('debian13/ui/styles.css');
  for (const marker of ['src="./design.mjs"', 'data-design-theme="hyperspace"', 'data-design-theme="bastilla-shan"',
                         'data-design-theme="original-mint"', 'data-design-theme="astyria-cosmic"',
                         'id="chat-focus-toggle"', 'id="design-feedback"']) {
    assert.ok(html.includes(marker), 'Missing design component: ' + marker);
  }
  assert.match(css, /--design-accent:\s*#75d6c2/);
  assert.match(css, /--design-accent:\s*#38bdf8/);
  assert.match(css, /--design-accent:\s*#b8a5ff/);
  assert.match(html, /id="ollama-status">Stan Ollamy niezweryfikowany/);
});

test('design preferences restore safely, update controls and persist browser-only', () => {
  const buttons = ['hyperspace', 'bastilla-shan', 'original-mint', 'astyria-cosmic'].map(theme => ({
    dataset: { designTheme: theme }, attributes: {}, listeners: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    addEventListener(k, cb) { this.listeners[k] = cb; },
    click() { this.listeners.click(); },
  }));
  const focus = {
    attributes: {}, listeners: {}, textContent: '',
    setAttribute(k, v) { this.attributes[k] = v; },
    addEventListener(k, cb) { this.listeners[k] = cb; },
    click() { this.listeners.click(); },
  };
  const feedback = {textContent: ''};
  const documentRef = {
    documentElement: {dataset: {}},
    querySelectorAll(selector) {
      assert.equal(selector, 'button[data-design-theme]');
      return buttons;
    },
    getElementById(id) {
      return id === 'chat-focus-toggle' ? focus : id === 'design-feedback' ? feedback : null;
    },
  };
  const store = new Map([['debian13-design-theme-v1', 'original-mint'], ['debian13-chat-focus-v1', '1']]);
  const storage = {
    getItem: key => store.get(key) ?? null,
    setItem: (key, val) => {store.set(key, val);},
  };
  const api = bindDesignControls(documentRef, storage);
  assert.equal(documentRef.documentElement.dataset.designTheme, 'original-mint');
  assert.equal(buttons[2].attributes['aria-pressed'], 'true');
  assert.equal(documentRef.documentElement.dataset.chatFocus, 'true');
  assert.match(focus.textContent, /Pokaż agentów/);
  buttons[3].click();
  assert.equal(documentRef.documentElement.dataset.designTheme, 'astyria-cosmic');
  assert.equal(store.get('debian13-design-theme-v1'), 'astyria-cosmic');
  assert.match(feedback.textContent, /Astyria Cosmic/);
  focus.click();
  assert.equal(documentRef.documentElement.dataset.chatFocus, 'false');
  assert.equal(store.get('debian13-chat-focus-v1'), '0');
  api.setTheme('invalid');
  assert.equal(documentRef.documentElement.dataset.designTheme, 'hyperspace');
});

test('invalid stored presets never inject arbitrary theme names', () => {
  assert.equal(normalizeDesignTheme('__prototype__'), 'hyperspace');
  assert.equal(normalizeDesignTheme(null), 'hyperspace');
  assert.equal(parseFocusPreference('true'), false);
  assert.equal(parseFocusPreference('1'), true);
});

test('SVL Hyperspace desktop is the default live Trixie navigation and is not a simulated system', () => {
  const html = repo('debian13/ui/index.html');
  const app = repo('debian13/ui/app.mjs');
  const css = repo('debian13/ui/styles.css');
  assert.match(html, /id="hyperspace-view"/);
  assert.match(html, /id="hyperspace-title">Enter the/);
  assert.match(html, /data-view="hyperspace"/);
  for (const target of ['chat','terminal','work','diagnostics','settings']) {
    assert.ok(html.includes('data-view="' + target + '"'), 'No launcher for ' + target);
  }
  assert.match(html, /workery do weryfikacji/);
  assert.doesNotMatch(html, /id="hyperspace-view"[\s\S]*Eksport GitHub[\s\S]*<\/section>/);
  assert.match(app, /hyperspace: 'hyperspace-view'/);
  assert.match(app, /diagnostics: 'diagnostics-view'/);
  assert.match(app, /hyperspace-api-state/);
  assert.match(css, /--design-accent: #67e8f9/);
  assert.match(css, /\.hyperspace-hero-grid/);
  assert.equal(normalizeDesignTheme('unknown'), 'hyperspace');
});


test('main-only Hyperspace sync refreshes real runtime views without claiming an AI Studio import', () => {
  const html = repo('debian13/ui/index.html');
  const app = repo('debian13/ui/app.mjs');
  const workflow = repo('.github/workflows/debian13-validation.yml');
  assert.match(html, /id="sync-system"/);
  assert.match(html, /id="hyperspace-sync-feedback"/);
  assert.match(html, /GitHub main → AI Studio: Pull changes/);
  assert.match(app, /document\.getElementById\('sync-system'\)\?\.addEventListener\('click', synchronizeSystemView\)/);
  assert.match(app, /loadStatus\(\), loadAgents\(\), loadOriginalAgents\(\), loadIntegrations\(\), refreshWork\(\)/);
  assert.doesNotMatch(app, /mmitek515-netizen\/Debian_13\/issues\//);
  assert.doesNotMatch(app, /api\.get\('mcp-info'\)/);
  assert.match(workflow, /push:\s*\n\s*branches: \[main\]/);
});

test('canonical HTML has no inline styles that the UI CSP (style-src self) would block', () => {
  const html = repo('debian13/ui/index.html');
  assert.doesNotMatch(html, /\sstyle\s*=/i);
  assert.doesNotMatch(html, /<style[\s>]/i);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.match(repo('debian13/ui/dev_server.py'), /style-src 'self'/);
});

test('diagnostics view is wired to the real API and formats honest measurements', async () => {
  const { summarizeDiagnostics, LOCAL_HELP_TEXT } = await import('./app.mjs');
  const html = repo('debian13/ui/index.html');
  const app = repo('debian13/ui/app.mjs');
  assert.match(app, /getDiagnostics: \(\) => request\('diagnostics'\)/);
  assert.match(app, /getElementById\('diagnose-alex'\)\?\.addEventListener\('click', prepareAlexDiagnosis\)/);
  for (const id of ['diagnostics-ram-bar', 'diagnostics-disk-bar', 'diagnostics-cpu-bar', 'hyperspace-ram']) {
    assert.ok(html.includes('id="' + id + '"'), 'Missing diagnostics element: ' + id);
  }
  const view = summarizeDiagnostics({
    cpu: { logicalCores: 4, loadAverage1m: 1 },
    memory: { totalMiB: 2048, usedMiB: 512 },
    disk: { totalGiB: 100, usedGiB: 90 },
    taskLedger: { queued: 3, cancelled: 1 },
  });
  assert.equal(view.cpu, '1.00');
  assert.equal(view.cpuPercent, 25);
  assert.match(view.cpuNote, /nie procent CPU/);
  assert.equal(view.ram, '25%');
  assert.equal(view.ramNote, '0.5 / 2.0 GiB zajęte');
  assert.equal(view.disk, '90%');
  assert.equal(view.tasks, '3');
  const missing = summarizeDiagnostics({ memory: { source: 'unavailable' } });
  assert.equal(missing.cpu, '—');
  assert.equal(missing.ram, '—');
  assert.equal(missing.ramPercent, null);
  assert.match(LOCAL_HELP_TEXT, /\/cli <polecenie>/);
  assert.match(app, /\^\\\/help\\s\*\$/);
});

test('GUI reports agent independence facts from the API without inventing work', async () => {
  const { agentIndependenceLabel, describeAgentWorker, taskStatusLabel, diagnosisDescription } = await import('./app.mjs');
  assert.equal(agentIndependenceLabel({ modelConfigured: false }), 'Bez roli · brak modelu');
  assert.equal(agentIndependenceLabel({ modelConfigured: true, dedicatedModel: true, model: 'm-alex', worker: 'running' }),
    'Bez roli · własny model m-alex · pracuje sam');
  assert.equal(agentIndependenceLabel({ modelConfigured: true, dedicatedModel: false, model: 'shared', worker: 'not_running' }),
    'Bez roli · model shared');
  assert.match(describeAgentWorker({ agentWorker: { enabled: false } }), /wyłączony/);
  assert.match(describeAgentWorker({ agentWorker: { enabled: true }, taskLedger: { automaticallyExecutingWorkers: 0 } }),
    /żaden agent nie ma skonfigurowanego modelu/);
  assert.match(describeAgentWorker({ agentWorker: { enabled: true, processed: 2, failed: 1 },
    taskLedger: { automaticallyExecutingWorkers: 4 } }), /pracują samodzielnie \(4 z modelem\).*Wykonane: 2 · błędy modelu: 1/);
  assert.equal(taskStatusLabel('completed'), 'wykonane przez agenta');
  assert.equal(taskStatusLabel('running'), 'agent pracuje');
  const text = diagnosisDescription({ memory: { totalMiB: 1024, usedMiB: 512 }, runtime: { distribution: 'Debian' } });
  assert.match(text, /RAM: 50%/);
  assert.ok(text.length <= 2000);
  assert.doesNotMatch(text, /jesteś|you are|rola|role/i);
});

test('chat keeps every agent conversation separate in the browser', () => {
  const app = repo('debian13/ui/app.mjs');
  const html = repo('debian13/ui/index.html');
  assert.match(app, /function separateAgentConversation\(\)/);
  assert.match(app, /activeSessionAgent = agent\.value/);
  for (const id of ['aria', 'alex', 'astyria', 'aurelius']) {
    assert.ok(html.includes(`data-tile-model="${id}"`), 'Missing model label for ' + id);
  }
  assert.doesNotMatch(html, /Bez specjalizacji/);
});

test('tiles name the real BastillaOS agents only when the API reports a live link', async () => {
  const { originalAgentLinkLabel, agentIndependenceLabel } = await import('./app.mjs');
  assert.equal(agentIndependenceLabel({ connection: 'svl_system' }), 'Prawdziwy agent · SVL_System');
  assert.equal(agentIndependenceLabel({ connection: 'original_bastillaos', worker: 'running' }),
    'Prawdziwy agent · BastillaOS · pracuje sam');
  assert.equal(originalAgentLinkLabel({ connected: true, chatLinked: true }), 'BastillaOS: połączony');
  assert.equal(originalAgentLinkLabel({ connected: true, chatLinked: false }), 'BastillaOS: tylko zadania');
  assert.equal(originalAgentLinkLabel({ workerVerified: true }), 'BastillaOS: agent pracuje');
  assert.equal(originalAgentLinkLabel({}, { registryConfigured: false }), 'BastillaOS: niepodłączony');
  assert.equal(originalAgentLinkLabel({}, { registryConfigured: true, link: { reason: 'source_rejected_token' } }),
    'BastillaOS: zły token');
  assert.equal(originalAgentLinkLabel({}, { registryConfigured: true, link: { reason: 'source_offline' } }),
    'BastillaOS: offline');
});

test('SVL Core reconnect results are explained without claiming a link', async () => {
  const { reconnectSummary } = await import('./app.mjs');
  const all = { Result: 'connected', Agents: { Aria: { connected: true }, Alex: { connected: true } } };
  assert.match(reconnectSummary(all), /Połączono wszystkich agentów \(BastillaOS\): Aria, Alex/);
  const svl = { Result: 'connected', Agents: { Aria: { connected: true, via: ['SVL_System'] } } };
  assert.match(reconnectSummary(svl), /\(SVL_System\): Aria/);
  assert.match(reconnectSummary({ Result: 'svl_no_model' }), /nie ma skonfigurowanego modelu/);
  assert.match(reconnectSummary({ Result: 'not_configured', Missing: ['ALEX_EXISTING_API_URL'] }), /ALEX_EXISTING_API_URL/);
  assert.match(reconnectSummary({ Result: 'source_rejected_token' }), /odrzucił token/);
  assert.match(reconnectSummary({ Result: 'no_agents_advertised' }), /DEBIAN13_ORIGINAL_WORKERS_ENABLED=1/);
  assert.match(repo('debian13/ui/dev_server.py'), /"POST", "\/api\/v1\/agents\/reconnect"/);
});
