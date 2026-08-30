/* MIT License - Copyright (c) fintonlabs.com */

/**
 * TCP client for the AbletonAI remote script running inside Live.
 *
 * Newline-delimited JSON, one request and one response per line, so a slow
 * reply can never bleed into the next command's buffer. Requests are matched
 * by id rather than by order, and the socket reconnects on its own -- Ableton
 * restarting mid-session is normal, not exceptional.
 */

import { Socket } from 'node:net';

export const DEFAULT_HOST = '127.0.0.1';
export const DEFAULT_PORT = 9878;

export class AbletonError extends Error {}
export class AbletonNotRunning extends Error {}

interface Pending {
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

export class AbletonBridge {
  private socket: Socket | null = null;
  private buffer = '';
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private connecting: Promise<void> | null = null;

  constructor(
    readonly host: string = DEFAULT_HOST,
    readonly port: number = DEFAULT_PORT,
    private readonly timeoutMs = 30_000,
  ) {}

  get connected(): boolean {
    return this.socket !== null && !this.socket.destroyed;
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    // Collapse concurrent connect attempts onto one in-flight promise.
    if (this.connecting) return this.connecting;

    this.connecting = new Promise<void>((resolve, reject) => {
      const socket = new Socket();
      socket.setNoDelay(true);

      const onError = (error: Error) => {
        socket.destroy();
        this.socket = null;
        reject(
          new AbletonNotRunning(
            `No AbletonAI remote script on ${this.host}:${this.port}. Start ` +
              'Ableton Live and select "AbletonAI" as a Control Surface in ' +
              `Preferences > Link, Tempo & MIDI. (${error.message})`,
          ),
        );
      };

      socket.once('error', onError);
      socket.connect(this.port, this.host, () => {
        socket.off('error', onError);
        socket.on('error', () => this.teardown(new AbletonNotRunning('connection lost')));
        socket.on('close', () => this.teardown(new AbletonNotRunning('Ableton closed the connection')));
        socket.on('data', (chunk) => this.onData(chunk));
        this.socket = socket;
        this.buffer = '';
        resolve();
      });
    }).finally(() => {
      this.connecting = null;
    });

    return this.connecting;
  }

  close(): void {
    this.teardown(new AbletonNotRunning('disconnected'));
  }

  private teardown(error: Error): void {
    for (const [, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(error);
    }
    this.pending.clear();
    if (this.socket) {
      this.socket.removeAllListeners();
      this.socket.destroy();
    }
    this.socket = null;
    this.buffer = '';
  }

  private onData(chunk: Buffer): void {
    this.buffer += chunk.toString('utf8');
    let newline: number;
    while ((newline = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) continue;

      let message: { id?: number; status?: string; message?: string; result?: unknown };
      try {
        message = JSON.parse(line);
      } catch {
        continue;
      }

      const entry = message.id != null ? this.pending.get(message.id) : undefined;
      if (!entry) continue;
      this.pending.delete(message.id!);
      clearTimeout(entry.timer);

      if (message.status === 'error') {
        entry.reject(new AbletonError(message.message ?? 'unknown error'));
      } else {
        entry.resolve((message.result ?? {}) as Record<string, unknown>);
      }
    }
  }

  /** Send one command and resolve with its `result` payload. */
  async call<T = Record<string, unknown>>(
    command: string,
    params: Record<string, unknown> = {},
  ): Promise<T> {
    try {
      return await this.send<T>(command, params);
    } catch (error) {
      // A stale socket survives an Ableton restart; retry once on a clean one.
      if (error instanceof AbletonNotRunning) {
        this.teardown(error);
        return this.send<T>(command, params);
      }
      throw error;
    }
  }

  private async send<T>(command: string, params: Record<string, unknown>): Promise<T> {
    await this.connect();
    const socket = this.socket;
    if (!socket) throw new AbletonNotRunning('not connected');

    const id = this.nextId++;
    const payload = JSON.stringify({ id, type: command, params }) + '\n';

    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new AbletonError(`Ableton did not respond to '${command}' in time`));
      }, this.timeoutMs);

      this.pending.set(id, {
        resolve: resolve as (value: Record<string, unknown>) => void,
        reject,
        timer,
      });
      socket.write(payload, (error) => {
        if (!error) return;
        this.pending.delete(id);
        clearTimeout(timer);
        reject(new AbletonNotRunning(error.message));
      });
    });
  }

  /** Cheap reachability probe that never throws. */
  async isAvailable(): Promise<boolean> {
    try {
      await this.call('ping');
      return true;
    } catch {
      return false;
    }
  }
}
