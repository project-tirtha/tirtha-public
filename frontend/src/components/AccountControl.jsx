import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import Icon from './Icon';
import { fetchSession, signOut, startSignIn } from '../utils/session';
import '../styles/AccountControl.css';

/**
 * Signing in, from the base view.
 *
 * Sign-in lived inside the contribute dialog and nowhere else, so the only way
 * to sign in was to start a contribution -- and the only way to find out you
 * were already signed in, or that your account is still awaiting approval, was
 * the same. Both are things a visitor should be able to see without committing
 * to an upload.
 *
 * It fetches the session itself rather than taking it as a prop. The alternative
 * was lifting session state through `Home` into a context for two consumers, on
 * a session endpoint that is a cheap cached read; a context can come later if a
 * third consumer appears, and would be a change to the shape of the app rather
 * than an addition to it.
 */
function AccountControl({ className = '' }) {
  const { t } = useTranslation();
  const [session, setSession] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchSession(controller.signal)
      .then(setSession)
      // A session that cannot be read is not an error worth showing anybody:
      // the control simply offers sign-in, which is what it would offer anyway.
      .catch(() => {});
    return () => controller.abort();
  }, []);

  const name = session?.contributor?.name || session?.contributor?.email;

  if (!session?.authenticated) {
    return (
      <button
        type="button"
        className={`account-control account-control--signin ${className}`}
        onClick={startSignIn}
        title={t('contribute.signIn')}
      >
        <Icon name="person" aria-hidden="true" />
        <span>{t('contribute.signIn')}</span>
      </button>
    );
  }

  return (
    <div className={`account-control account-control--in ${className}`}>
      {/* The name is the useful part; the address is what identifies the
          account, so it is the title rather than the label. */}
      <span className="account-name" title={session.contributor?.email}>
        {name}
      </span>
      <button
        type="button"
        className="account-signout"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          signOut()
            .then(() => window.location.reload())
            .catch(() => setBusy(false));
        }}
        aria-label={t('contribute.signOut')}
        title={t('contribute.signOut')}
      >
        <Icon name="logout" aria-hidden="true" />
      </button>
    </div>
  );
}

export default AccountControl;
