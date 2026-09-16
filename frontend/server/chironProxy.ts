/**
 * Server-side proxy for the parts of Chiron that need a secret.
 *
 * Runs inside the Vite dev/preview server (Node), never in the browser. The
 * browser calls same-origin `/api/*`; this middleware attaches the credential
 * and forwards to the real upstream. Secrets therefore never reach DevTools or
 * the built bundle.
 *
 *   /api/status                  what is configured (booleans + non-secret URLs)
 *   /api/ks/health               → KS GET /health          (no auth upstream)
 *   /api/ks/nodes?…              → KS GET /nodes           (Bearer CHIRON_KS_TOKEN)
 *   /api/ks/nodes/{id}           → KS GET /nodes/{id}      (Bearer CHIRON_KS_TOKEN)
 *   /api/gcal/calendars          → withone.ai passthrough: Google calendarList
 *   /api/gcal/events?calendarId… → withone.ai passthrough: Google events.list
 *
 * Deliberately an allowlist, and GET-only. KS writes belong to Mnemosyne and
 * calendar writes belong to Horae; the frontend has no route that could do
 * either, rather than a route it merely promises not to use.
 *
 * Mnemosyne is NOT proxied: it holds no secret, and the browser calls it
 * directly under the CORS policy in Mnemosyne/backend/src/main.rs.
 */
import type { Connect, Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';

export interface ProxyEnv {
  ksUrl: string;
  ksToken: string;
  oneApiBase: string;
  oneSecret: string;
  gcalConnectionKey: string;
}

/**
 * withone.ai action ids for the two read-only Google Calendar operations. One
 * requires the action id on every passthrough call; these are the published
 * ids from withone.ai/knowledge/google-calendar.
 *
 * Passthrough paths are RELATIVE to the action's baseUrl, which for both is
 * https://www.googleapis.com/calendar/v3 (GET /v1/knowledge?_id=… returns it).
 * So it is /calendars/{id}/events — not Google's full /calendar/v3/… path,
 * which One forwards to a URL that answers 404 for events.
 */
const GCAL_ACTION = {
  calendarList: 'conn_mod_def::GJ6Rk8ghCfI::FsO0bmOYSHOsPSIvrcOZxQ',
  eventsList: 'conn_mod_def::GJ6RlnIYK20::YzuWSmaVQgurletRDNJavA',
} as const;

const KS_TIMEOUT_MS = 10_000;
const ONE_TIMEOUT_MS = 20_000;

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(JSON.stringify(body));
}

/** Forward one GET upstream and relay status + body verbatim. */
async function forward(
  res: ServerResponse,
  upstream: string,
  label: string,
  headers: Record<string, string>,
  timeoutMs: number,
): Promise<void> {
  try {
    const r = await fetch(upstream, {
      method: 'GET',
      headers: { Accept: 'application/json', ...headers },
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await r.text();
    res.statusCode = r.status;
    res.setHeader('content-type', r.headers.get('content-type') ?? 'application/json');
    res.setHeader('cache-control', 'no-store');
    res.end(body);
  } catch (err) {
    // Process down, wrong port, DNS, or timeout: the upstream never answered.
    // 502 with a machine-readable code so the UI can say "không kết nối được"
    // for that one area instead of treating it as a bug.
    const timedOut = err instanceof Error && err.name === 'TimeoutError';
    sendJson(res, 502, {
      error: timedOut ? 'upstream_timeout' : 'upstream_unreachable',
      detail: `${label}: ${timedOut ? `không phản hồi sau ${timeoutMs / 1000}s` : 'không kết nối được'}`,
    });
  }
}

function gcalMissing(env: ProxyEnv): string[] {
  const missing: string[] = [];
  if (!env.oneSecret) missing.push('CHIRON_ONE_SECRET');
  if (!env.gcalConnectionKey) missing.push('CHIRON_ONE_GCAL_CONNECTION_KEY');
  return missing;
}

function onePassthrough(env: ProxyEnv, path: string, query: URLSearchParams): string {
  const base = env.oneApiBase.replace(/\/+$/, '');
  const qs = query.toString();
  return `${base}/v1/passthrough${path}${qs ? `?${qs}` : ''}`;
}

function oneHeaders(env: ProxyEnv, actionId: string): Record<string, string> {
  return {
    'x-one-secret': env.oneSecret,
    'x-one-connection-key': env.gcalConnectionKey,
    'x-one-action-id': actionId,
  };
}

function createHandler(env: ProxyEnv): Connect.NextHandleFunction {
  return (req: IncomingMessage, res: ServerResponse, next: Connect.NextFunction) => {
    const url = new URL(req.url ?? '/', 'http://proxy.local');
    const path = url.pathname;
    if (!path.startsWith('/api/')) return next();

    if (req.method !== 'GET') {
      return sendJson(res, 405, { error: 'method_not_allowed', detail: 'proxy này chỉ cho phép GET' });
    }

    // ------------------------------------------------------------ status
    if (path === '/api/status') {
      return sendJson(res, 200, {
        ks: { url: env.ksUrl, tokenConfigured: Boolean(env.ksToken) },
        gcal: { configured: gcalMissing(env).length === 0, missing: gcalMissing(env) },
      });
    }

    // ------------------------------------------------------------ Knowledge Store
    if (path === '/api/ks/health') {
      return void forward(res, `${env.ksUrl}/health`, 'Knowledge Store', {}, KS_TIMEOUT_MS);
    }
    const nodeMatch = /^\/api\/ks\/nodes(?:\/([^/]+))?$/.exec(path);
    if (nodeMatch) {
      if (!env.ksToken) {
        return sendJson(res, 503, {
          error: 'not_configured',
          detail: 'CHIRON_KS_TOKEN đang trống trong frontend/.env',
        });
      }
      // The id segment is forwarded as the browser encoded it; the regex has
      // already excluded "/", so it cannot step outside /nodes/. KS itself
      // answers 400 for anything that is not a UUID.
      const upstream = nodeMatch[1]
        ? `${env.ksUrl}/nodes/${nodeMatch[1]}`
        : `${env.ksUrl}/nodes${url.search}`;
      return void forward(
        res,
        upstream,
        'Knowledge Store',
        { Authorization: `Bearer ${env.ksToken}` },
        KS_TIMEOUT_MS,
      );
    }

    // ------------------------------------------------------------ Google Calendar via withone.ai
    if (path === '/api/gcal/calendars' || path === '/api/gcal/events') {
      const missing = gcalMissing(env);
      if (missing.length > 0) {
        return sendJson(res, 503, {
          error: 'not_configured',
          detail: `Thiếu ${missing.join(', ')} trong frontend/.env`,
        });
      }

      if (path === '/api/gcal/calendars') {
        const q = new URLSearchParams({ maxResults: '250' });
        const pageToken = url.searchParams.get('pageToken');
        if (pageToken) q.set('pageToken', pageToken);
        return void forward(
          res,
          onePassthrough(env, '/users/me/calendarList', q),
          'withone.ai',
          oneHeaders(env, GCAL_ACTION.calendarList),
          ONE_TIMEOUT_MS,
        );
      }

      const calendarId = url.searchParams.get('calendarId');
      const timeMin = url.searchParams.get('timeMin');
      const timeMax = url.searchParams.get('timeMax');
      if (!calendarId || !timeMin || !timeMax) {
        return sendJson(res, 400, {
          error: 'invalid_request',
          detail: 'cần calendarId, timeMin và timeMax',
        });
      }
      const q = new URLSearchParams({
        singleEvents: 'true',
        orderBy: 'startTime',
        timeMin,
        timeMax,
        maxResults: '250',
      });
      const pageToken = url.searchParams.get('pageToken');
      if (pageToken) q.set('pageToken', pageToken);
      return void forward(
        res,
        onePassthrough(env, `/calendars/${encodeURIComponent(calendarId)}/events`, q),
        'withone.ai',
        oneHeaders(env, GCAL_ACTION.eventsList),
        ONE_TIMEOUT_MS,
      );
    }

    return sendJson(res, 404, { error: 'not_proxied', detail: `${path} không nằm trong danh sách proxy` });
  };
}

export function chironProxy(env: ProxyEnv): Plugin {
  const handler = createHandler(env);
  return {
    name: 'chiron-proxy',
    configureServer(server) {
      server.middlewares.use(handler);
    },
    configurePreviewServer(server) {
      server.middlewares.use(handler);
    },
  };
}
