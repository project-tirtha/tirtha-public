/**
 * The page without a working map.
 *
 * Without WebGL, MapLibre threw during mount, the page-level boundary caught
 * it, and the whole page became "Something went wrong" -- the list and
 * Contribute included. These render the real `Home` with the map either
 * unavailable up front or throwing, and check the list-only view instead.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const env = vi.hoisted(() => ({ webgl: false, mapThrows: false }));

vi.mock('../utils/webgl', () => ({ hasWebGL: () => env.webgl }));
vi.mock('../components/MapView', () => ({
  default: () => {
    if (env.mapThrows) throw new Error('Failed to initialize WebGL');
    return <div data-testid="map" />;
  },
}));
vi.mock('../components/Sidebar', () => ({ default: () => <nav aria-label="Sites panel" /> }));
vi.mock('../components/RequestSiteModal', () => ({ default: () => null }));
vi.mock('../components/InfoModal', () => ({ default: () => null }));
vi.mock('../services/sites.service', async (importOriginal) => ({
  ...(await importOriginal()),
  fetchSites: vi.fn(async () => [
    { id: 'a', name: 'Konark Sun Temple', location: 'Puri, Odisha', placed: true, lat: 19.88, lng: 86.09 },
  ]),
  fetchSite: vi.fn(async () => ({ runs: [] })),
}));

import '../i18n/config';
import Home from './Home';
import { axeViolations } from '../test/axe';

window.matchMedia = () => ({ matches: false });

const renderHome = () =>
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );

describe('Home without a map', () => {
  beforeEach(() => {
    env.webgl = false;
    env.mapThrows = false;
  });

  it('shows the site list, not an error page, when WebGL is unavailable', async () => {
    renderHome();
    expect(await screen.findByText('Konark Sun Temple')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /map cannot be shown/i })).toBeInTheDocument();
    expect(screen.queryByTestId('map')).not.toBeInTheDocument();
    expect(screen.queryByText(/Something went wrong/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Contribute photographs: Konark/ })).toBeInTheDocument();
  });

  it('falls back to the same list when the map throws while starting', async () => {
    env.webgl = true;
    env.mapThrows = true;
    vi.spyOn(console, 'error').mockImplementation(() => {});
    renderHome();
    expect(await screen.findByText('Konark Sun Temple')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /map cannot be shown/i })).toBeInTheDocument();
    expect(screen.queryByText(/Something went wrong/i)).not.toBeInTheDocument();
    console.error.mockRestore();
  });

  it('renders the map when WebGL works', async () => {
    env.webgl = true;
    renderHome();
    expect(await screen.findByTestId('map')).toBeInTheDocument();
  });
});

describe('Home shell accessibility', () => {
  it('has no axe violations with the map in place', async () => {
    env.webgl = true;
    const { container } = renderHome();
    await screen.findByTestId('map');
    expect(await axeViolations(container)).toEqual([]);
  });

  it('has no axe violations in the list-only view', async () => {
    env.webgl = false;
    const { container } = renderHome();
    await screen.findByText('Konark Sun Temple');
    expect(await axeViolations(container)).toEqual([]);
  });
});
