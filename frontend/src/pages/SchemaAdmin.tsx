import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Field, type FieldError } from '../api'
import { useAuth } from '../auth'
import ErrorBox from '../components/ErrorBox'
import FieldEditor from '../components/FieldEditor'
import { formatDate } from '../hooks'

interface Draft {
  fields: Field[]
  changes: { latest_version: number | null; added: string[]; removed: string[]; changed: string[]; recalculate: string[]; has_changes: boolean }
  problems: FieldError[]
}
interface Version {
  version: number
  notes: string
  published_at: string
  published_by_name: string | null
  field_count: number
}

function rules(f: Field): string {
  const c = f.config
  const out: string[] = []
  if (c.min_length != null || c.max_length != null) out.push(`${c.min_length ?? 0}–${c.max_length ?? '∞'} chars`)
  if (c.regex) out.push(`/${c.regex}/`)
  if (c.min != null || c.max != null) out.push(`${c.min ?? '…'} to ${c.max ?? '…'}`)
  if (c.integer_only) out.push('whole')
  if (c.choices) out.push(`${c.multiple ? 'multiple' : 'single'}: ${c.choices.map((x) => x.label || x.value).join(', ')}`)
  if (f.type === 'list') out.push(`of ${c.item_type ?? 'text'}${c.max_items ? `, max ${c.max_items}` : ''}`)
  return out.join(' · ')
}

export default function SchemaAdmin() {
  const qc = useQueryClient()
  const { can } = useAuth()
  const admin = can('schema:admin')
  const [editing, setEditing] = useState<Field | null | undefined>(undefined)
  const [notes, setNotes] = useState('')
  const [published, setPublished] = useState<string | null>(null)
  const draft = useQuery({ queryKey: ['draft'], queryFn: () => api<Draft>('/internal/schema/draft') })
  const versions = useQuery({ queryKey: ['versions'], queryFn: () => api<Version[]>('/internal/schema/versions') })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['draft'] })
    qc.invalidateQueries({ queryKey: ['versions'] })
    qc.invalidateQueries({ queryKey: ['schema'] })
  }
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => api(path, { method: 'POST', body: body ?? {} }),
    onSuccess: refresh,
  })
  const del = useMutation({ mutationFn: (f: Field) => api(`/internal/fields/${f.id}`, { method: 'DELETE' }), onSuccess: refresh })
  const publish = useMutation({
    mutationFn: () => api<{ version: number; recalculating: string[] }>('/internal/schema/publish', { body: { notes } }),
    onSuccess: (r) => {
      setNotes('')
      setPublished(`Published version ${r.version}.${r.recalculating.length ? ` Recalculating ${r.recalculating.join(', ')} for existing atoms.` : ''}`)
      refresh()
      qc.invalidateQueries({ queryKey: ['atoms'] })
    },
  })

  const fields = draft.data?.fields ?? []
  const active = fields.filter((f) => f.status === 'active')
  const move = (f: Field, delta: number) => {
    const keys = fields.map((x) => x.key)
    const i = keys.indexOf(f.key)
    const j = i + delta
    if (j < 0 || j >= keys.length) return
    ;[keys[i], keys[j]] = [keys[j], keys[i]]
    action.mutate({ path: '/internal/fields/reorder', body: { keys } })
  }
  const ch = draft.data?.changes
  const changedKeys = new Set([...(ch?.added ?? []), ...(ch?.changed ?? [])])

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Fields</h1>
          <div className="muted small">The meta atom: which fields every atom can have. Edits go to a draft until you publish.</div>
        </div>
        {admin && <button className="primary" onClick={() => setEditing(null)}>New field</button>}
      </div>

      {admin && ch && (ch.has_changes || (draft.data?.problems.length ?? 0) > 0) && (
        <div className="card publish-bar">
          <div>
            <strong>Unpublished changes</strong>
            <div className="small">
              {ch.added.length > 0 && <div>Added: {ch.added.join(', ')}</div>}
              {ch.changed.length > 0 && <div>Changed: {ch.changed.join(', ')}</div>}
              {ch.removed.length > 0 && <div>Deprecated: {ch.removed.join(', ')}</div>}
              {ch.recalculate.length > 0 && <div>Will recalculate existing atoms for: {ch.recalculate.join(', ')}</div>}
            </div>
            {draft.data!.problems.map((p, i) => <div key={i} className="field-error">{p.message}</div>)}
          </div>
          <div className="publish-actions">
            <input placeholder="Notes for this version" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <button className="primary" disabled={publish.isPending || draft.data!.problems.length > 0} onClick={() => publish.mutate()}>
              Publish version {(ch.latest_version ?? 0) + 1}
            </button>
          </div>
        </div>
      )}
      {published && <div className="notice">{published}</div>}
      <ErrorBox error={draft.error ?? action.error ?? del.error ?? publish.error} />

      <div className="card flush">
        <table className="table">
          <thead>
            <tr>{admin && <th />}<th>Field</th><th>Type</th><th>Rules</th><th>Status</th><th /></tr>
          </thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={f.id} className={f.status === 'deprecated' ? 'dim' : ''}>
                {admin && (
                  <td className="nowrap">
                    <button className="icon" disabled={i === 0} onClick={() => move(f, -1)} aria-label="Move up">↑</button>
                    <button className="icon" disabled={i === fields.length - 1} onClick={() => move(f, 1)} aria-label="Move down">↓</button>
                  </td>
                )}
                <td>
                  <button className="link strong" onClick={() => setEditing(f)}>{f.label}</button>
                  {f.required && <span className="req"> *</span>}
                  <div className="mono small muted">{f.key}</div>
                </td>
                <td>
                  {f.type}
                  {f.calculated && (
                    <div><span className="pill blue">calculated</span>{f.time_dependent && <span className="pill gray" title="Uses today() or now()">time</span>}</div>
                  )}
                </td>
                <td className="small">
                  {f.calculated && <code className="expr">{f.expression}</code>}
                  <div className="muted">{rules(f)}</div>
                </td>
                <td>
                  {f.system && <span className="pill gray">built-in</span>}
                  {f.status === 'deprecated' && <span className="pill gray">deprecated</span>}
                  {changedKeys.has(f.key) && <span className="pill warn">draft</span>}
                </td>
                <td className="right nowrap">
                  {admin && !f.system && f.status === 'active' && (
                    <button className="link" onClick={() => action.mutate({ path: `/internal/fields/${f.id}/deprecate` })}>Deprecate</button>
                  )}
                  {admin && f.status === 'deprecated' && (
                    <button className="link" onClick={() => action.mutate({ path: `/internal/fields/${f.id}/restore` })}>Restore</button>
                  )}
                  {admin && !f.published && !f.system && (
                    <button className="link danger" onClick={() => del.mutate(f)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="muted small">{active.length} active fields.</div>

      <h3>Published versions</h3>
      <div className="card flush">
        <table className="table">
          <thead><tr><th>Version</th><th>Notes</th><th>Fields</th><th>Published</th></tr></thead>
          <tbody>
            {versions.data?.map((v) => (
              <tr key={v.version}>
                <td>v{v.version}</td>
                <td>{v.notes}</td>
                <td>{v.field_count}</td>
                <td className="small">{v.published_by_name} · {formatDate(v.published_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing !== undefined && (
        <FieldEditor
          field={editing}
          fields={fields}
          readOnly={!admin}
          onClose={() => {
            setEditing(undefined)
            refresh()
          }}
        />
      )}
    </div>
  )
}
