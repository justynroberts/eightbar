/* MIT License - Copyright (c) fintonlabs.com */

import { app, BrowserWindow, globalShortcut, ipcMain, nativeImage, screen, shell } from 'electron';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { AgentSidecar } from './agent.js';
import * as settings from './settings.js';
import { AbletonBridge, AbletonError, AbletonNotRunning } from './bridge.js';
import { listThemes, loadThemeByName, ROLE_COLOURS } from './theme.js';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

const here = join(fileURLToPath(import.meta.url), '..');
const devServer = process.env.VITE_DEV_SERVER;

const bridge = new AbletonBridge(
  process.env.ABLETON_HOST ?? '127.0.0.1',
  Number(process.env.ABLETON_PORT ?? 9878),
);

const agent = new AgentSidecar();
// The app's own location is the only reliable anchor: a bundle launched from
// Finder has `/` as its working directory.
agent.appPath = app.getAppPath();
let window: BrowserWindow | null = null;

/** The shortcut that shows the palette again, empty if it could not be taken. */
let shortcutLabel = '';

/** Remembered size and position, so the dock stays where it was put. */
const store = {
  width: 440,
  x: undefined as number | undefined,
  y: undefined as number | undefined,
  alwaysOnTop: true,
};

function createWindow(): void {
  // A tall, narrow palette that sits beside Live rather than covering it:
  // full working height, docked to the right edge of the display.
  const { workArea } = screen.getPrimaryDisplay();
  const width = Math.min(Math.max(store.width, 360), 620);

  // A remembered position can point at a display that is no longer attached.
  // Reject anything that does not land inside a current one, or the window
  // opens somewhere invisible.
  const onScreen =
    store.x !== undefined &&
    store.y !== undefined &&
    screen.getAllDisplays().some(({ workArea: area }) =>
      store.x! >= area.x - 40 &&
      store.x! < area.x + area.width - 80 &&
      store.y! >= area.y - 40 &&
      store.y! < area.y + area.height - 40,
    );

  window = new BrowserWindow({
    width,
    height: workArea.height,
    x: onScreen ? store.x : workArea.x + workArea.width - width,
    y: onScreen ? store.y : workArea.y,
    // This is a palette that stands beside Live, not a window that covers it.
    // Its size is fixed: maximising defeats the point, and dragging an edge
    // only ever makes it overlap the set it is meant to sit next to. On macOS
    // a double-click on the title bar zooms even with the button disabled, so
    // the system-wide preference has to be overridden as well.
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    alwaysOnTop: store.alwaysOnTop,
    // Live's chrome is dark grey, not black; match it behind the page so
    // there is no white flash before the theme loads.
    backgroundColor: '#2a2a2a',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 14, y: 14 },
    webPreferences: {
      preload: join(here, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  if (devServer) {
    // `npm run dev` starts Vite and Electron together, and Electron usually
    // wins: it asks for the URL before Vite is listening, gets
    // ERR_CONNECTION_REFUSED, and shows a blank window for the rest of the
    // session because nothing retries. So retry until it answers.
    const target = window;
    const loadDevServer = (attempt = 0): void => {
      target.loadURL(devServer).catch(() => {
        if (attempt >= 40 || target.isDestroyed()) {
          process.stderr.write(`[ui] dev server never came up at ${devServer}\n`);
          return;
        }
        setTimeout(() => loadDevServer(attempt + 1), 250);
      });
    };
    loadDevServer();
  } else {
    void window.loadFile(join(here, '../renderer/index.html'));
  }

  // Links to fintonlabs.com and the like open in the real browser.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });

  // Float above Live without stealing focus from it.
  window.setAlwaysOnTop(store.alwaysOnTop, 'floating');

  // macOS honours the "double-click title bar to zoom" system setting whatever
  // `maximizable` says, so refuse the zoom itself.
  window.on('maximize', () => window?.unmaximize());
  window.on('enter-full-screen', () => window?.setFullScreen(false));

  // The window is fixed in size but not in place, and the display it sits on
  // can change height -- a second monitor unplugged, a dock hidden. Keep it
  // filling the working height of whichever display it is on.
  window.on('moved', () => {
    if (!window || window.isDestroyed()) return;
    const bounds = window.getBounds();
    const { workArea } = screen.getDisplayNearestPoint({
      x: bounds.x, y: bounds.y,
    });
    if (bounds.height !== workArea.height) {
      place({ ...bounds, y: workArea.y, height: workArea.height });
    }
  });

  // Deliberately NOT setVisibleOnAllWorkspaces: on macOS that flips the app's
  // activation policy to accessory (LSUIElement), which removes the dock icon.
  // The window then has no way back once it goes behind something or is
  // closed, and the app looks like it has vanished while still running.
  if (process.platform === 'darwin') {
    app.setActivationPolicy('regular');
    void app.dock?.show();
  }

  window.show();

  const remember = () => {
    if (!window) return;
    const [w] = window.getSize();
    const [x, y] = window.getPosition();
    store.width = w ?? store.width;
    store.x = x;
    store.y = y;
  };
  window.on('resized', remember);
  window.on('moved', remember);

  window.on('closed', () => {
    window = null;
  });
}

/**
 * Move or size the window despite it being fixed.
 *
 * `resizable: false` makes macOS reject height changes from setBounds, which
 * would silently defeat both the snap and the fill-the-display behaviour. The
 * flag is lifted for the call and put straight back, so the user still cannot
 * drag an edge.
 */
function place(bounds: Electron.Rectangle): void {
  if (!window || window.isDestroyed()) return;
  window.setResizable(true);
  window.setBounds(bounds);
  window.setResizable(false);
}

/** Bring the palette back, wherever it went. */
function reveal(): void {
  if (!window || window.isDestroyed()) {
    createWindow();
    return;
  }
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

app.whenReady().then(() => {
  // In development the stock Electron binary supplies the dock icon -- the
  // icns in the builder config only exists in packaged builds. Set ours at
  // runtime so the dev app wears the mark too.
  if (process.platform === 'darwin' && app.dock) {
    // getAppPath() points at dist/main in dev, so try the source assets
    // (two up from the bundled main) as well as the packaged resources.
    const candidates = [
      join(here, '../../assets/icon.png'),
      join(app.getAppPath(), 'assets/icon.png'),
      join(process.resourcesPath ?? '', 'assets/icon.png'),
    ];
    const found = candidates.find((p) => existsSync(p));
    const mark = found ? nativeImage.createFromPath(found) : nativeImage.createEmpty();
    if (mark.isEmpty()) {
      process.stderr.write(
        `[ui] dock icon not found; looked in ${candidates.join(', ')}\n`,
      );
    } else {
      app.dock.setIcon(mark);
    }
  }

  createWindow();

  // Once hidden, the palette has no button left to press, so getting it back
  // has to work from inside Ableton. Alt-Cmd-E toggles it from anywhere.
  const accelerator = 'Alt+CommandOrControl+E';
  const registered = globalShortcut.register(accelerator, () => {
    if (window && !window.isDestroyed() && window.isVisible()) {
      window.hide();
    } else {
      reveal();
    }
  });
  if (!registered) {
    process.stderr.write(
      `[ui] could not register ${accelerator}; another app has it\n`,
    );
  }
  shortcutLabel = registered ? accelerator : '';

  // Clicking the dock icon must bring the window back, whether it was closed,
  // minimised or merely buried.
  app.on('activate', reveal);
});

app.on('window-all-closed', () => {
  bridge.close();
  if (process.platform !== 'darwin') app.quit();
});

// The sidecar must die with the app on every exit path, not only when the last
// window closes -- on macOS that event fires while the app stays alive.
app.on('will-quit', () => globalShortcut.unregisterAll());

app.on('before-quit', () => {
  bridge.close();
  agent.stop();
});

for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
  process.on(signal, () => {
    agent.stop();
    process.exit(0);
  });
}
process.on('exit', () => agent.stop());

// ---------------------------------------------------------------- IPC

/** Wrap a handler so Ableton failures come back as data, not as thrown IPC. */
function handle<T>(
  channel: string,
  fn: (...args: never[]) => Promise<T>,
): void {
  ipcMain.handle(channel, async (_event, ...args) => {
    try {
      return { ok: true, value: await fn(...(args as never[])) };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const kind =
        error instanceof AbletonNotRunning
          ? 'offline'
          : error instanceof AbletonError
            ? 'ableton'
            : 'error';
      return { ok: false, kind, error: message };
    }
  });
}

handle('ableton:status', async () => {
  const online = await bridge.isAvailable();
  if (!online) return { online: false, port: bridge.port };
  const info = await bridge.call<{ live_version?: string; commands?: string[] }>('ping');
  return {
    online: true,
    port: bridge.port,
    liveVersion: info.live_version ?? '?',
    commands: info.commands?.length ?? 0,
  };
});

handle('ableton:call', async (command: string, params: Record<string, unknown>) =>
  bridge.call(command, params ?? {}),
);

handle('theme:list', async () => listThemes());

handle('theme:load', async (name?: string) => {
  const tokens = await loadThemeByName(name);
  if (!tokens) {
    return { name: 'fallback', isDark: true, vars: {}, roles: ROLE_COLOURS.dark };
  }
  return { ...tokens, roles: ROLE_COLOURS[tokens.isDark ? 'dark' : 'light'] };
});

handle('window:hide', async () => {
  window?.hide();
  return { hidden: true, shortcut: shortcutLabel };
});

handle('window:shortcut', async () => ({ shortcut: shortcutLabel }));

handle('window:pin', async (pinned: boolean) => {
  store.alwaysOnTop = pinned;
  window?.setAlwaysOnTop(pinned, 'floating');
  return { pinned };
});

handle('window:snap', async (edge: 'left' | 'right') => {
  if (!window) return { edge };
  const { workArea } = screen.getPrimaryDisplay();
  const [width] = window.getSize();
  const w = width ?? store.width;
  place({
    x: edge === 'left' ? workArea.x : workArea.x + workArea.width - w,
    y: workArea.y,
    width: w,
    height: workArea.height,
  });
  return { edge };
});

// The agent streams; each step is pushed to the renderer as it happens rather
// than batched at the end, so a long arrangement shows progress.
async function claudeCliAvailable(): Promise<boolean> {
  try {
    await run('which', ['claude']);
    return true;
  } catch {
    return false;
  }
}

handle('settings:get', async () => settings.publicView(await claudeCliAvailable()));

handle('settings:set', async (patch: {
  backend?: settings.BackendChoice;
  apiKey?: string;
  model?: string;
}) => {
  if (patch.backend) await settings.save({ backend: patch.backend });
  if (patch.model !== undefined) await settings.save({ model: patch.model || undefined });
  if (patch.apiKey !== undefined) await settings.setApiKey(patch.apiKey);
  // Restart the core so the new credentials are picked up immediately.
  await agent.reconfigure(await settings.sidecarEnv());
  return settings.publicView(await claudeCliAvailable());
});

ipcMain.handle('agent:chat', async (event, message: string) => {
  const send = (payload: unknown) => {
    if (!event.sender.isDestroyed()) event.sender.send('agent:event', payload);
  };
  try {
    agent.envOverrides = await settings.sidecarEnv();
    await agent.chat(message, send);
    send({ kind: 'end' });
    return { ok: true };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    send({ kind: 'error', message: detail });
    send({ kind: 'end' });
    return { ok: false, error: detail };
  }
});

handle('agent:reset', async () => {
  await agent.reset();
  return { ok: true };
});

handle('agent:status', async () => ({ running: agent.running }));

handle('app:info', async () => ({
  version: app.getVersion(),
  remoteScriptInstalled: existsSync(
    join(app.getPath('home'), 'Music/Ableton/User Library/Remote Scripts/AbletonAI'),
  ),
}));
