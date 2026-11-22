import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import i18n from './i18n/config.js'
import EmbedRoot from './embed/EmbedRoot.jsx'
import { readEmbedConfig } from './embed/config.js'
import { LANGUAGES } from './utils/deployment.js'

const root = document.getElementById('root')
/*
  `/embed/<site>/` serves this same bundle with `data-embed-model` on `#root`
  (templates/tirtha/embed.html). Embed mode mounts the viewer alone: the
  router would match nothing under `/embed/` and render "Page not found".
*/
const embed = readEmbedConfig(root)

if (embed) {
  const language = document.documentElement.lang
  if (LANGUAGES.includes(language) && i18n.language !== language) {
    i18n.changeLanguage(language)
  }
  createRoot(root).render(
    <StrictMode>
      <EmbedRoot modelUrl={embed.modelUrl} siteId={embed.siteId} runId={embed.runId} />
    </StrictMode>,
  )
} else {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
