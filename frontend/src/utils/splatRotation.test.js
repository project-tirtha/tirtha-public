/**
 * The shape of what the splat loader is handed.
 *
 * `addSplatScene` does `new THREE.Quaternion().fromArray(rotationArray)`, which
 * reads `array[0..3]`. A `THREE.Quaternion` has no numeric indices, so handing
 * it the object yields four `undefined`s and `makeRotationFromQuaternion` turns
 * those into a NaN matrix -- every splat in the scene at no position at all.
 *
 * It is a silent, total failure with no error anywhere, and it happens for the
 * default rotation too, so it would have hidden *every* Gaussian splat model on
 * the site. Nothing in the test suite renders WebGL, and nothing ever will here,
 * so the boundary is what gets pinned: whatever crosses into the library must be
 * a plain array of four finite numbers.
 */
import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { eulerQuaternion } from '../utils/splatRotation';

describe('eulerQuaternion', () => {
  it('produces something the library can read with fromArray', () => {
    const value = eulerQuaternion([0, 0, 0]).toArray();
    const read = new THREE.Quaternion().fromArray(value);
    for (const component of ['x', 'y', 'z', 'w']) {
      expect(Number.isFinite(read[component]), `${component} is not a number`).toBe(true);
    }
  });

  it('never yields a NaN rotation matrix', () => {
    for (const degrees of [[0, 0, 0], [90, 0, 0], [-37, 12.5, 180], null, undefined]) {
      const read = new THREE.Quaternion().fromArray(eulerQuaternion(degrees).toArray());
      const matrix = new THREE.Matrix4().makeRotationFromQuaternion(read);
      expect(matrix.elements.some(Number.isNaN), `NaN for ${JSON.stringify(degrees)}`).toBe(
        false,
      );
    }
  });

  it('treats the stored values as degrees, not radians', () => {
    // `Run.model_rotation` comes from `rotaX/Y/Z`, which the old page fed to
    // <model-viewer> as `orientation="Zdeg Xdeg Ydeg"`. Reading them as radians
    // would rotate a model by 180 radians instead of 180 degrees.
    const [x, y, z, w] = eulerQuaternion([180, 0, 0]).toArray();
    expect(Math.abs(x)).toBeCloseTo(1, 5);
    expect(y).toBeCloseTo(0, 5);
    expect(z).toBeCloseTo(0, 5);
    expect(Math.abs(w)).toBeCloseTo(0, 5);
  });

  it('is the identity when nothing was stored, rather than throwing', () => {
    expect(eulerQuaternion(undefined).toArray()).toEqual([0, 0, 0, 1]);
    expect(eulerQuaternion([]).toArray()).toEqual([0, 0, 0, 1]);
  });
});
