import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, qs, type FormDefinition, type FormSummary, type Page, type RecordItem } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import { showValue } from '../hooks'

const PAGE_SIZE = 25

export default function RecordList() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const form = params.get('form') ?? ''
  const status = params.get('status') ?? 'active'
  const complete = params.get('complete') ?? ''
  const sort = params.get('sort') ?? '-updated_at'
  const page = Number(params.get('page') ?? 1)
  const [q, setQ] = useState(params.get('q') ?? '')

  const forms = useQuery({
    queryKey: ['forms', 'with-records'],
    queryFn: () => api<Page<FormSummary>>(`/api/v1/forms${qs({ status: 'all', page_size: 100 })}`),
  })
  const published = (forms.data?.items ?? []).filter((f) => f.latest_version)
  const selected = published.find((f) => f.key === form)
  const definition = useQuery({
    queryKey: ['form-version', form, 'latest'],
    queryFn: () => api<FormDefinition>(`/api/v1/forms/${form}/versions/latest`),
    enabled: !!selected,
  })
  const columns = (definition.data?.sections.flatMap((s) => s.items) ?? []).slice(0, 4)

  const query = { form: selected ? form : undefined, q: params.get('q'), status, complete, sort, page, page_size: PAGE_SIZE }
  const records = useQuery({
    queryKey: ['records', query],
    queryFn: () => api<Page<RecordItem>>(`/api/v1/records${qs(query)}`),
    placeholderData: keepPreviousData,
  })

  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    if (k !== 'page') next.delete('page')
    if (k === 'form') next.delete('sort')
    setParams(next)
  }
  const sortBy = (key: string) => set('sort', sort === key ? `-${key}` : key)
  const arrow = (key: string) => (sort === key ? ' ↑' : sort === `-${key}` ? ' ↓' : '')
  const pages = Math.max(1, Math.ceil((records.data?.total ?? 0) / PAGE_SIZE))
  const canCreate = can('records:write') && selected?.status === 'active'

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Records</h1>
          <div className="muted small">Saved answers to published forms. Pick a form to see its records and add new ones.</div>
        </div>
        {canCreate && <Link className="button primary" to={`/records/new?form=${selected.key}`}>New record</Link>}
      </div>
      <form className="toolbar" onSubmit={(e) => (e.preventDefault(), set('q', q))}>
        <select value={selected ? form : ''} onChange={(e) => set('form', e.target.value)}>
          <option value="">All forms</option>
          {published.map((f) => (
            <option key={f.id} value={f.key}>{f.name}{f.status === 'deprecated' ? ' (deprecated)' : ''}</option>
          ))}
        </select>
        <input type="search" className="grow" placeholder="Search answers…" value={q} onChange={(e) => setQ(e.target.value)} />
        <button>Search</button>
        <select value={complete} onChange={(e) => set('complete', e.target.value)}>
          <option value="">Complete or not</option>
          <option value="true">Complete</option>
          <option value="false">Incomplete</option>
        </select>
        <select value={status} onChange={(e) => set('status', e.target.value)}>
          <option value="active">Active</option>
          <option value="archived">Archived</option>
          <option value="all">All</option>
        </select>
      </form>
      {!selected && can('records:write') && published.length > 0 && (
        <div className="notice subtle">Pick a form above to add a new record.</div>
      )}
      {forms.data && published.length === 0 && (
        <div className="notice subtle">No form has been published yet. Records are saved against a published form.</div>
      )}
      <ErrorBox error={records.error ?? forms.error ?? definition.error} />
      <div className="card flush">
        <table className="table">
          <thead>
            <tr>
              <th>Record</th>
              {!selected && <th>Form</th>}
              {columns.map((c) => (
                <th key={c.key} className="sortable" onClick={() => sortBy(c.key)}>{c.label}{arrow(c.key)}</th>
              ))}
              <th>State</th>
              <th className="sortable" onClick={() => sortBy('updated_at')}>Updated{arrow('updated_at')}</th>
            </tr>
          </thead>
          <tbody>
            {records.data?.items.map((r) => (
              <tr key={r.id} className={r.status === 'archived' ? 'dim' : ''}>
                <td>
                  <Link to={`/records/${r.id}`} className="strong">{r.title}</Link>
                  <div className="muted small">v{r.form_version}{r.latest_form_version && r.latest_form_version > r.form_version ? ` · v${r.latest_form_version} available` : ''}</div>
                </td>
                {!selected && <td>{r.form.name}</td>}
                {columns.map((c) => <td key={c.key}>{showValue(r.answers[c.key])}</td>)}
                <td className="nowrap">
                  {r.complete ? <span className="pill approved">complete</span> : <span className="pill warn" title={`Missing: ${r.missing.join(', ')}`}>incomplete</span>}
                  {r.status === 'archived' && <span className="pill gray">archived</span>}
                </td>
                <td className="muted small nowrap">
                  {new Date(r.updated_at).toLocaleDateString()}
                  {r.updated_by_name && <div>{r.updated_by_name}</div>}
                </td>
              </tr>
            ))}
            {records.data && records.data.items.length === 0 && (
              <tr><td colSpan={columns.length + 4} className="muted center">No records found.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">{records.data?.total ?? 0} records</span>
        <button disabled={page <= 1} onClick={() => set('page', String(page - 1))}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button disabled={page >= pages} onClick={() => set('page', String(page + 1))}>Next</button>
      </div>
    </div>
  )
}
