import { useNavigate } from 'react-router-dom'

interface Node {
  id: string
  name: string | null
  status: string | null
  depth: number
}
interface Edge {
  id: string
  type: string
  type_label: string
  source_atom_id: string
  target_atom_id: string
  directed: boolean
  note: string
}

const TYPE_COLORS = ['#2563eb', '#d97706', '#059669', '#9333ea', '#dc2626', '#0891b2']

/** Concentric layout: the root in the middle, each hop on its own ring. */
export default function Graph({ root, nodes, edges }: { root: string; nodes: Node[]; edges: Edge[] }) {
  const navigate = useNavigate()
  const W = 720
  const H = 440
  const cx = W / 2
  const cy = H / 2
  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth))
  const ringGap = Math.min(cx, cy) / (maxDepth + 0.4)
  const pos = new Map<string, { x: number; y: number }>()
  const byDepth = new Map<number, Node[]>()
  for (const n of nodes) byDepth.set(n.depth, [...(byDepth.get(n.depth) ?? []), n])
  for (const [depth, list] of byDepth) {
    list.forEach((n, i) => {
      if (depth === 0) return pos.set(n.id, { x: cx, y: cy })
      const angle = (2 * Math.PI * i) / list.length - Math.PI / 2 + depth * 0.4
      pos.set(n.id, { x: cx + Math.cos(angle) * ringGap * depth * 1.15, y: cy + Math.sin(angle) * ringGap * depth })
    })
  }
  const types = [...new Set(edges.map((e) => e.type))]
  const color = (t: string) => TYPE_COLORS[types.indexOf(t) % TYPE_COLORS.length]

  if (nodes.length <= 1) return <div className="muted">No related atoms yet.</div>

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="graph" role="img" aria-label="Relations graph">
        <defs>
          {types.map((t) => (
            <marker key={t} id={`arrow-${t}`} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={color(t)} />
            </marker>
          ))}
        </defs>
        {edges.map((e) => {
          const a = pos.get(e.source_atom_id)
          const b = pos.get(e.target_atom_id)
          if (!a || !b) return null
          const dx = b.x - a.x
          const dy = b.y - a.y
          const len = Math.hypot(dx, dy) || 1
          const pad = 34
          const x2 = b.x - (dx / len) * pad
          const y2 = b.y - (dy / len) * pad
          return (
            <g key={e.id}>
              <title>{`${e.type_label}${e.note ? `: ${e.note}` : ''}`}</title>
              <line
                x1={a.x + (dx / len) * pad}
                y1={a.y + (dy / len) * pad}
                x2={x2}
                y2={y2}
                stroke={color(e.type)}
                strokeWidth={2}
                markerEnd={e.directed ? `url(#arrow-${e.type})` : undefined}
              />
            </g>
          )
        })}
        {nodes.map((n) => {
          const p = pos.get(n.id)!
          const isRoot = n.id === root
          return (
            <g key={n.id} className="graph-node" onClick={() => navigate(`/atoms/${n.id}`)} transform={`translate(${p.x},${p.y})`}>
              <title>{n.name ?? n.id}</title>
              <circle r={isRoot ? 30 : 26} className={isRoot ? 'root' : n.status === 'deprecated' ? 'deprecated' : ''} />
              <text textAnchor="middle" dy="0.35em">
                {(n.name ?? '?').length > 14 ? `${(n.name ?? '?').slice(0, 13)}…` : n.name}
              </text>
            </g>
          )
        })}
      </svg>
      <div className="legend">
        {types.map((t) => (
          <span key={t}>
            <span className="swatch" style={{ background: color(t) }} />
            {edges.find((e) => e.type === t)?.type_label}
          </span>
        ))}
      </div>
    </div>
  )
}
