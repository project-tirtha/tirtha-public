/**
 * `/embed/<site>/` -- the viewer alone, inside someone else's page.
 *
 * It used to boot the whole app, whose router matched nothing under `/embed/`
 * and rendered "Page not found" in every third-party iframe.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const env = vi.hoisted(() => ({ webgl: true }));
vi.mock('../utils/webgl', () => ({ hasWebGL: () => env.webgl }));
vi.mock('../components/ModelViewer3D', () => ({
  default: (props) => (
    <div
      data-testid="viewer"
      data-model-path={props.modelPath}
      data-model-bytes={String(props.modelBytes)}
      data-camera={JSON.stringify(props.camera)}
    />
  ),
}));
vi.mock('../services/sites.service', async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRun: vi.fn(),
}));

import '../i18n/config';
import { fetchRun } from '../services/sites.service';
import EmbedViewer from './EmbedViewer';
import { readEmbedConfig } from './config';

beforeEach(() => {
  env.webgl = true;
  fetchRun.mockReset();
});

describe('readEmbedConfig', () => {
  it('reads what embed.html puts on #root', () => {
    const root = document.createElement('div');
    root.setAttribute('data-embed-model', '/media/models/a.splat');
    root.setAttribute('data-embed-kind', 'GS');
    root.setAttribute('data-embed-site', 'site1');
    root.setAttribute('data-embed-run', 'run1');
    expect(readEmbedConfig(root)).toEqual({
      modelUrl: '/media/models/a.splat',
      kind: 'GS',
      siteId: 'site1',
      runId: 'run1',
    });
  });

  it('is null for the ordinary shell, whose #root carries nothing', () => {
    expect(readEmbedConfig(document.createElement('div'))).toBeNull();
    expect(readEmbedConfig(null)).toBeNull();
  });

  it('still recognises an embed whose model URL is empty', () => {
    const root = document.createElement('div');
    root.setAttribute('data-embed-model', '');
    expect(readEmbedConfig(root)).not.toBeNull();
  });
});

describe('EmbedViewer', () => {
  it('draws the run the API describes, with its camera and size', async () => {
    fetchRun.mockResolvedValue({
      id: 'run1',
      file_url: '/media/models/a.splat',
      model_bytes: 1234,
      camera: { position: [1, 2, 3] },
    });
    render(<EmbedViewer modelUrl="/media/models/a.sog" siteId="site1" runId="run1" />);
    const viewer = await screen.findByTestId('viewer');
    expect(viewer).toHaveAttribute('data-model-path', '/media/models/a.splat');
    expect(viewer).toHaveAttribute('data-model-bytes', '1234');
    expect(JSON.parse(viewer.dataset.camera)).toEqual({ position: [1, 2, 3] });
    expect(fetchRun).toHaveBeenCalledWith('run1', expect.anything());
  });

  it('falls back to the template URL when the API is unreachable', async () => {
    fetchRun.mockRejectedValue(new Error('offline'));
    render(<EmbedViewer modelUrl="/media/models/a.splat" siteId="site1" runId="run1" />);
    const viewer = await screen.findByTestId('viewer');
    expect(viewer).toHaveAttribute('data-model-path', '/media/models/a.splat');
    expect(viewer).toHaveAttribute('data-model-bytes', 'null');
  });

  it('says the model is unavailable rather than "Page not found"', async () => {
    render(<EmbedViewer modelUrl="" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('This model is not available.');
    expect(screen.queryByText(/not found/i)).not.toBeInTheDocument();
  });

  it('explains a missing WebGL instead of failing', async () => {
    env.webgl = false;
    render(<EmbedViewer modelUrl="/media/models/a.splat" />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/WebGL/);
    expect(screen.queryByTestId('viewer')).not.toBeInTheDocument();
  });

  it('renders no map, panel or router chrome', async () => {
    fetchRun.mockResolvedValue({ file_url: '/media/models/a.glb' });
    const { container } = render(<EmbedViewer modelUrl="" runId="run1" />);
    await waitFor(() => expect(screen.getByTestId('viewer')).toBeInTheDocument());
    expect(container.querySelectorAll('main')).toHaveLength(1);
    expect(container.querySelector('.maplibregl-map, nav, aside')).toBeNull();
  });
});
