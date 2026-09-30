import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev server talks to the API directly; nginx does this in production.
      '/api': { target: 'http://localhost:8000', changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, '') },
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
})
