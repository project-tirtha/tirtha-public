import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const securityHeaders = {
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}

// Baked in at build time. Defaults to production's prefix; `refresh-demo.sh`
// derives it from PRE_URL. A bundle built against the wrong base looks
// completely normal until a lazily-imported route is reached in a browser.
const assetBase = process.env.TIRTHA_STATIC_BASE || '/project/tirtha/static/';

export default defineConfig({
  base: assetBase,
  plugins: [
    react(),
    tailwindcss(),
  ],
  server: {
    headers: securityHeaders,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    headers: securityHeaders,
  },
  // MapLibre 6's worker is an ES module and MapLibre always starts it with
  // `{ type: 'module' }`; keep Vite's worker bundle in the same format.
  worker: {
    format: 'es',
  },
  build: {
    // Django serves the shell and reads this manifest for the hashed filenames
    // (internal design notes), so one build serves production, beta and the demo
    // without the prefix being compiled in.
    manifest: true,
    target: 'es2022',
    rollupOptions: {
      output: {
        /*
          **React first, and matched on a path rather than a substring.**

          `id.includes('three')` is a substring test against a full module path,
          and it swept React's own modules into the `three` group -- any path
          containing those five letters, which `node_modules/react-dom/...` does
          not but several transitive deps do. The entry needs React, so the
          entry statically imported the `three` chunk, so **969 kB of three.js
          shipped on first paint** and the `lazy(() => import('ModelViewer3D'))`
          in `Home.jsx` was decorative. Measured: eager payload fell from
          2,722,959 to 1,756,969 raw (736,795 to 482,639 gzipped, -35%) when
          React was given its own chunk.

          Anchored on `/node_modules/<name>/` so a package cannot be captured by
          a group it merely shares letters with.
        */
        manualChunks(id) {
          const inPackage = (name) => id.includes(`/node_modules/${name}/`);

          if (inPackage('react') || inPackage('react-dom') || inPackage('scheduler')) {
            return 'react';
          }
          if (inPackage('three') || id.includes('/node_modules/@react-three/')) {
            return 'three';
          }
          if (inPackage('maplibre-gl')) {
            return 'maplibre';
          }
          if (inPackage('i18next') || inPackage('react-i18next')) {
            return 'i18n';
          }
        },
      },
    },
  },
})
