import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useModalBehaviour } from '../hooks/useModalBehaviour';
import { getApiBaseUrl } from '../utils/apiConfig';
import { csrfHeaders } from '../utils/csrf';
import { deploymentPrefix } from '../utils/deployment';
import {
  extractCoordsFromGoogleMapsUrl,
  formatGeocodedAddress,
  reverseGeocode,
} from '../utils/geocoding';
import { useAnnouncement } from '../utils/announce';
import '../styles/RequestSiteModal.css';

/**
 * Ask for a heritage site to be added (internal design notes).
 *
 * This form used to post `FormData` to `/api/site-requests/submit/` with field
 * names of its own -- `site_name`, `terms_accepted`, `privacy_accepted`, plus
 * an `email` and an `image` file. The endpoint is `/api/v1/site-requests/`, it
 * reads `json.loads(request.body)`, and its field names are `name`,
 * `accepted_terms`, `accepted_privacy`. So the request 404'd; had the path been
 * right it would have 400'd on the multipart body; had the body been right the
 * consent check would have rejected it. Every submission anyone ever made from
 * this form was lost, and the form said "submitted successfully".
 *
 * It now speaks the endpoint's own contract, and collects the rest of what that
 * contract accepts -- district, affiliation, source, purview, restricted access
 * -- which are the questions the Google Form this replaces actually asked.
 *
 * The image picker is gone. The endpoint has never accepted a file, there is no
 * field on `SiteSubmissionRequest` to hold one, and adding anonymous file
 * upload is a new abuse surface rather than a fix. `source_url` carries the
 * evidence a reviewer needs, and photographs belong in the contribute flow,
 * where they are checked.
 */
const PURVIEWS = ['asi', 'state', 'other', 'unknown'];

const EMPTY = {
  requesterName: '',
  requesterEmail: '',
  siteName: '',
  description: '',
  district: '',
  stateName: '',
  country: '',
  mapsUrl: '',
  affiliation: '',
  sourceUrl: '',
  purview: 'unknown',
  restrictedAccess: '',
  agreeTerms: false,
  agreePrivacy: false,
};

function RequestSiteModal({ isOpen, onClose, mapCoordinates = null }) {
  const { t } = useTranslation();
  const panelRef = useRef(null);
  /*
    `initialEmail` was a prop nobody passed, so the field always started blank
    anyway. Prefilling it from the signed-in session is worth doing and is
    recorded in internal design notes; a prop that silently defaults to '' is not
    that feature, it only looks like it.
  */
  const [form, setForm] = useState({ ...EMPTY });
  const [errorMessage, setErrorMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [localCoordinates, setLocalCoordinates] = useState(mapCoordinates);
  const [resolvedAddress, setResolvedAddress] = useState('');

  /*
    Both messages appear as newly-created `role` elements, which announces
    nothing -- a live region has to exist before the change it reports. The
    outcome of submitting is the one thing a screen-reader user has no other
    way to learn here.
  */
  useAnnouncement(successMessage || errorMessage);


  useModalBehaviour(isOpen, onClose, panelRef);

  /*
    The pin the map handed us, kept in sync without an effect: React reruns this
    function before painting, so the new coordinate is used on the very first
    render after the prop changes rather than one frame later.
  */
  const [lastPin, setLastPin] = useState(mapCoordinates);
  if (mapCoordinates && mapCoordinates !== lastPin) {
    setLastPin(mapCoordinates);
    setLocalCoordinates(mapCoordinates);
  }

  // Reverse-geocode the pin, and fill in what it can tell us.
  useEffect(() => {
    if (!localCoordinates) return undefined;

    let active = true;
    (async () => {
      const data = await reverseGeocode(localCoordinates.lat, localCoordinates.lng);
      if (!active) return;

      if (!data) {
        setResolvedAddress(
          `${localCoordinates.lat.toFixed(6)}, ${localCoordinates.lng.toFixed(6)}`,
        );
        return;
      }

      setResolvedAddress(formatGeocodedAddress(data, localCoordinates));
      const addr = data.address || {};
      // Only ever fills a blank. Overwriting what someone typed because a
      // geocoder disagrees is how a form loses local knowledge -- which is the
      // one thing this form exists to collect.
      setForm((prev) => ({
        ...prev,
        stateName: prev.stateName || addr.state || '',
        country: prev.country || addr.country || '',
        district: prev.district || addr.state_district || addr.county || '',
      }));
    })();

    return () => {
      active = false;
    };
  }, [localCoordinates]);

  /*
    Forget everything when the dialog closes.

    Nothing was reset, and this component is never unmounted -- it returns null
    while closed and keeps its state. So opening it, dropping a pin, filling in
    the form and closing without submitting left all of it in place: reopening
    showed the previous pin, the previous resolved address, and any success
    message from the last request. Someone requesting a second site who did not
    re-drag the pin would submit their new name and description against the old
    coordinate, and the form would look, to them, like it had simply remembered
    where they were.

    Adjusted during render rather than in an effect, the same way `lastPin`
    above is: an effect would paint the stale form for one frame first, and
    resetting state in an effect body is the cascading-render pattern React
    warns about.
  */
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen);
    if (!isOpen) {
      setForm({ ...EMPTY });
      setErrorMessage('');
      setSuccessMessage('');
      setLocalCoordinates(null);
      setLastPin(null);
      setResolvedAddress('');
    }
  }

  if (!isOpen) return null;

  const handleChange = (event) => {
    const { name, value, type, checked } = event.target;
    setForm((prev) => ({ ...prev, [name]: type === 'checkbox' ? checked : value }));

    // A pasted Google Maps link is a location too. Done here rather than in an
    // effect on `form.mapsUrl`: it is a reaction to a person typing, not a
    // synchronisation with anything outside React.
    if (name === 'mapsUrl') {
      const coords = extractCoordsFromGoogleMapsUrl(value);
      if (coords) setLocalCoordinates(coords);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setErrorMessage('');
    setSuccessMessage('');

    if (!form.siteName.trim()) {
      setErrorMessage(t('request.errorName'));
      return;
    }
    if (!form.agreeTerms || !form.agreePrivacy) {
      setErrorMessage(t('request.errorConsent'));
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`${getApiBaseUrl()}/site-requests/`, {
        method: 'POST',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        credentials: 'same-origin',
        body: JSON.stringify({
          name: form.siteName.trim(),
          description: form.description,
          country: form.country || 'India',
          state: form.stateName,
          district: form.district,
          latitude: localCoordinates?.lat ?? null,
          longitude: localCoordinates?.lng ?? null,
          affiliation: form.affiliation,
          source_url: form.sourceUrl || form.mapsUrl,
          purview: form.purview,
          /*
            Three states, not two. `restricted_access` is nullable on purpose:
            sending someone to photograph a restricted site is a real risk, so
            "not answered" must not be recorded as "no". An empty select stays
            null rather than becoming `false`.
          */
          restricted_access:
            form.restrictedAccess === '' ? null : form.restrictedAccess === 'yes',
          accepted_terms: form.agreeTerms,
          accepted_privacy: form.agreePrivacy,
          requester_name: form.requesterName,
          requester_email: form.requesterEmail,
        }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        // The endpoint answers with `{"error": "..."}`; the old code read
        // `data.message`, so every server-side reason -- including the 429 that
        // says to slow down -- was replaced by a generic failure.
        throw new Error(data.error || t('request.errorGeneric'));
      }

      setSuccessMessage(t('request.success'));
      setForm({ ...EMPTY });
    } catch (err) {
      setErrorMessage(err.message || t('request.errorGeneric'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const titleId = 'request-site-title';

  return (
    /*
      `role="presentation"` on the scrim says it carries no semantics, which is
      what makes a click handler without a key handler acceptable here: there is
      nothing for a keyboard to reach, and Escape (handled by
      `useModalBehaviour`) is the keyboard equivalent of clicking away.
    */
    <div
      role="presentation"
      className="request-modal-scrim"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        className="request-modal-card"
      >
        <button
          type="button"
          className="request-modal-close"
          onClick={onClose}
          aria-label={t('a11y.closeDialog')}
        >
          <span aria-hidden="true">✕</span>
        </button>
        <h2 id={titleId} className="request-modal-title">
          {t('request.title')}
        </h2>
        <p className="request-modal-hint">
          {localCoordinates
            ? t('request.pinned', { place: resolvedAddress })
            : t('request.noPin')}
        </p>

        {/*
          Every control has a real `<label>`. They were placeholders before --
          which vanish the moment someone types, are not announced as names by
          several screen readers, and fail the contrast minimum in most browser
          defaults (WCAG 1.3.1, 3.3.2, 4.1.2).
        */}
        <form className="request-modal-form" onSubmit={handleSubmit}>
          <fieldset>
            <legend>{t('request.aboutSite')}</legend>

            <label htmlFor="rs-site-name">
              {t('request.siteName')} <span aria-hidden="true">*</span>
            </label>
            <input
              id="rs-site-name"
              name="siteName"
              value={form.siteName}
              onChange={handleChange}
              required
              autoComplete="off"
            />

            <label htmlFor="rs-description">{t('request.description')}</label>
            <textarea
              id="rs-description"
              name="description"
              value={form.description}
              onChange={handleChange}
              rows={4}
            />

            <div className="request-modal-row">
              <div>
                <label htmlFor="rs-district">{t('request.district')}</label>
                <input
                  id="rs-district"
                  name="district"
                  value={form.district}
                  onChange={handleChange}
                  autoComplete="off"
                />
              </div>
              <div>
                <label htmlFor="rs-state">{t('request.state')}</label>
                <input
                  id="rs-state"
                  name="stateName"
                  value={form.stateName}
                  onChange={handleChange}
                  autoComplete="off"
                />
              </div>
              <div>
                <label htmlFor="rs-country">{t('request.country')}</label>
                <input
                  id="rs-country"
                  name="country"
                  value={form.country}
                  onChange={handleChange}
                  autoComplete="off"
                />
              </div>
            </div>

            <label htmlFor="rs-maps-url">{t('request.mapsUrl')}</label>
            <input
              id="rs-maps-url"
              name="mapsUrl"
              type="url"
              value={form.mapsUrl}
              onChange={handleChange}
              aria-describedby="rs-maps-url-help"
            />
            <p id="rs-maps-url-help" className="request-modal-help">
              {t('request.mapsUrlHelp')}
            </p>
          </fieldset>

          <fieldset>
            <legend>{t('request.custody')}</legend>

            <label htmlFor="rs-purview">{t('request.purview')}</label>
            <select
              id="rs-purview"
              name="purview"
              value={form.purview}
              onChange={handleChange}
            >
              {PURVIEWS.map((value) => (
                <option key={value} value={value}>
                  {t(`request.purviewOptions.${value}`)}
                </option>
              ))}
            </select>

            <label htmlFor="rs-restricted">{t('request.restricted')}</label>
            <select
              id="rs-restricted"
              name="restrictedAccess"
              value={form.restrictedAccess}
              onChange={handleChange}
              aria-describedby="rs-restricted-help"
            >
              <option value="">{t('request.restrictedUnknown')}</option>
              <option value="yes">{t('request.restrictedYes')}</option>
              <option value="no">{t('request.restrictedNo')}</option>
            </select>
            <p id="rs-restricted-help" className="request-modal-help">
              {t('request.restrictedHelp')}
            </p>

            <label htmlFor="rs-source">{t('request.sourceUrl')}</label>
            <input
              id="rs-source"
              name="sourceUrl"
              type="url"
              value={form.sourceUrl}
              onChange={handleChange}
            />
          </fieldset>

          <fieldset>
            <legend>{t('request.aboutYou')}</legend>
            <p className="request-modal-help">{t('request.aboutYouHelp')}</p>

            <label htmlFor="rs-name">{t('request.yourName')}</label>
            <input
              id="rs-name"
              name="requesterName"
              value={form.requesterName}
              onChange={handleChange}
              autoComplete="name"
            />

            <label htmlFor="rs-email">{t('request.yourEmail')}</label>
            <input
              id="rs-email"
              name="requesterEmail"
              type="email"
              value={form.requesterEmail}
              onChange={handleChange}
              autoComplete="email"
            />

            <label htmlFor="rs-affiliation">{t('request.affiliation')}</label>
            <input
              id="rs-affiliation"
              name="affiliation"
              value={form.affiliation}
              onChange={handleChange}
              autoComplete="organization"
            />
          </fieldset>

          <fieldset>
            <legend>
              {t('request.consent')} <span aria-hidden="true">*</span>
            </legend>
            <div className="request-modal-check">
              <input
                id="rs-terms"
                type="checkbox"
                name="agreeTerms"
                checked={form.agreeTerms}
                onChange={handleChange}
              />
              {/*
                Built from the deployment prefix.

                They were absolute to smlab.niser.ac.in/project/tirtha, so the
                demo and beta both sent people to production's copy of a
                document that may differ from their own. `../#terms` was no
                better: it resolves against the current SPA path, so it is right
                from `/app/` and wrong from `/app/site/<id>/<slug>/`, and from
                `/or/app/` it lands on `/project/tirtha/or/#terms`, which is not
                a route at all.

                They point at the canonical documents now (`shell.document`),
                which is the text someone is actually consenting to.
              */}
              <label htmlFor="rs-terms">
                {t('request.agreeTerms')}{' '}
                <a href={`${deploymentPrefix()}/terms/`} target="_blank" rel="noopener noreferrer">
                  {t('request.terms')}
                </a>
              </label>
            </div>
            <div className="request-modal-check">
              <input
                id="rs-privacy"
                type="checkbox"
                name="agreePrivacy"
                checked={form.agreePrivacy}
                onChange={handleChange}
              />
              <label htmlFor="rs-privacy">
                {t('request.agreePrivacy')}{' '}
                <a
                  href={`${deploymentPrefix()}/privacy/`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('request.privacy')}
                </a>
              </label>
            </div>
          </fieldset>

          {/*
            `role="alert"` and `role="status"`: without them a screen-reader user
            submits, hears nothing, and has no way to know whether it worked.
          */}
          {errorMessage && (
            <p className="request-modal-error" role="alert">
              {errorMessage}
            </p>
          )}
          {successMessage && (
            <p className="request-modal-success" role="status">
              {successMessage}
            </p>
          )}

          <div className="request-modal-actions">
            <button type="button" onClick={onClose} disabled={isSubmitting}>
              {t('request.cancel')}
            </button>
            <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
              {isSubmitting ? t('request.submitting') : t('request.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default RequestSiteModal;
