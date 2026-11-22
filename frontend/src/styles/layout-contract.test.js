/**
 * The attribution bar holds still, and nothing covers it.
 *
 * The bar used to start where the floating panel ended (`left:
 * --sidebar-occupied`), so opening the panel slid it sideways and dragging the
 * panel's resize handle moved it on every frame. It is attribution the OSM
 * licence requires, not a moving part.
 *
 * It spans the window now, and the things that would otherwise cover it are
 * measured off one token instead of four literals. That is a contract across
 * three stylesheets, so it is asserted rather than remembered.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const read = (name) => readFileSync(resolve(here, name), "utf8");

const mapView = read("MapView.css");
const sidebar = read("Sidebar.css");
const home = read("Home.css");
const tokens = read("theme-variables.css");

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

describe("the attribution bar", () => {
  it("spans the window rather than starting after the panel", () => {
    const rule = stripComments(mapView).match(
      /\.map-custom-footer\s*\{[^}]*\}/,
    )[0];
    expect(rule).toMatch(/left:\s*0/);
    expect(rule).not.toMatch(/--sidebar-occupied/);
  });

  it("no longer needs the panel to publish its width", () => {
    // A custom property written to the document root on every frame of a
    // resize drag, for two readers that no longer exist.
    expect(stripComments(mapView)).not.toContain("--sidebar-occupied");
    expect(stripComments(sidebar)).not.toContain("--sidebar-occupied");
  });

  it("has a declared height in both the tall and the stacked case", () => {
    expect(tokens).toMatch(/--map-footer-height:\s*\d+px/);
    // The 600px rule stacks it into three lines; that override is load-bearing.
    const small = stripComments(mapView).match(
      /@media\s*\(max-width:\s*600px\)\s*\{[\s\S]*?\n\}/,
    );
    expect(small?.[0]).toMatch(/--map-footer-height:\s*\d+px/);
  });
});

describe("nothing covers it", () => {
  it("the sidebar reserves the bar its height", () => {
    const rule = stripComments(sidebar).match(/\n\.sidebar\s*\{[^}]*\}/)[0];
    expect(rule).toMatch(/--map-footer-height/);
  });

  it("the request button clears it from the same token, not a literal", () => {
    const stripped = stripComments(home);
    expect(stripped).toMatch(
      /\.btn\.request-bottom-right\s*\{[^}]*--map-footer-height/,
    );
    // The four hand-written copies of the bar's height are gone.
    expect(stripped).not.toMatch(/bottom:\s*64px/);
  });

  it("the control stack is bounded by it too, so it cannot run off the bottom", () => {
    const rule = stripComments(mapView).match(
      /\.map-control-stack\s*\{[^}]*\}/,
    )[0];
    expect(rule).toMatch(/max-height/);
    expect(rule).toMatch(/--map-footer-height/);
    // Wrapping to the left is what keeps a landscape phone usable.
    expect(rule).toMatch(/flex-wrap:\s*wrap-reverse/);
  });
});

describe("the desktop control row", () => {
  /*
    Every disclosure in the stack has to be re-anchored together.

    In the column the panels slide out to the left, where there is map. In the
    row that is where the next control sits. The basemap picker and the key were
    moved to drop downwards and the language menu was added later against the
    shared rule, so it kept sliding left over its neighbour -- the exact
    collision the media query exists to prevent.
  */
  const desktop = stripComments(mapView).match(
    /@media\s*\(min-width:\s*1024px\)\s*\{[\s\S]*\n\}/,
  )?.[0];

  it("exists", () => {
    expect(desktop).toBeTruthy();
  });

  it("re-anchors every panel in the stack, not only the first two", () => {
    for (const panel of [
      "map-legend-panel",
      "map-style-control",
      "map-language-control",
    ]) {
      expect(desktop).toContain(panel);
    }
  });

  it("drops them below their control rather than beside it", () => {
    const rule = desktop.match(/\.map-control-stack[^{]*\{[^}]*top:\s*calc\(100%[^}]*\}/);
    expect(rule).toBeTruthy();
    expect(rule[0]).toMatch(/right:\s*0/);
  });
});
