export function resolveBuild(mode, input) {
  if (!['debug', 'release'].includes(mode)) throw new Error('Build mode must be debug or release.');
  if (!input && mode === 'release') throw new Error('Release requires XIAOWO_MOBILE_API_BASE=https://host/api/v1.');
  const url = new URL(input || 'http://127.0.0.1:8000/api/v1');
  const loopback = url.origin === 'http://127.0.0.1:8000';
  if (url.username || url.password || url.search || url.hash) throw new Error('API base must not contain credentials, query or fragment.');
  if (url.protocol !== 'https:' && !(mode === 'debug' && url.protocol === 'http:' && loopback)) {
    throw new Error('HTTPS required; only debug builds may use http://127.0.0.1:8000.');
  }
  url.pathname = url.pathname.replace(/\/+$/, '');
  if (url.pathname !== '/api/v1') throw new Error('API base path must be exactly /api/v1.');
  return { mode, apiBase: url.toString().replace(/\/$/, ''), debug: mode === 'debug' };
}

export function checkApkSize(bytes) {
  if (!Number.isSafeInteger(bytes) || bytes <= 0 || bytes > 25_000_000) {
    throw new Error(`APK must be nonempty and ≤25,000,000 bytes; got ${bytes}.`);
  }
}
