import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

/**
 * Separate from vite.config.js so the build's asset base and Node-only bits do
 * not leak into the test environment (internal design notes).
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
  },
});
