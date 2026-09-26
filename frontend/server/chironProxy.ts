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
 *   /api/ks/edges?…              → KS GET /edges           (approved edges, for the concept map)
 *   /api/ks/notes…, /api/ks/extracted…
 *                                → note scan and concept review (KS_WRITE_ROUTES)
 *
 * Deliberately an allowlist of (method, path) pairs. The only KS writes open
 * here are the note-scan flow and its review step — and even those cannot put
 * a node into KS directly: concepts wait for an explicit accept.
 *
 * Mnemosyne is NOT proxied: it holds no secret, and the browser calls it
 * directly under the CORS policy in Mnemosyne/backend/src/main.rs.
 */
import type { Connect, Plugin } from 'vite';
import type { IncomingMessage, ServerResponse } from 'node:http';

export interface ProxyEnv {
  ksUrl: string;
  ksToken: string;
}

const KS_TIMEOUT_MS = 10_000;
// OCR runs on CPU: a 30-page PDF can take minutes. Must exceed KS's own OCR
// timeout (ks/settings.py OCR_TIMEOUT_SECONDS = 600), or the proxy gives up
// on a scan that KS is still going to finish.
const KS_OCR_TIMEOUT_MS = 620_000;
// Concept extraction is one LLM call on a reasoning model.
const KS_EXTRACT_TIMEOUT_MS = 180_000;
// Matches KS MAX_NOTE_UPLOAD_BYTES; refused here before a byte reaches KS.
const MAX_UPLOAD_BYTES = 40 * 1024 * 1024;

/**
 * The KS routes beyond plain reads. Each entry is one method on one path shape;
 * anything else under /api/ks is refused, whatever its method.
 */
const KS_WRITE_ROUTES: { method: string; pattern: RegExp; timeoutMs: number }[] = [
  { method: 'GET', pattern: /^\/notes$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'POST', pattern: /^\/notes$/, timeoutMs: KS_OCR_TIMEOUT_MS },
  { method: 'GET', pattern: /^\/notes\/[^/]+$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'PATCH', pattern: /^\/notes\/[^/]+$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'POST', pattern: /^\/notes\/[^/]+\/extract$/, timeoutMs: KS_EXTRACT_TIMEOUT_MS },
  { method: 'GET', pattern: /^\/extracted$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'GET', pattern: /^\/extracted\/[^/]+\/candidates$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'PATCH', pattern: /^\/extracted\/[^/]+$/, timeoutMs: KS_TIMEOUT_MS },
  { method: 'POST', pattern: /^\/extracted\/[^/]+\/(accept|discard)$/, timeoutMs: KS_TIMEOUT_MS },
];

class BodyTooLarge extends Error {}

/** Read a request body, refusing past the limit instead of buffering it all first. */
async function readBody(req: IncomingMessage, limit: number): Promise<Buffer> {
  const declared = Number(req.headers['content-length'] ?? 0);
  if (declared > limit) throw new BodyTooLarge();
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new BodyTooLarge();
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks);
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(JSON.stringify(body));
}

/** Forward one request upstream and relay status + body verbatim. */
async function forward(
  res: ServerResponse,
  upstream: string,
  label: string,
  headers: Record<string, string>,
  timeoutMs: number,
  init: { method?: string; body?: Buffer; contentType?: string } = {},
): Promise<void> {
  try {
    const r = await fetch(upstream, {
      method: init.method ?? 'GET',
      headers: {
        Accept: 'application/json',
        ...(init.contentType ? { 'Content-Type': init.contentType } : {}),
        ...headers,
      },
      body: init.body && init.body.length > 0 ? init.body : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
    const body = await r.text();
    res.statusCode = r.status;
    res.setHeader('content-type', r.headers.get('content-type') ?? 'application/json');
    res.setHeader('cache-control', 'no-store');
    res.end(body);
  } catch (err) {
    // Process down, wrong port, DNS, or timeout: the upstream never answered.
    // 502 with a machine-readable code so the UI can say "cannot reach"
    // for that one area instead of treating it as a bug.
    const timedOut = err instanceof Error && err.name === 'TimeoutError';
    sendJson(res, 502, {
      error: timedOut ? 'upstream_timeout' : 'upstream_unreachable',
      detail: `${label}: ${timedOut ? `no response after ${timeoutMs / 1000}s` : 'unreachable'}`,
    });
  }
}

function createHandler(env: ProxyEnv): Connect.NextHandleFunction {
  return async (req: IncomingMessage, res: ServerResponse, next: Connect.NextFunction) => {
    const url = new URL(req.url ?? '/', 'http://proxy.local');
    const path = url.pathname;
    if (!path.startsWith('/api/')) return next();

    // ------------------------------------------------------------ KS: note scan + review
    // Matched before the GET-only guard below because these are the only
    // non-GET routes the proxy has; everything else stays read-only.
    if (path.startsWith('/api/ks/notes') || path.startsWith('/api/ks/extracted')) {
      const sub = path.slice('/api/ks'.length);
      const route = KS_WRITE_ROUTES.find((r) => r.method === req.method && r.pattern.test(sub));
      if (!route) {
        return sendJson(res, 405, { error: 'method_not_allowed', detail: `${req.method} ${path} is not on the proxy allowlist` });
      }
      if (!env.ksToken) {
        return sendJson(res, 503, { error: 'not_configured', detail: 'CHIRON_KS_TOKEN is empty in frontend/.env' });
      }
      let body: Buffer | undefined;
      if (req.method !== 'GET') {
        try {
          body = await readBody(req, MAX_UPLOAD_BYTES);
        } catch (err) {
          if (err instanceof BodyTooLarge) {
            return sendJson(res, 413, { error: 'too_large', detail: `Upload limit is ${MAX_UPLOAD_BYTES / 1024 / 1024} MB in total` });
          }
          throw err;
        }
      }
      return void forward(
        res,
        `${env.ksUrl}${sub}${url.search}`,
        'Knowledge Store',
        { Authorization: `Bearer ${env.ksToken}` },
        route.timeoutMs,
        { method: req.method, body, contentType: req.headers['content-type'] },
      );
    }

    if (req.method !== 'GET') {
      return sendJson(res, 405, { error: 'method_not_allowed', detail: 'this proxy only allows GET here' });
    }

    // ------------------------------------------------------------ status
    if (path === '/api/status') {
      return sendJson(res, 200, {
        ks: { url: env.ksUrl, tokenConfigured: Boolean(env.ksToken) },
      });
    }

    // ------------------------------------------------------------ Knowledge Store
    if (path === '/api/ks/health') {
      return void forward(res, `${env.ksUrl}/health`, 'Knowledge Store', {}, KS_TIMEOUT_MS);
    }
    if (path === '/api/ks/edges') {
      if (!env.ksToken) {
        return sendJson(res, 503, { error: 'not_configured', detail: 'CHIRON_KS_TOKEN is empty in frontend/.env' });
      }
      return void forward(res, `${env.ksUrl}/edges${url.search}`, 'Knowledge Store', { Authorization: `Bearer ${env.ksToken}` }, KS_TIMEOUT_MS);
    }
    const nodeMatch = /^\/api\/ks\/nodes(?:\/([^/]+))?$/.exec(path);
    if (nodeMatch) {
      if (!env.ksToken) {
        return sendJson(res, 503, {
          error: 'not_configured',
          detail: 'CHIRON_KS_TOKEN is empty in frontend/.env',
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

    return sendJson(res, 404, { error: 'not_proxied', detail: `${path} is not on the proxy allowlist` });
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
