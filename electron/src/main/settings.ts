/* MIT License - Copyright (c) fintonlabs.com */

/**
 * Persisted app settings, including the Anthropic API key.
 *
 * The key is encrypted with Electron's safeStorage, which is backed by the
 * macOS Keychain, so it is never written to disk in plain text. Everything else
 * is ordinary JSON in the app's userData directory.
 *
 * The key is optional by design: without one the app drives the local `claude`
 * CLI instead, which is what makes it usable before anyone has pasted anything.
 */

import { app, safeStorage } from 'electron';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';

export type BackendChoice = 'auto' | 'anthropic' | 'claude-cli';

export interface Settings {
  backend: BackendChoice;
  /** Base64 of the safeStorage-encrypted key. Never the key itself. */
  apiKeyCipher?: string;
  model?: string;
  theme?: string;
  /** Electron accelerator for the global show/hide toggle. */
  toggleHotkey?: string;
}

export interface PublicSettings {
  backend: BackendChoice;
  hasApiKey: boolean;
  /** Only ever the last four characters, for confirmation in the UI. */
  apiKeyHint?: string;
  model?: string;
  encryptionAvailable: boolean;
  claudeCliAvailable: boolean;
  toggleHotkey: string;
}

// Alt-Cmd-E by default: reachable from inside Live, and unlikely to collide.
export const DEFAULT_HOTKEY = 'Alt+CommandOrControl+E';

const DEFAULTS: Settings = { backend: 'auto', toggleHotkey: DEFAULT_HOTKEY };

function settingsPath(): string {
  return join(app.getPath('userData'), 'settings.json');
}

let cache: Settings | null = null;

export async function load(): Promise<Settings> {
  if (cache) return cache;
  try {
    const raw = await readFile(settingsPath(), 'utf8');
    cache = { ...DEFAULTS, ...(JSON.parse(raw) as Settings) };
  } catch {
    cache = { ...DEFAULTS };
  }
  return cache;
}

export async function save(patch: Partial<Settings>): Promise<Settings> {
  const current = await load();
  cache = { ...current, ...patch };
  await mkdir(dirname(settingsPath()), { recursive: true });
  await writeFile(settingsPath(), JSON.stringify(cache, null, 2) + '\n', 'utf8');
  return cache;
}

/** Store a key, encrypted. Passing an empty string clears it. */
export async function setApiKey(key: string): Promise<void> {
  const trimmed = key.trim();
  if (!trimmed) {
    await save({ apiKeyCipher: undefined });
    return;
  }
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error(
      'This system has no secure storage available, so the key cannot be saved ' +
        'safely. Set ANTHROPIC_API_KEY in the environment instead.',
    );
  }
  const cipher = safeStorage.encryptString(trimmed).toString('base64');
  await save({ apiKeyCipher: cipher });
}

/** Decrypt the stored key. Returns null when there is none. */
export async function apiKey(): Promise<string | null> {
  const settings = await load();
  if (!settings.apiKeyCipher) return null;
  try {
    return safeStorage.decryptString(Buffer.from(settings.apiKeyCipher, 'base64'));
  } catch {
    // A key encrypted under a different machine or user account is unusable.
    return null;
  }
}

export async function publicView(
  claudeCliAvailable: boolean,
): Promise<PublicSettings> {
  const settings = await load();
  const key = await apiKey();
  return {
    backend: settings.backend,
    hasApiKey: Boolean(key),
    apiKeyHint: key ? `…${key.slice(-4)}` : undefined,
    model: settings.model,
    encryptionAvailable: safeStorage.isEncryptionAvailable(),
    claudeCliAvailable,
    toggleHotkey: settings.toggleHotkey ?? DEFAULT_HOTKEY,
  };
}

/** Environment overrides handed to the Python sidecar. */
export async function sidecarEnv(): Promise<Record<string, string>> {
  const settings = await load();
  const env: Record<string, string> = {
    ABLETON_AI_BACKEND: settings.backend,
  };
  const key = await apiKey();
  if (key) env.ANTHROPIC_API_KEY = key;
  if (settings.model) env.ABLETON_AI_MODEL = settings.model;
  return env;
}
