/**
 * The search box as a keyboard combobox (internal design notes).
 *
 * The list is reachable by arrow keys and Enter opens whatever is highlighted,
 * which is the whole of the pattern and none of it is reachable from a
 * utils-level test: the highlight lives in state `Home` owns, the results
 * arrive 200ms after the last keystroke, and what Enter does depends on both.
 * The defect this pins is already commented in the component -- the highlight
 * used to be reset to the first row whenever the result count changed, so
 * arrowing to the third result and typing one more character opened the wrong
 * site with no sign on screen that anything had moved.
 */
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import SearchBar from './SearchBar';
import { renderWithProviders } from '../test/utils';

/*
  Three of the four are in Puri, so typing "p" matches every row -- each name
  ends in "Temple" -- and "pu" drops the fourth. That is the narrowing the
  second test needs: a keystroke that changes the result count without moving
  the row that was highlighted.
*/
const SITES = [
  { id: 'kon', name: 'Konark Sun Temple', location: 'Puri, Odisha', lat: 19.88, lng: 86.09 },
  { id: 'gun', name: 'Gundicha Temple', location: 'Puri, Odisha', lat: 19.81, lng: 85.83 },
  { id: 'jag', name: 'Jagannath Temple', location: 'Puri, Odisha', lat: 19.8, lng: 85.81 },
  { id: 'lin', name: 'Lingaraj Temple', location: 'Bhubaneswar, Odisha', lat: 20.23, lng: 85.83 },
];

/**
 * `SearchBar` has no state of its own -- both instances share one set owned by
 * `Home`, deliberately, so a test has to supply it the way `Home` does.
 */
function SearchBarHost({ onSearchSelect }) {
  const [searchQuery, setSearchQuery] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  return (
    <SearchBar
      templeList={SITES}
      onSearchSelect={onSearchSelect}
      searchQuery={searchQuery}
      setSearchQuery={setSearchQuery}
      isFocused={isFocused}
      setIsFocused={setIsFocused}
      activeIndex={activeIndex}
      setActiveIndex={setActiveIndex}
    />
  );
}

/*
  The row the combobox says is current, read the way a screen reader reads it.

  Waited for rather than read once: the results and the highlight land in two
  commits, because the effect that keeps the highlight inside the list runs
  after the render that first draws the list. Reading immediately after the
  options appear catches the frame in between, where `aria-activedescendant` is
  still unset.
*/
function findHighlightedOption() {
  return waitFor(() => {
    const id = screen.getByRole('combobox').getAttribute('aria-activedescendant');
    expect(id).toBeTruthy();
    return document.getElementById(id);
  });
}

afterEach(() => {
  window.localStorage.clear();
});

describe('SearchBar keyboard navigation', () => {
  it('arrows down the results and opens the highlighted one on Enter', async () => {
    const onSearchSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<SearchBarHost onSearchSelect={onSearchSelect} />);

    const input = screen.getByRole('combobox');
    await user.type(input, 'temple');

    expect(await screen.findAllByRole('option')).toHaveLength(4);
    // Nothing is highlighted until the visitor arrows to it. The highlight
    // wears the ring that *is* the focus indicator for
    // `aria-activedescendant`, so pre-selecting row zero put a focus ring on a
    // row nobody had navigated to, from typing alone.
    expect(document.querySelector('.suggestion-item.active')).toBeNull();

    // First press takes the first row, which is what every other combobox does.
    await user.keyboard('{ArrowDown}');
    expect(await findHighlightedOption()).toHaveTextContent('Konark Sun Temple');

    await user.keyboard('{ArrowDown}{ArrowDown}');
    expect(await findHighlightedOption()).toHaveTextContent('Jagannath Temple');
    expect(screen.getAllByRole('option')[2]).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{Enter}');
    expect(onSearchSelect).toHaveBeenCalledTimes(1);
    expect(onSearchSelect).toHaveBeenCalledWith(SITES[2]);
  });

  it('still opens the top result on Enter when nobody has arrowed', async () => {
    /*
      The reason removing the pre-selected highlight is safe. `handleSearch`
      falls back to `allSuggestions[0]` when nothing is active, and the keydown
      handler only intercepts Enter when something *is* -- so typing and
      pressing Enter reaches the form submit and opens the top result, exactly
      as it did when row zero came pre-highlighted.
    */
    const onSearchSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<SearchBarHost onSearchSelect={onSearchSelect} />);

    const input = screen.getByRole('combobox');
    await user.type(input, 'temple');
    expect(await screen.findAllByRole('option')).toHaveLength(4);
    expect(document.querySelector('.suggestion-item.active')).toBeNull();

    await user.keyboard('{Enter}');
    expect(onSearchSelect).toHaveBeenCalledWith(SITES[0]);
  });

  it('leaves the highlight on its row when another character narrows the list', async () => {
    const onSearchSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<SearchBarHost onSearchSelect={onSearchSelect} />);

    const input = screen.getByRole('combobox');
    await user.type(input, 'p');
    expect(await screen.findAllByRole('option')).toHaveLength(4);

    // Three presses from nothing highlighted, where two sufficed when the
    // first row came pre-selected.
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');
    expect(await findHighlightedOption()).toHaveTextContent('Jagannath Temple');

    await user.type(input, 'u');
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(3));
    expect(await findHighlightedOption()).toHaveTextContent('Jagannath Temple');

    await user.keyboard('{Enter}');
    expect(onSearchSelect).toHaveBeenCalledWith(SITES[2]);
  });

  it('closes the list on Escape without choosing anything or clearing the query', async () => {
    const onSearchSelect = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<SearchBarHost onSearchSelect={onSearchSelect} />);

    const input = screen.getByRole('combobox');
    await user.type(input, 'temple');
    await screen.findAllByRole('option');
    expect(input).toHaveAttribute('aria-expanded', 'true');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(input).toHaveValue('temple');
    expect(onSearchSelect).not.toHaveBeenCalled();
  });
});
