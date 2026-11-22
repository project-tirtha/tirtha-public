import React from 'react';
import Icon from './Icon';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../hooks/useTheme';
import '../styles/ThemeToggle.css';

function ThemeToggle() {
  const { t } = useTranslation('theme');
  const { theme, toggleTheme } = useTheme();

  return (
    <button
      className="theme-toggle-btn"
      onClick={toggleTheme}
      title={t('theme:toggle')}
      aria-label={t('theme:toggle')}
    >
      {theme === 'light' ? (
        <Icon name="dark_mode" />
      ) : (
        <Icon name="light_mode" />
      )}
    </button>
  );
}

export default ThemeToggle;
