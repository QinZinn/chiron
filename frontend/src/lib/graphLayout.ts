/**
 * Force-directed layout for the concept map, small and dependency-free.
 *
 * Deterministic: nodes start on a circle per subject (so a subject's concepts
 * begin together and the same data always draws the same picture), then a
 * fixed number of steps of repulsion between every pair, spring pull along
 * edges, and a gentle pull to the centre. O(n²) per step, which is fine for
 * the few hundred nodes KS returns (its /nodes cap is 500).
 */
export interface LayoutNode {
  id: string;
  group: string;
}

export interface LayoutEdge {
  from: string;
  to: string;
}

export interface Point {
  x: number;
  y: number;
}

export function layout(nodes: LayoutNode[], edges: LayoutEdge[], size = 1000): Map<string, Point> {
  const n = nodes.length;
  const pos = new Map<string, Point>();
  if (n === 0) return pos;

  // Start: groups spread on a big circle, each group's nodes on a small one.
  const groups = [...new Set(nodes.map((d) => d.group))].sort((a, b) => a.localeCompare(b, 'en'));
  const members = new Map(groups.map((g) => [g, nodes.filter((d) => d.group === g)]));
  groups.forEach((g, gi) => {
    const ga = (2 * Math.PI * gi) / groups.length;
    const gr = groups.length > 1 ? size * 0.28 : 0;
    const list = members.get(g)!;
    list.forEach((d, i) => {
      const a = (2 * Math.PI * i) / list.length;
      const r = list.length > 1 ? size * 0.12 : 0;
      pos.set(d.id, { x: gr * Math.cos(ga) + r * Math.cos(a), y: gr * Math.sin(ga) + r * Math.sin(a) });
    });
  });

  const links = edges.filter((e) => pos.has(e.from) && pos.has(e.to) && e.from !== e.to);
  const ids = nodes.map((d) => d.id);
  const ideal = size / Math.max(4, Math.sqrt(n) * 1.6);
  const steps = n > 200 ? 120 : 300;

  for (let step = 0; step < steps; step++) {
    const heat = 1 - step / steps; // moves shrink as the layout settles
    const force = new Map<string, Point>(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < n; i++) {
      const a = pos.get(ids[i])!;
      for (let j = i + 1; j < n; j++) {
        const b = pos.get(ids[j])!;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1e-6) {
          // Two nodes on the same spot: nudge them apart deterministically.
          dx = (i - j) * 0.01;
          dy = 0.01;
          d2 = dx * dx + dy * dy;
        }
        const f = (ideal * ideal) / d2;
        const fa = force.get(ids[i])!;
        const fb = force.get(ids[j])!;
        fa.x += dx * f;
        fa.y += dy * f;
        fb.x -= dx * f;
        fb.y -= dy * f;
      }
    }
    for (const e of links) {
      const a = pos.get(e.from)!;
      const b = pos.get(e.to)!;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1e-3;
      const f = (d - ideal) / d / 2;
      force.get(e.from)!.x += dx * f;
      force.get(e.from)!.y += dy * f;
      force.get(e.to)!.x -= dx * f;
      force.get(e.to)!.y -= dy * f;
    }
    const cap = ideal * 0.5 * heat + 1;
    for (const id of ids) {
      const p = pos.get(id)!;
      const f = force.get(id)!;
      // Pull to the centre, strong enough that clusters with no edge between
      // them (two subjects) stay near each other instead of drifting apart.
      f.x -= p.x * 0.04;
      f.y -= p.y * 0.04;
      const len = Math.sqrt(f.x * f.x + f.y * f.y);
      const k = len > cap ? cap / len : 1;
      p.x += f.x * k;
      p.y += f.y * k;
    }
  }
  return pos;
}

/**
 * Scale positions into a `width`-wide box, keeping the aspect ratio, so node
 * and label sizes on screen do not depend on how far the layout spread out.
 * Returns the positions and the box height.
 */
export function fit(pos: Map<string, Point>, width: number, minHeight: number): { pos: Map<string, Point>; height: number } {
  const pts = [...pos.values()];
  if (pts.length === 0) return { pos, height: minHeight };
  const minX = Math.min(...pts.map((p) => p.x));
  const maxX = Math.max(...pts.map((p) => p.x));
  const minY = Math.min(...pts.map((p) => p.y));
  const maxY = Math.max(...pts.map((p) => p.y));
  const w = maxX - minX;
  const h = maxY - minY;
  // One node, or all on a line: nothing to scale by in that direction.
  const k = w > 0 ? width / w : 1;
  const height = Math.max(minHeight, h * k);
  const out = new Map<string, Point>();
  for (const [id, p] of pos) {
    out.set(id, { x: w > 0 ? (p.x - minX) * k : width / 2, y: h > 0 ? (p.y - minY) * k + (height - h * k) / 2 : height / 2 });
  }
  return { pos: out, height };
}

/**
 * Lay out each connected component on its own and pack them in rows.
 *
 * One force layout over the whole graph lets unconnected clusters (usually two
 * subjects) push each other far apart, which squeezes every cluster into a
 * corner once the picture is scaled to fit. Per component, positions are
 * scaled so the average edge is `edgeLength` long, whatever the layout chose;
 * nodes with no edge at all are laid out in a grid after the components.
 */
export function pack(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
  width: number,
  { edgeLength = 110, gap = 70, cell = 120 } = {},
): { pos: Map<string, Point>; height: number } {
  const parent = new Map(nodes.map((n) => [n.id, n.id]));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    parent.set(x, r);
    return r;
  };
  const links = edges.filter((e) => parent.has(e.from) && parent.has(e.to) && e.from !== e.to);
  for (const e of links) parent.set(find(e.from), find(e.to));

  const comps = new Map<string, LayoutNode[]>();
  for (const n of nodes) comps.set(find(n.id), [...(comps.get(find(n.id)) ?? []), n]);
  const connected = [...comps.values()].filter((c) => c.length > 1).sort((a, b) => b.length - a.length);
  const singles = [...comps.values()].filter((c) => c.length === 1).map((c) => c[0]);

  const pos = new Map<string, Point>();
  let x = 0;
  let y = 0;
  let rowH = 0;
  const place = (local: Map<string, Point>, w: number, h: number) => {
    if (x > 0 && x + w > width) {
      x = 0;
      y += rowH + gap;
      rowH = 0;
    }
    for (const [id, p] of local) pos.set(id, { x: x + p.x, y: y + p.y });
    x += w + gap;
    rowH = Math.max(rowH, h);
  };

  for (const comp of connected) {
    const ids = new Set(comp.map((n) => n.id));
    const own = links.filter((e) => ids.has(e.from) && ids.has(e.to));
    const raw = layout(comp, own);
    const avg = own.reduce((s, e) => {
      const a = raw.get(e.from)!;
      const b = raw.get(e.to)!;
      return s + Math.hypot(a.x - b.x, a.y - b.y);
    }, 0) / own.length;
    const k = avg > 0 ? edgeLength / avg : 1;
    const pts = [...raw.values()];
    const minX = Math.min(...pts.map((p) => p.x));
    const minY = Math.min(...pts.map((p) => p.y));
    const local = new Map<string, Point>();
    for (const [id, p] of raw) local.set(id, { x: (p.x - minX) * k, y: (p.y - minY) * k });
    const w = Math.max(...pts.map((p) => p.x)) * k - minX * k;
    const h = Math.max(...pts.map((p) => p.y)) * k - minY * k;
    place(local, w, h);
  }

  // Isolated concepts: a grid, grouped by subject so colours stay together.
  singles.sort((a, b) => a.group.localeCompare(b.group, 'en'));
  const perRow = Math.max(1, Math.floor((width + gap) / cell));
  if (singles.length > 0) {
    const local = new Map<string, Point>();
    singles.forEach((n, i) => local.set(n.id, { x: (i % perRow) * cell, y: Math.floor(i / perRow) * cell * 0.6 }));
    const w = (Math.min(singles.length, perRow) - 1) * cell;
    const h = Math.floor((singles.length - 1) / perRow) * cell * 0.6;
    place(local, w, h);
  }

  return { pos, height: y + rowH };
}
