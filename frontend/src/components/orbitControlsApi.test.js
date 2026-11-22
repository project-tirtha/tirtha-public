/**
 * The viewer's keyboard controls call methods on three-stdlib's OrbitControls.
 *
 * `rotateLeft` and `rotateUp` look like methods and are not: inside
 * OrbitControls they are module-scope functions, never assigned to the
 * instance. Only `dollyIn`/`dollyOut` of the four the keymap used are public.
 * So every arrow press threw `TypeError: controls.rotateLeft is not a
 * function` and the model did not move, while `+`/`-` worked -- which is
 * exactly why it looked functional.
 *
 * Keyboard orbit is how this viewer is reachable without a mouse. This test
 * reads the method names the component actually calls and asserts each one
 * exists on a real instance, so the next rename in the library is caught here
 * rather than by somebody trying to use the site with a keyboard.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { PerspectiveCamera } from "three";
import { OrbitControls } from "three-stdlib";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, "ModelViewer3D.jsx"), "utf8");

function methodsCalledOnControls() {
  // `controls.foo(` — the keymap's own call sites.
  const names = new Set();
  for (const match of source.matchAll(/\bcontrols\.([a-zA-Z]\w*)\s*\(/g)) {
    names.add(match[1]);
  }
  return [...names].sort();
}

function anInstance() {
  const camera = new PerspectiveCamera(50, 1, 0.1, 1000);
  return new OrbitControls(camera, document.createElement("div"));
}

describe("OrbitControls API the viewer depends on", () => {
  it("calls at least the methods the keyboard handler needs", () => {
    const called = methodsCalledOnControls();
    for (const required of [
      "setAzimuthalAngle",
      "getAzimuthalAngle",
      "dollyIn",
    ]) {
      expect(called).toContain(required);
    }
  });

  it("every method the component calls exists on a real instance", () => {
    const controls = anInstance();
    const missing = methodsCalledOnControls().filter(
      (name) => typeof controls[name] !== "function",
    );
    controls.dispose();
    expect(
      missing,
      `not functions on OrbitControls: ${missing.join(", ")}`,
    ).toEqual([]);
  });

  it("the two that caused the bug are still absent, so this test means something", () => {
    const controls = anInstance();
    expect(typeof controls.rotateLeft).not.toBe("function");
    expect(typeof controls.rotateUp).not.toBe("function");
    // ...while the two that always worked still do.
    expect(typeof controls.dollyIn).toBe("function");
    expect(typeof controls.dollyOut).toBe("function");
    controls.dispose();
  });

  it("the angle setters clamp within the polar limits", () => {
    const controls = anInstance();
    controls.setPolarAngle(Math.PI * 2);
    expect(controls.getPolarAngle()).toBeLessThanOrEqual(Math.PI);
    controls.setPolarAngle(-1);
    expect(controls.getPolarAngle()).toBeGreaterThanOrEqual(0);
    controls.dispose();
  });
});
