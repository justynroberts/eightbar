/* MIT License - Copyright (c) fintonlabs.com */

const $ = (id) => document.getElementById(id);

const transcript = $('transcript');
const setpanel = $('setpanel');
const input = $('input');
const sendBtn = $('btn-send');

let busy = false;
let turns = 0;

/* ------------------------------------------------------------------ theme */

$('btn-theme').addEventListener('click', () => {
  const root = document.documentElement;
  const current = root.dataset.theme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  root.dataset.theme = next;
  try { localStorage.setItem('ableton-ai-theme', next); } catch (e) { /* private mode */ }
});

/* ------------------------------------------------------------------ about */

const about = $('about');
$('btn-about').addEventListener('click', () => about.showModal());
$('btn-about-close').addEventListener('click', () => about.close());
about.addEventListener('click', (e) => { if (e.target === about) about.close(); });

/* -------------------------------------------------------------- transcript */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addTurn(role, text) {
  const wrap = el('div', `turn ${role}`);
  wrap.appendChild(el('div', 'turn-label', role === 'user' ? 'you' : 'ableton_ai'));
  const bubble = el('div', 'bubble', text || '');
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
  scroll();
  return bubble;
}

function scroll() { transcript.scrollTop = transcript.scrollHeight; }

/* The signature gesture: text types on behind a block caret. */
function typeOn(node, text) {
  node.classList.add('typing');
  const speed = Math.max(4, Math.min(18, 900 / Math.max(text.length, 1)));
  let i = 0;
  return new Promise((resolve) => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      node.textContent = text;
      node.classList.remove('typing');
      scroll();
      resolve();
      return;
    }
    const tick = setInterval(() => {
      // Reveal in small chunks so long replies don't crawl.
      i = Math.min(text.length, i + Math.ceil(text.length / 90));
      node.textContent = text.slice(0, i);
      scroll();
      if (i >= text.length) {
        clearInterval(tick);
        node.classList.remove('typing');
        resolve();
      }
    }, speed);
  });
}

function addToolRow(name, args) {
  const row = el('div', 'tool');
  row.appendChild(el('span', 'glyph', '·'));
  const mid = el('span', 'name');
  mid.appendChild(document.createTextNode(name + ' '));
  mid.appendChild(el('span', 'args', shortArgs(args)));
  row.appendChild(mid);
  row.appendChild(el('span', 'detail', 'running'));
  transcript.appendChild(row);
  scroll();
  return row;
}

function shortArgs(args) {
  if (!args) return '';
  const parts = [];
  for (const [k, v] of Object.entries(args)) {
    if (v === null || v === undefined) continue;
    let s = typeof v === 'object' ? `[${Array.isArray(v) ? v.length : '…'}]` : String(v);
    if (s.length > 18) s = s.slice(0, 17) + '…';
    parts.push(`${k}=${s}`);
    if (parts.length >= 4) break;
  }
  return parts.length ? `(${parts.join(' ')})` : '';
}

function finishToolRow(row, ok, data) {
  row.classList.add(ok ? 'ok' : 'err');
  row.querySelector('.glyph').textContent = ok ? '✓' : '✕';
  const detail = row.querySelector('.detail');
  if (!ok) { detail.textContent = data || 'failed'; return; }
  const r = data || {};
  detail.textContent =
    r.summary || r.duration || r.name ||
    (r.placements !== undefined ? `${r.placements} placed` : '') ||
    (r.track_index !== undefined ? `track ${r.track_index}` : '') || 'ok';
}

function addNotice(message) {
  transcript.appendChild(el('div', 'notice', message));
  scroll();
}

/* ------------------------------------------------------------------- chat */

async function send(message) {
  if (busy || !message.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  addTurn('user', message);
  input.value = '';
  autosize();
  turns += 1;
  $('turn-count').textContent = `${turns} turn${turns === 1 ? '' : 's'}`;

  const pending = new Map();
  let bubble = null;

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    if (!response.ok) throw new Error(`server returned ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;

        let event;
        try { event = JSON.parse(line.slice(6)); } catch (e) { continue; }

        if (event.kind === 'text') {
          bubble = addTurn('assistant', '');
          await typeOn(bubble, event.text);
        } else if (event.kind === 'tool_start') {
          pending.set(event.name, addToolRow(event.name, event.input));
        } else if (event.kind === 'tool_end') {
          const row = pending.get(event.name);
          if (row) {
            finishToolRow(row, event.ok, event.ok ? event.result : event.error);
            pending.delete(event.name);
          }
        } else if (event.kind === 'error') {
          addNotice(event.message);
        }
      }
    }
  } catch (err) {
    addNotice(String(err.message || err));
  } finally {
    busy = false;
    sendBtn.disabled = false;
    refresh();
    input.focus();
  }
}

sendBtn.addEventListener('click', () => send(input.value));

input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input.value); }
});
input.addEventListener('input', autosize);

function autosize() {
  input.style.height = 'auto';
  input.style.height = Math.min(160, input.scrollHeight) + 'px';
}

$('examples').addEventListener('click', (e) => {
  if (e.target.tagName === 'BUTTON') send(e.target.textContent);
});

$('btn-reset').addEventListener('click', async () => {
  await fetch('/api/reset', { method: 'POST' });
  transcript.innerHTML = '';
  turns = 0;
  $('turn-count').textContent = '';
});

$('btn-refresh').addEventListener('click', refresh);

/* --------------------------------------------------------------- set panel */

const ROLE_HINTS = [
  ['kick', 'kick'], ['sub', 'sub'], ['bass', 'bass'], ['hat', 'drums'],
  ['drum', 'drums'], ['perc', 'perc'], ['chord', 'chords'], ['arp', 'arp'],
  ['lead', 'lead'], ['hook', 'hook'], ['pad', 'pad'], ['ris', 'riser'],
  ['impact', 'impact'], ['crash', 'impact'], ['voc', 'vocal'], ['fx', 'fx'],
  ['build', 'drums'],
];

function roleOf(name) {
  const lower = (name || '').toLowerCase();
  for (const [needle, role] of ROLE_HINTS) if (lower.includes(needle)) return role;
  return 'fx';
}

function roleColour(role) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(`--role-${role}`).trim() || 'var(--ink-faint)';
}

async function refresh() {
  let status;
  try {
    status = await (await fetch('/api/status')).json();
  } catch (e) {
    setLive(false, 'server unreachable');
    return;
  }

  $('txt-backend').textContent = `backend: ${status.backend || 'none'}`;
  setLive(status.ableton, status.ableton ? 'live: connected' : 'live: offline');

  if (!status.ableton) {
    setpanel.innerHTML = '';
    const box = el('div', 'section');
    box.appendChild(el('p', 'empty',
      `No remote script on port ${status.port || 9878}. Install it, restart Live, ` +
      'then enable AbletonAI under Preferences > Link, Tempo & MIDI > Control Surface.'));
    setpanel.appendChild(box);
    $('chip-tempo').textContent = 'tempo: —';
    $('chip-tracks').textContent = 'tracks: —';
    return;
  }

  const song = status.song || {};
  $('chip-tempo').textContent = `tempo: ${(song.tempo || 0).toFixed(1)}`;
  $('chip-tracks').textContent = `tracks: ${song.track_count ?? 0}`;
  $('set-stamp').textContent = new Date().toLocaleTimeString();

  let arr = {};
  try { arr = await (await fetch('/api/arrangement')).json(); } catch (e) { /* optional */ }

  renderSet(song, arr);
}

function setLive(ok, text) {
  const dot = $('dot-live');
  dot.className = `dot ${ok ? 'on' : 'off'}`;
  $('txt-live').textContent = text;
}

function renderSet(song, arr) {
  setpanel.innerHTML = '';

  // --- transport / key facts
  const head = el('div', 'section');
  head.appendChild(el('h3', null, 'Transport'));
  const meta = el('div', 'meta');
  meta.innerHTML =
    `<span>bpm <b>${(song.tempo || 0).toFixed(1)}</b></span>` +
    `<span>sig <b>${song.signature || '4/4'}</b></span>` +
    `<span>scenes <b>${song.scene_count ?? 0}</b></span>` +
    `<span>state <b>${song.is_playing ? 'playing' : 'stopped'}</b></span>`;
  head.appendChild(meta);
  setpanel.appendChild(head);

  // --- tracks
  const tracks = el('div', 'section');
  tracks.appendChild(el('h3', null, `Tracks (${(song.tracks || []).length})`));
  if (!(song.tracks || []).length) {
    tracks.appendChild(el('p', 'empty', 'No tracks yet.'));
  }
  (song.tracks || []).forEach((t, i) => {
    const row = el('div', 'track');
    row.style.animationDelay = `${Math.min(i * 25, 400)}ms`;
    const swatch = el('div', 'swatch');
    swatch.style.background = roleColour(roleOf(t.name));
    row.appendChild(swatch);
    row.appendChild(el('div', 'idx', String(t.index).padStart(2, '0')));
    row.appendChild(el('div', 'nm', t.name || '(unnamed)'));
    const clips = (t.clips || []).length;
    row.appendChild(el('div', 'clips',
      `${clips} clip${clips === 1 ? '' : 's'}${t.muted ? ' · mute' : ''}`));
    tracks.appendChild(row);
  });
  setpanel.appendChild(tracks);

  // --- arrangement
  const box = el('div', 'section');
  const end = arr.end_bars || 0;
  const mins = Math.floor((arr.duration_seconds || 0) / 60);
  const secs = Math.round((arr.duration_seconds || 0) % 60);
  box.appendChild(el('h3', null,
    end ? `Arrangement — ${Math.round(end)} bars · ${mins}:${String(secs).padStart(2, '0')}`
        : 'Arrangement'));

  if (!end || !(arr.tracks || []).length) {
    box.appendChild(el('p', 'empty', 'Timeline is empty. Ask for an arrangement.'));
    setpanel.appendChild(box);
    return;
  }

  const ruler = el('div', 'ruler');
  for (let i = 0; i < 8; i++) ruler.appendChild(el('span', null, String(Math.round(end * i / 8))));
  box.appendChild(ruler);

  (arr.tracks || []).forEach((t, i) => {
    const row = el('div', 'arr-row');
    row.appendChild(el('div', 'lbl', t.name || `track ${t.index}`));
    const lane = el('div', 'arr-lane');
    const colour = roleColour(roleOf(t.name));
    (t.clips || []).forEach((c, j) => {
      const block = el('div', 'arr-clip');
      block.style.left = `${(c.start_bars / end) * 100}%`;
      block.style.width = `${Math.max(0.4, (c.length_bars / end) * 100)}%`;
      block.style.background = colour;
      block.style.animationDelay = `${Math.min(i * 40 + j * 6, 700)}ms`;
      block.title = `${c.name} — bar ${Math.round(c.start_bars)}, ${Math.round(c.length_bars)} bars`;
      lane.appendChild(block);
    });
    row.appendChild(lane);
    box.appendChild(row);
  });

  setpanel.appendChild(box);
}

refresh();
setInterval(() => { if (!busy) refresh(); }, 8000);
input.focus();
