import { defineConfig } from 'vite';
import { resolve } from 'node:path';

// The renderer is a plain TypeScript single page -- no framework. Its look is
// driven entirely by CSS custom properties written at runtime from Ableton's
// own theme file, so there is nothing here to configure per theme.
export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  build: {
    outDir: resolve(__dirname, 'dist/renderer'),
    emptyOutDir: true,
  },
  server: { port: 7818, strictPort: true },
});
