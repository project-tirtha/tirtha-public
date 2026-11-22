import axe from 'axe-core';

/**
 * Real accessibility checks, not regexes over source.
 *
 * axe-core runs against the rendered DOM. Two rules are off because jsdom
 * cannot answer them, not because they do not matter: `color-contrast` needs
 * layout and computed colours jsdom does not have, and `region` asks for the
 * whole page to sit in landmarks -- these tests render one component at a
 * time, so it would flag every one of them for being a fragment.
 *
 * Returns the violations as readable strings, so a failure says what and
 * where rather than "expected [] to equal [Object]".
 */
export async function axeViolations(container = document.body) {
  const results = await axe.run(container, {
    resultTypes: ['violations'],
    rules: {
      'color-contrast': { enabled: false },
      region: { enabled: false },
    },
  });
  return results.violations.map(
    (violation) =>
      `${violation.id}: ${violation.help} -> ${violation.nodes
        .map((node) => node.target.join(' '))
        .join(', ')}`,
  );
}
