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
