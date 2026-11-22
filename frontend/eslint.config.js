import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // Vendored, generated Emscripten output (Basis Universal); not linted.
  globalIgnores(['dist', 'public/basis/basis_transcoder.js']),
  {
    // The KTX2 transcoder bootstrap runs as a Web Worker.
    files: ['public/basis/ktx2-bootstrap.js'],
    languageOptions: { globals: globals.worker },
  },
  {
    // Tests run under vitest with `globals: true`, so `describe`, `it`,
    // `expect` and `vi` are defined without being imported, and `global` is
    // the Node one.
    files: ['**/*.test.{js,jsx}', 'src/test/**'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, ...globals.vitest },
    },
  },
  {
    // Build and tooling config runs in Node, not the browser: `process`,
    // `__dirname` and friends are defined there and nowhere else.
    files: ['*.config.js', 'vite.config.js', 'eslint.config.js'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
])
