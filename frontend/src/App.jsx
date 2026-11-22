import { useTranslation } from "react-i18next";

import { ThemeProvider } from "./context/ThemeContext";
import AppRoutes from "./routes/Approutes";
import ThemeToggle from "./components/ThemeToggle";
import { REGION_ID } from './utils/announce';

function App() {
  const { t } = useTranslation();

  return (
    <ThemeProvider>
      {/*
        Skip link, first in the tab order (WCAG 2.4.1 Bypass Blocks). Without
        it a keyboard user tabs the theme toggle, the sidebar header, the search
        field and the whole site list before reaching the map on every visit.

        It is styled off-screen rather than hidden -- `display: none` would take
        it out of the tab order and defeat the point. See `.skip-link` in
        index.css.

        The label is translated: an accessible name left in English is an
        English-only label for anyone reading in Odia or Hindi (internal design notes).
      */}
      <a className="skip-link" href="#main">
        {t("a11y.skipToContent")}
      </a>
      {/*
        The page's one live region, mounted for the life of the app rather than
        created alongside each message -- see `utils/announce.js` for why a
        region that appears with its text announces nothing.
      */}
      <div id={REGION_ID} className="sr-only" role="status" aria-live="polite" />

      <ThemeToggle />
      <AppRoutes />
    </ThemeProvider>
  );
}

export default App;
