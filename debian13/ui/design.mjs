/* Live Trixie GUI design preferences; never touches provider or agent state. */
export const DESIGN_THEMES = Object.freeze([
  'hyperspace',
  'bastilla-shan',
  'original-mint',
  'astyria-cosmic',
]);
const THEME_KEY = 'debian13-design-theme-v1';
const FOCUS_KEY = 'debian13-chat-focus-v1';

const LABELS = Object.freeze({
  'hyperspace': 'Hyperspace Desktop',
  'bastilla-shan': 'Bastilla Shan',
  'original-mint': 'Debian Original',
  'astyria-cosmic': 'Astyria Cosmic',
});

export function normalizeDesignTheme(value) {
  return DESIGN_THEMES.includes(value) ? value : 'hyperspace';
}

export function parseFocusPreference(value) {
  return value === '1';
}

export function bindDesignControls(doc, storage = null) {
  const root = doc?.documentElement;
  if (!root) return null;
  const buttons = [...doc.querySelectorAll('button[data-design-theme]')];
  const focusButton = doc.getElementById('chat-focus-toggle');
  const feedback = doc.getElementById('design-feedback');

  const read = (key) => {
    try { return storage?.getItem(key) ?? null; }
    catch { return null; }
  };
  const save = (key, value) => {
    try { storage?.setItem(key, value); }
    catch { /* Private browsing or disabled localStorage: GUI still works. */ }
  };

  const setTheme = (requested, persist = true) => {
    const theme = normalizeDesignTheme(requested);
    root.dataset.designTheme = theme;
    for (const button of buttons) {
      button.setAttribute('aria-pressed', String(button.dataset.designTheme === theme));
    }
    if (feedback) feedback.textContent = 'Wybrany motyw: ' + LABELS[theme] +
      '. Zmiana dotyczy wyłącznie tej przeglądarki.';
    if (persist) save(THEME_KEY, theme);
    return theme;
  };

  const setFocus = (enabled, persist = true) => {
    const isFocused = enabled === true;
    root.dataset.chatFocus = String(isFocused);
    if (focusButton) {
      focusButton.setAttribute('aria-pressed', String(isFocused));
      focusButton.textContent = isFocused ? '⤡ Zmniejsz (Pokaż agentów)' : '⤢ Pełny ekran';
      focusButton.setAttribute('aria-label', isFocused
        ? 'Wyłącz pełny ekran i pokaż agentów'
        : 'Włącz czat na cały ekran');
    }
    if (persist) save(FOCUS_KEY, isFocused ? '1' : '0');
    return isFocused;
  };

  setTheme(read(THEME_KEY), false);
  setFocus(parseFocusPreference(read(FOCUS_KEY)), false);

  for (const button of buttons) {
    button.addEventListener('click', () => setTheme(button.dataset.designTheme));
  }
  focusButton?.addEventListener('click', () => {
    setFocus(root.dataset.chatFocus !== 'true');
  });

  return {setTheme, setFocus};
}

if (typeof document !== 'undefined') {
  let storage = null;
  try { storage = window.localStorage; }
  catch { /* Browsers may deny storage access. */ }
  bindDesignControls(document, storage);
}
