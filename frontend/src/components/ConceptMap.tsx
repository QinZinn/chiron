/**
 * Concept map — Knowledge Store nodes and their APPROVED edges as a graph.
 *
 * Only what a person approved is drawn: KS `GET /edges` never returns pending
 * or rejected edges. Prerequisites have an arrow (from → to); `related` and
 * `contrasts_with` are undirected, the latter dashed. Colour is the subject.
 */
import { useMemo, useState } from 'react';
import type { KsEdge, KsNode } from '../api/ks';
import { href } from '../lib/route';
import { pack } from '../lib/graphLayout';

const SUBJECT_COLOURS = ['var(--frost)', 'var(--green)', 'var(--yel)', 'var(--pur)', 'var(--red)', 'var(--frost2)'];
// Width the components are packed into; node radius and font are in these units.
const BOX_W = 640;
// Past this many nodes, labels only show for the hovered/selected node and its
// neighbours; a few hundred labels at once are unreadable.
const ALWAYS_LABEL_UP_TO = 60;

export const RELATION_LABEL: Record<KsEdge['relation_type'], string> = {
  prerequisite: 'cần học trước',
  related: 'liên quan',
  contrasts_with: 'đối lập với',
};

function short(s: string, n = 28): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export function ConceptMap({ nodes, edges, selectedId }: { nodes: KsNode[]; edges: KsEdge[]; selectedId?: string }) {
  const [hover, setHover] = useState<string>();
  const ids = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const shown = useMemo(() => edges.filter((e) => ids.has(e.from) && ids.has(e.to)), [edges, ids]);
  const { pos, height } = useMemo(
    () => pack(nodes.map((n) => ({ id: n.id, group: n.subject })), shown, BOX_W),
    [nodes, shown],
  );
  const subjects = useMemo(() => [...new Set(nodes.map((n) => n.subject))].sort((a, b) => a.localeCompare(b, 'vi')), [nodes]);
  const colour = (subject: string) => SUBJECT_COLOURS[subjects.indexOf(subject) % SUBJECT_COLOURS.length];

  const focus = hover ?? selectedId;
  const neighbours = useMemo(() => {
    const s = new Set<string>();
    if (!focus) return s;
    for (const e of shown) {
      if (e.from === focus) s.add(e.to);
      if (e.to === focus) s.add(e.from);
    }
    return s;
  }, [focus, shown]);

  if (nodes.length === 0) return null;
  // Room for labels under the edge nodes and wide titles at the sides.
  const padX = 90;
  const padY = 40;
  const labelAll = nodes.length <= ALWAYS_LABEL_UP_TO;

  return (
    <div className="cmap">
      <svg viewBox={`${-padX} ${-padY} ${BOX_W + 2 * padX} ${height + 2 * padY + 20}`} role="img" aria-label={`Bản đồ ${nodes.length} khái niệm, ${shown.length} liên kết`}>
        <defs>
          <marker id="cmap-arrow" viewBox="0 0 10 10" refX="17" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--mut)" />
          </marker>
        </defs>
        {shown.map((e) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          const lit = focus !== undefined && (e.from === focus || e.to === focus);
          return (
            <line
              key={e.id}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              className={`cmap-edge cmap-${e.relation_type}${lit ? ' cmap-lit' : ''}`}
              markerEnd={e.relation_type === 'prerequisite' ? 'url(#cmap-arrow)' : undefined}
            >
              <title>{`${RELATION_LABEL[e.relation_type]}`}</title>
            </line>
          );
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id)!;
          const on = n.id === selectedId;
          const near = focus !== undefined && (n.id === focus || neighbours.has(n.id));
          const dim = focus !== undefined && !near;
          return (
            <a key={n.id} href={href({ view: 'knowledge', nodeId: n.id })} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(undefined)}>
              <g className={`cmap-node${dim ? ' cmap-dim' : ''}`} data-node-id={n.id}>
                <circle cx={p.x} cy={p.y} r={on ? 10 : 8} fill={colour(n.subject)} stroke={on ? 'var(--tx)' : 'var(--bg)'} strokeWidth={on ? 3 : 2} />
                {(labelAll || near || on) && (
                  <text x={p.x} y={p.y + 22} textAnchor="middle" className="cmap-label">{short(n.title)}</text>
                )}
                <title>{`${n.title} · ${n.subject}`}</title>
              </g>
            </a>
          );
        })}
      </svg>
      <div className="cmap-legend">
        {subjects.map((s) => (
          <span key={s}><i style={{ background: colour(s) }} />{s}</span>
        ))}
        <span className="cmap-key cmap-key-prerequisite">→ {RELATION_LABEL.prerequisite}</span>
        <span className="cmap-key cmap-key-related">— {RELATION_LABEL.related}</span>
        <span className="cmap-key cmap-key-contrasts_with">┄ {RELATION_LABEL.contrasts_with}</span>
      </div>
    </div>
  );
}
