import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Same-origin requests to Django so session and CSRF cookies just work.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
