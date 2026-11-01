import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: the UI calls same-origin `/api/*` and Vite forwards it to the local
// FastAPI process, so the backend needs no CORS. Change here if the API port changes.
const API_TARGET = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: API_TARGET } },
  },
})
