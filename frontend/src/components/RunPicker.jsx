import React from 'react';
import { useTranslation } from 'react-i18next';

import '../styles/RunPicker.css';

/**
 * Choose which reconstruction of a site the viewer shows.
 *
 * A site accumulates runs over time -- 21 sites have more than one, up to six,
 * and 16 have both a photogrammetry mesh and a Gaussian splat of the same
 * place. The viewer always opened the newest, so every earlier version was
 * published and unreachable. This is the control that reaches them.
 *
 * It is a native `<select>`, deliberately. Six options at most is exactly the
 * case the element was made for: keyboard, touch and screen-reader behaviour
 * come from the browser, so there is no focus code to get wrong, and inside
 * the viewer dialog's focus trap a native popup is the one widget that cannot
 * escape it -- a hand-rolled listbox in a portal would.
 *
 * `runs` arrives already filtered to what the viewer can draw and already
 * sorted newest-first; that decision is `firstViewableRun`'s and is not
 * repeated here. This component knows nothing of the service layer.
 */

/**
 * `ended_at` is the one field every published run has that means "when this
 * version appeared", and it is the only thing an option says about a run.
 *
 * Not `status`: 15 GS runs in production carry `status="Manual"` with
 * ~0-second durations and sometimes no stage rows at all, because they were
 * placed by hand rather than by the pipeline. "Manual" and "0 min" are facts
 * about how the file got there, not about the model, and mean nothing to a
 * visitor. Not the id either -- a UUID is not a name.
 */
function optionLabel(run, formatter, latestSuffix) {
  const date = formatter.format(new Date(run.ended_at));
  return latestSuffix ? `${date} ${latestSuffix}` : date;
}

/**
 * One group per kind, ordered by the group's newest member, so the group that
 * holds the latest model comes first -- and since `runs` is already
 * newest-first, the first run of each kind to appear *is* its newest, and the
 * order in which kinds are first met is the order the groups should take.
 */
function groupByKind(runs) {
  const groups = new Map();
  for (const run of runs) {
    if (!groups.has(run.kind)) groups.set(run.kind, []);
    groups.get(run.kind).push(run);
  }
  return [...groups.entries()];
}

function RunPicker({ runs = [], selectedRunId, onSelect, notice = null, id = 'run-picker' }) {
  const { t, i18n } = useTranslation();

  /*
    38 of the 59 visible sites have exactly one run. For them a picker with one
    choice would be chrome for its own sake, so there is nothing -- unless a
    shared link asked for a version that could not be shown, in which case the
    notice still has to explain why the visitor is looking at a different one.
  */
  if (runs.length < 2 && !notice) return null;

  const formatter = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' });
  const latestId = runs[0]?.id;
  const noticeText =
    notice === 'missing' ? t('site.runNotFound') : notice === 'unviewable' ? t('site.runUnsupported') : null;

  return (
    <div className="run-picker">
      {runs.length >= 2 && (
        <>
          {/* The visible word belongs to the header, which has no room for it;
              the association still has to exist for the control to have a
              caption in the accessibility tree, hence hidden rather than
              absent. */}
          <label htmlFor={id} className="sr-only">
            {t('site.runs')}
          </label>
          <select
            id={id}
            className="run-picker-select"
            value={selectedRunId ?? latestId}
            onChange={(event) => onSelect?.(event.target.value)}
            aria-label={t('a11y.runPicker')}
          >
            {groupByKind(runs).map(([kind, members]) => (
              <optgroup key={kind} label={t(`site.runKind.${kind}`)}>
                {members.map((run) => (
                  <option key={run.id} value={run.id}>
                    {optionLabel(run, formatter, run.id === latestId ? t('site.runLatest') : null)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </>
      )}
      {/* `role="status"` because the fallback happens without any action from
          the visitor: they followed a link and got a different model than the
          one it named. Sighted visitors see the text; a screen reader would
          otherwise hear the viewer open and nothing about the substitution. */}
      {noticeText && (
        <p className="run-picker-notice" role="status">
          {noticeText}
        </p>
      )}
    </div>
  );
}

export default RunPicker;
