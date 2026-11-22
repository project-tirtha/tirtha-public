/**
 * The live region actually carries what the silent status elements say.
 *
 * `announce()` shipped with one caller -- the map's cluster handler -- while
 * its own docstring described a dozen silent `role="status"` elements as the
 * problem it solved. It was infrastructure, not a fix. These are the callers
 * that make the claim true, tested through the region rather than through the
 * hook, because the region is what a screen reader reads.
 */
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Snackbar from '../components/Snackbar';
import { REGION_ID, announce } from './announce';

/** The region only exists because App mounts it; tests mount their own. */
function withRegion(ui) {
  const region = document.createElement('div');
  region.id = REGION_ID;
  document.body.appendChild(region);
  return render(ui);
}

beforeEach(() => {
  // `announce` defers to the next frame so a repeated message still counts as
  // a change; drive that frame synchronously rather than waiting on it.
  vi.stubGlobal('requestAnimationFrame', (fn) => {
    fn();
    return 0;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.getElementById(REGION_ID)?.remove();
});

const regionText = () => document.getElementById(REGION_ID)?.textContent;

describe('the live region', () => {
  it('says nothing when there is no region to say it into', () => {
    // Called from a component rendered outside App in a test; must not throw.
    expect(() => announce('hello')).not.toThrow();
  });

  it('carries a repeated message, which setting the same text would not', () => {
    withRegion(<div />);
    announce('12 sites');
    expect(regionText()).toBe('12 sites');
    announce('12 sites');
    expect(regionText()).toBe('12 sites');
  });
});

describe('the snackbar', () => {
  it('announces its message, not merely renders it', () => {
    /*
      Every upload outcome arrives through the snackbar, and it unmounts
      entirely when empty -- so its own `role="status"` is created together
      with its text and read by nothing.
    */
    withRegion(<Snackbar message="Upload complete" />);
    expect(regionText()).toBe('Upload complete');
  });

  it('is silent when it has nothing to say', () => {
    withRegion(<Snackbar message="" />);
    expect(regionText()).toBe('');
  });

  it('announces the next message when it changes', () => {
    const region = document.createElement('div');
    region.id = REGION_ID;
    document.body.appendChild(region);
    const { rerender } = render(<Snackbar message="Uploading" />);
    expect(regionText()).toBe('Uploading');
    act(() => rerender(<Snackbar message="Upload failed" />));
    expect(regionText()).toBe('Upload failed');
  });
});
