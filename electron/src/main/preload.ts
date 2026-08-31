/* MIT License - Copyright (c) fintonlabs.com */

/**
 * The renderer's only door to the main process.
 *
 * Everything crosses as plain data through a fixed set of channels -- no
 * remote module, no node integration -- so a rogue string in a clip name can
 * never reach the filesystem or the socket.
 */

import { contextBridge, ipcRenderer } from 'electron';

export interface Result<T> {
  ok: boolean;
  value?: T;
  error?: string;
  kind?: 'offline' | 'ableton' | 'error';
}

export interface AbletonStatus {
  online: boolean;
  port: number;
  liveVersion?: string;
  commands?: number;
}

export interface ThemePayload {
  name: string;
  isDark: boolean;
  vars: Record<string, string>;
  roles: Record<string, string>;
}

const api = {
  status: (): Promise<Result<AbletonStatus>> => ipcRenderer.invoke('ableton:status'),

  call: <T = Record<string, unknown>>(
    command: string,
    params: Record<string, unknown> = {},
  ): Promise<Result<T>> => ipcRenderer.invoke('ableton:call', command, params),

  listThemes: (): Promise<Result<{ name: string; path: string }[]>> =>
    ipcRenderer.invoke('theme:list'),

  loadTheme: (name?: string): Promise<Result<ThemePayload>> =>
    ipcRenderer.invoke('theme:load', name),

  info: (): Promise<Result<{ version: string; remoteScriptInstalled: boolean }>> =>
    ipcRenderer.invoke('app:info'),

  pin: (pinned: boolean): Promise<Result<{ pinned: boolean }>> =>
    ipcRenderer.invoke('window:pin', pinned),

  snap: (edge: 'left' | 'right'): Promise<Result<{ edge: string }>> =>
    ipcRenderer.invoke('window:snap', edge),

  hide: (): Promise<Result<{ hidden: boolean; shortcut: string }>> =>
    ipcRenderer.invoke('window:hide'),

  shortcut: (): Promise<Result<{ shortcut: string }>> =>
    ipcRenderer.invoke('window:shortcut'),

  getSettings: (): Promise<Result<Record<string, unknown>>> =>
    ipcRenderer.invoke('settings:get'),

  setSettings: (patch: Record<string, unknown>): Promise<Result<Record<string, unknown>>> =>
    ipcRenderer.invoke('settings:set', patch),

  chat: (message: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke('agent:chat', message),

  resetChat: (): Promise<Result<{ ok: boolean }>> => ipcRenderer.invoke('agent:reset'),

  /** Subscribe to streamed agent steps. Returns an unsubscribe function. */
  onAgentEvent: (handler: (event: Record<string, unknown>) => void): (() => void) => {
    const listener = (_e: unknown, payload: Record<string, unknown>) => handler(payload);
    ipcRenderer.on('agent:event', listener);
    return () => { ipcRenderer.off('agent:event', listener); };
  },
};

contextBridge.exposeInMainWorld('ableton', api);

export type AbletonApi = typeof api;
