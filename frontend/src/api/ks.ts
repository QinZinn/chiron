/**
 * Knowledge Store (Python/Flask). Reached only through the Vite proxy at
 * /api/ks/*, which attaches the Bearer token server-side (server/chironProxy.ts).
 * Read-only: GET /health, /nodes, /nodes/{id}. Shapes mirror ks/http_app.py.
 */
import { request } from './http';

const S = 'Knowledge Store' as const;

export interface KsNode {
  id: string;
  title: string;
  subject: string;
  summary: string;
}

/** ks/models.py SourceModule. */
export const KS_SOURCE_MODULES = ['mnemosyne', 'lexiflash'] as const;
export type KsSourceModule = (typeof KS_SOURCE_MODULES)[number];

/** ks/settings.py MAX_NODE_LIMIT — above this KS answers 400, it does not truncate. */
export const KS_MAX_NODE_LIMIT = 500;

export interface ProxyStatus {
  ks: { url: string; tokenConfigured: boolean };
  gcal: { configured: boolean; missing: string[] };
}

export const ks = {
  health: () => request<{ status: string }>(S, '/api/ks/health', { timeoutMs: 5_000 }),

  listNodes: (opts: { subject?: string; sourceModule?: KsSourceModule; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.subject) q.set('subject', opts.subject);
    if (opts.sourceModule) q.set('source_module', opts.sourceModule);
    q.set('limit', String(opts.limit ?? KS_MAX_NODE_LIMIT));
    return request<{ nodes: KsNode[] }>(S, `/api/ks/nodes?${q}`);
  },

  getNode: (id: string) => request<KsNode>(S, `/api/ks/nodes/${encodeURIComponent(id)}`),
};

/** What the proxy has been configured with (no secrets, just booleans + URLs). */
export async function proxyStatus(): Promise<ProxyStatus> {
  const res = await fetch('/api/status');
  if (!res.ok) throw new Error(`proxy /api/status: HTTP ${res.status}`);
  return res.json();
}
