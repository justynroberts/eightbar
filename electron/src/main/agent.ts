/* MIT License - Copyright (c) fintonlabs.com */

/**
 * Runs the Python core as a local sidecar and streams its agent events.
 *
 * This is a development bridge, not the destination. The music theory,
 * generators, variations, arrangement planning and the agent loop all live in
 * the Python package today; porting them to TypeScript is in progress. Until
 * that lands, spawning the core we already have beats shipping a chat box that
 * does nothing.
 *
 * The sidecar is bound to 127.0.0.1 on an ephemeral port, is started only when
 * first needed, and is killed with the app.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { join, resolve } from 'node:path';

export interface AgentEvent {
  kind: 'text' | 'tool_start' | 'tool_end' | 'error' | 'done' | 'end';
  [key: string]: unknown;
}

/** Where the Python core lives, relative to this Electron project. */
const PROJECT_ROOT = resolve(process.cwd(), '..');

function pythonExecutable(): string | null {
  const candidates = [
    process.env.ABLETON_AI_PYTHON,
    join(PROJECT_ROOT, '.venv/bin/python'),
    join(PROJECT_ROOT, '.venv/bin/python3'),
  ].filter(Boolean) as string[];
  return candidates.find((p) => existsSync(p)) ?? null;
}

async function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolvePort(port));
    });
  });
}

export class AgentSidecar {
  private child: ChildProcess | null = null;
  private port = 0;
  private starting: Promise<void> | null = null;

  get running(): boolean {
    return this.child !== null && this.child.exitCode === null;
  }

  get baseUrl(): string {
    return `http://127.0.0.1:${this.port}`;
  }

  /** Extra environment for the sidecar, supplied by the settings store. */
  envOverrides: Record<string, string> = {};

  async start(): Promise<void> {
    if (this.running) return;
    if (this.starting) return this.starting;

    this.starting = (async () => {
      const python = pythonExecutable();
      if (!python) {
        throw new Error(
          'Python core not found. Expected a virtualenv at ' +
            `${join(PROJECT_ROOT, '.venv')} — run: uv venv && uv pip install -e ".[dev]"`,
        );
      }

      this.port = await freePort();
      const child = spawn(python, ['-m', 'ableton_ai.server'], {
        cwd: PROJECT_ROOT,
        env: {
          ...process.env,
          ABLETON_AI_UI_PORT: String(this.port),
          ABLETON_PORT: process.env.ABLETON_PORT ?? '9878',
          PYTHONUNBUFFERED: '1',
          ...this.envOverrides,
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      child.stdout?.on('data', (d) => process.stdout.write(`[core] ${d}`));
      child.stderr?.on('data', (d) => process.stderr.write(`[core] ${d}`));
      child.on('exit', (code) => {
        if (code !== 0 && code !== null) {
          process.stderr.write(`[core] exited with ${code}\n`);
        }
        this.child = null;
      });

      this.child = child;
      await this.waitUntilReady();
    })().finally(() => {
      this.starting = null;
    });

    return this.starting;
  }

  /** Poll the sidecar's status endpoint until it answers or we give up. */
  private async waitUntilReady(timeoutMs = 25_000): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    let lastError = 'no response';
    while (Date.now() < deadline) {
      if (!this.running) throw new Error(`Python core exited: ${lastError}`);
      try {
        const response = await fetch(`${this.baseUrl}/api/status`);
        if (response.ok) return;
        lastError = `HTTP ${response.status}`;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    throw new Error(`Python core did not start in time (${lastError})`);
  }

  stop(): void {
    this.child?.kill('SIGTERM');
    this.child = null;
  }

  /** Apply new environment and restart, so a key change takes effect at once. */
  async reconfigure(env: Record<string, string>): Promise<void> {
    this.envOverrides = env;
    if (this.running) {
      this.stop();
      await new Promise((r) => setTimeout(r, 300));
    }
  }

  async reset(): Promise<void> {
    if (!this.running) return;
    await fetch(`${this.baseUrl}/api/reset`, { method: 'POST' });
  }

  /**
   * Send one message and invoke `onEvent` for each agent step.
   * The core streams server-sent events, one JSON object per step.
   */
  async chat(message: string, onEvent: (event: AgentEvent) => void): Promise<void> {
    await this.start();

    const response = await fetch(`${this.baseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    if (!response.ok || !response.body) {
      throw new Error(`core returned HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let split: number;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const line = frame.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as AgentEvent);
        } catch {
          // A partial or malformed frame is not worth killing the stream over.
        }
      }
    }
  }
}
