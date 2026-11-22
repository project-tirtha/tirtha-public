import React from 'react';
import { useTranslation } from 'react-i18next';

import { useAnnouncement } from '../utils/announce';
import '../styles/Snackbar.css';

function Snackbar({ message, variant = 'info', onClose }) {
  const { t } = useTranslation();
  /*
    Before the early return, because a hook cannot run conditionally -- and
    `announce(undefined)` is a no-op, so the empty case costs nothing.

    The `role="status"` below is created together with its text and then
    removed again, which announces nothing: a live region has to exist before
    the change it is meant to report. Every upload outcome in this app arrives
    through here.
  */
  useAnnouncement(message);
  if (!message) return null;

  return (
    <div className={`snackbar snackbar-${variant}`} role="status" aria-live="polite">
      <span>{message}</span>
      {onClose && (
        <button type="button" className="snackbar-close" onClick={onClose} aria-label={t('a11y.dismissNotification')}>
          ×
        </button>
      )}
    </div>
  );
}

export default Snackbar;
