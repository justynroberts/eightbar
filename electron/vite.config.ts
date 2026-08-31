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
  // Bind IPv4 explicitly. Vite's default binds ::1 only, while the main
  // process asks for http://127.0.0.1:7818 -- so `npm run dev` came up with a
  // blank window and ERR_CONNECTION_REFUSED, with a dev server that was
  // running perfectly the whole time.
  server: { host: '127.0.0.1', port: 7818, strictPort: true },
});
