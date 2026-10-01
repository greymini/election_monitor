import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The proxy used to run unconditionally, including under VITE_FIXTURES=1 where
// there is no API behind it, so every /api request in fixture mode became a
// connection-refused error in the console that read as an application fault.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const fixtures = env.VITE_FIXTURES === '1' || env.VITE_FIXTURES === 'true'
  const target = env.VITE_API_URL || 'http://localhost:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      // Fixture mode serves every response from web/src/fixtures and must not
      // reach the network at all, so there is no proxy to misread. Without
      // fixtures, /api goes to the dev stack: scripts/dev_stack.py serves the
      // API on 8000, and nginx does this rewrite in production.
      proxy: fixtures
        ? undefined
        : {
            '/api': {
              target,
              changeOrigin: true,
              // The API is mounted at the root (GET /acs, not GET /api/acs),
              // so the prefix the frontend uses is stripped here.
              rewrite: (p) => p.replace(/^\/api/, ''),
            },
          },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      rollupOptions: {
        output: {
          // Block in-charges open this on a phone over a rural connection, so the
          // map and charting libraries must not be in the first payload. Routes
          // are lazy-loaded in App.tsx; this keeps the vendors separable.
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            charts: ['recharts'],
            map: ['leaflet', 'react-leaflet'],
            i18n: ['i18next', 'react-i18next'],
          },
        },
      },
    },
  }
})
