import { defineConfig } from 'vite';

// The build goes straight into the Databricks App's static folder
// (tasks/WP8_databricks_app.md, D033), served by dbapp/server.py. Relative
// base so the app works at the root of the Databricks App URL or under any
// prefix. publicDir is off: app/public/data is the retired India map data
// (D024) and must not be shipped. Dev requests to /api go to a local
// uvicorn on port 8000 (or VF_API_PORT), which must be running. The only fixture path is
// the dev-only ?fixtures=1 switch in src/api.ts; production builds carry none.
export default defineConfig({
  base: './',
  publicDir: false,
  build: {
    outDir: '../dbapp/static',
    emptyOutDir: true,
    target: 'es2022',
    chunkSizeWarningLimit: 2000,
    assetsInlineLimit: 0,
  },
  server: {
    port: Number(process.env.VF_DEV_PORT ?? 5173),
    strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${process.env.VF_API_PORT ?? '8000'}`, changeOrigin: true } },
  },
  preview: { port: 4173, strictPort: true },
});
