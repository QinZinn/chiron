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
export const KS_SOURCE_MODULES = ['mnemosyne', 'lexiflash', 'note_scan'] as const;
export type KsSourceModule = (typeof KS_SOURCE_MODULES)[number];

/** ks/settings.py MAX_NODE_LIMIT — above this KS answers 400, it does not truncate. */
export const KS_MAX_NODE_LIMIT = 500;

export interface OcrLine {
  text: string;
  confidence: number;
}

export interface OcrPage {
  source: string;
  number: number;
  text: string;
  lines: OcrLine[];
  /** null for a page where OCR found no text at all. */
  mean_confidence: number | null;
  low_confidence_count: number;
}

export interface Note {
  id: string;
  title: string;
  filenames: string[];
  text: string;
  status: 'draft' | 'extracted';
  transcript_id: string | null;
  created_at: string;
  updated_at: string;
  page_count: number;
  ocr_pages?: OcrPage[];
}

export interface ReviewConcept {
  id: string;
  title: string;
  subject: string;
  summary: string;
  status: 'pending_review' | 'accepted' | 'discarded';
  node_id: string | null;
  source_module?: string;
}

export interface AcceptResult {
  node_id: string;
  /** false: matched an existing concept (duplicate detection) rather than adding one. */
  created: boolean;
  candidates: { node_id: string; title: string; score: number }[];
}

/** OCR is CPU-bound in the ocr container; a long PDF takes minutes. */
const OCR_TIMEOUT_MS = 630_000;
const EXTRACT_TIMEOUT_MS = 190_000;

export interface ProxyStatus {
  ks: { url: string; tokenConfigured: boolean };
}

/** An approved edge between two concepts (KS `GET /edges`), both ends already merge-resolved. */
export interface KsEdge {
  id: string;
  from: string;
  to: string;
  relation_type: 'prerequisite' | 'related' | 'contrasts_with';
  /** related / contrasts_with have no direction. */
  symmetric: boolean;
}

export const ks = {
  listEdges: () => request<{ edges: KsEdge[] }>(S, '/api/ks/edges'),

  health: () => request<{ status: string }>(S, '/api/ks/health', { timeoutMs: 5_000 }),

  listNodes: (opts: { subject?: string; sourceModule?: KsSourceModule; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.subject) q.set('subject', opts.subject);
    if (opts.sourceModule) q.set('source_module', opts.sourceModule);
    q.set('limit', String(opts.limit ?? KS_MAX_NODE_LIMIT));
    return request<{ nodes: KsNode[] }>(S, `/api/ks/nodes?${q}`);
  },

  getNode: (id: string) => request<KsNode>(S, `/api/ks/nodes/${encodeURIComponent(id)}`),

  // ---- ghi chép scan
  scanNote: (files: File[], title?: string) => {
    const form = new FormData();
    for (const f of files) form.append('files', f, f.name);
    if (title?.trim()) form.append('title', title.trim());
    return request<Note>(S, '/api/ks/notes', { method: 'POST', body: form, timeoutMs: OCR_TIMEOUT_MS });
  },

  listNotes: () => request<{ notes: Note[] }>(S, '/api/ks/notes'),

  getNote: (id: string) =>
    request<Note & { concepts: ReviewConcept[] }>(S, `/api/ks/notes/${encodeURIComponent(id)}`),

  updateNote: (id: string, patch: { title?: string; text?: string }) =>
    request<Note>(S, `/api/ks/notes/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch }),

  extractNote: (id: string) =>
    request<{ extracted: number; concepts: ReviewConcept[] }>(S, `/api/ks/notes/${encodeURIComponent(id)}/extract`, {
      method: 'POST',
      timeoutMs: EXTRACT_TIMEOUT_MS,
    }),

  // ---- duyệt khái niệm
  editConcept: (id: string, patch: { title?: string; subject?: string; summary?: string }) =>
    request<ReviewConcept>(S, `/api/ks/extracted/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch }),

  acceptConcept: (id: string) =>
    request<AcceptResult>(S, `/api/ks/extracted/${encodeURIComponent(id)}/accept`, { method: 'POST' }),

  discardConcept: (id: string) =>
    request<{ discarded: boolean }>(S, `/api/ks/extracted/${encodeURIComponent(id)}/discard`, { method: 'POST' }),
};

/** What the proxy has been configured with (no secrets, just booleans + URLs). */
export async function proxyStatus(): Promise<ProxyStatus> {
  const res = await fetch('/api/status');
  if (!res.ok) throw new Error(`proxy /api/status: HTTP ${res.status}`);
  return res.json();
}
