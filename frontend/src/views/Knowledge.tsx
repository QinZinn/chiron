/**
 * Kiến thức — Knowledge Store GET /nodes and GET /nodes/{id}, via the proxy.
 * Nodes are concepts the learner has studied ("Định luật Newton 2"), not sessions.
 */
import { useMemo, useState } from 'react';
import { ks, KS_MAX_NODE_LIMIT, KS_SOURCE_MODULES, type KsNode, type KsSourceModule } from '../api/ks';
import { useAsync } from '../lib/useAsync';
import { href, navigate } from '../lib/route';
import { ErrorNotice, Loading, PageHeader } from '../components/ui';

const SOURCE_LABEL: Record<KsSourceModule, string> = { mnemosyne: 'Mnemosyne', lexiflash: 'LexiFlash' };

export function KnowledgeView({ nodeId }: { nodeId?: string }) {
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
            <p>Từ Knowledge Store — “second brain” lưu khái niệm đã học, rút ra từ các phiên Học bài.</p>
          </div>
        </div>
        <div className="stats">
          <div><div className="stat-k">Khái niệm</div><div className="stat-v" style={{ color: 'var(--frost)' }}>{nodes ? nodes.length : '—'}</div></div>
          <div><div className="stat-k">Môn học</div><div className="stat-v">{nodes ? subjects : '—'}</div></div>
        </div>
        <div className="toolbar" style={{ marginBottom: 16 }}>
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

        {nodes && nodes.length > 0 && (
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
