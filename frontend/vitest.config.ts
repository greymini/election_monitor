import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Unit and component tests (jsdom). Browser tests live in e2e/ and run under
// Playwright; they are excluded here.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    // Live-API mode: the tests mock fetch, so the real request() path runs.
    env: { VITE_FIXTURES: '0', VITE_API_BASE: '/api' },
  },
})
