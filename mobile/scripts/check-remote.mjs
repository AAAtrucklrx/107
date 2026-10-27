#!/usr/bin/env node
// Node >=20. Usage: node mobile/scripts/check-remote.mjs https://host/api/v1
// Creates one anonymous session and one public chat run; never imports school credentials.
const ORIGIN = 'https://localhost';
const WRONG_ORIGIN = 'https://localhost.invalid';
const REQUEST_MS = 30_000;
const STREAM_MS = 180_000;
const STREAM_IDLE_MS = 45_000;
const MAX_BODY = 1_048_576;
const EVENT_TYPES = ['run.created', 'stage.changed', 'data.table', 'answer.delta', 'answer.completed', 'run.failed', 'run.cancelled'];

class CheckFailure extends Error {}
function check(condition, code) {
  if (!condition) throw new CheckFailure(code);
}
function reportFailure(step, error) {
  // Fetch/JSON errors may contain URLs or response data: never print them or their causes.
  console.error(`FAIL step=${step} code=${error instanceof CheckFailure ? error.message : 'REQUEST_OR_PROTOCOL_ERROR'}`);
  process.exitCode = 1;
}
function apiBase(args) {
  check(args.length === 1, 'ONE_HTTPS_API_BASE_REQUIRED');
  const input = args[0];
  check(/^https:\/\/[^/?#@\\\s]+\/api\/v1$/.test(input), 'INVALID_HTTPS_API_BASE');
  let url;
  try { url = new URL(input); } catch { throw new CheckFailure('INVALID_HTTPS_API_BASE'); }
  check(url.protocol === 'https:' && url.pathname === '/api/v1' && !url.username && !url.password && !url.search && !url.hash, 'INVALID_HTTPS_API_BASE');
  check(process.env.NODE_TLS_REJECT_UNAUTHORIZED !== '0', 'TLS_VERIFICATION_REQUIRED');
  return `${url.origin}/api/v1`;
}
function noCookies(response) {
  check(!response.headers.has('set-cookie'), 'UNEXPECTED_SET_COOKIE');
}
function cors(response) {
  noCookies(response);
  check(response.headers.get('access-control-allow-origin') === ORIGIN, 'EXACT_ORIGIN_REQUIRED');
  check(!response.headers.has('access-control-allow-credentials'), 'CREDENTIALS_MUST_BE_DISABLED');
}
function status(response, expected) {
  check(response.status === expected, 'UNEXPECTED_HTTP_STATUS');
}
async function json(response) {
  check(response.headers.get('content-type')?.split(';')[0].trim() === 'application/json', 'JSON_CONTENT_TYPE_REQUIRED');
  check(response.body, 'BODY_REQUIRED');
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0;
  let body = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      check(bytes <= MAX_BODY, 'JSON_BODY_TOO_LARGE');
      body += decoder.decode(value, { stream: true });
    }
    body += decoder.decode();
    return JSON.parse(body);
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
async function request(base, step, path, options, consume, timeout = REQUEST_MS) {
  const controller = new AbortController();
  let expired = false;
  const timer = setTimeout(() => { expired = true; controller.abort(); }, timeout);
  let response;
  try {
    response = await fetch(`${base}${path}`, {
      ...options,
      headers: { Origin: ORIGIN, Accept: 'application/json', ...options.headers },
      credentials: 'omit', redirect: 'error', signal: controller.signal,
    });
    console.log(`HTTP step=${step} status=${response.status}`);
    return await consume(response, controller);
  } catch (error) {
    if (expired) throw new CheckFailure('REQUEST_TIMEOUT');
    throw error;
  } finally {
    clearTimeout(timer);
    await response?.body?.cancel().catch(() => {});
  }
}
async function stream(response, controller, runId) {
  status(response, 200);
  cors(response);
  check(response.headers.get('content-type')?.split(';')[0].trim() === 'text/event-stream', 'SSE_CONTENT_TYPE_REQUIRED');
  check(response.body, 'SSE_BODY_REQUIRED');
  const counts = Object.fromEntries([...EVENT_TYPES, 'other'].map(type => [type, 0]));
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let buffer = '';
  let bytes = 0;
  let lastId = 0;
  let terminal = false;
  let answer = false;
  let idleTimer;
  let idleExpired = false;
  const resetIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { idleExpired = true; controller.abort(); }, STREAM_IDLE_MS);
  };
  function frame(text) {
    const lines = text.split(/\r?\n/);
    const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart());
    if (!data.length) return; // Keepalive comments contain no event data.
    check(!terminal, 'EVENT_AFTER_TERMINAL');
    const event = JSON.parse(data.join('\n'));
    const named = lines.find(line => line.startsWith('event:'))?.slice(6).trim();
    check(event && typeof event.type === 'string' && event.type === named && event.run_id === runId, 'INVALID_SSE_ENVELOPE');
    check(Number.isSafeInteger(event.id) && event.id > lastId, 'INVALID_SSE_SEQUENCE');
    lastId = event.id;
    counts[EVENT_TYPES.includes(event.type) ? event.type : 'other'] += 1;
    if (event.type === 'answer.delta' && typeof event.data?.delta === 'string' && event.data.delta.trim()) answer = true;
    check(event.type !== 'run.failed' && event.type !== 'run.cancelled', 'SSE_UNSUCCESSFUL_TERMINAL');
    if (event.type === 'answer.completed') {
      check(event.data?.stage === 'completed' && event.data?.truncated === false, 'SSE_INCOMPLETE_ANSWER');
      terminal = true;
    }
  }
  resetIdle();
  try {
    while (true) {
      const { value, done } = await reader.read();
      resetIdle();
      if (value) bytes += value.byteLength;
      check(bytes <= MAX_BODY * 4, 'SSE_BODY_TOO_LARGE');
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        frame(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
      }
      check(buffer.length <= MAX_BODY, 'SSE_FRAME_TOO_LARGE');
      if (done) break;
    }
    check(!buffer.trim(), 'SSE_UNTERMINATED_FRAME');
    check(terminal && answer, 'SSE_SUCCESSFUL_ANSWER_REQUIRED');
  } catch (error) {
    if (idleExpired) throw new CheckFailure('SSE_IDLE_TIMEOUT');
    throw error;
  } finally {
    clearTimeout(idleTimer);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
    for (const [type, count] of Object.entries(counts)) {
      if (count) console.log(`SSE event=${type} count=${count}`);
    }
  }
}
async function main() {
  const base = apiBase(process.argv.slice(2));
  let token;
  let step = 'preflight';
  const bearer = () => ({ Authorization: `Bearer ${token}` });
  try {
    await request(base, step, '/auth/mobile/bootstrap', {
      method: 'OPTIONS', headers: { 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type' },
    }, response => {
      status(response, 200);
      cors(response);
      check(response.headers.get('access-control-allow-methods')?.split(',').map(value => value.trim()).includes('POST'), 'POST_PREFLIGHT_REQUIRED');
      const headers = response.headers.get('access-control-allow-headers')?.toLowerCase().split(',').map(value => value.trim()) ?? [];
      check(headers.includes('authorization') && headers.includes('content-type'), 'BEARER_PREFLIGHT_REQUIRED');
    });
    step = 'preflight-denied';
    await request(base, step, '/auth/mobile/bootstrap', {
      method: 'OPTIONS', headers: { Origin: WRONG_ORIGIN, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type' },
    }, response => {
      noCookies(response);
      check(!response.headers.has('access-control-allow-origin'), 'WRONG_ORIGIN_WAS_ALLOWED');
      check(response.status >= 400 && response.status < 500, 'WRONG_ORIGIN_NOT_REJECTED');
    });
    step = 'bootstrap';
    await request(base, step, '/auth/mobile/bootstrap', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }, async response => {
      status(response, 200);
      const payload = await json(response);
      check(typeof payload?.access_token === 'string' && /^[A-Za-z0-9_-]{43}$/.test(payload.access_token), 'INVALID_BOOTSTRAP_TOKEN');
      token = payload.access_token; // Capture before later assertions, so finally can revoke it.
      check(payload.token_type === 'Bearer', 'BEARER_TOKEN_REQUIRED');
      cors(response);
    });
    step = 'session';
    await request(base, step, '/auth/session', { headers: bearer() }, async response => {
      status(response, 200);
      cors(response);
      const payload = await json(response);
      check(payload?.principal?.auth_mode === 'anonymous' && payload.principal.authenticated === false && payload.principal.id === null && payload.principal.profile === null && payload.principal.is_admin === false, 'ANONYMOUS_SESSION_REQUIRED');
      check(payload.capabilities?.public_chat === true && ['personal_academic', 'server_history', 'admin_console', 'knowledge_review', 'production_publish'].every(key => payload.capabilities[key] === false), 'ANONYMOUS_CAPABILITIES_REQUIRED');
    });
    step = 'academic-denied';
    await request(base, step, '/academic/courses', { headers: bearer() }, response => { status(response, 401); cors(response); });
    step = 'chat-create';
    const run = await request(base, step, '/chat/runs', {
      method: 'POST', headers: { ...bearer(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: '请简短介绍一下小蜗助手能提供哪些公共功能。', mode: 'local', conversation_id: null }),
    }, async response => {
      status(response, 200);
      cors(response);
      const payload = await json(response);
      check(typeof payload?.run_id === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(payload.run_id), 'INVALID_RUN_ID');
      check(payload.events_url === `/chat/runs/${payload.run_id}/events`, 'INVALID_EVENTS_PATH');
      return payload;
    });
    step = 'chat-events';
    await request(base, step, run.events_url, { headers: { ...bearer(), Accept: 'text/event-stream' } }, (response, controller) => stream(response, controller, run.run_id), STREAM_MS);
  } catch (error) {
    reportFailure(step, error);
  } finally {
    if (token) {
      // Independently attempt both checks, even if an earlier check or logout fails.
      try {
        await request(base, 'logout', '/auth/logout', { method: 'POST', headers: { ...bearer(), 'Content-Type': 'application/json' }, body: '{}' }, async response => {
          status(response, 200);
          cors(response);
          check((await json(response))?.logged_out === true, 'LOGOUT_NOT_CONFIRMED');
        });
      } catch (error) { reportFailure('logout', error); }
      try {
        await request(base, 'revoked-session', '/auth/session', { headers: bearer() }, response => { status(response, 401); cors(response); });
      } catch (error) { reportFailure('revoked-session', error); }
      token = undefined;
    }
  }
  if (!process.exitCode) console.log('PASS remote-mobile-smoke');
}

await main().catch(error => reportFailure('configuration', error));
