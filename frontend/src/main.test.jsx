/**
 * The entry point picks embed mode from the DOM the server rendered.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';

vi.mock('./App.jsx', () => ({ default: () => <div data-testid="full-app" /> }));
vi.mock('./embed/EmbedViewer.jsx', () => ({
  default: (props) => <div data-testid="embed" data-model={props.modelUrl} data-run={props.runId} />,
}));

afterEach(() => {
  document.body.innerHTML = '';
  vi.resetModules();
});

async function boot(rootAttributes) {
  const root = document.createElement('div');
  root.id = 'root';
  for (const [name, value] of Object.entries(rootAttributes)) root.setAttribute(name, value);
  document.body.appendChild(root);
  await import('./main.jsx');
}

describe('main.jsx', () => {
  it('mounts only the viewer for /embed/', async () => {
    await boot({ 'data-embed-model': '/m/a.splat', 'data-embed-run': 'r1' });
    const embed = await screen.findByTestId('embed');
    expect(embed).toHaveAttribute('data-model', '/m/a.splat');
    expect(embed).toHaveAttribute('data-run', 'r1');
    expect(screen.queryByTestId('full-app')).not.toBeInTheDocument();
  });

  it('mounts the full app everywhere else', async () => {
    await boot({});
    expect(await screen.findByTestId('full-app')).toBeInTheDocument();
    expect(screen.queryByTestId('embed')).not.toBeInTheDocument();
  });
});
