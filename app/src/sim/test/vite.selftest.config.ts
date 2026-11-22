// Node build of the simulator self-test (no new dependencies: Vite only).
//   cd app && node_modules/.bin/vite build -c src/sim/test/vite.selftest.config.ts && node node_modules/.sim-selftest/selftest.mjs
import { defineConfig } from 'vite';

const appDir = decodeURIComponent(new URL('../../../', import.meta.url).pathname);

export default defineConfig({
  root: appDir,
  publicDir: false,
  logLevel: 'warn',
  build: {
    ssr: 'src/sim/selftest.ts',
    outDir: 'node_modules/.sim-selftest',
    emptyOutDir: true,
    target: 'node20',
    minify: false,
    rollupOptions: { output: { entryFileNames: 'selftest.mjs' } },
  },
});
