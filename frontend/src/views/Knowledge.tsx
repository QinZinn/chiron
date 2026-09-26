/**
 * Kiến thức — Knowledge Store GET /nodes and GET /nodes/{id}, via the proxy.
 * Nodes are concepts the learner has studied ("Định luật Newton 2"), not sessions.
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

const SOURCE_LABEL: Record<KsSourceModule, string> = { mnemosyne: 'Mnemosyne', lexiflash: 'LexiFlash', note_scan: 'Scan ghi chép' };

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
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'vi'));
  }, [filtered]);
  const subjects = useMemo(() => new Set((nodes ?? []).map((n) => n.subject)).size, [nodes]);
  const truncated = nodes?.length === KS_MAX_NODE_LIMIT;

  return (
    <main className="main">
      <PageHeader title="Kiến thức">
        <button className="btn btn-secondary btn-soft" onClick={nodesQ.reload}>
          <i className="ph ph-arrow-clockwise" />Tải lại
        </button>
      </PageHeader>
      <div className="page">
        <div className="page-head">
          <div>
            <h2>{nodes ? `${nodes.length} khái niệm${truncated ? '+' : ''}` : 'Khái niệm đã học'}</h2>
            <p>Từ Knowledge Store — “second brain” lưu khái niệm đã học, rút ra từ các phiên Học bài và ghi chép scan.</p>
          </div>
        </div>
        <div className="stats">
          <div><div className="stat-k">Khái niệm</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{nodes ? nodes.length : '—'}</div></div>
          <div><div className="stat-k">Môn học</div><div className="stat-v">{nodes ? subjects : '—'}</div></div>
        </div>
        <div className="toolbar" style={{ marginBottom: 16 }}>
          <div className="pomo-tabs" role="tablist" aria-label="Cách xem" style={{ minWidth: 220 }}>
            <button role="tab" aria-selected={view === 'list'} className={`pomo-tab${view === 'list' ? ' pomo-tab-on' : ''}`} onClick={() => switchView('list')}>
              <i className="ph ph-list-bullets" /> Danh sách
            </button>
            <button role="tab" aria-selected={view === 'map'} className={`pomo-tab${view === 'map' ? ' pomo-tab-on' : ''}`} onClick={() => switchView('map')}>
              <i className="ph ph-graph" /> Bản đồ
            </button>
          </div>
          <input className="input input-sm" style={{ width: 220 }} lang="vi" placeholder="Tìm trong kết quả…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <form
            style={{ display: 'flex', gap: 6 }}
            onSubmit={(e) => {
              e.preventDefault();
              setAppliedSubject(subject.trim());
            }}
          >
            <input className="input input-sm" style={{ width: 180 }} lang="vi" placeholder="Môn học (khớp đúng)" value={subject} onChange={(e) => setSubject(e.target.value)} />
            <button className="btn btn-secondary btn-soft" type="submit"><i className="ph ph-funnel" />Lọc</button>
          </form>
          <select className="input" style={{ width: 'auto' }} value={sourceModule} onChange={(e) => setSourceModule(e.target.value as KsSourceModule | '')}>
            <option value="">Mọi nguồn</option>
            {KS_SOURCE_MODULES.map((m) => <option key={m} value={m}>{SOURCE_LABEL[m]}</option>)}
          </select>
        </div>
        <hr className="rule" style={{ marginBottom: 18 }} />

        {nodesQ.loading && !nodes && <Loading label="Đang tải khái niệm từ Knowledge Store…" />}
        {nodesQ.error != null && <ErrorNotice error={nodesQ.error} onRetry={nodesQ.reload} />}
        {nodes && nodes.length === 0 && (
          <div className="notice notice-info">
            <i className="ph ph-info" />
            <div>
              <div className="notice-title">Knowledge Store chưa có khái niệm nào{appliedSubject || sourceModule ? ' khớp bộ lọc' : ''}</div>
              <div>Khái niệm được thêm khi transcript Học bài được rút (job <code>extract</code>) và bạn chấp nhận chúng.</div>
            </div>
          </div>
        )}
        {truncated && (
          <div className="notice notice-warn" style={{ marginBottom: 14 }}>
            <i className="ph ph-warning" />
            <div>Đang hiện {KS_MAX_NODE_LIMIT} khái niệm đầu tiên (giới hạn của KS). Lọc theo môn để thu hẹp.</div>
          </div>
        )}

        {nodes && nodes.length > 0 && view === 'map' && (
          <div className="kn">
            <div style={{ minWidth: 0 }}>
              {edgesQ.loading && !edgesQ.data && <Loading label="Đang tải liên kết…" />}
              {edgesQ.error != null && <ErrorNotice error={edgesQ.error} onRetry={edgesQ.reload} />}
              {edgesQ.data && (
                <>
                  {edgesQ.data.edges.length === 0 && (
                    <div className="notice notice-info" style={{ marginBottom: 12 }}>
                      <i className="ph ph-info" />
                      <div>
                        <div className="notice-title">Chưa có liên kết nào được duyệt</div>
                        <div>
                          Bản đồ chỉ vẽ liên kết đã được người duyệt. Chạy <code>ks suggest-edges</code> để LLM gợi ý, rồi
                          duyệt bằng <code>ks list-pending</code> / <code>ks approve</code>. Hiện mỗi khái niệm là một điểm rời.
                        </div>
                      </div>
                    </div>
                  )}
                  <ConceptMap nodes={filtered} edges={edgesQ.data.edges} selectedId={nodeId} />
                </>
              )}
            </div>
            <div className="kn-detail">
              {nodeId ? <NodeDetail key={nodeId} id={nodeId} /> : <div className="wk-desc">Chọn một khái niệm trên bản đồ để xem đầy đủ.</div>}
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
              {filtered.length === 0 && <div className="wk-desc">Không có khái niệm nào khớp “{search}”.</div>}
            </div>
            <div className="kn-detail">
              {nodeId ? <NodeDetail key={nodeId} id={nodeId} /> : <div className="wk-desc">Chọn một khái niệm để xem đầy đủ.</div>}
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
          <div>Khái niệm này đã được gộp vào khái niệm đang hiển thị.</div>
        </div>
      )}
      <div className="kn-summary">{n.summary}</div>
      <div style={{ marginTop: 16 }}>
        <button className="btn btn-secondary btn-soft" onClick={() => navigate({ view: 'knowledge' })}>
          <i className="ph ph-x" />Đóng
        </button>
      </div>
    </>
  );
}
