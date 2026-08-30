// Bundles the Electron main and preload processes with esbuild.
// Vite owns the renderer; this owns everything that runs in Node.
import { build, context } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { spawn } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const watch = process.argv.includes('--watch');
const dev = process.argv.includes('--dev');

const shared = {
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'esm',
  sourcemap: dev,
  minify: !dev,
  // Electron is provided by the runtime and cannot be bundled.
  external: ['electron'],
  logLevel: 'warning',
};

const targets = [
  {
    entryPoints: [resolve(root, 'src/main/index.ts')],
    outfile: resolve(root, 'dist/main/index.js'),
  },
  {
    entryPoints: [resolve(root, 'src/main/preload.ts')],
    outfile: resolve(root, 'dist/main/preload.cjs'),
    format: 'cjs',
  },
];

if (!watch) {
  await Promise.all(targets.map((t) => build({ ...shared, ...t })));
  console.log('main + preload built');
  process.exit(0);
}

const contexts = await Promise.all(targets.map((t) => context({ ...shared, ...t })));
await Promise.all(contexts.map((c) => c.watch()));
console.log('watching main + preload');

// Give the first build a moment to land before Electron loads it.
await new Promise((r) => setTimeout(r, 500));
const electronBin = (await import('electron')).default;
const child = spawn(electronBin, [resolve(root, 'dist/main/index.js')], {
  stdio: 'inherit',
  env: { ...process.env, VITE_DEV_SERVER: process.env.VITE_DEV_SERVER ?? '' },
});
child.on('close', () => process.exit(0));
