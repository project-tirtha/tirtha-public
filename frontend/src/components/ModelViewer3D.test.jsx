/**
 * The viewer's outer wrapper: the large-model prompt and the error overlay's
 * wording. The canvas itself needs WebGL, which jsdom does not have, so it is
 * stubbed -- what is under test is what happens *before* and *around* it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';

const env = vi.hoisted(() => ({ constrained: false }));

vi.mock('@react-three/fiber', async (importOriginal) => ({
  ...(await importOriginal()),
  Canvas: () => <div data-testid="canvas" />,
}));
vi.mock('../utils/device', async (importOriginal) => ({
  ...(await importOriginal()),
  isConstrainedDevice: () => env.constrained,
}));

import '../i18n/config';
import ModelViewer3D from './ModelViewer3D';
import { axeViolations } from '../test/axe';

const MB = 1024 * 1024;

beforeEach(() => {
  env.constrained = false;
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('large models on constrained devices', () => {
  it('asks before loading a 200 MB model on a phone', () => {
    env.constrained = true;
    render(<ModelViewer3D modelPath="/m/big.splat" modelBytes={200 * MB} siteName="Konark" />);
    expect(screen.getByText('Load 200 MB model?')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Load the model' }));
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('does not ask on a desktop', () => {
    render(<ModelViewer3D modelPath="/m/big.splat" modelBytes={200 * MB} />);
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('does not ask for a small model', () => {
    env.constrained = true;
    render(<ModelViewer3D modelPath="/m/small.splat" modelBytes={10 * MB} />);
    expect(screen.getByTestId('canvas')).toBeInTheDocument();
  });

  it('asks the server for the size when the API does not give one', async () => {
    env.constrained = true;
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      headers: { get: (name) => (name === 'content-length' ? String(120 * MB) : null) },
    }));
    render(<ModelViewer3D modelPath="/m/big.splat" />);
    expect(await screen.findByText('Load 120 MB model?')).toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/m/big.splat'),
      expect.objectContaining({ method: 'HEAD' }),
    );
  });

  it('loads anyway when the size cannot be learned', async () => {
    env.constrained = true;
    globalThis.fetch = vi.fn(async () => {
      throw new Error('offline');
    });
    render(<ModelViewer3D modelPath="/m/big.splat" />);
    expect(await screen.findByTestId('canvas')).toBeInTheDocument();
  });

  it('asks again for a different model', () => {
    env.constrained = true;
    const { rerender } = render(<ModelViewer3D modelPath="/m/a.splat" modelBytes={90 * MB} />);
    fireEvent.click(screen.getByRole('button', { name: 'Load the model' }));
    rerender(<ModelViewer3D modelPath="/m/b.splat" modelBytes={90 * MB} />);
    expect(screen.getByText('Load 90 MB model?')).toBeInTheDocument();
  });

  it('has no axe violations in the prompt', async () => {
    env.constrained = true;
    const { container } = render(
      <ModelViewer3D modelPath="/m/big.splat" modelBytes={200 * MB} onContributeClick={() => {}} />,
    );
    expect(await axeViolations(container)).toEqual([]);
  });
});

describe('the viewer wrapper', () => {
  it('has no axe violations with nothing published', async () => {
    const { container } = render(
      <ModelViewer3D modelPath={null} resolved onContributeClick={() => {}} />,
    );
    expect(await axeViolations(container)).toEqual([]);
  });

  it('has no axe violations while loading', async () => {
    const { container } = render(<ModelViewer3D modelPath="/m/a.splat" siteName="Konark" />);
    expect(await axeViolations(container)).toEqual([]);
  });

  it('names the failure in the reading language and folds the raw text away', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const { container } = render(<ModelViewer3D modelPath="/m/a.splat" siteName="Konark" />);
      // Nothing arrives: the no-start timer fires.
      await act(async () => {
        vi.advanceTimersByTime(10 * 60 * 1000);
      });
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('The model did not start downloading.');
      const details = container.querySelector('details.error-detail');
      expect(details).not.toBeNull();
      expect(details.open).toBe(false);
      expect(details).toHaveTextContent(/the download never began/);
      vi.useRealTimers();
      expect(await axeViolations(container)).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });
});
