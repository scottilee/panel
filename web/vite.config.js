import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The API has no /api prefix, so the dev proxy lists its routes
    // literally ('/run' also covers /run/{id}/stream and /run/{id}/cancel).
    // In production uvicorn serves the UI and the API from one origin, so
    // these same unprefixed paths work directly and no CORS is needed.
    proxy: {
      '/run': 'http://localhost:8000',
      '/compare': 'http://localhost:8000',
      '/check': 'http://localhost:8000',
      '/benchmark': 'http://localhost:8000',
      '/personas': 'http://localhost:8000',
      '/examples': 'http://localhost:8000',
    },
  },
})
