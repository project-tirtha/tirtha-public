import { createContext } from 'react';

/**
 * Split out of `ThemeContext.jsx` so that file exports only components.
 *
 * Vite's fast refresh cannot preserve state across an edit to a module that
 * exports both a component and something else -- it has to fall back to a full
 * reload, which in this app means losing the map camera, the open panel and any
 * half-filled form on every save.
 */
export const ThemeContext = createContext(null);
