import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const target = 'http://127.0.0.1:8000'
const paths = ['/config', '/auth', '/search', '/answer', '/admin', '/healthz']

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(paths.map((p) => [p, target])) },
  preview: { proxy: Object.fromEntries(paths.map((p) => [p, target])) },
})
