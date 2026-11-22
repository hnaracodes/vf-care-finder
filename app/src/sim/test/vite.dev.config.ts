// Dev server for the standalone simulator page (app/sim-dev.html), with /api
// proxied to this engineer's uvicorn port (8824 by default, or VF_API_PORT).
//   cd app && node_modules/.bin/vite -c src/sim/test/vite.dev.config.ts   then open /sim-dev.html
import { defineConfig } from 'vite';

const appDir = decodeURIComponent(new URL('../../../', import.meta.url).pathname);
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const env = ((globalThis as any).process?.env ?? {}) as Record<string, string | undefined>;
const apiPort = Number(env.VF_API_PORT ?? 8824);

export default defineConfig({
  root: appDir,
  publicDir: false,
  server: {
    port: Number(env.VF_DEV_PORT ?? 5824),
    strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true } },
  },
});
