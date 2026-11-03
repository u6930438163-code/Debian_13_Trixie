import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

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
