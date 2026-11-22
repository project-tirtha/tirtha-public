import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { ThemeContext } from './theme';
import { readStored, writeStored } from '../utils/storage';

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    /*
      The stored choice first, then the system's. Defaulting straight to light
      ignored `prefers-color-scheme`, so someone whose whole desktop is dark got
      a white page every time -- and this app is mostly map, which is the worst
      thing to have flash white.

      Storage goes through `utils/storage`, which cannot throw.
    */
    const saved = readStored('theme');
    if (saved === 'light' || saved === 'dark') return saved;
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    writeStored('theme', theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((prevTheme) => (prevTheme === 'light' ? 'dark' : 'light'));
  }, []);

  const isDark = theme === 'dark';
  const isLight = theme === 'light';

  const value = useMemo(() => ({
    theme,
    toggleTheme,
    isDark,
    isLight,
  }), [isDark, isLight, theme, toggleTheme]);

  return (
    <ThemeContext.Provider value={value}>
      {children}
    </ThemeContext.Provider>
  );
}
