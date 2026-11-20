import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vue source always calls relative /api/... paths. In dev, Vite proxies them
// to FastAPI. In production the built dist/ is served by FastAPI itself
// (one port), so no proxy and no CORS issue.
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
