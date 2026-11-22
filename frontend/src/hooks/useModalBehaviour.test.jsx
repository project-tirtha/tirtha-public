/**
 * What happens the moment a dialog opens.
 *
 * Two things this got wrong, both visible rather than theoretical: it focused
 * the first control, so opening the 3D viewer drew a 3px focus ring around the
 * Fullscreen button in the corner and looked like a rendering fault; and it
 * depended on `onClose`, which callers rebuild every render, so the whole
 * effect re-ran and re-focused constantly.
 */
import { render, screen } from '@testing-library/react';
import { useRef } from 'react';
import { describe, expect, it } from 'vitest';

import { useModalBehaviour } from './useModalBehaviour';

function Dialog({ isOpen }) {
  const panelRef = useRef(null);
  // Rebuilt every render, as `Home` does for all four of its dialogs.
  useModalBehaviour(isOpen, () => {}, panelRef);
  if (!isOpen) return null;
  return (
    <div ref={panelRef} role="dialog" aria-modal="true" aria-label="A model">
      <button type="button">Fullscreen</button>
      <button type="button">Close</button>
    </div>
  );
}

describe('opening a dialog', () => {
  it('focuses the dialog, not the control in its corner', () => {
    render(<Dialog isOpen />);
    const panel = screen.getByRole('dialog');
    expect(document.activeElement).toBe(panel);
    expect(document.activeElement).not.toBe(screen.getByRole('button', { name: 'Fullscreen' }));
  });

  it('makes the dialog focusable without the caller remembering to', () => {
    // -1: reachable programmatically, never a tab stop of its own.
    render(<Dialog isOpen />);
    expect(screen.getByRole('dialog').tabIndex).toBe(-1);
  });

  it('does not re-focus when the parent re-renders', () => {
    /*
      The effect used to depend on `onClose`. A background reverse geocode
      landing a second after the dialog opened threw a keyboard user back to
      the first control.
    */
    const { rerender } = render(<Dialog isOpen />);
    const close = screen.getByRole('button', { name: 'Close' });
    close.focus();

    // A parent re-render, which is what a background fetch resolving causes.
    rerender(<Dialog isOpen />);

    expect(
      document.activeElement,
      'the effect re-ran and took focus back from the user',
    ).toBe(close);
  });
});
