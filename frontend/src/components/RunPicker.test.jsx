/**
 * The version picker: what it shows, what it hides, and when it is absent.
 *
 * Rendered against the real locale files (`testI18n`) rather than a stub `t`,
 * because two of these assertions are about *translations*: that the group
 * label in Odia is Odia, not the English fallback, and that the option text
 * never carries the word "Manual" -- a status the API sends for 15 GS runs and
 * which this component must not surface however the run is described.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { I18nextProvider } from 'react-i18next';

import RunPicker from './RunPicker';
import { testI18n } from '../test/utils';

/*
  Newest-first, as the caller delivers them. The two GS runs are the production
  shape that motivated the "never render status" rule: placed by hand, finished
  in the same second they started, no stage rows behind them.
*/
const GS_NEW = {
  id: 'gs-2026',
  kind: 'GS',
  status: 'Manual',
  started_at: '2026-03-02T09:00:00Z',
  ended_at: '2026-03-02T09:00:00Z',
};
const AV_2025 = {
  id: 'av-2025',
  kind: 'aV',
  status: 'Archived',
  started_at: '2025-07-14T04:00:00Z',
  ended_at: '2025-07-14T06:30:00Z',
};
const GS_OLD = {
  id: 'gs-2024',
  kind: 'GS',
  status: 'Manual',
  started_at: '2024-11-20T12:00:00Z',
  ended_at: '2024-11-20T12:00:00Z',
};
const RUNS = [GS_NEW, AV_2025, GS_OLD];

function renderPicker(props = {}, language = 'en') {
  const i18n = testI18n(language);
  return {
    i18n,
    ...render(
      <I18nextProvider i18n={i18n}>
        <RunPicker runs={RUNS} selectedRunId={GS_NEW.id} onSelect={vi.fn()} {...props} />
      </I18nextProvider>,
    ),
  };
}

describe('RunPicker', () => {
  it('renders nothing for a single run with no notice', () => {
    const { container } = renderPicker({ runs: [GS_NEW], notice: null });
    expect(container).toBeEmptyDOMElement();
  });

  it('groups the runs by kind under the localised kind labels', () => {
    const { i18n } = renderPicker();
    const groups = screen.getAllByRole('group');
    expect(groups).toHaveLength(2);
    // The group holding the newest model first, then the other.
    expect(groups[0]).toHaveAttribute('label', i18n.t('site.runKind.GS'));
    expect(groups[1]).toHaveAttribute('label', i18n.t('site.runKind.aV'));
    // Newest-first within a group, and each run in the group it belongs to.
    const gsOptions = within(groups[0]).getAllByRole('option');
    expect(gsOptions.map((option) => option.value)).toEqual([GS_NEW.id, GS_OLD.id]);
    expect(within(groups[1]).getAllByRole('option').map((option) => option.value)).toEqual([AV_2025.id]);
  });

  it('marks only the newest run overall as the latest', () => {
    const { i18n } = renderPicker();
    const latest = i18n.t('site.runLatest');
    const options = screen.getAllByRole('option');
    const flagged = options.filter((option) => option.textContent.includes(latest));
    expect(flagged).toHaveLength(1);
    expect(flagged[0]).toHaveValue(GS_NEW.id);
  });

  it('describes a run by its date and never by its status', () => {
    renderPicker();
    for (const option of screen.getAllByRole('option')) {
      // A medium date in any locale carries the year; "Manual", "Archived",
      // the id and a duration are the things a visitor must not be shown.
      expect(option.textContent).toMatch(/202[456]/);
      expect(option.textContent).not.toMatch(/Manual/i);
      expect(option.textContent).not.toMatch(/Archived/i);
      expect(option.textContent).not.toContain(option.value);
      expect(option.textContent).not.toMatch(/\bmin\b/i);
    }
  });

  it('reports the chosen run id on change', async () => {
    const onSelect = vi.fn();
    renderPicker({ onSelect });
    const select = screen.getByRole('combobox');
    await userEvent.selectOptions(select, AV_2025.id);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(AV_2025.id);
  });

  it('labels the control for the accessibility tree', () => {
    const { i18n } = renderPicker({ id: 'viewer-run' });
    const select = screen.getByRole('combobox');
    expect(select).toHaveAttribute('id', 'viewer-run');
    expect(select).toHaveAttribute('aria-label', i18n.t('a11y.runPicker'));
    expect(screen.getByText(i18n.t('site.runs'))).toHaveAttribute('for', 'viewer-run');
  });

  it.each([
    ['missing', 'site.runNotFound'],
    ['unviewable', 'site.runUnsupported'],
  ])('announces the %s fallback as a status', (notice, key) => {
    const { i18n } = renderPicker({ notice });
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t(key));
  });

  it('shows the notice even when there is only one run to show', () => {
    // A shared link to a version that is gone still needs explaining, and the
    // absence-of-chrome rule for single-run sites must not swallow it.
    const { i18n } = renderPicker({ runs: [GS_NEW], notice: 'missing' });
    expect(screen.getByRole('status')).toHaveTextContent(i18n.t('site.runNotFound'));
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('labels the kinds in Odia when rendered in Odia', () => {
    const { i18n } = renderPicker({}, 'or');
    const label = screen.getAllByRole('group')[0].getAttribute('label');
    expect(label).toBe(i18n.t('site.runKind.GS'));
    // Odia script, not the English string a missing key would fall back to.
    expect(label).toMatch(/[଀-୿]/);
    expect(label).not.toBe('Gaussian splat');
  });
});
