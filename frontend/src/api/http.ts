/**
 * One fetch wrapper for every backend, so that every failure arrives as an
 * ApiError with a `kind` the UI can switch on:
 *
 *   unreachable      the service never answered (down, wrong port, blocked by CORS)
 *   timeout          it did not answer in time
 *   not_configured   the frontend's own .env is missing something (proxy says so)
 *   unauthenticated  no token, or the token was rejected (Mnemosyne 401)
 *   http             it answered with an error status; `status` + `message` set
 *
 * Views render `unreachable` as "không kết nối được <module>" for their own
 * area only — one module down never takes the rest of the app with it.
 */

import { getToken } from './session';

export type Service = 'Mnemosyne' | 'Knowledge Store';

export type ApiErrorKind = 'unreachable' | 'timeout' | 'not_configured' | 'http' | 'unauthenticated';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly service: Service;
  readonly status?: number;
  readonly code?: string;

  constructor(opts: { kind: ApiErrorKind; service: Service; message: string; status?: number; code?: string }) {
    super(opts.message);
    this.name = 'ApiError';
    this.kind = opts.kind;
    this.service = opts.service;
    this.status = opts.status;
    this.code = opts.code;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** LLM-backed calls (Socratic, quiz generation) legitimately take tens of seconds. */
  timeoutMs?: number;
  /** Skip the bearer token — only /health, which Mnemosyne serves to anyone. */
  anonymous?: boolean;
  signal?: AbortSignal;
}

/** Pull a human message out of the error bodies the three backends use. */
function messageFrom(body: unknown, fallback: string): { message: string; code?: string } {
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>;
    // Mnemosyne: {"error": "<message>"}; KS and the proxy: {"error": code, "detail": msg}.
    if (typeof b.detail === 'string') {
      return { message: b.detail, code: typeof b.error === 'string' ? b.error : undefined };
    }
    if (typeof b.error === 'string') return { message: b.error };
    if (typeof b.message === 'string') return { message: b.message };
  }
  if (typeof body === 'string' && body.trim()) return { message: body.trim().slice(0, 300) };
  return { message: fallback };
}

export async function request<T>(service: Service, url: string, opts: RequestOptions = {}): Promise<T> {
  // Mnemosyne is the only service the browser authenticates to directly; the
  // others are reached through the proxy, which holds their credentials.
  const headers: Record<string, string> = {};
  // FormData (file uploads) sets its own multipart Content-Type with the
  // boundary; setting it here would strip the boundary and break the upload.
  const isForm = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  if (opts.body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
  if (service === 'Mnemosyne' && !opts.anonymous) {
    const token = getToken();
    if (!token) {
      throw new ApiError({
        kind: 'unauthenticated',
        service,
        message: 'Chưa có token Mnemosyne — dán token vào Cài đặt.',
      });
    }
    headers.Authorization = `Bearer ${token}`;
  }

  const timeoutMs = opts.timeoutMs ?? 15_000;
  const timeout = AbortSignal.timeout(timeoutMs);
  const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout;

  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
      signal,
    });
  } catch (err) {
    if (opts.signal?.aborted) throw err; // caller cancelled; not an outage
    if (timeout.aborted) {
      throw new ApiError({
        kind: 'timeout',
        service,
        message: `${service} không phản hồi sau ${Math.round(timeoutMs / 1000)} giây.`,
      });
    }
    // fetch() rejects with a TypeError for a refused connection AND for a CORS
    // rejection; the browser does not let script tell the two apart.
    throw new ApiError({ kind: 'unreachable', service, message: `Không kết nối được ${service}.` });
  }

  const text = await res.text();
  let body: unknown = text;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      /* keep raw text */
    }
  }

  if (!res.ok) {
    const { message, code } = messageFrom(body, `${service} trả về HTTP ${res.status}.`);
    // 401 from Mnemosyne means the token is wrong or revoked — a different
    // problem from an outage, and a different thing to tell the learner.
    if (res.status === 401) {
      throw new ApiError({ kind: 'unauthenticated', service, message, status: 401 });
    }
    // The proxy reports its own conditions with codes; map them to kinds.
    if (code === 'not_configured') {
      throw new ApiError({ kind: 'not_configured', service, message, status: res.status, code });
    }
    if (code === 'upstream_unreachable') {
      throw new ApiError({ kind: 'unreachable', service, message: `Không kết nối được ${service}.`, status: res.status, code });
    }
    if (code === 'upstream_timeout') {
      throw new ApiError({ kind: 'timeout', service, message, status: res.status, code });
    }
    throw new ApiError({ kind: 'http', service, message, status: res.status, code });
  }
  return body as T;
}
