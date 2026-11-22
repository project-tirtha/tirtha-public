/** What `templates/tirtha/embed.html` puts on `#root`. */
export function readEmbedConfig(root) {
  if (!root || !root.hasAttribute('data-embed-model')) return null;
  return {
    modelUrl: root.getAttribute('data-embed-model') || '',
    kind: root.getAttribute('data-embed-kind') || '',
    siteId: root.getAttribute('data-embed-site') || '',
    runId: root.getAttribute('data-embed-run') || '',
  };
}
