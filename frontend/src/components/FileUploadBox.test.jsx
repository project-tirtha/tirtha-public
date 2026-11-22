/**
 * The drop box is the only way into the upload form, and its failure mode is
 * silence: a file input that still holds the last pick fires no `change` event
 * for the same file, so the picker closes and nothing at all happens.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';

// The shipped i18n instance, which `initReactI18next` registers globally, so
// the box resolves its real dropzone caption without a provider around it.
import '../i18n/config';
import FileUploadBox from './FileUploadBox';

const photo = () => new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });

describe('FileUploadBox', () => {
  /*
    `role="button"` promises native button behaviour, and a native button fires
    on Space as well as Enter. This only handled Enter, so a keyboard user who
    pressed Space got a scrolled page and no file picker -- and this box is the
    only keyboard route to one.
  */
  it('opens the picker on Space, not only Enter', () => {
    const { container } = render(
      <FileUploadBox selectedFiles={[]} onFilesChange={() => {}} />,
    );
    const box = container.querySelector('[role="button"]');
    const input = container.querySelector('input[type="file"]');
    const clicks = vi.spyOn(input, 'click');

    // Counted as increments, not absolutes: activating the box opens the
    // picker through more than one path and the total is not the point.
    fireEvent.keyDown(box, { key: 'Enter' });
    const afterEnter = clicks.mock.calls.length;
    expect(afterEnter).toBeGreaterThan(0);

    fireEvent.keyDown(box, { key: ' ' });
    expect(clicks.mock.calls.length).toBeGreaterThan(afterEnter);
  });

  it('does not scroll the page when Space activates it', () => {
    const { container } = render(
      <FileUploadBox selectedFiles={[]} onFilesChange={() => {}} />,
    );
    const box = container.querySelector('[role="button"]');
    const event = new KeyboardEvent('keydown', {
      key: ' ',
      bubbles: true,
      cancelable: true,
    });
    box.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  it('hands the picked files to the caller', () => {
    const onFilesChange = vi.fn();
    const { container } = render(
      <FileUploadBox selectedFiles={[]} onFilesChange={onFilesChange} />,
    );

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [photo()] },
    });

    expect(onFilesChange).toHaveBeenCalledTimes(1);
    expect(onFilesChange.mock.calls[0][0].map((file) => file.name)).toEqual(['photo.jpg']);
  });

  it('empties the input after a pick, so the same file can be chosen again', () => {
    const { container } = render(
      <FileUploadBox selectedFiles={[]} onFilesChange={() => {}} />,
    );
    const input = container.querySelector('input[type="file"]');
    /*
      The write is watched rather than read back: jsdom reports '' for a file
      input's value whether or not anything cleared it, so the assignment is
      the only thing that tells the two apart. In a browser that assignment is
      what lets the same photograph be picked a second time -- left in place,
      the input's value never changes and no `change` event is dispatched.
    */
    const writes = [];
    Object.defineProperty(input, 'value', {
      configurable: true,
      get: () => '',
      set: (value) => {
        writes.push(value);
      },
    });

    fireEvent.change(input, { target: { files: [photo()] } });

    expect(writes).toEqual(['']);
  });
});
