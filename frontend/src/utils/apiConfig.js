import { deploymentPrefix } from './deployment';

/**
 * Where the API lives, read at runtime rather than compiled in.
 *
 * Django serves this app under a path prefix (`PRE_URL`) that differs per
 * deployment -- `/project/tirtha/` in production, `/project/tirthademo/` on the
 * demo, `/beta/tirtha/` on beta. The shell template emits the live value as a
 * meta tag, so one build serves all of them. `VITE_API_BASE` still wins when
 * set, which is what the standalone `npm run dev` server uses.
 */
/*
  The fallback matters more than it looks. It was `''`, so with no meta tag
  every call became a bare `/sites/` -- a path that exists on no deployment, and
  a 404 that reads as "the API is down" rather than "the prefix is missing".
  `/api/v1` is at least right for a deployment mounted at the root, which is
  what the Vite dev server proxies to.
*/
const DEFAULT_API_BASE = '/api/v1';

function prefixFromDocument() {
	const prefix = deploymentPrefix();
	return prefix ? `${prefix}/api/v1` : DEFAULT_API_BASE;
}

export function getApiBaseUrl() {
	const configured = import.meta.env.VITE_API_BASE;
	if (configured) return configured.replace(/\/$/, '');
	return prefixFromDocument();
}

export function logApiCall(endpoint, method, details = null) {
	if (!import.meta.env.DEV) {
		return;
	}

	if (details) {
		console.log(`[api] ${method} ${endpoint}`, details);
		return;
	}

	console.log(`[api] ${method} ${endpoint}`);
}