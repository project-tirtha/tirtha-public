import { afterEach, describe, expect, it } from 'vitest';

import { isConstrainedDevice, LARGE_MODEL_BYTES, shouldConfirmModelLoad, toMegabytes } from './device';
import { viewerErrorKey } from './viewerErrors';

const desktop = { userAgent: 'Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0', deviceMemory: 16 };
const originalMatchMedia = window.matchMedia;

afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

describe('isConstrainedDevice', () => {
  it('is false for an ordinary desktop', () => {
    window.matchMedia = () => ({ matches: false });
    expect(isConstrainedDevice(desktop)).toBe(false);
  });

  it('is true on Save-Data, low memory, or a phone', () => {
    window.matchMedia = () => ({ matches: false });
    expect(isConstrainedDevice({ ...desktop, connection: { saveData: true } })).toBe(true);
    expect(isConstrainedDevice({ ...desktop, deviceMemory: 4 })).toBe(true);
    expect(isConstrainedDevice({ ...desktop, userAgentData: { mobile: true } })).toBe(true);
    expect(
      isConstrainedDevice({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)' }),
    ).toBe(true);
  });

  it('recognises a touch-first narrow screen without a user agent hint', () => {
    window.matchMedia = () => ({ matches: true });
    expect(isConstrainedDevice({ userAgent: 'Safari' })).toBe(true);
  });
});

describe('shouldConfirmModelLoad', () => {
  const phone = { userAgent: 'Android Mobile' };

  it('asks only for a large model on a constrained device', () => {
    expect(shouldConfirmModelLoad(LARGE_MODEL_BYTES + 1, phone)).toBe(true);
    expect(shouldConfirmModelLoad(LARGE_MODEL_BYTES, phone)).toBe(false);
    window.matchMedia = () => ({ matches: false });
    expect(shouldConfirmModelLoad(300 * 1024 * 1024, desktop)).toBe(false);
  });

  it('does not ask when the size is unknown (an API without model_bytes)', () => {
    expect(shouldConfirmModelLoad(undefined, phone)).toBe(false);
    expect(shouldConfirmModelLoad(null, phone)).toBe(false);
  });

  it('rounds megabytes up', () => {
    expect(toMegabytes(50 * 1024 * 1024 + 1)).toBe(51);
  });
});

describe('viewerErrorKey', () => {
  it.each([
    ['WebGL context lost -- the browser ran out of graphics memory', 'viewer.error.contextLost'],
    ['RangeError: Array buffer allocation failed', 'viewer.error.memory'],
    ['No data arrived for this model within 60s -- the download never began', 'viewer.error.noStart'],
    ['Stalled at 3/112 files with 109 outstanding', 'viewer.error.stalled'],
    ['TypeError: Failed to fetch', 'viewer.error.network'],
    ['HTTP 404: Not Found', 'viewer.error.notFound'],
    ['SyntaxError: Unexpected token < in JSON', 'viewer.error.format'],
    ['something nobody anticipated', 'viewer.error.generic'],
  ])('maps %j', (message, key) => {
    expect(viewerErrorKey(message)).toBe(key);
  });
});
