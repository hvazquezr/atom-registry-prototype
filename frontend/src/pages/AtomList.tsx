import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, atomName, qs, type Atom, type Field, type Page } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import ValueView from '../components/ValueView'
import { useSchema } from '../hooks'

interface Filter {
  key: string
  op: string
  value: string
}

const OPS: Record<string, { value: string; label: string }[]> = {
  text: [{ value: 'contains', label: 'contains' }, { value: 'eq', label: 'is' }, { value: 'exists', label: 'has a value' }],
  number: [{ value: 'eq', label: '=' }, { value: 'gte', label: '≥' }, { value: 'lte', label: '≤' }, { value: 'gt', label: '>' }, { value: 'lt', label: '<' }],
  date: [{ value: 'gte', label: 'on or after' }, { value: 'lte', label: 'on or before' }, { value: 'eq', label: 'on' }],
  options: [{ value: 'eq', label: 'is' }, { value: 'in', label: 'is any of' }],
  list: [{ value: 'contains', label: 'contains' }],
}

function FilterRow({ fields, filter, onChange, onRemove }: { fields: Field[]; filter: Filter; onChange: (f: Filter) => void; onRemove: () => void }) {
  const field = fields.find((f) => f.key === filter.key)
  const ops = OPS[field?.type ?? 'text']
  return (
    <div className="filter-row">
      <select value={filter.key} onChange={(e) => {
        const f = fields.find((x) => x.key === e.target.value)
        onChange({ key: e.target.value, op: OPS[f?.type ?? 'text'][0].value, value: '' })
      }}>
        {fields.map((f) => (
          <option key={f.key} value={f.key}>{f.label}</option>
        ))}
      </select>
      <select value={filter.op} onChange={(e) => onChange({ ...filter, op: e.target.value })}>
        {ops.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {filter.op !== 'exists' &&
        (field?.type === 'options' && filter.op === 'eq' ? (
          <select value={filter.value} onChange={(e) => onChange({ ...filter, value: e.target.value })}>
            <option value="">—</option>
            {(field.config.choices ?? []).map((c) => (
              <option key={c.value} value={c.value}>{c.label || c.value}</option>
            ))}
          </select>
        ) : (
          <input
            type={field?.type === 'date' ? 'date' : field?.type === 'number' ? 'number' : 'text'}
            value={filter.value}
            placeholder={filter.op === 'in' ? 'a,b,c' : ''}
            onChange={(e) => onChange({ ...filter, value: e.target.value })}
          />
        ))}
      <button className="link" onClick={onRemove}>Remove</button>
    </div>
  )
}

export default function AtomList() {
  const { can } = useAuth()
  const schema = useSchema()
  const [params, setParams] = useSearchParams()
  const [q, setQ] = useState(params.get('q') ?? '')
  const [filters, setFilters] = useState<Filter[]>([])
  const status = params.get('status') ?? 'active'
  const sort = params.get('sort') ?? '-updated_at'
  const page = Number(params.get('page') ?? 1)

  const fields = schema.data?.fields ?? []
  const columns = fields.filter((f) => f.key !== 'name' && f.key !== 'description').slice(0, 4)
  const active = filters.filter((f) => f.op === 'exists' || f.value !== '')
  const query: Record<string, string | number> = { q: params.get('q') ?? '', status, sort, page, page_size: 25 }
  for (const f of active) query[`filter[${f.key}][${f.op}]`] = f.op === 'exists' ? 'true' : f.value

  const atoms = useQuery({
    queryKey: ['atoms', query],
    queryFn: () => api<Page<Atom>>(`/api/v1/atoms${qs(query)}`),
    placeholderData: keepPreviousData,
  })

  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    if (k !== 'page') next.delete('page')
    setParams(next)
  }
  const sortBy = (key: string) => set('sort', sort === key ? `-${key}` : key)
  const arrow = (key: string) => (sort === key ? ' ↑' : sort === `-${key}` ? ' ↓' : '')
  const pages = Math.max(1, Math.ceil((atoms.data?.total ?? 0) / 25))

  return (
    <div>
      <div className="page-head">
        <h1>Atoms</h1>
        {can('atoms:write') && <Link className="button primary" to="/atoms/new">New atom</Link>}
      </div>
      <form className="toolbar" onSubmit={(e) => (e.preventDefault(), set('q', q))}>
        <input type="search" className="grow" placeholder="Search names, descriptions, options…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button>Search</button>
        <select value={status} onChange={(e) => set('status', e.target.value)}>
          <option value="active">Active</option>
          <option value="deprecated">Deprecated</option>
          <option value="all">All</option>
        </select>
        {fields.length > 0 && (
          <button type="button" onClick={() => setFilters([...filters, { key: fields[0].key, op: OPS[fields[0].type][0].value, value: '' }])}>
            Add filter
          </button>
        )}
      </form>
      {filters.length > 0 && (
        <div className="filters">
          {filters.map((f, i) => (
            <FilterRow key={i} fields={fields} filter={f}
              onChange={(nf) => setFilters(filters.map((x, j) => (j === i ? nf : x)))}
              onRemove={() => setFilters(filters.filter((_, j) => j !== i))} />
          ))}
        </div>
      )}
      <ErrorBox error={atoms.error ?? schema.error} />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr>
              <th className="sortable" onClick={() => sortBy('name')}>Name{arrow('name')}</th>
              {columns.map((c) => (
                <th key={c.key} className="sortable" onClick={() => sortBy(c.key)}>{c.label}{arrow(c.key)}</th>
              ))}
              <th className="sortable" onClick={() => sortBy('updated_at')}>Updated{arrow('updated_at')}</th>
            </tr>
          </thead>
          <tbody>
            {atoms.data?.items.map((a) => (
              <tr key={a.id}>
                <td>
                  <Link to={`/atoms/${a.id}`} className="strong">{atomName(a.values)}</Link>
                  {a.status === 'deprecated' && <span className="pill gray">deprecated</span>}
                  {typeof a.values.description === 'string' && <div className="muted small clamp">{a.values.description}</div>}
                </td>
                {columns.map((c) => (
                  <td key={c.key}><ValueView field={c} value={a.values[c.key]} /></td>
                ))}
                <td className="muted small nowrap">{new Date(a.updated_at).toLocaleDateString()}</td>
              </tr>
            ))}
            {atoms.data && atoms.data.items.length === 0 && (
              <tr><td colSpan={columns.length + 2} className="muted center">No atoms found.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">{atoms.data?.total ?? 0} atoms</span>
        <button disabled={page <= 1} onClick={() => set('page', String(page - 1))}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button disabled={page >= pages} onClick={() => set('page', String(page + 1))}>Next</button>
      </div>
    </div>
  )
}
