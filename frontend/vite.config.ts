import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * The browser never talks to Rime or LiveKit's key-bearing endpoints directly: credentials
 * stay server-side (spec §21). Everything the UI needs arrives through these two proxies.
 */
const AGENT = process.env.AGENT_ORIGIN ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: false,
    allowedHosts: true,
    proxy: {
      '/api': { target: AGENT, changeOrigin: true },
      '/lab': { target: AGENT, changeOrigin: true, ws: false },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
