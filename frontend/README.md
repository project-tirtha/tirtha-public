# Tirtha Frontend

The React application behind the [Tirtha](https://smlab.niser.ac.in/project/tirtha/) website: a map of heritage sites, a Gaussian-splat 3D viewer, the contribution (upload) flow, site requests and contributor accounts. It is served by Django: the Django shell template reads Vite's manifest and loads the built bundle, so the site works with server-rendered metadata for link previews and crawlers.

## Stack

- **React 19** with **React Router 7**
- **Vite 8** (build and dev server), **Tailwind CSS 4** plus CSS custom properties for theming
- **MapLibre GL JS** for the map (state boundaries from `public/India/INDIA_STATES.geojson`)
- **three.js** with **@react-three/fiber**/**drei** and **@mkkellogg/gaussian-splats-3d** for the splat viewer; KTX2 textures are transcoded by the **Basis Universal** transcoder in `public/basis/`
- **i18next** / **react-i18next**: English, Odia and Hindi
- **axios** for the Django JSON API; **exifr**, **heic2any** and **browser-image-compression** for client-side image handling during upload
- **ESLint 10** for linting; **Vitest 4** + Testing Library + jsdom + axe-core for tests

## Requirements

- Node.js 24 and npm 11

## Commands

```bash
npm ci              # install exactly what package-lock.json pins
npm run dev         # Vite dev server with HMR (http://localhost:5173)
npm run lint        # ESLint
npx vitest run      # the test suite (same as `npm test`)
npm run build       # production build into dist/, with dist/.vite/manifest.json
npm run preview     # serve the production build locally
```

`TIRTHA_STATIC_BASE` sets the public URL prefix the built chunks are loaded from. It defaults to the production prefix (`/project/tirtha/static/`); a deployment served under another `PRE_URL` must set it at build time, e.g. `TIRTHA_STATIC_BASE="/${PRE_URL}static/" npm run build`.

## Configuration

Copy `.env.example` to `.env` for local development and set `VITE_GOOGLE_CLIENT_ID` to your Google OAuth client ID (used for contributor sign-in). Never commit `.env`.

## Layout

```
src/
  components/   Map, viewer, sidebar, contribute/upload and request dialogs, ...
  pages/        Home (the map + viewer page)
  embed/        The chrome-free embeddable viewer
  routes/       Route definitions
  services/     API clients (sites, documents)
  utils/        Upload batching, image validation, geocoding, storage, ...
  hooks/        Shared React hooks
  context/      Theme context
  i18n/, locales/  Translation configuration and strings
  styles/       Component CSS and theme variables
  test/         Test setup and helpers
public/
  basis/        Basis Universal transcoder (Apache-2.0)
  India/        State boundaries for the map (see ../THIRD_PARTY_NOTICES.md)
```

## License

Part of Tirtha, licensed under the GNU Affero General Public License v3.0 or later. See [`../LICENSE`](../LICENSE) and [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
