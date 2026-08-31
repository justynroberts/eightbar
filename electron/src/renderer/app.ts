/* MIT License - Copyright (c) fintonlabs.com */

/**
 * Renderer. Talks only to the preload bridge; it has no node access.
 *
 * The whole palette is written from Ableton's own theme file at boot, so the
 * window matches whichever skin Live is wearing.
 */

interface Result<T> {
  ok: boolean;
  value?: T;
  error?: string;
  kind?: string;
}

interface ThemePayload {
  name: string;
  isDark: boolean;
  vars: Record<string, string>;
  roles: Record<string, string>;
}

interface ThemeEntry { name: string; path: string }

interface AbletonApi {
  status(): Promise<Result<{ online: boolean; port: number; liveVersion?: string; commands?: number }>>;
  call<T>(command: string, params?: Record<string, unknown>): Promise<Result<T>>;
  listThemes(): Promise<Result<ThemeEntry[]>>;
  loadTheme(name?: string): Promise<Result<ThemePayload>>;
  info(): Promise<Result<{ version: string; remoteScriptInstalled: boolean }>>;
  pin(pinned: boolean): Promise<Result<{ pinned: boolean }>>;
  snap(edge: 'left' | 'right'): Promise<Result<{ edge: string }>>;
  hide(): Promise<Result<{ hidden: boolean; shortcut: string }>>;
  shortcut(): Promise<Result<{ shortcut: string }>>;
  chat(message: string): Promise<{ ok: boolean; error?: string }>;
  resetChat(): Promise<Result<{ ok: boolean }>>;
  onAgentEvent(handler: (event: Record<string, unknown>) => void): () => void;
  getSettings(): Promise<Result<PublicSettings>>;
  setSettings(patch: Record<string, unknown>): Promise<Result<PublicSettings>>;
}

interface PublicSettings {
  backend: 'auto' | 'anthropic' | 'claude-cli';
  hasApiKey: boolean;
  apiKeyHint?: string;
  encryptionAvailable: boolean;
  claudeCliAvailable: boolean;
}

declare global {
  interface Window { ableton: AbletonApi }
}

export {};

const api = window.ableton;
function $<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`renderer: no element with id "${id}"`);
  return node as T;
}

/** For elements that legitimately may not be present. */
const maybe = (id: string): HTMLElement | null => document.getElementById(id);

const transcript = $('transcript');
const setpanel = $('setpanel');
const arrangepanel = $('arrangepanel');
const input = $<HTMLTextAreaElement>('input');
const sendBtn = $<HTMLButtonElement>('send');

const THEME_KEY = 'ableton-ai-skin';
let busy = false;
let turns = 0;

// ------------------------------------------------------------------ theme

async function applyTheme(name?: string): Promise<void> {
  const result = await api.loadTheme(name);
  const payload: ThemePayload | undefined = result.value;
  if (!result.ok || !payload) return;
  const { vars, roles, isDark, name: applied } = payload;

  const root = document.documentElement;
  for (const [key, value] of Object.entries(vars)) root.style.setProperty(key, value);
  for (const [role, colour] of Object.entries(roles)) {
    root.style.setProperty(`--role-${role}`, colour);
  }
  // Only drives `color-scheme`; every colour comes from Live's own file.
  root.dataset.theme = isDark ? 'dark' : 'light';

  const note = document.getElementById('skin-note');
  if (note) note.textContent = `Following Ableton's "${applied}" skin.`;
  try { localStorage.setItem(THEME_KEY, applied); } catch { /* private mode */ }
}

async function populateThemes(): Promise<void> {
  const list = await api.listThemes();
  const themes: ThemeEntry[] = list.value ?? [];
  if (!list.ok || themes.length === 0) return;

  let saved: string | null = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch { /* ignore */ }

  const select = $<HTMLSelectElement>('theme-select');
  select.innerHTML = '';
  for (const theme of themes) {
    const option = document.createElement('option');
    option.value = theme.name;
    option.textContent = theme.name;
    select.appendChild(option);
  }
  if (saved && themes.some((t) => t.name === saved)) select.value = saved;
  else if (themes.some((t) => t.name === 'Default Dark Neutral Medium')) {
    select.value = 'Default Dark Neutral Medium';
  }
  await applyTheme(select.value);
  select.addEventListener('change', () => void applyTheme(select.value));
}

// ------------------------------------------------------------- transcript

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addTurn(who: 'you' | 'ai', text: string): HTMLElement {
  maybe('placeholder')?.remove();
  const wrap = el('div', `turn ${who}`);
  wrap.appendChild(el('div', 'turn-label', who === 'you' ? 'you' : 'ableton ai'));
  const bubble = el('div', 'bubble', text);
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
  transcript.scrollTop = transcript.scrollHeight;
  return bubble;
}

function shortArgs(args: unknown): string {
  if (!args || typeof args !== 'object') return '';
  const parts: string[] = [];
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (value === null || value === undefined) continue;
    let text = typeof value === 'object'
      ? `[${Array.isArray(value) ? value.length : '…'}]`
      : String(value);
    if (text.length > 14) text = `${text.slice(0, 13)}…`;
    parts.push(`${key}=${text}`);
    if (parts.length >= 3) break;
  }
  return parts.length ? `(${parts.join(' ')})` : '';
}

function addToolRow(name: string, args: unknown): HTMLElement {
  maybe('placeholder')?.remove();
  const row = el('div', 'tool-row');
  row.appendChild(el('span', 'mark', '·'));
  const middle = el('span', 'name');
  middle.appendChild(document.createTextNode(`${name} `));
  middle.appendChild(el('span', 'args', shortArgs(args)));
  row.appendChild(middle);
  row.appendChild(el('span', 'detail', 'running'));
  transcript.appendChild(row);
  transcript.scrollTop = transcript.scrollHeight;
  return row;
}

function finishToolRow(row: HTMLElement, ok: boolean, data: unknown): void {
  row.classList.add(ok ? 'ok' : 'bad');
  const mark = row.querySelector('.mark');
  if (mark) mark.textContent = ok ? '✓' : '✕';
  const detail = row.querySelector('.detail');
  if (!detail) return;
  if (!ok) { detail.textContent = String(data ?? 'failed'); return; }
  const r = (data ?? {}) as Record<string, unknown>;
  // Show whichever field actually says something useful about the result.
  const candidates = [
    r.summary,
    r.duration,
    r.placements !== undefined ? `${r.placements} placed` : undefined,
    r.name,
    r.preset,
    r.loaded,
    r.track_index !== undefined ? `track ${r.track_index}` : undefined,
  ];
  const first = candidates.find((v) => typeof v === 'string' && v.length > 0);
  detail.textContent = (first as string) ?? 'ok';
}

function addNotice(message: string): void {
  maybe('placeholder')?.remove();
  transcript.appendChild(el('div', 'notice', message));
  transcript.scrollTop = transcript.scrollHeight;
}

// ---------------------------------------------------------------- set view

const ROLE_HINTS: [string, string][] = [
  ['kick', 'kick'], ['sub', 'sub'], ['bass', 'bass'], ['hat', 'drums'],
  ['drum', 'drums'], ['perc', 'perc'], ['chord', 'chords'], ['arp', 'arp'],
  ['lead', 'lead'], ['hook', 'hook'], ['pad', 'pad'], ['ris', 'riser'],
  ['impact', 'impact'], ['crash', 'impact'], ['voc', 'vocal'], ['fx', 'fx'],
  ['build', 'drums'],
];

function roleOf(name: string): string {
  const lower = (name || '').toLowerCase();
  for (const [needle, role] of ROLE_HINTS) if (lower.includes(needle)) return role;
  return 'fx';
}

const roleColour = (role: string) => `var(--role-${role})`;

interface Track {
  index: number; name: string; clips: { slot: number; name: string }[];
  muted?: boolean; devices?: string[];
}
interface Song { tempo: number; signature: string; is_playing: boolean;
  scene_count: number; track_count: number; tracks: Track[] }
interface Arrangement { end_bars: number; duration_seconds: number;
  tracks: { index: number; name: string;
    clips: { name: string; start_bars: number; length_bars: number }[] }[] }

function renderSet(song: Song): void {
  setpanel.innerHTML = '';

  const transport = el('div', 'block');
  transport.appendChild(el('h3', undefined, 'Transport'));
  const facts = el('div', 'facts');
  facts.innerHTML =
    `<span>bpm <b>${(song.tempo ?? 0).toFixed(1)}</b></span>` +
    `<span>sig <b>${song.signature ?? '4/4'}</b></span>` +
    `<span>scenes <b>${song.scene_count ?? 0}</b></span>` +
    `<span>${song.is_playing ? 'playing' : 'stopped'}</span>`;
  transport.appendChild(facts);
  setpanel.appendChild(transport);

  // Track list, drawn with Live's own track-header rhythm.
  const tracks = el('div', 'block');
  tracks.appendChild(el('h3', undefined, `Tracks (${song.tracks.length})`));
  if (!song.tracks.length) tracks.appendChild(el('p', 'empty', 'No tracks yet.'));

  let silent = 0;
  for (const track of song.tracks) {
    const row = el('div', 'track');
    const strip = el('div', 'strip');
    strip.style.background = roleColour(roleOf(track.name));
    row.appendChild(strip);
    row.appendChild(el('div', 'idx', String(track.index).padStart(2, '0')));
    row.appendChild(el('div', 'nm', track.name || '(unnamed)'));

    const device = track.devices?.[0];
    // A MIDI track with no instrument makes no sound -- flag it, do not hide it.
    if (!device && track.clips.length) silent += 1;
    const meta = device ?? (track.clips.length ? 'no instrument' : '');
    const cell = el('div', 'meta', meta);
    if (!device && track.clips.length) cell.style.color = 'var(--record)';
    row.appendChild(cell);
    tracks.appendChild(row);
  }
  setpanel.appendChild(tracks);

  if (silent) {
    const warn = el('p', 'hint',
      `${silent} track${silent === 1 ? '' : 's'} with clips have no instrument ` +
      'and will be silent. Ask: "give every track an instrument".');
    warn.style.color = 'var(--record)';
    setpanel.appendChild(warn);
  }
}

function renderArrangement(
  arrangement: Arrangement | null,
  markers: { name: string; start_bar: number }[],
): void {
  arrangepanel.innerHTML = '';
  const bars = arrangement?.end_bars ?? 0;

  if (!bars || !arrangement?.tracks.length) {
    arrangepanel.appendChild(
      el('p', 'empty', 'Timeline is empty. Ask for an arrangement.'));
    return;
  }

  const seconds = arrangement.duration_seconds ?? 0;
  const mm = Math.floor(seconds / 60);
  const ss = String(Math.round(seconds % 60)).padStart(2, '0');

  const block = el('div', 'block');
  block.appendChild(el('h3', undefined, `${Math.round(bars)} bars · ${mm}:${ss}`));

  const ruler = el('div', 'ruler');
  for (let i = 0; i < 6; i++) {
    ruler.appendChild(el('span', undefined, String(Math.round((bars * i) / 6))));
  }
  block.appendChild(ruler);

  arrangement.tracks.forEach((track, i) => {
    const row = el('div', 'lane-row');
    row.appendChild(el('div', 'label', track.name));
    const lane = el('div', 'lane');
    const colour = roleColour(roleOf(track.name));
    track.clips.forEach((clip, j) => {
      const node = el('div', 'clip');
      node.style.left = `${(clip.start_bars / bars) * 100}%`;
      node.style.width = `${Math.max(0.3, (clip.length_bars / bars) * 100)}%`;
      node.style.background = colour;
      node.style.animationDelay = `${Math.min(i * 30 + j * 4, 500)}ms`;
      node.title = `${clip.name} — bar ${Math.round(clip.start_bars)}, ` +
        `${Math.round(clip.length_bars)} bars`;
      lane.appendChild(node);
    });
    row.appendChild(lane);
    block.appendChild(row);
  });

  if (markers.length) {
    const strip = el('div', 'markers');
    for (const marker of markers) {
      const chip = el('span', 'marker');
      chip.innerHTML = `${marker.name} <b>${Math.round(marker.start_bar)}</b>`;
      strip.appendChild(chip);
    }
    block.appendChild(strip);
  }

  arrangepanel.appendChild(block);
}

// The Sounds tab lists the instrument each musical role will be given.
// These defaults live in the Python core (sounds.py) and are mirrored here;
// once the agent is ported they will be read from it rather than restated.
const ROLE_DEFAULTS: [string, string][] = [
  ['kick', 'Drum Rack'], ['drums', 'Drum Rack'], ['perc', 'Drum Rack'],
  ['bass', 'Operator'], ['sub', 'Operator'], ['chords', 'Wavetable'],
  ['arp', 'Wavetable'], ['lead', 'Wavetable'], ['hook', 'Wavetable'],
  ['pad', 'Wavetable'], ['riser', 'Wavetable'], ['impact', 'Drum Rack'],
];

function renderPrefs(): void {
  const host = $('prefs');
  host.innerHTML = '';
  for (const [role, device] of ROLE_DEFAULTS) {
    const row = el('div', 'pref');
    row.appendChild(el('span', 'role', role));
    row.appendChild(el('span', 'device', device));
    host.appendChild(row);
  }
}

// ---------------------------------------------------------------- settings

function describeSettings(s: PublicSettings): string {
  if (s.hasApiKey) return `Key saved (${s.apiKeyHint}).`;
  if (s.claudeCliAvailable) return 'No key — using the local claude CLI.';
  return 'No key and no claude CLI found; the agent cannot run.';
}

async function loadSettings(): Promise<void> {
  const result = await api.getSettings();
  if (!result.ok || !result.value) return;
  const settings = result.value;
  $<HTMLSelectElement>('backend-select').value = settings.backend;
  $('key-state').textContent = describeSettings(settings);
  if (!settings.encryptionAvailable) {
    $('key-state').textContent =
      'No secure storage on this system — set ANTHROPIC_API_KEY instead.';
  }
}

async function applySettings(patch: Record<string, unknown>): Promise<void> {
  const state = $('key-state');
  state.textContent = 'Saving…';
  const result = await api.setSettings(patch);
  if (!result.ok || !result.value) {
    state.textContent = result.error ?? 'could not save';
    return;
  }
  state.textContent = describeSettings(result.value);
  $<HTMLInputElement>('api-key').value = '';
}

$<HTMLSelectElement>('backend-select').addEventListener('change', (event) => {
  void applySettings({ backend: (event.target as HTMLSelectElement).value });
});
$('save-key').addEventListener('click', () => {
  void applySettings({ apiKey: $<HTMLInputElement>('api-key').value });
});
$('clear-key').addEventListener('click', () => {
  void applySettings({ apiKey: '' });
});

// -------------------------------------------------------------- refreshing

async function refresh(): Promise<void> {
  try {
    await refreshInner();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    for (const panel of [setpanel, arrangepanel]) {
      panel.innerHTML = '';
      panel.appendChild(el('p', 'empty', `Could not refresh: ${message}`));
    }
  }
}

async function refreshInner(): Promise<void> {
  const status = await api.status();
  const led = $('led');
  const online = status.ok && status.value?.online === true;

  led.className = `led ${online ? 'on' : 'off'}`;
  const liveState = $('live-state');
  liveState.textContent = online
    ? `Live ${status.value?.liveVersion ?? ''}`.trim()
    : 'offline';
  liveState.title = `Last read ${new Date().toLocaleTimeString()}`;

  if (!online) {
    setpanel.innerHTML = '';
    setpanel.appendChild(el('p', 'empty',
      `No remote script on port ${status.value?.port ?? 9878}. ` +
      'Install it, restart Live, then enable AbletonAI under ' +
      'Preferences > Link, Tempo & MIDI > Control Surface.'));
    $('tempo').textContent = '— bpm';
    $('tracks').textContent = '— tracks';
    return;
  }

  const [songResult, arrResult, locResult] = await Promise.all([
    api.call<Song>('get_song'),
    api.call<Arrangement>('get_arrangement'),
    api.call<{ locators: { name: string; start_bar: number }[] }>('get_locators'),
  ]);
  if (!songResult.ok || !songResult.value) {
    const message = songResult.error ?? 'could not read the set';
    setpanel.innerHTML = '';
    setpanel.appendChild(el('p', 'empty', message));
    arrangepanel.innerHTML = '';
    arrangepanel.appendChild(el('p', 'empty', message));
    return;
  }

  const song = songResult.value;
  $('tempo').textContent = `${song.tempo.toFixed(1)} bpm`;
  $('tracks').textContent = `${song.track_count} tracks`;

  renderSet(song);
  renderArrangement(
    arrResult.ok ? (arrResult.value ?? null) : null,
    locResult.ok ? (locResult.value?.locators ?? []) : [],
  );
}

// ------------------------------------------------------------------- chat

async function send(text: string): Promise<void> {
  if (busy || !text.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  addTurn('you', text);
  input.value = '';
  autosize();
  turns += 1;

  const pending = new Map<string, HTMLElement>();

  // Each agent step arrives as its own event, so progress is visible while a
  // long arrangement is being built rather than only at the end.
  const unsubscribe = api.onAgentEvent((event) => {
    const kind = String(event.kind ?? '');
    if (kind === 'text') {
      addTurn('ai', String(event.text ?? ''));
    } else if (kind === 'tool_start') {
      const name = String(event.name ?? 'tool');
      pending.set(name, addToolRow(name, event.input));
    } else if (kind === 'tool_end') {
      const name = String(event.name ?? 'tool');
      const row = pending.get(name);
      if (row) {
        finishToolRow(row, event.ok === true, event.ok ? event.result : event.error);
        pending.delete(name);
      }
    } else if (kind === 'error') {
      addNotice(String(event.message ?? 'the agent failed'));
    }
  });

  try {
    const result = await api.chat(text);
    if (!result.ok && result.error) addNotice(result.error);
  } catch (error) {
    addNotice(error instanceof Error ? error.message : String(error));
  } finally {
    unsubscribe();
    busy = false;
    sendBtn.disabled = false;
    await refresh();
    input.focus();
  }
}

function autosize(): void {
  input.style.height = 'auto';
  input.style.height = `${Math.min(150, input.scrollHeight)}px`;
}

// ------------------------------------------------------------------ wiring

sendBtn.addEventListener('click', () => void send(input.value));
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    void send(input.value);
  }
});
input.addEventListener('input', autosize);

$('suggestions').addEventListener('click', (event) => {
  const target = event.target as HTMLElement;
  if (target.tagName === 'BUTTON') void send(target.textContent ?? '');
});

$('refresh').addEventListener('click', () => void refresh());

// --- tabs
const tabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
function showTab(name: string): void {
  for (const tab of tabs) {
    const active = tab.dataset.tab === name;
    tab.setAttribute('aria-selected', String(active));
    const panel = document.getElementById(`tab-${tab.dataset.tab}`);
    if (panel) panel.hidden = !active;
  }
  try { localStorage.setItem('ableton-ai-tab', name); } catch { /* ignore */ }
}
for (const tab of tabs) {
  tab.addEventListener('click', () => showTab(tab.dataset.tab ?? 'chat'));
}

// --- a small message that fades, for state changes with no visible result
let toastTimer: number | undefined;
function toast(message: string, ms = 2600): void {
  const box = $('toast');
  $('toast-text').textContent = message;
  box.dataset.show = 'true';
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    box.dataset.show = 'false';
  }, ms);
}

// --- float above Live, and snap to either edge
let pinned = true;
const pinBtn = $<HTMLButtonElement>('pin');
const pinLabel = $('pin-label');
pinBtn.addEventListener('click', () => {
  pinned = !pinned;
  pinBtn.setAttribute('aria-pressed', String(pinned));
  // The word changes with the state. A pressed-looking glyph is ambiguous
  // about whether it describes what is happening or what clicking would do.
  pinLabel.textContent = pinned ? 'Float' : 'Behind';
  pinBtn.title = pinned
    ? 'Floating above Ableton -- click to let it go behind'
    : 'Behind Ableton -- click to float above it';
  void api.pin(pinned);
});

// --- hide the palette, and say how to get it back
let showAgain = '\u2325\u2318E';
void api.shortcut().then((result) => {
  if (result.ok && result.value?.shortcut) {
    showAgain = result.value.shortcut
      .replace('Alt', '\u2325')
      .replace('CommandOrControl', '\u2318')
      .replace(/\+/g, '');
    $('about-shortcut').textContent = showAgain;
    $('hide').title = `Hide the palette (${showAgain} to bring it back)`;
  }
});

$('hide').addEventListener('click', () => {
  // Say it before hiding: once the window is gone there is nowhere to read it.
  toast(`Hidden. Press ${showAgain} to bring it back.`);
  window.setTimeout(() => void api.hide(), 700);
});

let edge: 'left' | 'right' = 'right';
$('snap').addEventListener('click', () => {
  edge = edge === 'right' ? 'left' : 'right';
  void api.snap(edge);
});

const about = $<HTMLDialogElement>('about');
$('about-open').addEventListener('click', () => about.showModal());
$('about-close').addEventListener('click', () => about.close());
about.addEventListener('click', (event) => {
  if (event.target === about) about.close();
});

void (async () => {
  let startTab = 'chat';
  try { startTab = localStorage.getItem('ableton-ai-tab') ?? 'chat'; } catch { /* ignore */ }
  showTab(startTab);
  await populateThemes();
  renderPrefs();
  await loadSettings();
  const info = await api.info();
  if (info.ok && info.value) $('version').textContent = `v${info.value.version}`;
  await refresh();
  setInterval(() => { if (!busy) void refresh(); }, 8000);
  input.focus();
})();
