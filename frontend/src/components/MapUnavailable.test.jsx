import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import '../i18n/config';
import MapUnavailable from './MapUnavailable';
import { axeViolations } from '../test/axe';

const SITES = [
  { id: 'a', name: 'Konark Sun Temple', location: 'Puri, Odisha' },
  { id: 'b', name: 'Lingaraj Temple', location: 'Bhubaneswar, Odisha' },
  { id: 'c', name: 'Mukteśvara Temple', location: 'Bhubaneswar, Odisha' },
];

describe('MapUnavailable', () => {
  it('lists every site and says why there is no map', () => {
    render(<MapUnavailable sites={SITES} onSelect={() => {}} onContribute={() => {}} />);
    expect(screen.getByRole('heading', { name: /cannot be shown/i })).toBeInTheDocument();
    for (const site of SITES) expect(screen.getByText(site.name)).toBeInTheDocument();
  });

  it('searches without caring about accents', () => {
    render(<MapUnavailable sites={SITES} onSelect={() => {}} />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'muktesvara' } });
    expect(screen.getByText('Mukteśvara Temple')).toBeInTheDocument();
    expect(screen.queryByText('Konark Sun Temple')).not.toBeInTheDocument();
  });

  it('selects a site and opens Contribute for it', () => {
    const onSelect = vi.fn();
    const onContribute = vi.fn();
    render(<MapUnavailable sites={SITES} onSelect={onSelect} onContribute={onContribute} />);
    fireEvent.click(screen.getByText('Lingaraj Temple'));
    expect(onSelect).toHaveBeenCalledWith(SITES[1]);
    fireEvent.click(screen.getByRole('button', { name: /Contribute photographs: Konark/ }));
    expect(onContribute).toHaveBeenCalledWith(SITES[0]);
  });

  it('has no axe violations', async () => {
    const { container } = render(
      <MapUnavailable sites={SITES} onSelect={() => {}} onContribute={() => {}} />,
    );
    expect(await axeViolations(container)).toEqual([]);
  });
});
