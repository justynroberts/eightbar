/* MIT License - Copyright (c) fintonlabs.com */

/**
 * Runs the core as a local sidecar and streams its agent events.
 *
 * Two ways to find it, in order:
 *
 *  1. The frozen binary shipped inside the app bundle (Resources/core). This is
 *     what a packaged build uses: it carries its own Python, so the app works
 *     on a machine with no checkout and no virtualenv. Before it existed a
 *     signed DMG could connect to Live and show the set but not answer a single
 *     message anywhere but the developer's own machine.
 *  2. A checkout's virtualenv, which is what `npm run dev` uses so a code
 *     change does not need a 20-second freeze to try.
 *
 * The sidecar is bound to 127.0.0.1 on an ephemeral port, is started only when
 * first needed, and is killed with the app.
 */

import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
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

/** The frozen core inside a packaged app, if this is one. */
function bundledCore(appPath: string): string | null {
  const candidates = [
    // Packaged: Contents/Resources/core/ableton-ai-core, with app.asar under
    // Resources too, so the app path's parent is the right place to look.
    join(process.resourcesPath ?? '', 'core', 'ableton-ai-core'),
    join(appPath, '..', 'core', 'ableton-ai-core'),
    // Built but not packaged, for testing the frozen core in development.
    join(appPath, '..', '..', 'dist', 'core', 'ableton-ai-core'),
  ];
  return candidates.find((p) => p && existsSync(p)) ?? null;
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

/**
 * Kill any core left behind by a previous run.
 *
 * The sidecar is a child process, and a parent that is killed rather than
 * quitted does not take it with it. Those orphans keep their port and keep
 * serving the code they were started with, so a chat can end up talking to a
 * build from hours ago and failing on things that were fixed since. Six of
 * them had accumulated before this was noticed.
 */
function killOrphans(currentPid: number | null): number {
  try {
    const out = execFileSync('pgrep', ['-f', 'ableton_ai.server|ableton-ai-core'], {
      encoding: 'utf8',
    });
    const pids = out
      .split('\n')
      .map((line) => Number(line.trim()))
      .filter((pid) => Number.isInteger(pid) && pid > 0 && pid !== currentPid);
    for (const pid of pids) {
      try {
        process.kill(pid, 'SIGTERM');
      } catch {
        // Already gone, or not ours to kill.
      }
    }
    return pids.length;
  } catch {
    // pgrep exits non-zero when nothing matches, which is the common case.
    return 0;
  }
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
      // The frozen core wins: a packaged app must not depend on a checkout
      // happening to be present on the machine it was copied to.
      const frozen = bundledCore(this.appPath);
      const root = frozen ? null : findProjectRoot(this.appPath);
      const python = frozen ?? pythonExecutable(root);
      const args = frozen ? [] : ['-m', 'ableton_ai.server'];
      const cwd = frozen ? dirname(frozen) : root!;

      if (!python) {
        throw new Error(
          'Core not found. Eightbar looked for the bundled core in ' +
            "the app's Resources, then for a checkout containing both " +
            `pyproject.toml and .venv (searched from ${this.appPath}). ` +
            'In development, run the app from inside the checkout or set ' +
            'ABLETON_AI_ROOT. To create the environment: ' +
            'uv venv && uv pip install -e ".[dev]". ' +
            'To build the bundled core: npm run build:core',
        );
      }

      // Anything left over from a previous run is serving stale code.
      const orphans = killOrphans(this.child?.pid ?? null);
      if (orphans) {
        process.stdout.write(`[core] cleared ${orphans} orphaned sidecar(s)\n`);
      }

      this.port = await freePort();
      const child = spawn(python, args, {
        cwd,
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
    const pid = this.child?.pid ?? null;
    this.child?.kill('SIGTERM');
    this.child = null;
    // Belt and braces: if the child had already re-parented, SIGTERM on the
    // handle does nothing and the process survives the app.
    killOrphans(pid === null ? null : -1);
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
