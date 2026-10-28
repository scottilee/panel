import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Section 3's HTTP API table has no /api prefix (POST /run, not
    // POST /api/run) -- api/main.py's routes match that exactly, so the
    // dev proxy has to target those literal paths rather than a generic
    // /api namespace that nothing on the backend actually answers to. In
    // production both are served from the same uvicorn origin, so the
    // frontend's fetch calls use these same unprefixed paths directly.
    proxy: {
      '/run': 'http://localhost:8000',
      '/compare': 'http://localhost:8000',
      '/personas': 'http://localhost:8000',
      '/examples': 'http://localhost:8000',
    },
  },
})
