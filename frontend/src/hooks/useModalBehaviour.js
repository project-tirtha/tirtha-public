import { useEffect, useRef } from 'react';

/**
 * The four things a modal owes a keyboard user (internal design notes).
 *
 * - Escape closes it (WCAG 2.1.2).
 * - Focus moves inside on open, rather than being left on the page underneath.
 * - Tab stays inside while it is open (2.4.3).
 * - Focus returns to whatever opened it on close, so the tab position survives.
 *
 * `role="dialog" aria-modal="true"` is a promise that these hold, and keeping it
 * is about forty lines of focus handling. `InfoModal` had them;
 * `ContributeModal`, `RequestSiteModal` and `ModalViewer` were modals that never
 * declared themselves dialogs at all, so a screen reader was never told the rest
 * of the page had gone away.
 *
 * A hook rather than a wrapper component: each of these dialogs owns its own
 * markup and its own entry animation, and a shell would throw that away for no
 * accessibility gain. What must not happen is a second hand-written copy of the
 * trap -- that is how four modals drift until only one keeps the promise.
 */
export function useModalBehaviour(isOpen, onClose, panelRef) {
  const openerRef = useRef(null);

  /*
    `onClose` is held in a ref, and is deliberately not a dependency below.

    Callers rebuild it on every render -- `Home` defines all of them inline --
    so depending on it made this effect tear down and re-run on every parent
    render. Each re-run calls `focusable()[0]?.focus()`, so a keyboard user
    typing in the third field of the contribute dialog was thrown back to the
    first control whenever anything upstream re-rendered. A background reverse
    geocode landing a second after the dialog opens does exactly that.

    `ContributeModal` already documents this trap and routes around it locally;
    the other three modals did not get that fix. Doing it here means a fifth
    modal cannot reintroduce it.
  */
  const onCloseRef = useRef(onClose);
  // In an effect rather than during render: assigning a ref while rendering is
  // a side effect, and React may render without committing.
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return undefined;

    openerRef.current = document.activeElement;

    const focusable = () =>
      Array.from(
        panelRef.current?.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
        // A disabled control is not a tab stop, and including it means Shift+Tab
        // from the first real one lands on nothing.
      ).filter((element) => !element.disabled && element.offsetParent !== null);

    /*
      The panel, not its first control.

      Focusing `focusable()[0]` put a 3px focus ring on whatever happened to be
      first -- for the 3D viewer that is the Fullscreen button in the top-right
      corner, so opening a model drew a blue ring around it that looked like a
      rendering fault. Programmatic focus with no preceding key press counts as
      `:focus-visible` in Chrome, so the ring showed for mouse users too.

      Focusing the dialog itself is the usual pattern and is better in both
      directions: a screen reader announces the dialog and its label rather than
      "Fullscreen, button", and Tab then moves to the first control the way it
      would if the dialog had just appeared. The `-1` is set here rather than in
      each caller's markup so no dialog can be added without it.
    */
    if (panelRef.current) {
      panelRef.current.tabIndex = -1;
      panelRef.current.focus({ preventScroll: true });
    } else {
      focusable()[0]?.focus();
    }

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        onCloseRef.current?.();
        return;
      }
      if (event.key !== 'Tab') return;

      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      openerRef.current?.focus?.();
    };
  }, [isOpen, panelRef]);
}

export default useModalBehaviour;
