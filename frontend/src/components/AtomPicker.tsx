import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, atomName, qs, type Atom, type Page } from '../api'

export default function AtomPicker({ exclude, onPick }: { exclude?: string[]; onPick: (a: Atom) => void }) {
  const [q, setQ] = useState('')
  const { data, isFetching } = useQuery({
    queryKey: ['atom-picker', q],
    queryFn: () => api<Page<Atom>>(`/api/v1/atoms${qs({ q, page_size: 8, sort: 'name' })}`),
    enabled: q.trim().length > 0,
  })
  const items = (data?.items ?? []).filter((a) => !exclude?.includes(a.id))
  return (
    <div className="picker">
      <input type="search" placeholder="Search for an atom…" value={q} onChange={(e) => setQ(e.target.value)} />
      {q && (
        <div className="picker-results">
          {isFetching && <div className="muted small">Searching…</div>}
          {!isFetching && items.length === 0 && <div className="muted small">No atoms match.</div>}
          {items.map((a) => (
            <button
              type="button"
              key={a.id}
              className="picker-item"
              onClick={() => {
                onPick(a)
                setQ('')
              }}
            >
              {atomName(a.values)}
              {typeof a.values.description === 'string' && <span className="muted small"> {a.values.description.slice(0, 60)}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
