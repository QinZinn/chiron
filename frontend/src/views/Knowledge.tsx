/**
 * Knowledge — Knowledge Store GET /nodes and GET /nodes/{id}, via the proxy.
 * Nodes are concepts the learner has studied ("Newton's second law"), not sessions.
 *
 * Two views of the same filtered nodes: a list grouped by subject, and a
 * concept map that adds KS GET /edges (approved edges only).
 */
import { useMemo, useState } from 'react';
import { ks, KS_MAX_NODE_LIMIT, KS_SOURCE_MODULES, type KsNode, type KsSourceModule } from '../api/ks';
import { useAsync } from '../lib/useAsync';
import { href, navigate } from '../lib/route';
import { ErrorNotice, Loading, PageHeader } from '../components/ui';
import { ConceptMap } from '../components/ConceptMap';

const SOURCE_LABEL: Record<KsSourceModule, string> = { mnemosyne: 'Mnemosyne', lexiflash: 'LexiFlash', note_scan: 'Note scan' };

type ViewMode = 'list' | 'map';
const VIEW_KEY = 'chiron.knowledgeView';

function loadView(): ViewMode {
  try {
    return localStorage.getItem(VIEW_KEY) === 'map' ? 'map' : 'list';
  } catch {
    return 'list';
  }
}

export function KnowledgeView({ nodeId }: { nodeId?: string }) {
  const [view, setView] = useState<ViewMode>(loadView);
  const edgesQ = useAsync(() => ks.listEdges(), [view], view === 'map');
  const switchView = (v: ViewMode) => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      // Storage blocked: the choice lasts until the page reloads.
    }
  };
  const [subject, setSubject] = useState('');
  const [appliedSubject, setAppliedSubject] = useState('');
  const [sourceModule, setSourceModule] = useState<KsSourceModule | ''>('');
  const [search, setSearch] = useState('');

  const nodesQ = useAsync(
    () => ks.listNodes({ subject: appliedSubject || undefined, sourceModule: sourceModule || undefined, limit: KS_MAX_NODE_LIMIT }),
    [appliedSubject, sourceModule],
  );

  const nodes = nodesQ.data?.nodes;
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (nodes ?? []).filter((n) => !q || n.title.toLowerCase().includes(q) || n.summary.toLowerCase().includes(q));
  }, [nodes, search]);
  const groups = useMemo(() => {
    const m = new Map<string, KsNode[]>();
    for (const n of filtered) m.set(n.subject, [...(m.get(n.subject) ?? []), n]);
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'en'));
  }, [filtered]);
  const subjects = useMemo(() => new Set((nodes ?? []).map((n) => n.subject)).size, [nodes]);
  const truncated = nodes?.length === KS_MAX_NODE_LIMIT;

  return (
    <main className="main">
      <PageHeader title="Knowledge">
        <button className="btn btn-secondary btn-soft" onClick={nodesQ.reload}>
          <i className="ph ph-arrow-clockwise" />Reload
        </button>
      </PageHeader>
      <div className="page">
        <div className="page-head">
          <div>
            <h2>{nodes ? `${nodes.length}${truncated ? '+' : ''} concept${nodes.length === 1 ? '' : 's'}` : 'Concepts you have learned'}</h2>
            <p>From the Knowledge Store — a “second brain” of the concepts you have learned, extracted from Study sessions and scanned notes.</p>
          </div>
        </div>
        <div className="stats">
          <div><div className="stat-k">Concepts</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{nodes ? nodes.length : '—'}</div></div>
          <div><div className="stat-k">Subjects</div><div className="stat-v">{nodes ? subjects : '—'}</div></div>
        </div>
        <div className="toolbar" style={{ marginBottom: 16 }}>
          <div className="pomo-tabs" role="tablist" aria-label="View" style={{ minWidth: 220 }}>
            <button role="tab" aria-selected={view === 'list'} className={`pomo-tab${view === 'list' ? ' pomo-tab-on' : ''}`} onClick={() => switchView('list')}>
              <i className="ph ph-list-bullets" /> List
            </button>
            <button role="tab" aria-selected={view === 'map'} className={`pomo-tab${view === 'map' ? ' pomo-tab-on' : ''}`} onClick={() => switchView('map')}>
              <i className="ph ph-graph" /> Map
            </button>
          </div>
          <input className="input input-sm" style={{ width: 220 }} lang="en" placeholder="Search the results…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <form
            style={{ display: 'flex', gap: 6 }}
            onSubmit={(e) => {
              e.preventDefault();
              setAppliedSubject(subject.trim());
            }}
          >
            <input className="input input-sm" style={{ width: 180 }} lang="en" placeholder="Subject (exact match)" value={subject} onChange={(e) => setSubject(e.target.value)} />
            <button className="btn btn-secondary btn-soft" type="submit"><i className="ph ph-funnel" />Filter</button>
          </form>
          <select className="input" style={{ width: 'auto' }} value={sourceModule} onChange={(e) => setSourceModule(e.target.value as KsSourceModule | '')}>
            <option value="">All sources</option>
            {KS_SOURCE_MODULES.map((m) => <option key={m} value={m}>{SOURCE_LABEL[m]}</option>)}
          </select>
        </div>
        <hr className="rule" style={{ marginBottom: 18 }} />

        {nodesQ.loading && !nodes && <Loading label="Loading concepts from the Knowledge Store…" />}
        {nodesQ.error != null && <ErrorNotice error={nodesQ.error} onRetry={nodesQ.reload} />}
        {nodes && nodes.length === 0 && (
          <div className="notice notice-info">
            <i className="ph ph-info" />
            <div>
              <div className="notice-title">The Knowledge Store has no concepts{appliedSubject || sourceModule ? ' matching these filters' : ' yet'}</div>
              <div>Concepts are added when a Study transcript or a scanned note is extracted and you accept them.</div>
            </div>
          </div>
        )}
        {truncated && (
          <div className="notice notice-warn" style={{ marginBottom: 14 }}>
            <i className="ph ph-warning" />
            <div>Showing the first {KS_MAX_NODE_LIMIT} concepts (the Knowledge Store's limit). Filter by subject to narrow it down.</div>
          </div>
        )}

        {nodes && nodes.length > 0 && view === 'map' && (
          <div className="kn">
            <div style={{ minWidth: 0 }}>
              {edgesQ.loading && !edgesQ.data && <Loading label="Loading links…" />}
              {edgesQ.error != null && <ErrorNotice error={edgesQ.error} onRetry={edgesQ.reload} />}
              {edgesQ.data && (
                <>
                  {edgesQ.data.edges.length === 0 && (
                    <div className="notice notice-info" style={{ marginBottom: 12 }}>
                      <i className="ph ph-info" />
                      <div>
                        <div className="notice-title">No approved links yet</div>
                        <div>
                          The map only draws links a person has approved. Run <code>ks suggest-edges</code> for LLM suggestions,
                          then review them with <code>ks list-pending</code> / <code>ks approve</code>. For now every concept is a lone dot.
                        </div>
                      </div>
                    </div>
                  )}
                  <ConceptMap nodes={filtered} edges={edgesQ.data.edges} selectedId={nodeId} />
                </>
              )}
            </div>
            <div className="kn-detail">
              {nodeId ? <NodeDetail key={nodeId} id={nodeId} /> : <div className="wk-desc">Pick a concept on the map to see it in full.</div>}
            </div>
          </div>
        )}

        {nodes && nodes.length > 0 && view === 'list' && (
          <div className="kn">
            <div>
              {groups.map(([subj, list]) => (
                <div key={subj}>
                  <div className="section-label" style={{ marginTop: 4 }}>{subj} · {list.length}</div>
                  <div className="list" style={{ marginBottom: 18 }}>
                    {list.map((n) => (
                      <a key={n.id} className={`wk wk-click${nodeId === n.id ? ' wk-sel' : ''}`} href={href({ view: 'knowledge', nodeId: n.id })}>
                        <div style={{ minWidth: 0 }}>
                          <div className="wk-title" style={{ fontSize: 15, marginBottom: 4 }}>{n.title}</div>
                          <p className="wk-desc clamp2">{n.summary}</p>
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              ))}
              {filtered.length === 0 && <div className="wk-desc">No concept matches “{search}”.</div>}
            </div>
            <div className="kn-detail">
              {nodeId ? <NodeDetail key={nodeId} id={nodeId} /> : <div className="wk-desc">Pick a concept to see it in full.</div>}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

function NodeDetail({ id }: { id: string }) {
  const q = useAsync(() => ks.getNode(id), [id]);
  if (q.loading) return <Loading />;
  if (q.error) return <ErrorNotice error={q.error} onRetry={q.reload} compact />;
  const n = q.data!;
  return (
    <>
      <span className="tag tag-frost tag-sm">{n.subject}</span>
      <h3>{n.title}</h3>
      {n.id !== id && (
        // KS answers a merged node with its target (one hop), under a different id.
        <div className="notice notice-info" style={{ marginBottom: 12 }}>
          <i className="ph ph-git-merge" />
          <div>This concept was merged into the one shown here.</div>
        </div>
      )}
      <div className="kn-summary">{n.summary}</div>
      <div style={{ marginTop: 16 }}>
        <button className="btn btn-secondary btn-soft" onClick={() => navigate({ view: 'knowledge' })}>
          <i className="ph ph-x" />Close
        </button>
      </div>
    </>
  );
}
