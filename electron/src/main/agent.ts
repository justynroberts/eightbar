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
import { dirname, join } from 'node:path';

export interface AgentEvent {
  kind: 'text' | 'tool_start' | 'tool_end' | 'error' | 'done' | 'end';
  [key: string]: unknown;
}

/**
 * Find the Python core.
 *
 * Deriving this from `process.cwd()` is wrong: a bundle launched from Finder
 * inherits `/` as its working directory, so the search looked for `/.venv` and
 * the app only ever worked when started from a terminal inside the checkout.
 *
 * Walk up from the app itself instead, looking for a directory that holds both
 * a `pyproject.toml` and a virtualenv. That finds it in development and when
 * the built app sits inside the checkout, and fails honestly everywhere else.
 */
function findProjectRoot(appPath: string): string | null {
  const candidates: string[] = [];

  const configured = process.env.ABLETON_AI_ROOT;
  if (configured) candidates.push(configured);

  // Walk up from the app bundle, then from the working directory.
  for (const start of [appPath, process.cwd()]) {
    let dir = start;
    for (let depth = 0; depth < 8; depth += 1) {
      candidates.push(dir);
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }

  for (const dir of candidates) {
    if (
      existsSync(join(dir, 'pyproject.toml')) &&
      (existsSync(join(dir, '.venv/bin/python')) ||
        existsSync(join(dir, '.venv/bin/python3')))
    ) {
      return dir;
    }
  }
  return null;
}

function pythonExecutable(root: string | null): string | null {
  const explicit = process.env.ABLETON_AI_PYTHON;
  if (explicit && existsSync(explicit)) return explicit;
  if (!root) return null;
  return (
    [join(root, '.venv/bin/python'), join(root, '.venv/bin/python3')].find((p) =>
      existsSync(p),
    ) ?? null
  );
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
  /** Where the app itself lives; the search for the core starts here. */
  appPath: string = process.cwd();

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
      const root = findProjectRoot(this.appPath);
      const python = pythonExecutable(root);
      if (!python || !root) {
        throw new Error(
          'Python core not found. Eightbar looked for a directory containing ' +
            'both pyproject.toml and .venv, starting from the app and its ' +
            `working directory (searched from ${this.appPath}). Either run the ` +
            'app from inside the checkout, or set ABLETON_AI_ROOT to the ' +
            'project directory. To create the environment: ' +
            'uv venv && uv pip install -e ".[dev]"',
        );
      }

      this.port = await freePort();
      const child = spawn(python, ['-m', 'ableton_ai.server'], {
        cwd: root,
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
