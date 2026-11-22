/**
 * The panel: the archive it lists, and its three quiet failures.
 *
 * Merged from two files that arrived together, each with its own fixture for
 * the same component -- which is precisely the divergence the rest of this pass
 * has been removing. One `SITES`, one set of props, two rendering styles kept
 * only where each is needed: `renderWithProviders` for the list, and plain
 * `render`/`rerender` for the tests that drive a prop change.
 *
 * The archive, listed (internal design notes).
 *
 * This branch used to render `null`, which is how every visit starts, so the
 * only way to find a site was to already know its name. It is worth a test
 * because it is the one place the whole list is readable, and because the row
 * is the second path into `onSearchSelect` -- the search box is the first, and
 * a list that draws the right names while handing the wrong site to the map
 * looks entirely correct until you click it.
 *
 * The status word beside each row is asserted too. It exists so that a reader
 * who cannot see the pin's colour still learns the state (WCAG 1.4.1), and a
 * missing string there is invisible on screen.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { I18nextProvider } from 'react-i18next';

import Sidebar from './Sidebar';
import { ThemeProvider } from '../context/ThemeContext';
import { fetchSiteStats } from '../services/sites.service';
import { markerFor } from '../utils/siteStatus';
import { renderWithProviders, testI18n } from '../test/utils';

vi.mock('../services/sites.service', () => ({ fetchSiteStats: vi.fn() }));

vi.mock('../utils/siteStatus', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, markerFor: vi.fn(actual.markerFor) };
});

const SITES = [
  { id: 'kon', name: 'Konark Sun Temple', location: 'Puri, Odisha', mapStatus: 'published' },
  {
    id: 'lin',
    name: 'Lingaraj Temple',
    location: 'Bhubaneswar, Odisha',
    mapStatus: 'in_progress',
  },
  { id: 'jag', name: 'Jagannath Temple', location: 'Puri, Odisha', mapStatus: 'needs_images' },
];

/** The search state `Home` owns; the panel only passes it through. */
function props(overrides = {}) {
  return {
    isVisible: true,
    templeList: SITES,
    selectedTemple: null,
    searchQuery: '',
    setSearchQuery: vi.fn(),
    isSearchFocused: false,
    setIsSearchFocused: vi.fn(),
    searchActiveIndex: -1,
    setSearchActiveIndex: vi.fn(),
    ...overrides,
  };
}

/*
  The same providers `renderWithProviders` supplies, as an element rather than a
  call, because these three tests drive a *prop change* and so need `rerender`
  with a new element. Without the i18n provider every `t()` returns its own key,
  so an assertion on visible text would be comparing "stats.loading" to
  "Loading…" and reporting it as a missing element.
*/
const i18n = testI18n('en');

function panel(overrides = {}) {
  return (
    <I18nextProvider i18n={i18n}>
      <ThemeProvider>
        <Sidebar {...props(overrides)} />
      </ThemeProvider>
    </I18nextProvider>
  );
}

/** The rejection `fetchSiteStats` re-throws when its signal is aborted. */
function abortError() {
  const error = new Error('The operation was aborted.');
  error.name = 'AbortError';
  return error;
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchSiteStats.mockImplementation(() => new Promise(() => {}));
});

function renderSidebar({ onSearchSelect = vi.fn(), language = 'en' } = {}) {
  renderWithProviders(
    <Sidebar
      isVisible
      templeList={SITES}
      onSearchSelect={onSearchSelect}
      selectedTemple={null}
      searchQuery=""
      setSearchQuery={vi.fn()}
      isSearchFocused={false}
      setIsSearchFocused={vi.fn()}
      searchActiveIndex={-1}
      setSearchActiveIndex={vi.fn()}
    />,
    { language },
  );
  return onSearchSelect;
}

describe('Sidebar site list', () => {
  it('lists every site with its place and its status in words', () => {
    renderSidebar();

    expect(screen.getByRole('heading', { name: '3 sites on the map' })).toBeInTheDocument();

    for (const site of SITES) {
      const row = screen.getByRole('button', { name: new RegExp(site.name) });
      expect(row).toHaveTextContent(site.location);
    }

    expect(screen.getByRole('button', { name: /Konark Sun Temple/ })).toHaveTextContent(
      'model available',
    );
    expect(screen.getByRole('button', { name: /Lingaraj Temple/ })).toHaveTextContent(
      'reconstruction in progress',
    );
    expect(screen.getByRole('button', { name: /Jagannath Temple/ })).toHaveTextContent(
      'needs images',
    );
  });

  it('opens the site whose row was clicked, not another one', async () => {
    const user = userEvent.setup();
    const onSearchSelect = renderSidebar();

    await user.click(screen.getByRole('button', { name: /Lingaraj Temple/ }));

    expect(onSearchSelect).toHaveBeenCalledTimes(1);
    expect(onSearchSelect).toHaveBeenCalledWith(SITES[1]);
  });

  it('renders the panel chrome in Odia rather than falling back to English', () => {
    renderSidebar({ language: 'or' });

    expect(
      screen.getByRole('heading', { name: 'ମାନଚିତ୍ରରେ 3ଟି ସ୍ଥାନ' }),
    ).toBeInTheDocument();
  });
});

describe('the collapsed panel', () => {
  it('is inert, so its controls leave the tab order with it', () => {
    // jsdom does not implement what `inert` *does* to focus, so this asserts
    // the attribute is on the element the stylesheet clips to zero width --
    // which is the whole of the fix the browser acts on.
    const { container, rerender } = render(panel({ isVisible: false }));

    expect(container.querySelector('aside.sidebar')).toHaveAttribute('inert');
    // Every site is still in the markup, which is why the attribute matters:
    // without it these are ~50 Tab stops on a panel that is not on screen.
    expect(container.querySelectorAll('.sidebar-site-row').length).toBe(SITES.length);

    rerender(panel({ isVisible: true }));
    expect(container.querySelector('aside.sidebar')).not.toHaveAttribute('inert');
  });
});

describe('switching site while stats are in flight', () => {
  it('keeps the new site loading rather than reporting the empty figures', async () => {
    fetchSiteStats.mockImplementation(
      (siteId, signal) =>
        new Promise((resolve, reject) => {
          signal.addEventListener('abort', () => reject(abortError()));
        }),
    );

    // From the list, as a visitor arrives: it is the *selection change* that
    // puts the panel into its loading state.
    const { rerender } = render(panel());
    await act(async () => rerender(panel({ selectedTemple: SITES[0] })));
    expect(screen.getByText('Loading…')).toBeInTheDocument();

    // Konark's request is aborted here; Lingaraj's is still outstanding.
    await act(async () => rerender(panel({ selectedTemple: SITES[1] })));

    expect(screen.getByText('Loading…')).toBeInTheDocument();
    expect(screen.queryByText('No contributions yet.')).not.toBeInTheDocument();
  });
});

describe('dragging the resize handle', () => {
  it('moves the edge without re-rendering the site list', () => {
    const { container } = render(panel());
    const aside = container.querySelector('aside.sidebar');

    fireEvent.mouseDown(container.querySelector('.sidebar-resize-handle'));
    markerFor.mockClear();

    fireEvent.mouseMove(window, { clientX: 340 });
    fireEvent.mouseMove(window, { clientX: 300 });

    // A render per event would rebuild every row's pin data URL; the drag
    // writes the width straight onto the panel instead.
    expect(markerFor).not.toHaveBeenCalled();
    expect(aside.style.width).toBe('300px');

    // On release the state catches up, and the panel keeps the width it was
    // dragged to rather than snapping back to the last committed one.
    fireEvent.mouseUp(window);
    expect(aside.style.width).toBe('300px');
  });
});

/**
 * A site nobody has located yet is still part of the archive.
 *
 * `normalizeSite` used to return `null` for them, and `fetchSites` filtered
 * those out -- so 12 of 34 demo sites (29 of 59 in production) existed in no
 * surface at all, and the 19 ARKs minted against their runs resolved correctly
 * on the server and were then thrown away by the client.
 */
describe('sites with no coordinate', () => {
  const MIXED = [
    { id: 'kon', name: 'Konark Sun Temple', location: 'Puri, Odisha', mapStatus: 'published', placed: true },
    { id: 'gat', name: 'Gatiswara Temple', location: 'Khordha, Odisha', mapStatus: 'published', placed: false },
  ];

  it('lists them under their own heading rather than dropping them', () => {
    renderWithProviders(<Sidebar {...props({ templeList: MIXED })} />);

    // "1 sites" -- the heading had one form and counted with it. `notLocated`
    // beside it reads correctly at one, which is what made the other obvious.
    expect(screen.getByRole('heading', { name: '1 site on the map' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '1 not yet located' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Gatiswara Temple/ })).toBeInTheDocument();
  });

  it('says why the row has no pin, for a reader who cannot see that it has none', () => {
    renderWithProviders(<Sidebar {...props({ templeList: MIXED })} />);
    expect(screen.getByRole('button', { name: /Gatiswara Temple/ })).toHaveTextContent(
      'no coordinate yet',
    );
  });

  it('opens one when its row is clicked, the same as any other site', async () => {
    const onSearchSelect = vi.fn();
    renderWithProviders(<Sidebar {...props({ templeList: MIXED, onSearchSelect })} />);

    await userEvent.click(screen.getByRole('button', { name: /Gatiswara Temple/ }));
    expect(onSearchSelect).toHaveBeenCalledWith(MIXED[1]);
  });
});

/*
  The document links open in place, and are still links.

  The standalone pages are what an unfurl describes, what a person cites, and
  what a middle-click opens; a dialog can be none of those. So these stay `<a>`
  elements with their canonical `href`, and only a plain left-click is
  intercepted. The second test is the one that matters -- an implementation
  that simply called `preventDefault()` would pass the first and quietly take
  away every other way of opening the document.
*/
describe('the document links', () => {
  const documentLink = () =>
    screen.getByRole('link', { name: /terms of use/i });

  it('opens the document in place on a plain click', async () => {
    const onDocumentClick = vi.fn();
    renderWithProviders(
      <Sidebar
        isVisible
        templeList={SITES}
        onSearchSelect={vi.fn()}
        selectedTemple={null}
        searchQuery=""
        setSearchQuery={vi.fn()}
        isSearchFocused={false}
        setIsSearchFocused={vi.fn()}
        searchActiveIndex={-1}
        setSearchActiveIndex={vi.fn()}
        onDocumentClick={onDocumentClick}
      />,
    );

    const link = documentLink();
    // Still a real URL, whatever the click does.
    expect(link.getAttribute('href')).toMatch(/\/terms\/$/);

    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    await act(async () => {
      link.dispatchEvent(event);
    });
    expect(onDocumentClick).toHaveBeenCalledWith('terms', expect.any(String));
    expect(event.defaultPrevented, 'the navigation was not suppressed').toBe(true);
  });

  it('leaves a ctrl-click alone, so it still opens the page', async () => {
    const onDocumentClick = vi.fn();
    renderWithProviders(
      <Sidebar
        isVisible
        templeList={SITES}
        onSearchSelect={vi.fn()}
        selectedTemple={null}
        searchQuery=""
        setSearchQuery={vi.fn()}
        isSearchFocused={false}
        setIsSearchFocused={vi.fn()}
        searchActiveIndex={-1}
        setSearchActiveIndex={vi.fn()}
        onDocumentClick={onDocumentClick}
      />,
    );

    const event = new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
    });
    await act(async () => {
      documentLink().dispatchEvent(event);
    });
    expect(onDocumentClick).not.toHaveBeenCalled();
    expect(event.defaultPrevented, 'a ctrl-click was swallowed').toBe(false);
  });

  it('is a plain link when nothing is listening', async () => {
    // Every other consumer of this component passes no handler, and must get
    // working links rather than dead ones.
    renderSidebar();
    const event = new MouseEvent('click', { bubbles: true, cancelable: true });
    await act(async () => {
      documentLink().dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(false);
  });
});
