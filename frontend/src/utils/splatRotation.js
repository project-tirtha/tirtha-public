import * as THREE from 'three';

/**
 * `Run.model_rotation` (degrees) as a quaternion, in the ZXY order
 * `<model-viewer>` used for `orientation="Zdeg Xdeg Ydeg"`.
 *
 * In its own module so `ModelViewer3D.jsx` exports only a component: Vite's
 * fast refresh cannot preserve state across an edit to a file that exports both,
 * and a full reload here loses the loaded model and the map behind it.
 *
 * **What crosses into the splat loader must be a flat array.**
 * `addSplatScene` does `new THREE.Quaternion().fromArray(rotationArray)`, which
 * reads `array[0..3]`; a `THREE.Quaternion` has no numeric indices, so passing
 * the object yields four `undefined`s and `makeRotationFromQuaternion` turns
 * those into a NaN matrix -- every splat in the scene at no position at all.
 * It happens for the default rotation too, so getting this wrong hides every
 * Gaussian splat model on the site, silently.
 *
 * Anything that is not three finite numbers becomes the identity. A partial
 * `model_rotation` -- `[]`, or a row where only two of `rotaX/Y/Z` were ever set
 * -- would otherwise reach `degToRad(undefined)` and produce the same NaN. No
 * rotation is the right answer to "we do not know the rotation"; NaN is not.
 */
export function eulerQuaternion(degrees) {
  const values = Array.isArray(degrees) ? degrees.map(Number) : [];
  const [x, y, z] = values.length === 3 && values.every(Number.isFinite) ? values : [0, 0, 0];
  return new THREE.Quaternion().setFromEuler(
    new THREE.Euler(
      THREE.MathUtils.degToRad(x),
      THREE.MathUtils.degToRad(y),
      THREE.MathUtils.degToRad(z),
      'ZXY',
    ),
  );
}
